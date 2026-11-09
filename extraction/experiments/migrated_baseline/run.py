"""严格旧流程对照：隔离执行cb2e962原代码，仅注入当前中文提示词。"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import types
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
BASELINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'extraction'))
import prompts_zh as current_prompts
from openai import OpenAI

BASE_COMMIT = 'cb2e962b1ab836f8ba1060c25895124f43dd1b57'
MODULES = ('models', 'scientometrics_ontology', 'evidence_validation_agent',
           'local_formula_evidence', 'validation_agent', 'evaluation_agent',
           'triplet_metrics', 'extract_md_deepseek')


def frozen_source(name):
    """从本目录读取旧代码，校验清单，运行时不依赖Git或当前同名模块。"""
    manifest = json.loads((BASELINE_DIR / 'manifest.json').read_text())
    if manifest['base_commit'] != BASE_COMMIT or name not in MODULES:
        raise ValueError('旧流程代码清单与基准提交不一致')
    source = (BASELINE_DIR / 'frozen' / f'{name}.py').read_bytes()
    if hashlib.sha256(source).hexdigest() != manifest['source_sha256'][name]:
        raise ValueError(f'旧流程冻结源码已改变：{name}，请核对基准版本')
    return source


def prompt_adapter():
    """仅提示词适配：旧详情使用字符串，Validator使用旧流程专用模板。"""
    module = types.ModuleType('prompts_zh')
    for name, value in vars(current_prompts).items():
        if not name.startswith('__'):
            setattr(module, name, value)
    module.INDICATOR_DETAILS_PROMPT = (
        current_prompts.INDICATOR_DETAILS_PROMPT + '\nevidence 是一个连续原文字符串。\n'
    )
    module.VALIDATION_PROMPT = current_prompts.LEGACY_VALIDATION_PROMPT
    return module


@contextmanager
def legacy_runtime(sources=None):
    """暂时替换本地模块绑定；原源文件逐字编译，结束后恢复当前模块。不得并行使用。"""
    sources = sources or {name: frozen_source(name) for name in MODULES}
    names = ('prompts_zh',) + MODULES
    previous = {name: sys.modules.get(name) for name in names}
    loaded = {}
    try:
        sys.modules['prompts_zh'] = prompt_adapter()
        for name in MODULES:
            module = types.ModuleType(name)
            # 保留旧代码SCRIPT_DIR语义；不从工作区读取这些模块的实现。
            module.__file__ = str(ROOT / 'extraction' / f'{name}.py')
            sys.modules[name] = module
            exec(compile(sources[name], module.__file__, 'exec'), module.__dict__)
            loaded[name] = module
        yield loaded
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--markdown', type=Path)
    parser.add_argument('--output', '--output-root', dest='output', type=Path)
    parser.add_argument('--check-only', action='store_true', help='仅加载和核查原代码，不调用API、不创建实验目录')
    parser.add_argument('--paper-id')
    parser.add_argument('--date', default=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat())
    parser.add_argument('--model')
    parser.add_argument('--validator-model')
    parser.add_argument('--evaluator-model')
    parser.add_argument('--base-url')
    parser.add_argument('--threshold', type=float, default=80.0)
    parser.add_argument('--max-tokens', type=int, default=16384)
    parser.add_argument('--thinking', choices=('disabled','enabled'), default='disabled')
    parser.add_argument('--no-local-formula-completion', action='store_true')
    args = parser.parse_args()
    sources = {name: frozen_source(name) for name in MODULES}
    hashes = {name: hashlib.sha256(source).hexdigest() for name, source in sources.items()}
    with legacy_runtime(sources) as modules:
        old = modules['extract_md_deepseek']
        # 旧extract函数仍自行执行原来的实体清单措辞替换，不更改其函数体。
        if args.check_only:
            print(json.dumps(dict(base_commit=BASE_COMMIT, mode='严格旧流程＋当前中文提示词',
                frozen_modules=list(MODULES), source_sha256=hashes, api_calls=0,
                relation_evidence_schema='paragraph字符串', detail_evidence_schema='字符串',
                evidence_array_wrapping=False, candidate_review=False, evidence_retrieval=False,
                condition_role_selection=False, exclusivity='旧提交本体阶段检查，不使用验证后门控',
                judge_evidence='旧调用方式，直接传paragraph字符串，不添加多片段模板'), ensure_ascii=False, indent=2))
            return
        if not args.markdown or not args.output:
            parser.error('必须指定--markdown和新的--output目录')
        out = args.output.resolve()
        if out.exists():
            parser.error('目录已存在，不能覆盖历史实验；请指定新目录')
        markdown = args.markdown.expanduser().read_text(encoding='utf-8-sig')
        if not markdown.strip(): parser.error('原文为空')
        if not 0 <= args.threshold <= 100 or args.max_tokens <= 0:
            parser.error('阈值须为0至100，max-tokens须为正数')
        paper_id = args.paper_id or args.markdown.stem
        for label,value in [('date',args.date),('paper-id',paper_id)]:
            if not value or Path(value).name != value or value in ('.','..'):
                parser.error(f'{label}必须是单个非空目录或文件名')
        adapter = sys.modules['prompts_zh']
        prompt_names = ('SYSTEM_INSTRUCTION','METADATA_EXTRACTION','INDICATOR_DETAILS_PROMPT',
                        'ENTITY_EXTRACTION_PROMPT','KG_LINKING_PROMPT','VALIDATION_PROMPT','EVALUATION_PROMPT')
        prompts = {name:getattr(adapter,name) for name in prompt_names}
        save(out/'config.json',dict(mode='strict_legacy_current_prompts',base_commit=BASE_COMMIT,
            document_path=str(args.markdown.resolve()),document_sha256=hashlib.sha256(markdown.encode()).hexdigest(),
            base_source_sha256=hashes,prompt_sha256={k:hashlib.sha256(v.encode()).hexdigest() for k,v in prompts.items()},
            options={k:v for k,v in vars(args).items() if k not in ('markdown','output','base_url')},
            evidence_array_wrapping=False,post_validation_exclusivity=False,
            comparison_note='计算行为和Schema加载旧提交原实现；最新提示词不是旧版提示词复现。'))
        save(out/'injected_prompts.json',prompts)
        for name, source in sources.items():
            dest=out/'base_commit_code'/f'{name}.py';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(source)
        for name, source in [('run.py',Path(__file__).read_bytes()),('prompts_zh.py',(ROOT/'extraction/prompts_zh.py').read_bytes())]:
            dest=out/'launcher_code'/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(source)
        # 仅审计实际请求；不缓存或改写请求、响应、重试参数。
        def recording_client(*a, **kw):
            client=OpenAI(*a,**kw)
            create=client.chat.completions.create
            def recorded(**request):
                response=create(**request)
                save(out/'api_calls'/f'{uuid.uuid4().hex}.json',dict(request=request,response=response.model_dump()))
                return response
            client.chat.completions.create=recorded
            return client
        old.OpenAI=recording_client
        argv=['extract_md_deepseek.py','--markdown',str(args.markdown.expanduser()),'--output-root',str(out),
              '--date',args.date,'--paper-id',paper_id,'--threshold',str(args.threshold),
              '--max-tokens',str(args.max_tokens),'--thinking',args.thinking]
        for flag,value in [('--model',args.model),('--validator-model',args.validator_model),
                           ('--evaluator-model',args.evaluator_model),('--base-url',args.base_url)]:
            if value is not None: argv += [flag,value]
        if args.no_local_formula_completion: argv.append('--no-local-formula-completion')
        previous_argv=sys.argv
        try:
            sys.argv=argv
            old.main()  # 旧main原封不动：抽取→旧本体→旧模糊核验→旧Validator→旧Evaluator→旧table3。
            result=out/'evaluated_papers'/args.date/f'{paper_id}.json'
            data=json.loads(result.read_text());ts=data['kg_extraction_triplets']
            kept=[t for t in ts if all(t.get(f) for f in ('ontology_conformed','evidence_validated','valid'))]
            # 下列均为运行后的结果副本，不写回旧流程数据、不改变判定。
            save(out/'final_graph.json',dict(paper_entity=data.get('paper_entity'),entities_extracted=data['entities_extracted'],
                retained_triplets=kept,retention_rule='ontology_conformed AND evidence_validated AND valid；Evaluator不参与筛选'))
            summary=dict(complete=True,mode='strict_legacy_current_prompts',indicator_entities=len(data['entities_extracted']),
                candidates=len(ts),kept=len(kept),evaluator_positive_kept=sum(t['eval'] for t in kept),
                c_triplet=sum(t['eval'] for t in kept)/len(kept) if kept else None,
                evaluated_file=str(result),table3_file=str(out/'reports'/args.date/paper_id/'table3.md'),
                finished_utc=datetime.now(timezone.utc).isoformat())
            save(out/'summary.json',summary);save(out/'status.json',dict(complete=True));print(json.dumps(summary,ensure_ascii=False,indent=2))
        except BaseException as error:
            save(out/'status.json',dict(complete=False,error_type=type(error).__name__,message=str(error)))
            raise
        finally:
            sys.argv=previous_argv


if __name__=='__main__': main()

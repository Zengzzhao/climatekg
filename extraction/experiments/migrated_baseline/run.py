"""共享两请求抽取；保留隔离的旧本体、证据核验和Validator。"""
import argparse
import copy
import os
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
from extract_md_deepseek import extract as shared_extract
from evaluation_agent import evaluate_data as independent_evaluate_data
from llm_backend import add_backend_argument, configure_backend, adapt_request, openai_client as OpenAI

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
    """仅提示词适配：旧抽取与Validator使用专用模板，详情使用字符串。"""
    module = types.ModuleType('prompts_zh')
    for name, value in vars(current_prompts).items():
        if not name.startswith('__'):
            setattr(module, name, value)
    module.INDICATOR_DETAILS_PROMPT = (
        current_prompts.INDICATOR_DETAILS_PROMPT + '\nevidence 是一个连续原文字符串。\n'
    )
    module.KG_LINKING_PROMPT = current_prompts.LEGACY_KG_LINKING_PROMPT
    module.VALIDATION_PROMPT = current_prompts.LEGACY_VALIDATION_PROMPT
    return module


def legacy_compatible_extraction(data):
    """Keep the common first response; adapt only detail spans to the old consumer."""
    result = copy.deepcopy(data)
    for triplet in result['kg_extraction_triplets']:
        triplet.pop('evidence', None)
    for detail in result.get('indicator_details', []):
        for kind in ('definitions', 'formulas', 'limitations'):
            for claim in detail[kind]:
                spans = claim['evidence']
                if isinstance(spans, list):
                    claim['evidence'] = next((span for span in spans if claim['text'] in span), '')
    return result


def unified_legacy_extract(client, model, text, max_tokens, thinking='disabled',
                           complete_formulas=False):
    """Use the shared two-request extractor, retaining paragraph evidence."""
    data = shared_extract(client, model, text, max_tokens, thinking, relation_evidence=True)
    return legacy_compatible_extraction(data)


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
        # Keep extraction/Validator frozen, but use the same independent final
        # judge for paragraph and evidence inputs. Prior valid flags are preserved.
        loaded['evaluation_agent'].evaluate_data = independent_evaluate_data
        loaded['extract_md_deepseek'].evaluate_data = independent_evaluate_data
        loaded['extract_md_deepseek'].extract = unified_legacy_extract
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


def gate_validator_input(old):
    """Send only prior-stage survivors; retain excluded records for the audit."""
    original=old.validate_data
    def validate(data,client,provider,model):
        eligible=[]
        for t in data['kg_extraction_triplets']:
            passed=t.get('ontology_conformed') is True and t.get('evidence_validated') is True
            t['validator_called']=passed
            if passed:
                t.pop('validator_skip_reason',None)
                eligible.append(t)
            else:
                t.update(valid=False,validation_response='not_sent_prior_filters',
                    validator_skip_reason='ontology_failed' if t.get('ontology_conformed') is not True else 'evidence_match_failed')
        # The list is new; its triplet objects are shared with the audited input.
        return original(dict(data,kg_extraction_triplets=eligible),client,provider,model)
    old.validate_data=validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--markdown', type=Path)
    parser.add_argument('--output', '--output-root', dest='output', type=Path)
    parser.add_argument('--check-only', action='store_true', help='仅加载和核查原代码，不调用API、不创建实验目录')
    parser.add_argument('--entity-constrained-relations', action='store_true',
                        help='兼容旧命令；当前统一抽取始终约束实体清单端点')
    parser.add_argument('--ungated-validator',action='store_true',
                        help='Reproduce historical behavior: validate every extracted record, including prior-stage failures')
    parser.add_argument('--extract-only', action='store_true', help='仅运行联合清单与关系证据两次抽取')
    parser.add_argument('--paper-id')
    parser.add_argument('--date', default=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat())
    parser.add_argument('--model')
    parser.add_argument('--validator-model')
    parser.add_argument('--evaluator-model')
    parser.add_argument('--base-url')
    parser.add_argument('--threshold', type=float, default=80.0)
    parser.add_argument('--max-tokens', type=int, default=16384)
    parser.add_argument('--thinking', choices=('disabled','enabled'), default='disabled')
    # 接受历史命令中的开关。短证据模式始终关闭跨块公式补全。
    parser.add_argument('--no-local-formula-completion', action='store_true',
                        default=True, help=argparse.SUPPRESS)
    from dotenv import load_dotenv
    load_dotenv(ROOT / "extraction/.env")
    add_backend_argument(parser)
    args = parser.parse_args()
    sources = {name: frozen_source(name) for name in MODULES}
    hashes = {name: hashlib.sha256(source).hexdigest() for name, source in sources.items()}
    with legacy_runtime(sources) as modules:
        old = modules['extract_md_deepseek']
        mode = 'unified_legacy_paragraph'
        if not args.ungated_validator:
            gate_validator_input(old)
            mode += '_filtered_validator'
        # 冻结源码不改写；运行时仅用共享两请求抽取替换旧extract，后续处理保留。
        if args.check_only:
            print(json.dumps(dict(base_commit=BASE_COMMIT, mode=mode,
                entity_constrained_relations=True,extraction_requests=2,
                frozen_modules=list(MODULES), source_sha256=hashes, api_calls=0,
                relation_evidence_schema='paragraph字符串', detail_evidence_schema='联合请求为数组，旧消费者适配为单个连续字符串',
                local_formula_completion=False,
                evidence_array_wrapping=False, candidate_review=False, evidence_retrieval=False,
                condition_role_selection=False, exclusivity='旧提交本体阶段检查，不使用验证后门控',
                validator_input='all_extracted' if args.ungated_validator else 'ontology_conformed AND evidence_validated',
                judge_evidence='旧调用方式，直接传paragraph字符串，不添加多片段模板'), ensure_ascii=False, indent=2))
            return
        backend = configure_backend(args)
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
        save(out/'config.json',dict(service=backend.public_config(),mode=mode,base_commit=BASE_COMMIT,
            evaluator_implementation='shared_current_independent_evidence_judge',
            evaluator_source_sha256={name:hashlib.sha256((ROOT/'extraction'/name).read_bytes()).hexdigest()
                for name in ('evaluation_agent.py','evaluation_rubric.py')},
            entity_constrained_relations=True,extraction_requests=2,
            validator_input='all_extracted' if args.ungated_validator else 'ontology_conformed AND evidence_validated',
            document_path=str(args.markdown.resolve()),document_sha256=hashlib.sha256(markdown.encode()).hexdigest(),
            base_source_sha256=hashes,prompt_sha256={k:hashlib.sha256(v.encode()).hexdigest() for k,v in prompts.items()},
            options={k:v for k,v in vars(args).items() if k not in ('markdown','output','base_url')},
            evidence_array_wrapping=False,post_validation_exclusivity=False,
            unified_extractor_sha256=hashlib.sha256((ROOT/'extraction/extract_md_deepseek.py').read_bytes()).hexdigest(),
            extraction_only=args.extract_only,
            comparison_note='共享联合清单一次及基于清单的全部关系和paragraph一次；旧本体/证据匹配/Validator及共享独立Evaluator保留。'))
        save(out/'injected_prompts.json',prompts)
        save(out/'relation_stage_adapter.json',dict(endpoint_constraint='inventory',
            context_added=['entities_extracted','indicator_details'],
            extraction_requests=2,shared_joint_inventory=True,paragraph_schema_unchanged=True,
            downstream_stages_unchanged=True))
        for name, source in sources.items():
            dest=out/'base_commit_code'/f'{name}.py';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(source)
        for name, source in [('run.py',Path(__file__).read_bytes()),('prompts_zh.py',(ROOT/'extraction/prompts_zh.py').read_bytes()),
                             ('evaluation_agent.py',(ROOT/'extraction/evaluation_agent.py').read_bytes()),
                             ('evaluation_rubric.py',(ROOT/'extraction/evaluation_rubric.py').read_bytes()),
                             ('extract_md_deepseek.py',(ROOT/'extraction/extract_md_deepseek.py').read_bytes()),
                             ('entity_names.py',(ROOT/'extraction/entity_names.py').read_bytes()),
                             ('extraction_policy.py',(ROOT/'extraction/extraction_policy.py').read_bytes())]:
            dest=out/'launcher_code'/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(source)
        # 仅审计实际请求；不缓存或改写请求、响应、重试参数。
        save(out/'launcher_backend.json',backend.public_config())
        (out/'launcher_code/llm_backend.py').write_bytes((ROOT/'extraction/llm_backend.py').read_bytes())
        def recording_client(*a, **kw):
            client=OpenAI(*a,**kw)
            create=client.chat.completions.create
            def recorded(**request):
                request = adapt_request(request)
                response=create(**request)
                save(out/'api_calls'/f'{uuid.uuid4().hex}.json',dict(request=request,response=response.model_dump()))
                return response
            client.chat.completions.create=recorded
            return client
        old.OpenAI=recording_client
        if args.extract_only:
            try:
                with recording_client(api_key=os.environ['DEEPSEEK_API_KEY'],
                                      base_url=args.base_url, timeout=180, max_retries=2) as client:
                    data=old.extract(client,backend.model,markdown,args.max_tokens,args.thinking,
                                     complete_formulas=False)
                save(out/'01_direct_extraction.json',data)
                summary=dict(complete=True,scope='extraction_only',mode=mode,extraction_requests=2,
                    indicator_entities=len(data['entities_extracted']),
                    candidates=len(data['kg_extraction_triplets']),
                    finished_utc=datetime.now(timezone.utc).isoformat())
                save(out/'summary.json',summary)
                save(out/'status.json',dict(complete=True,scope='extraction_only'))
                print(json.dumps(summary,ensure_ascii=False,indent=2))
                return
            except BaseException as error:
                save(out/'status.json',dict(complete=False,error_type=type(error).__name__,message=str(error)))
                raise
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
            old.main()  # 两请求抽取适配→旧本体→旧模糊核验→旧Validator→共享Evaluator→旧table3。
            result=out/'evaluated_papers'/args.date/f'{paper_id}.json'
            data=json.loads(result.read_text());ts=data['kg_extraction_triplets']
            kept=[t for t in ts if all(t.get(f) for f in ('ontology_conformed','evidence_validated','valid'))]
            # 下列均为运行后的结果副本，不写回旧流程数据、不改变判定。
            save(out/'final_graph.json',dict(paper_entity=data.get('paper_entity'),entities_extracted=data['entities_extracted'],
                retained_triplets=kept,retention_rule='ontology_conformed AND evidence_validated AND valid；Evaluator不参与筛选'))
            summary=dict(complete=True,mode=mode,indicator_entities=len(data['entities_extracted']),
                candidates=len(ts),kept=len(kept),evaluator_positive_kept=sum(t['eval'] for t in kept),
                validator_input_count=sum(t.get('validator_called',True) for t in ts),
                validator_pass_count=sum(t.get('validator_called',True) and t['valid'] for t in ts),
                validator_reject_count=sum(t.get('validator_called',True) and not t['valid'] for t in ts),
                validator_skipped_count=sum(not t.get('validator_called',True) for t in ts),
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

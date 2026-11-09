"""逐篇运行当前科学计量新流程；每篇使用全文候选与条件证据选取。"""
import argparse
from collections import defaultdict
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo
from dotenv import dotenv_values

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
SINGLE_ENTRY=HERE/'experiments/full_first_paper.py'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def scan(root):
    papers=[];missing=[]
    for folder in sorted((p for p in root.iterdir() if p.is_dir()),key=lambda p:p.name):
        path=folder/'full.md'
        if not path.is_file():missing.append(folder.name);continue
        text=path.read_text(encoding='utf-8-sig')
        if not text.strip():raise ValueError(f'论文原文为空：{path}')
        relative=path.relative_to(root).as_posix()
        papers.append(dict(paper_id='paper_'+digest(relative.encode())[:16],title=folder.name,
            document_path=str(path.resolve()),relative_path=relative,
            document_sha256=digest(text.encode()),characters=len(text)))
    if len({p['paper_id'] for p in papers})!=len(papers):raise ValueError('论文编号发生碰撞')
    return papers,missing


def read(path):
    return json.loads(path.read_text())


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)


def inspect_attempts(directory):
    # 请求登记也占用一次尝试，覆盖子进程创建输出目录前就中断的情形。
    attempts=set(directory.glob('attempt_[0-9][0-9][0-9]')) if directory.exists() else set()
    if directory.exists():
        attempts.update(directory/p.name.removesuffix('.request.json')
                        for p in directory.glob('attempt_[0-9][0-9][0-9].request.json'))
    attempts=sorted(attempts)
    for attempt in attempts:
        summary=attempt/'summary.json';status=attempt/'status.json'
        if summary.is_file() and status.is_file() and read(summary).get('complete') and read(status).get('complete'):
            return attempt,read(summary),attempts
    return None,None,attempts


def load_stage_table(summary,directory=None):
    # 单篇入口的table3_file指向供人阅读的Markdown；结构化数据在同名JSON。
    path=Path(summary['table3_file']) if summary.get('table3_file') else Path(directory)/'table3.json'
    if path.suffix.lower()=='.md':path=path.with_suffix('.json')
    elif path.suffix.lower()!='.json':raise ValueError(f'阶段报告格式不支持：{path}')
    if not path.is_file():raise FileNotFoundError(f'阶段统计JSON不存在：{path}；论文结果保留，请检查报告文件')
    return read(path)


def same_experiment_config(previous,current):
    """仅允许调度器自身修复；单篇算法、提示词、模型及参数仍须逐项一致。"""
    key=str((HERE/'batch_extract.py').relative_to(ROOT))
    def protected(config):
        return dict(config,source_sha256={k:v for k,v in config['source_sha256'].items() if k!=key})
    return protected(previous)==protected(current)


def aggregate(out,papers,*,stem='batch'):
    records=[];stages={}
    for paper in papers:
        directory=out/'papers'/paper['paper_id']
        completed,summary,attempts=inspect_attempts(directory)
        row=dict(paper_id=paper['paper_id'],title=paper['title'],document_path=paper['document_path'],
                 attempts=len(attempts),status='completed' if completed else ('failed_or_interrupted' if attempts else 'pending'))
        if completed:
            row.update(result_directory=str(completed),summary=summary)
            table=load_stage_table(summary,completed)
            for stage in table['rows']:
                item=stages.setdefault(stage['stage'],dict(stage=stage['stage'],output_triplets=0,evaluator_correct=0))
                item['output_triplets']+=stage['output_triplets'];item['evaluator_correct']+=stage['evaluator_correct']
        records.append(row)
    complete=[r for r in records if r['status']=='completed']
    for stage in stages.values():
        n=stage['output_triplets'];stage['c_triplet_percent']=100*stage['evaluator_correct']/n if n else None
    kept=sum(r['summary']['kept'] for r in complete)
    positive=sum(r['summary']['evaluator_positive_kept'] for r in complete)
    result=dict(papers_total=len(papers),papers_completed=len(complete),
        papers_failed_or_interrupted=sum(r['status']=='failed_or_interrupted' for r in records),
        papers_pending=sum(r['status']=='pending' for r in records),
        candidates=sum(r['summary']['expanded_candidates'] if 'expanded_candidates' in r['summary']
                       else r['summary']['direct_candidates']
                       for r in complete),kept=kept,evaluator_positive_kept=positive,
        c_triplet=positive/kept if kept else None,stage_metrics=list(stages.values()),papers=records,
        complete=len(complete)==len(papers),
        note='只汇总完成论文，每篇取最早完成的一次，不按效果挑选；C_triplet按关系数汇总，不是逐论文百分比平均，也不是人工召回率。')
    save(out/f'{stem}_summary.json',result)
    title='# 当前科学计量新流程批量实验进度'+(f'（前{len(papers)}篇）' if stem!='batch' else '')
    lines=[title,'',
        f"论文总数：{len(papers)}；完成：{len(complete)}；失败或中断：{result['papers_failed_or_interrupted']}；待处理：{result['papers_pending']}。",'',
        '仅汇总已完成论文，失败不计作关系为假；未全部完成时不代表全数据集结果。','',
        '|阶段|关系数|Evaluator认可|C_triplet|','|---|---:|---:|---:|']
    for s in stages.values():
        rate=s['c_triplet_percent'];value=f'{rate:.2f}%' if rate is not None else '不适用'
        lines.append(f"|{s['stage']}|{s['output_triplets']}|{s['evaluator_correct']}|{value}|")
    lines+=['','每篇输入、输出与尝试次数见batch_summary.json，论文清单与冻结配置见manifest.json。','']
    report_name='report.md' if stem=='batch' else f'{stem}_report.md'
    (out/report_name).write_text('\n'.join(lines))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-root',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--dry-run',action='store_true',help='仅扫描和展示计划，不调用API、不创建输出目录')
    scope=parser.add_mutually_exclusive_group()
    scope.add_argument('--limit',type=int,help='截断实验清单，仅选前N篇；需要单独输出目录，后续不能在原目录扩展')
    scope.add_argument('--stop-after',type=int,help='保留完整清单，本次仅处理按标题排序的前N篇（包括已完成项）；可在同目录增大N续跑')
    parser.add_argument('--retry-failed',action='store_true',help='显式从头重试失败论文，保留原尝试目录；不重跑成功论文')
    parser.add_argument('--model')
    parser.add_argument('--workers',type=int,default=2,help='每篇内部验证／评估并发数；论文之间仍逐篇串行')
    args=parser.parse_args()
    root=args.input_root.expanduser().resolve();out=args.output.expanduser().resolve()
    if not root.is_dir():parser.error('input-root不是目录')
    if args.limit is not None and args.limit<1:parser.error('limit必须为正数')
    if args.stop_after is not None and args.stop_after<1:parser.error('stop-after必须为正数')
    if args.workers<1:parser.error('workers必须为正数')
    papers,missing=scan(root)
    if args.limit:papers=papers[:args.limit]
    if not papers:parser.error('没有找到论文子目录中的full.md')
    scheduled=papers[:args.stop_after] if args.stop_after is not None else papers
    duplicates=defaultdict(list)
    for p in papers:duplicates[p['document_sha256']].append(p['relative_path'])
    duplicate_groups=[v for v in duplicates.values() if len(v)>1]
    if args.dry_run:
        print(json.dumps(dict(input_root=str(root),papers_manifest=len(papers),papers_planned=len(scheduled),
            stop_after=args.stop_after,missing_full_md=missing,
            duplicate_document_groups=duplicate_groups,output=str(out),api_calls=0,
            execution='逐篇独立子进程，已完成项跳过；失败项仅在显式retry-failed时从头重试',
            examples=[dict(paper_id=p['paper_id'],title=p['title']) for p in scheduled[:3]]),ensure_ascii=False,indent=2))
        return
    env=dotenv_values(HERE/'.env')
    model=args.model or os.getenv('DEEPSEEK_MODEL') or env.get('DEEPSEEK_MODEL') or 'deepseek-chat'
    endpoint=os.getenv('DEEPSEEK_BASE_URL') or env.get('DEEPSEEK_BASE_URL') or 'https://api.deepseek.com'
    sources=[HERE/name for name in ['batch_extract.py','extract_md_deepseek.py','prompts_zh.py',
        'evidence_index.py','evidence_selection.py','evidence_schema.py',
        'relation_evidence.py','scientometrics_ontology.py','validation_agent.py','evaluation_agent.py',
        'triplet_metrics.py','experiments/full_first_paper.py']]
    config=dict(workflow='new_condition_paths',input_root=str(root),model=model,
        endpoint_sha256=digest(endpoint.encode()),workers=args.workers,
        selector='coherent',validator_feedback=True,targeted_expansion=True,
        pool_size=12,max_expansions=1,limit=6,max_chars=14000,
        max_tokens=16384,thinking='disabled',
        source_sha256={str(p.relative_to(ROOT)):digest(p.read_bytes()) for p in sources})
    manifest=dict(config=config,papers=papers,missing_full_md=missing,duplicate_document_groups=duplicate_groups,
                  run_date=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat())
    if out.exists() and not (out/'manifest.json').exists():parser.error('输出目录已存在但不是本批量入口目录，请换新目录')
    out.mkdir(parents=True,exist_ok=True)
    with (out/'.batch.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:parser.error('该批次已在运行，不能同时重复启动')
        if (out/'manifest.json').exists():
            previous=read(out/'manifest.json')
            if not same_experiment_config(previous['config'],config) or previous['papers']!=papers:
                parser.error('输入、提示词、代码或实验配置已变化，请换新输出目录，不能混合续跑')
            key=str((HERE/'batch_extract.py').relative_to(ROOT))
            old_digest=previous['config']['source_sha256'].get(key)
            new_digest=config['source_sha256'][key]
            if old_digest!=new_digest:
                # 原清单不回写；独立记录调度修复及当前源码，保留实验版本来历。
                audit=out/'orchestrator_revisions.json'
                revisions=read(audit) if audit.exists() else []
                if not revisions or revisions[-1]['current_sha256']!=new_digest:
                    revisions.append(dict(original_sha256=old_digest,current_sha256=new_digest,
                        recorded_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                        protected_experiment_config_unchanged=True,
                        note='仅批量调度／汇总代码更新；原始清单、单篇算法、提示词、模型参数及输入保持不变。'))
                    save(audit,revisions)
                snapshot=out/'orchestrator_snapshots'/f'{new_digest}.py'
                snapshot.parent.mkdir(parents=True,exist_ok=True);snapshot.write_bytes((HERE/'batch_extract.py').read_bytes())
                print('已记录批量调度器修复；单篇实验配置一致，继续使用原结果与目录。',flush=True)
            manifest=previous
        else:save(out/'manifest.json',manifest)
        def progress():
            full=aggregate(out,papers)
            selected=aggregate(out,scheduled,stem=f'first_{len(scheduled)}') if args.stop_after is not None else full
            return full,selected
        if args.stop_after is not None:
            print(f'完整清单{len(papers)}篇；本次仅处理前{len(scheduled)}篇，已完成项计入范围并跳过。',flush=True)
        progress()
        for i,paper in enumerate(scheduled,1):
            # 防止批次运行期间共享提示词／源码或未处理论文被修改。
            if any(digest((ROOT/name).read_bytes())!=h for name,h in config['source_sha256'].items()):
                raise RuntimeError('运行期间代码或提示词发生变化，已停止批次，原结果保留')
            if digest(Path(paper['document_path']).read_text(encoding='utf-8-sig').encode())!=paper['document_sha256']:
                raise RuntimeError('运行期间论文原文发生变化，已停止批次')
            directory=out/'papers'/paper['paper_id']
            completed,_,attempts=inspect_attempts(directory)
            if completed:
                print(f"[{i}/{len(scheduled)}] 跳过已完成：{paper['title']}",flush=True);continue
            if attempts and not args.retry_failed:
                print(f"[{i}/{len(scheduled)}] 保留失败记录，未自动重试：{paper['title']}",flush=True);continue
            directory.mkdir(parents=True,exist_ok=True)
            attempt=directory/f'attempt_{len(attempts)+1:03d}'
            command=[sys.executable,str(SINGLE_ENTRY),'--markdown',paper['document_path'],
                '--output',str(attempt),'--workers',str(args.workers)]
            log=directory/f'{attempt.name}.log'
            print(f"[{i}/{len(scheduled)}] 开始：{paper['title']}；日志：{log}",flush=True)
            # 先登记尝试，意外中断也不会在下次启动时无提示地重复付费。
            save(directory/f'{attempt.name}.request.json',dict(document_sha256=paper['document_sha256'],command=command))
            try:
                with log.open('w') as handle:
                    child_env=os.environ.copy()
                    child_env['DEEPSEEK_BASE_URL']=endpoint
                    child_env['DEEPSEEK_MODEL']=model
                    result=subprocess.run(command,cwd=ROOT,stdout=handle,stderr=subprocess.STDOUT,env=child_env)
            except KeyboardInterrupt:
                print('批次已中断，已完成论文保留；下次同命令跳过完成项。',flush=True)
                progress();raise
            except OSError as error:
                save(attempt/'status.json',dict(complete=False,error_type=type(error).__name__,message=str(error)))
                print(f"[{i}/{len(scheduled)}] 启动失败：{paper['title']}；{type(error).__name__}",flush=True)
                progress();continue
            # 若子进程在创建输出前失败，也保留一个失败目录。
            if not attempt.exists():save(attempt/'status.json',dict(complete=False,error_type='launcher_failed',returncode=result.returncode))
            completed,_,_=inspect_attempts(directory)
            print(f"[{i}/{len(scheduled)}] {'完成' if completed else '失败，详情见日志'}：{paper['title']}",flush=True)
            progress()
        summary,selected=progress()
        terminal={k:v for k,v in summary.items() if k not in ('papers','stage_metrics')}
        terminal['current_scope']=dict(papers_targeted=len(scheduled),papers_completed=selected['papers_completed'],
            complete=selected['complete'],summary_file=f'first_{len(scheduled)}_summary.json' if args.stop_after is not None else 'batch_summary.json')
        print(json.dumps(terminal,ensure_ascii=False,indent=2))
        if not selected['complete']:raise SystemExit(1)


if __name__=='__main__':main()

"""Frozen-candidate repair and full fresh entity->candidate->evidence experiments."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'extraction'))
from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat import ChatCompletion
from evidence_index import EvidenceIndex
from relation_evidence import collect_evidence
from evidence_schema import evidence_text
from prompts_zh import multi_evidence_evaluation_prompt
from validation_agent import parse_label
from extract_md_deepseek import extract,verify_evidence
from scientometrics_ontology import annotate_scientometrics


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');temp.replace(path)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['fixed','fresh','calibration'])
    p.add_argument('--output',type=Path,default=ROOT/'reports/condition_paths_2026-11-03/run1')
    p.add_argument('--workers',type=int,default=2)
    p.add_argument('--cache-root',type=Path,help='Reuse identical saved API requests; never change labels')
    args=p.parse_args();out=args.output;out.mkdir(parents=True,exist_ok=True)
    load_dotenv(ROOT/'extraction/.env');model=os.getenv('DEEPSEEK_MODEL','deepseek-chat')
    manifest=json.loads((ROOT/'reports/validator_false_negatives_2026-11-02/manifest.json').read_text())
    save(out/'manifest.json',manifest)
    for name in ['extract_md_deepseek.py','relation_evidence.py','evidence_index.py','evidence_schema.py','evidence_selection.py','prompts_zh.py','validation_agent.py','evaluation_agent.py','scientometrics_ontology.py']:
        path=out/'code'/name;raw=(ROOT/'extraction'/name).read_bytes();path.parent.mkdir(exist_ok=True)
        if path.exists() and path.read_bytes()!=raw:raise ValueError('Use a new output directory after code changes')
        path.write_bytes(raw)
    template=multi_evidence_evaluation_prompt()
    ref=ROOT/'reports/relation_evidence_final_2026-11-02/frozen_code/prompts_zh.py'
    spec=importlib.util.spec_from_file_location('frozen_rubric',ref);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    assert template==mod.multi_evidence_evaluation_prompt(),'Evaluator rubric changed'
    save(out/'config.json',dict(model=model,selector='condition-path coverage',max_expansions=1,
        judge_sha256=hashlib.sha256(template.encode()).hexdigest(),frozen_local=str(ROOT/'reports/relation_evidence_v2_2026-11-02')))
    lock=threading.Lock()
    def client():
        cl=OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'],base_url=os.getenv('DEEPSEEK_BASE_URL'),timeout=45,max_retries=0)
        create=cl.chat.completions.create
        def cached(**kw):
            key=hashlib.sha256(json.dumps(kw,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            path=out/'api_calls'/f'{key}.json'
            if path.exists():return ChatCompletion.model_validate(json.loads(path.read_text())['response'])
            if args.cache_root is not None:
                cached=args.cache_root/'api_calls'/f'{key}.json'
                if cached.exists():
                    saved=json.loads(cached.read_text());save(path,{**saved,'reused_from':str(cached)})
                    return ChatCompletion.model_validate(saved['response'])
            r=create(**kw);save(path,dict(request=kw,response=r.model_dump()));return r
        cl.chat.completions.create=cached
        return cl
    def judge(cl,t):
        prompt=template.format(source=t['source_entity_instance'],source_type=t['source_entity_type'],relation=t['relation'],target=t['target_entity_instance'],target_type=t['target_entity_type'],paragraph=evidence_text(t))
        h=hashlib.sha256((model+'\n'+prompt).encode()).hexdigest()
        for d in ['relation_evidence_audit_v4_2026-11-02','relation_evidence_audit_v3_2026-11-02','relation_evidence_audit_2026-11-02']:
            cached=ROOT/'reports'/d/'requests'/f'{h}.json'
            if cached.exists():return json.loads(cached.read_text())['supported']
        r=cl.chat.completions.create(model=model,temperature=0,max_tokens=128,extra_body={'thinking':{'type':'disabled'}},messages=[{'role':'user','content':prompt}])
        if r.choices[0].finish_reason!='stop':raise ValueError('Judge incomplete')
        return parse_label(r.choices[0].message.content or '')
    def fixed_paper(m):
        pid=m['paper_id'];dest=out/'fixed'/f'{pid}.json'
        if dest.exists():return json.loads(dest.read_text())
        text=Path(m['path']).read_text();assert hashlib.sha256(text.encode()).hexdigest()==m['sha256']
        index=EvidenceIndex(text);records=[];repaired={}
        with client() as cl:
            for n in range(1,4):
                old=json.loads((ROOT/f'reports/relation_evidence_v2_2026-11-02/round_{n}/{pid}.json').read_text())
                for c,original in zip(old['comparisons'],old['data']['kg_extraction_triplets']):
                    if not c['eligible']:continue
                    record=dict(round=n,paper=pid,index=c['index'],local=c['local_valid'],repeat=c['repeat_valid'],arms={})
                    key=tuple(original[k] for k in ('source_entity_instance','source_entity_type','relation','target_entity_instance','target_entity_type'))
                    for method in ['cover','topk']:
                        if c['local_valid']:
                            t=copy.deepcopy(original);t['valid']=True;t['eval']=judge(cl,t)
                        else:
                            if (key,method) not in repaired:
                                t=copy.deepcopy(original);t.pop('eval',None);t.pop('evaluation_response',None)
                                collect_evidence(t,index,cl,model,method=method)
                                t['eval']=judge(cl,t)
                                repaired[key,method]=t
                            t=copy.deepcopy(repaired[key,method])
                        record['arms'][method]=dict(valid=t['valid'],eval=t['eval'],triplet=t)
                    records.append(record)
        save(dest,records);print(pid,'fixed done',flush=True);return records
    def fresh_paper(m):
        pid=m['paper_id'];dest=out/'fresh'/f'{pid}.json'
        if dest.exists():return json.loads(dest.read_text())
        text=Path(m['path']).read_text();assert hashlib.sha256(text.encode()).hexdigest()==m['sha256']
        with client() as cl:
            extracted=out/'fresh_candidates'/f'{pid}.json'
            if extracted.exists():data=json.loads(extracted.read_text())
            else:
                data=extract(cl,model,text,16384);save(extracted,data)
            annotate_scientometrics(data)
            index=EvidenceIndex(text)
            # Pair both arms on the SAME new entity-constrained candidates.
            arms={}
            for method in ['cover','topk']:
                d=copy.deepcopy(data)
                for t in d['kg_extraction_triplets']:
                    if t['ontology_conformed']:
                        collect_evidence(t,index,cl,model,method=method)
                    else:t.update(evidence=[],evidence_validated=False,valid=False)
                    t['eval']=judge(cl,t)
                verify_evidence(d,text);arms[method]=d
        save(dest,arms);print(pid,'fresh done',flush=True);return arms
    def calibration():
        fixtures=json.loads((ROOT/'reports/baseline_optimization_v2_2026-11-02/calibration/cases.json').read_text())
        def case(c):
            path=out/'calibration'/f"{c['id']}.json"
            if path.exists():return json.loads(path.read_text())
            t=dict(source_entity_instance=c['source'],source_entity_type=c['source_type'],relation=c['relation'],target_entity_instance=c['target'],target_entity_type=c['target_type'])
            with client() as cl:collect_evidence(t,EvidenceIndex(c['paragraph']),cl,model)
            r=dict(id=c['id'],gold=c['gold'],predicted=t['valid'],triplet=t);save(path,r);return r
        with ThreadPoolExecutor(max_workers=args.workers) as pool:rows=list(pool.map(case,fixtures))
        save(out/'calibration/summary.json',dict(total=len(rows),correct=sum(r['gold']==r['predicted'] for r in rows),false_accept=sum(r['predicted'] and not r['gold'] for r in rows),false_reject=sum(not r['predicted'] and r['gold'] for r in rows)))
    if args.mode=='calibration':calibration()
    else:
        def safe_paper(m):
            try:return (fixed_paper if args.mode=='fixed' else fresh_paper)(m)
            except Exception as exc:
                error=dict(paper=m['paper_id'],error_type=type(exc).__name__,message=str(exc))
                save(out/'errors'/f"{args.mode}_{m['paper_id']}.json",error)
                return error
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            groups=list(pool.map(safe_paper,manifest))
        if any(isinstance(g,dict) and 'error_type' in g for g in groups):
            raise RuntimeError('Some papers failed; inspect errors/ and resume with the same directory.')
        metrics={}
        for method in ['cover','topk']:
            if args.mode=='fixed':ts=[r['arms'][method] for g in groups for r in g]
            else:ts=[t for g in groups for t in g[method]['kg_extraction_triplets'] if t['ontology_conformed'] and t['evidence_validated']]
            kept=[t for t in ts if t['valid']];correct=sum(t['eval'] for t in kept)
            metrics[method]=dict(kept=len(kept),evaluator_correct=correct,c_triplet=correct/len(kept) if kept else None)
        save(out/f'{args.mode}_summary.json',metrics);print(json.dumps(metrics),flush=True)

if __name__=='__main__':main()

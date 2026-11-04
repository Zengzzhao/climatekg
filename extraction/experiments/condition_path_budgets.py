"""Evidence-budget ablation on the initial identical retrieval/annotation pool.

No new role annotation. Final Validator and frozen Evaluator are independent;
evaluator decisions never affect evidence selection or retention.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'extraction'))
from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat import ChatCompletion
from evidence_index import EvidenceIndex
from evidence_selection import ROLES,paths_for,select_evidence
from relation_evidence import attach_evidence
from validation_agent import validate_triplet,parse_label
from prompts_zh import multi_evidence_evaluation_prompt
from evidence_schema import evidence_text


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,default=ROOT/'reports/condition_paths_2026-11-03/run2')
    p.add_argument('--offline',action='store_true',help='Replay role annotations: coverage and text cost only, no model verdicts')
    args=p.parse_args();load_dotenv(ROOT/'extraction/.env');model=os.getenv('DEEPSEEK_MODEL')
    manifest=json.loads((args.run/'manifest.json').read_text());indices={m['paper_id']:EvidenceIndex(Path(m['path']).read_text()) for m in manifest}
    global ROLES,paths_for,select_evidence
    spec=importlib.util.spec_from_file_location('frozen_selector',args.run/'code/evidence_selection.py')
    frozen=importlib.util.module_from_spec(spec);spec.loader.exec_module(frozen)
    ROLES,paths_for,select_evidence=frozen.ROLES,frozen.paths_for,frozen.select_evidence
    unique={}
    for f in (args.run/'fixed').glob('*.json'):
        for r in json.loads(f.read_text()):
            if r['local']:continue
            t=r['arms']['cover']['triplet']
            k=(r['paper'],t['source_entity_instance'],t['relation'],t['target_entity_instance'])
            unique[k]=t
    def work(job):
        key,t=job;index=indices[key[0]]
        roles=sorted({q for path in paths_for(t) for q in path})
        query=' '.join(roles)+' '+' '.join(ROLES[q] for q in roles)
        pool=index.rank(t,query,limit=12);ids={u.id for _,u in pool}
        supports=[s for s in t['evidence_completion']['supports'] if s['unit_id'] in ids]
        rows=[]
        with OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'],base_url=os.getenv('DEEPSEEK_BASE_URL'),timeout=180,max_retries=2) as cl:
            create=cl.chat.completions.create
            def cached(**kw):
                h=hashlib.sha256(json.dumps(kw,sort_keys=True,ensure_ascii=False).encode()).hexdigest();path=args.run/'api_calls'/f'{h}.json'
                if path.exists():return ChatCompletion.model_validate(json.loads(path.read_text())['response'])
                response=create(**kw);path.write_text(json.dumps(dict(request=kw,response=response.model_dump()),ensure_ascii=False,indent=2)+'\n');return response
            cl.chat.completions.create=cached
            for budget in [1,2,4,6]:
                for method in ['cover','topk']:
                    choice=select_evidence(t,pool,supports,method=method,limit=budget)
                    candidate={k:t[k] for k in ['source_entity_instance','source_entity_type','relation','target_entity_instance','target_entity_type']}
                    attach_evidence(candidate,index,pool,choice)
                    valid=None if args.offline else bool(choice['complete'] and parse_label(validate_triplet(cl,'deepseek',model,candidate)))
                    evaluated=None
                    if valid:
                        prompt=multi_evidence_evaluation_prompt().format(source=candidate['source_entity_instance'],source_type=candidate['source_entity_type'],relation=candidate['relation'],target=candidate['target_entity_instance'],target_type=candidate['target_entity_type'],paragraph=evidence_text(candidate))
                        response=cl.chat.completions.create(model=model,temperature=0,max_tokens=128,extra_body={'thinking':{'type':'disabled'}},messages=[{'role':'user','content':prompt}])
                        if response.choices[0].finish_reason!='stop':raise ValueError('Incomplete judge')
                        evaluated=parse_label(response.choices[0].message.content or '')
                    rows.append(dict(key=key,budget=budget,method=method,complete=choice['complete'],valid=valid,eval=evaluated,characters=choice['characters'],segments=len(choice['ids'])))
        return rows
    with ThreadPoolExecutor(max_workers=4) as pool:rows=[r for group in pool.map(work,unique.items()) for r in group]
    summary=[]
    for budget in [1,2,4,6]:
        for method in ['cover','topk']:
            rs=[r for r in rows if r['budget']==budget and r['method']==method]
            summary.append(dict(budget=budget,method=method,cases=len(rs),complete=sum(r['complete'] for r in rs),kept=None if args.offline else sum(r['valid'] for r in rs),correct=None if args.offline else sum(r['eval'] is True for r in rs)))
    (args.run/('budget_offline_summary.json' if args.offline else 'budget_summary.json')).write_text(json.dumps(dict(summary=summary,rows=rows),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,indent=2),flush=True)

if __name__=='__main__':main()

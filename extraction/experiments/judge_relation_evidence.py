"""Blind fixed-rubric evidence audit for both arms of a completed experiment.

The judge sees only candidate and its evidence, never prior flags, condition
reasons, silver labels or arm names. Identical requests share one saved response.
These are model-based evidence support ratings, not expert truth labels.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'extraction'))
from dotenv import load_dotenv
from openai import OpenAI
from prompts_zh import multi_evidence_evaluation_prompt
from relation_evidence import evidence_text
from validation_agent import parse_label


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    temp.replace(path)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiment',type=Path,default=ROOT/'reports/relation_evidence_v2_2026-11-02')
    p.add_argument('--cache-root',type=Path,nargs='*',default=[],help='Reuse identical blind-judge requests without changing their labels')
    p.add_argument('--output',type=Path,default=ROOT/'reports/relation_evidence_audit_2026-11-02')
    args=p.parse_args()
    load_dotenv(ROOT/'extraction/.env')
    model=os.getenv('DEEPSEEK_MODEL','deepseek-chat')
    template=multi_evidence_evaluation_prompt()
    summary=json.loads((args.experiment/'summary.json').read_text())
    jobs={}
    bindings=[]
    for file in sorted(args.experiment.glob('round_*/*.json')):
        data=json.loads(file.read_text())
        for comp,t in zip(data['comparisons'],data['data']['kg_extraction_triplets']):
            if not comp['eligible']:
                continue
            for arm,field in [('local','local_valid'),('completion','completed_valid')]:
                paragraph=t['paragraph'] if arm=='local' else evidence_text(t)
                prompt=template.format(source=t['source_entity_instance'],source_type=t['source_entity_type'],
                    relation=t['relation'],target=t['target_entity_instance'],target_type=t['target_entity_type'],paragraph=paragraph)
                h=hashlib.sha256((model+'\n'+prompt).encode()).hexdigest()
                jobs[h]=prompt
                bindings.append(dict(round=comp['round'],paper=comp['paper'],index=comp['index'],arm=arm,
                                     kept=comp[field],silver=comp['silver'],request_hash=h))
    def call(job):
        h,prompt=job
        path=args.output/'requests'/f'{h}.json'
        if path.exists():
            return h,json.loads(path.read_text())['supported']
        for cache in args.cache_root:
            cached = cache/'requests'/f'{h}.json'
            if cached.exists():
                row=json.loads(cached.read_text())
                assert row['prompt']==prompt
                save(path,{**row,'cached_from':str(cached)})
                return h,row['supported']
        with OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'],base_url=os.getenv('DEEPSEEK_BASE_URL'),timeout=180,max_retries=2) as cl:
            r=cl.chat.completions.create(model=model,temperature=0,max_tokens=128,extra_body={'thinking':{'type':'disabled'}},messages=[{'role':'user','content':prompt}])
        if r.choices[0].finish_reason!='stop':raise ValueError('Judge incomplete')
        raw=r.choices[0].message.content or ''
        verdict=parse_label(raw)
        save(path,dict(prompt=prompt,response=r.model_dump(),supported=verdict))
        return h,verdict
    with ThreadPoolExecutor(max_workers=6) as pool:
        verdicts=dict(pool.map(call,jobs.items()))
    for b in bindings:b['evidence_supported']=verdicts[b['request_hash']]
    metrics={}
    for arm in ['local','completion']:
        rows=[b for b in bindings if b['arm']==arm and b['kept']]
        correct=sum(b['evidence_supported'] for b in rows)
        metrics[arm]=dict(kept=len(rows),evidence_supported=correct,evidence_unsupported=len(rows)-correct,
                          c_triplet=correct/len(rows) if rows else None)
    save(args.output/'summary.json',dict(metrics=metrics,unique_judge_requests=len(jobs),bindings=bindings,
                                       template_sha256=hashlib.sha256(template.encode()).hexdigest()))
    print(json.dumps(metrics,indent=2),flush=True)

if __name__=='__main__':main()

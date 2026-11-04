"""Live condition-classifier calibration and fresh full-pipeline smoke experiments."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'extraction'))
from dotenv import load_dotenv
from openai import OpenAI
from relation_evidence import EvidenceIndex, complete_relation


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['calibration', 'fresh'])
    parser.add_argument('--output', type=Path, default=ROOT/'reports/relation_evidence_checks_2026-11-02')
    parser.add_argument('--manifest', type=Path, default=ROOT/'reports/validator_false_negatives_2026-11-02/manifest.json')
    args = parser.parse_args()
    load_dotenv(ROOT/'extraction/.env')
    model = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')
    args.output.mkdir(parents=True, exist_ok=True)
    names = ['relation_evidence.py', 'prompts_zh.py', 'extract_md_deepseek.py', 'scientometrics_ontology.py', 'validation_agent.py', 'evaluation_agent.py']
    for name in names:
        path = args.output/'frozen_code'/name
        raw = (ROOT/'extraction'/name).read_bytes()
        if path.exists() and path.read_bytes() != raw:
            raise ValueError('Code differs from snapshot: use a new output directory')
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
    if args.mode == 'calibration':
        # Authored in an earlier experiment before the current implementation.
        fixtures = json.loads((ROOT/'reports/baseline_optimization_v2_2026-11-02/calibration/cases.json').read_text())
        save(args.output/'calibration/cases.json', fixtures)
        def run(case):
            dst = args.output/'calibration'/f"{case['id']}.json"
            if dst.exists():
                return json.loads(dst.read_text())
            t = dict(source_entity_instance=case['source'], source_entity_type=case['source_type'],
                     relation=case['relation'], target_entity_instance=case['target'], target_entity_type=case['target_type'],
                     paragraph=case['paragraph'])
            cl = OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url=os.getenv('DEEPSEEK_BASE_URL'), timeout=180, max_retries=2)
            create = cl.chat.completions.create
            def traced(**kwargs):
                response = create(**kwargs)
                save(args.output/'api_calls'/(uuid.uuid4().hex+'.json'), dict(request=kwargs,response=response.model_dump()))
                return response
            cl.chat.completions.create = traced
            logs = []
            with cl:
                complete_relation(t, EvidenceIndex(case['paragraph']), cl, model, max_rounds=2, request_log=logs.append)
            data = dict(id=case['id'], gold=case['gold'], predicted=t['valid'], correct=case['gold']==t['valid'], triplet=t, requests=logs)
            save(dst, data)
            return data
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(run, fixtures))
        metrics = dict(total=len(rows), correct=sum(r['correct'] for r in rows),
                       false_accepts=sum(r['predicted'] and not r['gold'] for r in rows),
                       false_rejects=sum(not r['predicted'] and r['gold'] for r in rows))
        save(args.output/'calibration/summary.json', dict(metrics=metrics, rows=rows))
        print(json.dumps(metrics), flush=True)
    else:
        manifest = json.loads(args.manifest.read_text())
        save(args.output/'manifest.json', manifest)
        def run(m):
            pid = m['paper_id']
            text = Path(m['path']).read_bytes()
            assert hashlib.sha256(text).hexdigest() == m['sha256']
            dst = args.output/'fresh/pipeline/evaluated_papers/2026-11-02'/f'{pid}.json'
            if dst.exists():
                return dict(paper=pid,cached=True)
            log = args.output/'fresh'/f'{pid}.log'
            log.parent.mkdir(parents=True,exist_ok=True)
            cmd = [str(ROOT/'.venv/bin/python'), str(ROOT/'extraction/extract_md_deepseek.py'),
                   '--markdown',m['path'],'--paper-id',pid,'--date','2026-11-02',
                   '--output-root',str(args.output/'fresh/pipeline'),'--relation-evidence','--force']
            with log.open('w') as output:
                result = subprocess.run(cmd,cwd=ROOT,stdout=output,stderr=subprocess.STDOUT)
            if result.returncode:
                raise RuntimeError(f'Fresh pipeline failed for {pid}; see {log}')
            data = json.loads(dst.read_text())
            ts = data['kg_extraction_triplets']
            kept = [t for t in ts if t['ontology_conformed'] and t['evidence_validated'] and t['valid']]
            row = dict(paper=pid,candidates=len(ts),kept=len(kept),local_kept=sum(
                t['ontology_conformed'] and t.get('pre_completion',{}).get('evidence_validated',t['evidence_validated'])
                and t.get('pre_completion',{}).get('valid',t['valid']) for t in ts),
                evaluated_correct=sum(t['eval'] for t in kept),
                completed=sum(t.get('evidence_status')=='supported' for t in ts))
            print(row,flush=True)
            return row
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(run,manifest))
        save(args.output/'fresh/summary.json',rows)

if __name__=='__main__':
    main()

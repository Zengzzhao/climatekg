"""Fixed-candidate completion vs local reruns; full-document silver labels frozen first.

Run from repo root. Never modifies historical experiment outputs. Cached responses
are keyed by exact requests; candidate generation, evidence and evaluation kept apart.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'extraction'))
from dotenv import load_dotenv
from openai import OpenAI
from relation_evidence import complete_rejected, evidence_text
from scientometrics_ontology import annotate_scientometrics
from validation_agent import validate_triplet, parse_label
from prompts_zh import multi_evidence_evaluation_prompt, VALIDATION_PROMPT


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def key(t):
    return tuple(t[k] for k in ('source_entity_instance', 'source_entity_type', 'relation', 'target_entity_instance', 'target_entity_type'))


def run(args):
    load_dotenv(ROOT / 'extraction/.env')
    model = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text())
    save(out / 'manifest.json', manifest)
    save(out / 'config.json', dict(model=model, rounds=3, completion_rounds=args.rounds, silver_root=str(args.silver_root) if args.silver_root else None, local_root=str(args.local_root) if args.local_root else None,
        local_prompt_sha256=hashlib.sha256(VALIDATION_PROMPT.encode()).hexdigest(),
        evaluation_prompt=multi_evidence_evaluation_prompt(), source=str(args.baseline.resolve()),
        gold='full-document model silver labels, frozen before retrieval; not expert gold'))
    for name in ['relation_evidence.py', 'prompts_zh.py', 'scientometrics_ontology.py', 'extract_md_deepseek.py', 'evaluation_agent.py', 'validation_agent.py']:
        save_path = out / 'frozen_code' / name
        raw = (ROOT / 'extraction' / name).read_bytes()
        save_path.parent.mkdir(exist_ok=True)
        if save_path.exists() and save_path.read_bytes() != raw:
            raise ValueError('Code changed: choose a new experiment directory')
        save_path.write_bytes(raw)
    def client():
        cl = OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url=os.getenv('DEEPSEEK_BASE_URL'), timeout=180, max_retries=2)
        create = cl.chat.completions.create
        def traced(**kwargs):
            result = create(**kwargs)
            save(out / 'api_calls' / (uuid.uuid4().hex + '.json'), dict(
                request=kwargs, response=result.model_dump()))
            return result
        cl.chat.completions.create = traced
        return cl

    def paper(m):
        pid = m['paper_id']
        text = Path(m['path']).read_text(encoding='utf-8-sig')
        assert hashlib.sha256(text.encode()).hexdigest() == m['sha256']
        runs = []
        for n in range(1, 4):
            src = args.baseline / f'round_{n}/pipeline/evaluated_papers/2026-11-02' / f'{pid}.json'
            runs.append(json.loads(src.read_text()))
        candidates = {key(t): t for d in runs for t in d['kg_extraction_triplets']}
        keys = list(candidates)
        labels_path = out / 'silver' / f'{pid}.json'
        if labels_path.exists():
            labels = json.loads(labels_path.read_text())
        elif args.silver_root is not None:
            labels = json.loads((args.silver_root / f'{pid}.json').read_text())
            save(labels_path, labels)
        elif not keys:
            labels = []
            save(labels_path, labels)
        else:
            # One shared full-document judgment per unique relation, independent
            # of validator flags, retrieved contexts or experimental outcomes.
            items = [{**{f: t[f] for f in ('source_entity_instance', 'source_entity_type', 'relation', 'target_entity_instance', 'target_entity_type')}, 'id': i} for i, t in enumerate(candidates.values())]
            system = multi_evidence_evaluation_prompt().split('Source: {source}')[0] + '\nAssess every candidate independently against the ENTIRE supplied paper. Reply JSON: {"labels": [{"id": 0, "supported": true, "reason": "short grounded reason"}]}. Do not use external facts. All labels are independent.'
            prompt = json.dumps({'candidates': items, 'paper': text}, ensure_ascii=False)
            with client() as cl:
                r = cl.chat.completions.create(model=model, temperature=0, max_tokens=5000, response_format={'type': 'json_object'}, extra_body={'thinking': {'type': 'disabled'}}, messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}])
            if r.choices[0].finish_reason != 'stop':
                raise ValueError('Incomplete full document judge')
            labels = json.loads(r.choices[0].message.content)['labels']
            assert {l['id'] for l in labels} == set(range(len(keys))) and len(labels) == len(keys)
            assert all(type(l['supported']) is bool for l in labels)
            save(labels_path, labels)
            save(out / 'requests' / f'{pid}_silver.json', dict(system=system, prompt=prompt, raw=r.choices[0].message.content, usage=r.usage.model_dump()))
        silver = {keys[l['id']]: l['supported'] for l in labels}
        results = []
        for n, source in enumerate(runs, 1):
            path = out / f'round_{n}' / f'{pid}.json'
            if path.exists():
                results += json.loads(path.read_text())['comparisons']
                continue
            data = copy.deepcopy(source)
            annotate_scientometrics(data)
            comparisons = []
            frozen_local = None
            if args.local_root:
                frozen_local = json.loads((args.local_root / f'round_{n}' / f'{pid}.json').read_text())['comparisons']
                assert len(frozen_local) == len(data['kg_extraction_triplets'])
            with client() as cl:
                # Recheck local evidence in every candidate. Historical valid is
                # reported separately; the experiment baseline uses today's rubric.
                for i, t in enumerate(data['kg_extraction_triplets']):
                    if frozen_local is not None:
                        assert tuple(frozen_local[i]['key']) == key(t)
                        local = frozen_local[i]['local_valid']
                    else:
                        local = parse_label(validate_triplet(cl, 'deepseek', model, t))
                    comp = dict(round=n, paper=pid, index=i, key=key(t), silver=silver[key(t)],
                                historical_valid=t['valid'], local_valid=local,
                                eligible=bool(t['ontology_conformed'] and t['evidence_validated']))
                    t['valid'] = local
                    comparisons.append(comp)
                # Repeated-original control uses three extra votes. Joint validation
                # can add requests in completion; report actual costs separately.
                # No eval label enters recovery or its stopping rule.
                for i, (comp, t) in enumerate(zip(comparisons, data['kg_extraction_triplets'])):
                    if frozen_local is not None:
                        comp['repeat_valid'] = frozen_local[i]['repeat_valid']
                        comp['repeat_labels'] = frozen_local[i].get('repeat_labels', [])
                        continue
                    comp['repeat_valid'] = comp['local_valid']
                    if comp['eligible'] and not t['valid']:
                        control = [parse_label(validate_triplet(cl, 'deepseek', model, t)) for _ in range(args.rounds + 1)]
                        comp['repeat_labels'] = control
                        comp['repeat_valid'] = any(control)
                logs = []
                attempted, recovered = complete_rejected(data, text, cl, model, max_rounds=args.rounds, request_log=logs.append)
                for comp, t in zip(comparisons, data['kg_extraction_triplets']):
                    comp['completed_valid'] = t['valid']
                    comp['status'] = t.get('evidence_status', 'local')
                    comp['coverage'] = t.get('evidence_completion', {}).get('coverage')
            record = dict(comparisons=comparisons, data=data, attempted=attempted, recovered=recovered)
            save(path, record)
            save(out / 'requests' / f'{pid}_round_{n}.json', logs)
            results += comparisons
            print(pid, 'round', n, 'attempted', attempted, 'supported', recovered, flush=True)
        return results
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = [row for group in pool.map(paper, manifest) for row in group]
    metrics = {}
    for field in ('historical_valid', 'local_valid', 'repeat_valid', 'completed_valid'):
        kept = [r for r in rows if r['eligible'] and r[field]]
        tp = sum(r['silver'] for r in kept)
        positives = sum(r['silver'] and r['eligible'] for r in rows)
        metrics[field] = dict(kept=len(kept), silver_correct=tp, silver_incorrect=len(kept)-tp,
                             precision=tp/len(kept) if kept else None,
                             candidate_recall=tp/positives if positives else None)
    calls = [json.loads(p.read_text()) for p in (out / 'api_calls').glob('*.json')]
    usage = dict(requests=len(calls), prompt_tokens=sum(c['response']['usage']['prompt_tokens'] for c in calls),
                 completion_tokens=sum(c['response']['usage']['completion_tokens'] for c in calls))
    summary = dict(metrics=metrics, usage=usage, rows=rows,
                   recovered=[r for r in rows if r['eligible'] and not r['local_valid'] and r['completed_valid']])
    save(out / 'summary.json', summary)
    print(json.dumps(metrics, indent=2), flush=True)

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, default=ROOT/'reports/validator_false_negatives_2026-11-02/manifest.json')
    p.add_argument('--baseline', type=Path, default=ROOT/'reports/early_stage_optimization_final_2026-11-02')
    p.add_argument('--output', type=Path, default=ROOT/'reports/relation_evidence_v1_2026-11-02')
    p.add_argument('--rounds', type=int, default=2)
    p.add_argument('--local-root', type=Path, help='Reuse frozen local and repetition controls for the same exact candidate records')
    p.add_argument('--silver-root', type=Path, help='Frozen full-document labels from an earlier run of these exact candidates')
    run(p.parse_args())

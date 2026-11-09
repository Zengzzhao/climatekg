"""Fresh first-paper pipeline, with new-run-only API caching and stage checkpoints.

Never loads historical entities, candidates or verdicts. On interruption, resume the
same code/output directory; no failure is counted as a semantic rejection.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'extraction'))
from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat import ChatCompletion
from extract_md_deepseek import extract, verify_evidence
from evidence_index import EvidenceIndex
from scientometrics_ontology import annotate_scientometrics, enforce_relation_exclusivity
from relation_evidence import collect_evidence
from evaluation_agent import evaluate_triplet
from validation_agent import parse_label
from prompts_zh import VALIDATION_PROMPT, multi_evidence_evaluation_prompt
from triplet_metrics import table3, write_report


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--markdown', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--workers', type=int, default=2)
    args = p.parse_args()
    if args.workers < 1:
        p.error('--workers must be positive')
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    text = args.markdown.read_text(encoding='utf-8-sig')
    index = EvidenceIndex(text)
    load_dotenv(ROOT / 'extraction/.env')
    model = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')
    config = dict(document_path=str(args.markdown.resolve()), document_sha256=index.sha256,
        characters=len(text), model=model, workers=args.workers, fresh_entity_extraction=True,
        historical_candidates_used=False, external_cache_used=False,
        selector='coherent', validator_feedback=True, targeted_expansion=True,
        pool_size=12, max_expansions=1, limit=6, max_chars=14000,
        validator_sha256=hashlib.sha256(VALIDATION_PROMPT.encode()).hexdigest(),
        evaluator_sha256=hashlib.sha256(multi_evidence_evaluation_prompt().encode()).hexdigest())
    path = out / 'config.json'
    if path.exists() and json.loads(path.read_text()) != config:
        raise ValueError('Config/source changed: choose a new output directory')
    save(path, config)
    for name in ['extract_md_deepseek.py', 'evidence_index.py', 'evidence_selection.py',
                 'evidence_schema.py', 'relation_evidence.py', 'scientometrics_ontology.py',
                 'prompts_zh.py', 'validation_agent.py', 'evaluation_agent.py', 'triplet_metrics.py',
                 'experiments/full_first_paper.py']:
        raw = (ROOT / 'extraction' / name).read_bytes()
        dest = out / 'code' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.read_bytes() != raw:
            raise ValueError('Code changed: choose a new output directory')
        dest.write_bytes(raw)
    def client():
        cl = OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url=os.getenv('DEEPSEEK_BASE_URL'),
                    timeout=90, max_retries=0)
        create = cl.chat.completions.create
        def cached(**kw):
            h = hashlib.sha256(json.dumps(kw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            dest = out / 'api_calls' / f'{h}.json'
            if dest.exists():
                return ChatCompletion.model_validate(json.loads(dest.read_text())['response'])
            r = create(**kw)
            save(dest, dict(request=kw, response=r.model_dump()))
            return r
        cl.chat.completions.create = cached
        return cl
    started = time.monotonic()
    stage = 'extraction'
    try:
        dest = out / '01_direct_extraction.json'
        if dest.exists():
            data = json.loads(dest.read_text())
        else:
            with client() as cl:
                data = extract(cl, model, text, 16384)
            save(dest, data)
        print('Fresh entities:', len(data['entities_extracted']), 'direct candidates:',
              len(data['kg_extraction_triplets']), flush=True)
        stage = 'ontology'
        annotate_scientometrics(data)
        save(out / '03_ontology.json', data)
        stage = 'evidence_and_validation'
        def validate(item):
            i, original = item
            dest = out / 'validation_checkpoints' / f'{i:04d}.json'
            if dest.exists():
                return json.loads(dest.read_text())
            t = copy.deepcopy(original)
            if t['ontology_conformed']:
                with client() as cl:
                    collect_evidence(t, index, cl, model)
            else:
                t.update(evidence=[], evidence_validated=False, valid=False,
                         evidence_status='not_in_ontology', validation_response='not_in_ontology')
            save(dest, t)
            print(f"Validator {i}: {t['source_entity_instance']} {t['relation']} {t['target_entity_instance']} = {t['valid']}", flush=True)
            return t
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            data['kg_extraction_triplets'] = list(pool.map(validate, enumerate(data['kg_extraction_triplets'])))
        verify_evidence(data, text)
        enforce_relation_exclusivity(data)
        save(out / '04_validated.json', data)
        stage = 'evaluation'
        def evaluate(item):
            i, original = item
            dest = out / 'evaluation_checkpoints' / f'{i:04d}.json'
            if dest.exists():
                return json.loads(dest.read_text())
            t = copy.deepcopy(original)
            with client() as cl:
                t['evaluation_response'] = evaluate_triplet(cl, 'deepseek', model, t)
            t['eval'] = parse_label(t['evaluation_response'])
            save(dest, t)
            print(f'Evaluator {i}: {t["eval"]}', flush=True)
            return t
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            data['kg_extraction_triplets'] = list(pool.map(evaluate, enumerate(data['kg_extraction_triplets'])))
        save(out / '05_evaluated.json', data)
        rows = table3(data['kg_extraction_triplets'])
        write_report(rows, out, 'table3', [out / '05_evaluated.json'])
        kept = [t for t in data['kg_extraction_triplets'] if all(t[f] for f in ('ontology_conformed', 'evidence_validated', 'valid'))]
        save(out / 'final_graph.json', dict(title=data['title'], paper_entity=data['paper_entity'],
            entities_extracted=data['entities_extracted'], retained_triplets=kept,
            retention_rule='ontology_conformed AND evidence_validated AND valid; eval is not a filter'))
        summary = dict(complete=True, indicator_entities=len(data['entities_extracted']),
            direct_candidates=len(json.loads((out / '01_direct_extraction.json').read_text())['kg_extraction_triplets']),
            kept=len(kept), evaluator_positive_kept=sum(t['eval'] for t in kept),
            c_triplet=sum(t['eval'] for t in kept)/len(kept) if kept else None,
            elapsed_seconds_this_invocation=round(time.monotonic()-started, 2),
            finished_utc=datetime.now(timezone.utc).isoformat())
        save(out / 'summary.json', summary)
        save(out / 'status.json', dict(complete=True))
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    except Exception as exc:
        save(out / 'status.json', dict(complete=False, stage=stage, error_type=type(exc).__name__, message=str(exc)))
        raise


if __name__ == '__main__':
    main()

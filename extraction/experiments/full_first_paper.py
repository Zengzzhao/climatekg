"""Fresh first-paper pipeline, with new-run-only API caching and stage checkpoints.

Never loads historical entities, candidates or verdicts. On interruption, resume the
same code/output directory; no failure is counted as a semantic rejection.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
import hashlib
from functools import partial
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
from relation_evidence import annotate_pool, collect_evidence
from evidence_selection import select_evidence
from validation_agent import validate_relation_conditions
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
    p.add_argument('--compact-bge', type=Path, help='Use the tuned 350-character first pool')
    p.add_argument('--repair-attempts', type=int, default=0,
                   help='Fresh attempts for malformed model responses')
    args = p.parse_args()
    if args.workers < 1 or args.repair_attempts < 0:
        p.error('Invalid worker or repair count')
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
    if args.compact_bge:
        from dataclasses import asdict
        from evidence_retrieval import FrozenBGE, retrieval_query
        from relation_chain_first_pool import CompactEvidenceIndex, BEST_FIRST_POOL_CONFIG, BEST_FIRST_POOL_PACK
        config['compact_retrieval'] = dict(config=asdict(BEST_FIRST_POOL_CONFIG), pack=BEST_FIRST_POOL_PACK,
                                           revision=FrozenBGE.revision, model=FrozenBGE.model,
                                           validator='conditions')
        config['repair_attempts'] = args.repair_attempts
    path = out / 'config.json'
    if path.exists() and json.loads(path.read_text()) != config:
        raise ValueError('Config/source changed: choose a new output directory')
    save(path, config)
    names = ['extract_md_deepseek.py', 'evidence_index.py', 'evidence_selection.py',
                 'evidence_schema.py', 'relation_evidence.py', 'scientometrics_ontology.py',
                 'prompts_zh.py', 'validation_agent.py', 'evaluation_agent.py', 'triplet_metrics.py',
                 'experiments/full_first_paper.py']
    if args.compact_bge:
        names += ['evidence_chunking.py','evidence_retrieval.py','experiments/relation_chain_first_pool.py']
    for name in names:
        raw = (ROOT / 'extraction' / name).read_bytes()
        dest = out / 'code' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.read_bytes() != raw:
            raise ValueError('Code changed: choose a new output directory')
        dest.write_bytes(raw)

    def perform(operation):
        cl = OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url=os.getenv('DEEPSEEK_BASE_URL'),
                    timeout=180, max_retries=2 if args.compact_bge else 0)
        create = cl.chat.completions.create
        last = None
        def cached(**kw):
            nonlocal last
            h = hashlib.sha256(json.dumps(kw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            dest = out / 'api_calls' / f'{h}.json'
            last = dest
            if dest.exists():
                return ChatCompletion.model_validate(json.loads(dest.read_text())['response'])
            r = create(**kw)
            save(dest, dict(request=kw, response=r.model_dump()))
            return r
        cl.chat.completions.create = cached
        with cl:
            for attempt in range(args.repair_attempts+1):
                last = None
                try:
                    return operation(cl)
                except (ValueError, KeyError) as exc:
                    if attempt==args.repair_attempts or last is None or not last.exists():
                        raise
                    audit = out/'malformed_responses'/f'{last.stem}-{uuid.uuid4().hex}.json'
                    audit.parent.mkdir(parents=True,exist_ok=True)
                    last.replace(audit)
                    save(audit.with_suffix('.error.json'),dict(error=str(exc),cached_request=last.name))
    started = time.monotonic()
    stage = 'extraction'
    try:
        dest = out / '01_direct_extraction.json'
        if dest.exists():
            data = json.loads(dest.read_text())
        else:
            data = perform(lambda cl:extract(cl, model, text, 16384))
            save(dest, data)
        print('Fresh entities:', len(data['entities_extracted']), 'direct candidates:',
              len(data['kg_extraction_triplets']), flush=True)
        stage = 'ontology'
        annotate_scientometrics(data)
        save(out / '03_ontology.json', data)
        stage = 'evidence_and_validation'
        oriented = {}
        if args.compact_bge:
            index = CompactEvidenceIndex(text,FrozenBGE(args.compact_bge))
            for i,t in enumerate(data['kg_extraction_triplets']):
                if not t['ontology_conformed'] or (out/'validation_checkpoints'/f'{i:04d}.json').exists():
                    continue
                oriented[i] = index.orient_alternative(t) if t['relation']=='alternative_to' else dict(t)
                index.rank(oriented[i],retrieval_query(oriented[i]))

        def validate(item):
            i, original = item
            dest = out / 'validation_checkpoints' / f'{i:04d}.json'
            if dest.exists():
                return json.loads(dest.read_text())
            t = copy.deepcopy(original)
            if t['ontology_conformed']:
                def collect(cl):
                    candidate = copy.deepcopy(oriented.get(i,original))
                    if args.compact_bge:
                        collect_evidence(candidate,index,cl,model,
                            annotator=partial(annotate_pool,decomposed=True,task_scoped=True),
                            selector=partial(select_evidence,decomposed=True,task_scoped=True,
                                             explicit_variant_scopes=True),
                            validator=partial(validate_relation_conditions,cl,model))
                        candidate['evidence_completion']['proof_orientation'] = [
                            candidate['source_entity_instance'],candidate['target_entity_instance']]
                        for field in ('source_entity_instance','target_entity_instance'):
                            candidate[field] = original[field]
                    else:
                        collect_evidence(candidate,index,cl,model)
                    return candidate
                t = perform(collect)
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
            def evaluate_once(cl):
                response = evaluate_triplet(cl,'deepseek',model,t)
                return response,parse_label(response)
            t['evaluation_response'],t['eval'] = perform(evaluate_once)
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

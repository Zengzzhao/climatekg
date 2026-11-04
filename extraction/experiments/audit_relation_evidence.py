"""Offline grounding/retrieval audit on a supplied manifest and saved candidates.

No API calls, judgments or performance claims. Retrieval is run with all necessary
conditions unknown to audit budgets and source provenance before live diagnosis.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'extraction'))
from relation_evidence import EvidenceIndex, requirements, verify_units


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, default=ROOT/'reports/validator_false_negatives_2026-11-02/manifest.json')
    p.add_argument('--baseline', type=Path, default=ROOT/'reports/early_stage_optimization_final_2026-11-02')
    p.add_argument('--output', type=Path, default=ROOT/'reports/relation_evidence_offline_2026-11-02/audit.json')
    args = p.parse_args()
    records = []
    for m in json.loads(args.manifest.read_text()):
        text = Path(m['path']).read_text(encoding='utf-8-sig')
        assert hashlib.sha256(text.encode()).hexdigest() == m['sha256']
        index = EvidenceIndex(text)
        for n in range(1, 4):
            path = args.baseline/f"round_{n}/pipeline/evaluated_papers/2026-11-02/{m['paper_id']}.json"
            for i, t in enumerate(json.loads(path.read_text())['kg_extraction_triplets']):
                units = index.seed(t['paragraph'])
                for _ in range(2):
                    new = index.retrieve(t, list(requirements(t['relation'])), {u.id for u in units}, 4)
                    for u in new:
                        if sum(len(x.text) for x in units) + len(u.text) <= 18000:
                            units.append(u)
                record = dict(paper=m['paper_id'], round=n, index=i,
                    grounded=verify_units({'evidence_units': [asdict(u) for u in units]}, text),
                    unique=len({u.id for u in units}) == len(units),
                    characters=sum(len(u.text) for u in units),
                    sections=sorted({u.section for u in units}),
                    units=[asdict(u) for u in units])
                assert record['grounded'] and record['unique'] and record['characters'] <= 18000
                records.append(record)
    result = dict(candidate_records=len(records), grounded_records=sum(r['grounded'] for r in records),
                  multi_section_records=sum(len(r['sections']) > 1 for r in records),
                  max_characters=max((r['characters'] for r in records), default=0),
                  caveat='Offline source/budget checks only; no relation verdict or recall/precision measured.',
                  records=records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'records'}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()

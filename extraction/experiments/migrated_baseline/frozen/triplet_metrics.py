"""Table-3 cumulative attrition and evaluator-judged triplet correctness.

C_triplet = 100 * evaluator-positive triplets / all triplets surviving that stage.
The evaluator is the judge, not a filter. Counts refer to extracted records, not
unique graph edges. Batch rates use pooled counts (micro averaging).
"""
import argparse
import csv
import io
import json
from pathlib import Path

STAGES = [
    ('Triplet and evidence extractor agent', None),
    ('Ontology conformance agent', 'ontology_conformed'),
    ('Evidence validator agent', 'evidence_validated'),
    ('Validator agent', 'valid'),
]
FLAGS = ('ontology_conformed', 'evidence_validated', 'valid', 'eval')


def table3(triplets):
    if not isinstance(triplets, list):
        raise ValueError('kg_extraction_triplets must be a list')
    for i, t in enumerate(triplets):
        if not isinstance(t, dict):
            raise ValueError(f'Triplet {i}: expected object')
        for flag in FLAGS:
            if type(t.get(flag)) is not bool:
                raise ValueError(f'Triplet {i}: {flag} must be present and boolean; complete all stages first')
    rows=[]
    survivors=triplets
    for name, flag in STAGES:
        count=len(survivors) if flag else None
        if flag:
            survivors=[t for t in survivors if t[flag]]
        output=len(survivors)
        correct=sum(t['eval'] for t in survivors)
        rows.append(dict(stage=name,input_triplets=count,
                         filtered=count-output if flag else None,
                         output_triplets=output,evaluator_correct=correct,
                         c_triplet_percent=100*correct/output if output else None))
    return rows


def markdown_table(rows):
    lines=['| Stage | Input Triplets | Filtered | Output Triplets | Evaluator Correct | C_triplet (%) |',
           '|---|---:|---:|---:|---:|---:|']
    for r in rows:
        rate=r['c_triplet_percent']
        fields=[r['stage'],r['input_triplets'],r['filtered'],r['output_triplets'],r['evaluator_correct'],
                f'{rate:.2f}' if rate is not None else 'N/A']
        lines.append('| '+' | '.join('—' if f is None else str(f) for f in fields)+' |')
    return '\n'.join(lines)+'\n'


def write_report(rows, directory, stem, sources):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    payload={'metric':'C_triplet (%)','formula':'100 * evaluator_correct / output_triplets',
             'aggregation':'pooled triplet records (micro average); no deduplication',
             'zero_denominator':'null / N/A','sources':[str(p) for p in sources],'rows':rows}
    (directory/f'{stem}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (directory/f'{stem}.md').write_text(markdown_table(rows),encoding='utf-8')
    stream=io.StringIO(newline='')
    writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (directory/f'{stem}.csv').write_text(stream.getvalue(),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root',type=Path,default=Path(__file__).resolve().parent/'output')
    parser.add_argument('--dates',nargs='+',required=True,help='Evaluated date partitions to pool')
    parser.add_argument('--paper-id',help='Optional exact paper filename stem, within every selected partition')
    parser.add_argument('--report-dir',type=Path,help='Export JSON, CSV and Markdown reports here')
    args=parser.parse_args()
    paths=[]
    for date in args.dates:
        directory=args.output_root/'evaluated_papers'/date
        selected=([directory/f'{args.paper_id}.json'] if args.paper_id else sorted(directory.glob('*.json')))
        if not selected or any(not path.is_file() for path in selected):
            parser.error(f'No matching evaluated files in {directory}; run evaluation_agent.py first')
        paths.extend(selected)
    paths=sorted(set(paths))
    triplets=[]
    for path in paths:
        data=json.loads(path.read_text(encoding='utf-8'))
        records=data.get('kg_extraction_triplets')
        try:table3(records)
        except ValueError as exc:parser.error(f'{path}: {exc}')
        triplets.extend(records)
    rows=table3(triplets)
    print(f'{len(paths)} papers, {len(triplets)} candidate triplet records\n')
    print(markdown_table(rows))
    if args.report_dir:
        write_report(rows,args.report_dir,'table3',paths)


if __name__=='__main__':main()

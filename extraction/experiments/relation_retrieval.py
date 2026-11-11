"""Two-relation retrieval benchmark. Human annotations are evaluation-only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'extraction'))
from evidence_chunking import atoms
from evidence_index import EvidenceIndex, EvidenceUnit, reading_form
from evidence_schema import verify_units


def save(path, data):
    from full_first_paper import save as atomic_save
    atomic_save(path, data)


def annotation_key(text):
    text = reading_form(text.replace(r'\\', '\\'))
    return re.sub(r'[^a-z0-9]', '', text)


def ground_annotation(text, markdown, spans):
    key = annotation_key(text)
    ranked = []
    for i, (lo, _, section) in enumerate(spans):
        for _, hi, end_section in spans[i:i+8]:
            if section != end_section:
                break
            actual = annotation_key(markdown[lo:hi])
            if not .88 * len(key) <= len(actual) <= 1.14 * len(key):
                continue
            score = SequenceMatcher(None, key, actual, autojunk=False).ratio()
            ranked.append((score, lo, hi, section))
    ranked.sort(reverse=True)
    if not ranked or ranked[0][0] < .95 or (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < .02):
        raise ValueError(f'Uncertain gold mapping: {text[:125]!r}, {ranked[:2]}')
    score, lo, hi, section = ranked[0]
    return dict(annotation=text, alignment=score,
                unit=asdict(EvidenceUnit(f'gold-{lo}', lo, hi, section, markdown[lo:hi])))


def annotations(path, markdown):
    data = [json.loads(re.sub(r'(?<!\\)\\([()])', r'\\\\\1', re.sub(r',\s*$', '', x.strip())))
            for x in re.findall(r'```json\s*([\s\S]*?)```', path.read_text())]
    partials = {x['relation']: x for x in data if 'paragraph' in x}
    spans = atoms(markdown)
    cases = {}
    for x in data:
        if 'evidence' not in x:
            continue
        relation = x['type']
        cases[relation] = dict(candidate=dict(
            source_entity_instance=x['source'], source_entity_type='Indicator',
            target_entity_instance=x['target'], target_entity_type='Indicator',
            relation=relation, ontology_conformed=True),
            gold=[ground_annotation(t, markdown, spans) for t in x['evidence']],
            partial=ground_annotation(partials[relation]['paragraph'], markdown, spans))
    return cases


def metrics(units, gold):
    """Count each reference span once, including when adjacent units jointly cover it."""
    ranges = []
    for lo, hi in sorted((u['start'], u['end']) for u in units):
        if ranges and lo <= ranges[-1][1]:
            ranges[-1][1] = max(hi, ranges[-1][1])
        else:
            ranges.append([lo, hi])
    covered = []
    best_unit = []
    for g in gold:
        lo, hi = g['unit']['start'], g['unit']['end']
        ranges_for_g = [(a, b) for a, b in ranges if a < hi and lo < b]
        cursor = lo
        complete = True
        for a, b in ranges_for_g:
            if g['unit']['text'][cursor-lo:max(cursor, a)-lo].strip():
                complete = False
            cursor = max(cursor, min(b, hi))
        covered.append(bool(complete and not g['unit']['text'][cursor-lo:].strip()))
        best_unit.append(any(u['start'] <= lo and u['end'] >= hi for u in units))
    gold_ranges = sorted({(g['unit']['start'], g['unit']['end']) for g in gold})
    relevant = sum(max(0, min(b, y)-max(a, x)) for a, b in ranges for x, y in gold_ranges)
    total = sum(b-a for a, b in ranges)
    return dict(hit=sum(covered), count=len(covered), recall=sum(covered)/len(covered),
                hit_mask=covered, single_unit_hits=sum(best_unit), unique_chars=total,
                char_precision=relevant/total if total else 0, input_chars=sum(len(u['text']) for u in units))


def api_client(out, tag='primary'):
    from dotenv import load_dotenv
    from openai import OpenAI
    from openai.types.chat import ChatCompletion
    load_dotenv(ROOT / 'extraction/.env')
    endpoint = os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
    url = urlparse(endpoint)
    if url.scheme != 'https' or url.netloc != 'api.deepseek.com':
        raise ValueError('This experiment is scoped to api.deepseek.com')
    model = os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash')
    cl = OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url=endpoint, timeout=75, max_retries=1)
    create = cl.chat.completions.create

    def cached(**kwargs):
        digest = hashlib.sha256(json.dumps(kwargs, sort_keys=True).encode()).hexdigest()
        path = out / 'api' / tag / f'{digest}.json'
        if path.exists():
            return ChatCompletion.model_validate(json.loads(path.read_text())['response'])
        response = create(**kwargs)
        save(path, dict(request=kwargs, response=response.model_dump()))
        return response

    cl.chat.completions.create = cached
    return cl, model


def run_controls(cases, markdown, out, plan=None, tag='primary', judge='current'):
    from validation_agent import validate_relation_conditions, validate_triplet_with_feedback
    validate = validate_relation_conditions if judge=='conditions' else validate_triplet_with_feedback

    def run(task):
        relation, name, candidate, units = task
        triplet = dict(candidate, evidence=sorted(units, key=lambda u: u['start']))
        assert verify_units(triplet, markdown)
        client, model = api_client(out, tag)
        with client:
            result = validate(client, model, triplet)
        directory = 'controls' if judge=='current' else 'controls_conditions'
        save(out / directory / f'{relation}-{name}.json', dict(triplet=triplet, result=result))
        print(relation, name, result['verdict'], result['reason'], flush=True)

    tasks = [(r, name, c['candidate'], units) for r, c in cases.items()
             for name, units in [('gold', [g['unit'] for g in c['gold']]),
                                 ('partial', [c['partial']['unit']])]]
    if plan:
        for row in json.loads(plan.read_text()):
            relation = row['relation']
            case = cases[relation]
            units = [g['unit'] for g in case['gold']]
            for lo, hi in row.get('append_offsets', []):
                units.append(asdict(EvidenceUnit(f'control-{lo}', lo, hi, 'control', markdown[lo:hi])))
            candidate = dict(case['candidate'], **row.get('candidate_overrides', {}))
            tasks.append((relation,row['name'],candidate,units))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, tasks))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--markdown', type=Path, required=True)
    p.add_argument('--annotations', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--phase', choices=['align', 'controls', 'sweep', 'live'], default='align')
    p.add_argument('--bge', type=Path)
    p.add_argument('--plan', type=Path)
    p.add_argument('--tag', default='primary')
    p.add_argument('--judge', choices=['current','conditions'], default='current')
    args = p.parse_args()
    markdown = args.markdown.read_text(encoding='utf-8-sig')
    cases = annotations(args.annotations, markdown)
    save(args.output / 'alignment.json', dict(document_sha256=EvidenceIndex(markdown).sha256, cases=cases))
    if args.phase == 'controls':
        run_controls(cases, markdown, args.output, args.plan, args.tag, args.judge)
    elif args.phase == 'sweep':
        sweep(args, markdown, cases)
    elif args.phase == 'live':
        run_live(args, markdown, cases)


def rank_budget(units, max_chars):
    selected = []
    for unit in units:
        length = len(unit['text'])
        if length <= max_chars:
            selected.append(unit)
            max_chars -= length
    return selected


def config_id(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]


def run_live(args, markdown, cases):
    from functools import partial
    from evidence_retrieval import FrozenBGE, RetrievalConfig, RetrievalIndex, retrieval_query
    from evidence_selection import select_evidence
    from relation_evidence import annotate_pool, collect_evidence
    from validation_agent import validate_relation_conditions

    if not args.plan:
        raise ValueError('The live phase requires --plan')
    plan = json.loads(args.plan.read_text())
    plan_dir = args.output/'live'/args.tag
    save(plan_dir/'plan.json', plan)
    embedder = FrozenBGE(args.bge) if args.bge else None
    prepared = []
    for entry in plan:
        config = RetrievalConfig(**entry['config'])
        index = RetrievalIndex(markdown, config, embedder)
        for relation in entry.get('relations', cases):
            case = cases[relation]
            # Materialize the primary rank locally before launching API workers.
            candidate = case['candidate']
            index.rank(candidate, retrieval_query(candidate), limit=12)
            prepared.append((entry, index, relation, case))

    def run(task):
        entry, index, relation, case = task
        destination = plan_dir / f"{entry['name']}-{relation}.json"
        if destination.exists():
            return json.loads(destination.read_text())
        candidate = dict(case['candidate'])
        client, model = api_client(args.output, args.tag)
        guided = entry.get('decomposed', False)
        task_scoped = entry.get('task_scoped', False)
        with client:
            result = collect_evidence(candidate, index, client, model, max_rounds=0,
                annotator=partial(annotate_pool,decomposed=guided,task_scoped=task_scoped),
                selector=partial(select_evidence,decomposed=guided,task_scoped=task_scoped),
                validator=partial(validate_relation_conditions,client,model) if args.judge=='conditions' else None)
        stage = result['evidence_completion']['trace'][0]
        pool = stage['retrieved_evidence']
        measured = dict(method=entry['name'], config=asdict(index.config), relation=relation,
            decomposed=guided, task_scoped=task_scoped, judge=args.judge,
            pool=metrics(pool,case['gold']), final=metrics(result['evidence'],case['gold']),
            valid=result['valid'], exact_source=result['evidence_validated'],
            selected_ids=stage['selection']['ids'], path_coverage=stage['selection']['coverage'],
            verdict=stage['validator_feedback'], result=result)
        save(destination, measured)
        print(entry['name'], relation, measured['pool']['hit'], measured['final']['hit'],
              measured['valid'], stage['validator_feedback']['reason'], flush=True)
        return measured

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, prepared))
    save(plan_dir/'summary.json', [{k:v for k,v in row.items() if k!='result'} for row in results])


def sweep(args, markdown, cases):
    from dataclasses import replace
    from statistics import mean
    from evidence_retrieval import FrozenBGE, RetrievalConfig, RetrievalIndex, retrieval_query

    embedder = FrozenBGE(args.bge) if args.bge else None
    index_by_chunk = {}
    rows = {}

    def index_for(config):
        key = config.strategy, config.chars, config.sentences
        if key not in index_by_chunk:
            index_by_chunk[key] = RetrievalIndex(markdown, config, embedder)
        index = index_by_chunk[key]
        index.config = config
        return index

    def measure(config, phase):
        settings = asdict(config)
        key = config_id(settings)
        if key in rows:
            return
        index = index_for(config)
        results = {}
        for r, case in cases.items():
            candidate = case['candidate']
            pool = [asdict(u) for _,u in index.rank(candidate, retrieval_query(candidate), limit=12)]
            results[r] = dict(pool=metrics(pool, case['gold']), top6=metrics(pool[:6], case['gold']),
                at_1200=metrics(rank_budget(pool, 1200), case['gold']),
                at_1600=metrics(rank_budget(pool, 1600), case['gold']),
                at_2400=metrics(rank_budget(pool, 2400), case['gold']),
                full_recall_rank=next((k for k in range(1,13) if metrics(pool[:k],case['gold'])['recall']==1),None),
                ids=[u['id'] for u in pool], spans=[(u['start'],u['end']) for u in pool])
        rows[key] = dict(id=key, phase=phase, config=settings, results=results, n_units=len(index.units),
            oversize=sum(len(u.text)>config.chars for u in index.units),
            max_unit_chars=max(len(u.text) for u in index.units),
            macro_recall=mean(v['pool']['recall'] for v in results.values()),
            macro_recall_at_6=mean(v['top6']['recall'] for v in results.values()),
            macro_recall_at_1200=mean(v['at_1200']['recall'] for v in results.values()),
            macro_recall_at_1600=mean(v['at_1600']['recall'] for v in results.values()),
            macro_recall_at_2400=mean(v['at_2400']['recall'] for v in results.values()),
            macro_char_precision=mean(v['pool']['char_precision'] for v in results.values()),
            mean_input_chars=mean(v['pool']['input_chars'] for v in results.values()))

    def objective(row):
        return (row['macro_recall'], row['macro_recall_at_1600'], row['macro_recall_at_1200'],
                row['macro_recall_at_2400'], row['macro_recall_at_6'],
                row['macro_char_precision'], -row['mean_input_chars'])

    methods = ('sparse','bm25','dense','hybrid','facets','chains')
    baseline = RetrievalConfig()
    measure(baseline, 'original')
    if args.plan:
        for entry in json.loads(args.plan.read_text()):
            measure(RetrievalConfig(**entry['config']), entry.get('name','focus'))
    else:
        layouts = [('legacy',2600,3)] + [
            ('char', cap, 3) for cap in (1200,800,500,350,220)
        ] + [
            (strategy, cap, count) for strategy in ('sentence','window','context','bounded')
            for cap,count in ((1200,5),(800,4),(500,3),(500,2),(350,2),(220,1))
        ]
        for strategy, cap, count in layouts:
            layout = replace(baseline, strategy=strategy, chars=cap, sentences=count)
            configs = [
                layout,
                replace(layout, method='sparse', query_style='facets'),
                replace(layout, method='bm25'),
                replace(layout, method='bm25', query_style='legacy'),
                replace(layout, method='facets', dense=0.),
                replace(layout, method='chains', dense=0.),
            ]
            if embedder:
                configs.extend(replace(layout, method=method) for method in ('dense','hybrid','facets','chains'))
            for config in configs:
                measure(config, 'initial')
            print('initial', strategy, cap, count, len(rows), flush=True)

        for method in methods:
            method_rows = sorted((v for v in rows.values() if v['config']['method']==method),
                                 key=objective, reverse=True)
            seeds = []
            for row in method_rows:
                cfg = RetrievalConfig(**row['config'])
                layout_key = cfg.strategy, cfg.chars, cfg.sentences
                if layout_key not in [(c.strategy,c.chars,c.sentences) for c in seeds]:
                    seeds.append(cfg)
                if len(seeds) == 3:
                    break
            for cfg in seeds:
                if method == 'sparse':
                    for mention in (.0,.5,1.,2.,3.):
                        for lexical,structure in ((.5,0.),(1.,.25),(1.5,.5),(2.,0.)):
                            measure(replace(cfg,mention=mention,lexical=lexical,structure=structure), 'refine')
                elif method == 'bm25':
                    for k1 in (.6,1.2,1.8,2.4):
                        for b in (.0,.4,.75,1.):
                            measure(replace(cfg,k1=k1,b=b), 'refine')
                elif method == 'hybrid':
                    for dense in (.15,.3,.5,.7,.85):
                        measure(replace(cfg,dense=dense), 'refine')
                elif method in ('facets','chains'):
                    for dense in ((0.,.25,.5,.75,1.) if embedder else (0.,)):
                        for anchor in (.0,.3,.65,.9):
                            for weight in ((.25,.5,.75,.9) if method=='facets' else (.0,.35,.75,1.5)):
                                settings = {'coverage':weight} if method=='facets' else {'edge':weight}
                                measure(replace(cfg,dense=dense,anchor=anchor,**settings), 'refine')
                    finalists = sorted((v for v in rows.values() if v['config']['method']==method
                        and (v['config']['strategy'],v['config']['chars'],v['config']['sentences'])
                        == (cfg.strategy,cfg.chars,cfg.sentences)),key=objective,reverse=True)
                    best = RetrievalConfig(**finalists[0]['config'])
                    for temp in (.025,.08,.2):
                        for redundancy in (.0,.15,.4,.65):
                            measure(replace(best,temperature=temp,redundancy=redundancy), 'refine')
                    if method=='chains':
                        for beam in (4,8,16,24,32):
                            measure(replace(best,beam=beam), 'refine')

    ordered = sorted(rows.values(),key=objective,reverse=True)
    best_by_method = {m:next((v for v in ordered if v['config']['method']==m),None) for m in methods}
    best_small = {m:next((v for v in ordered if v['config']['method']==m and v['max_unit_chars']<=500),None)
                  for m in methods}
    original = rows[config_id(asdict(baseline))]
    # Retain full matched-layout comparisons and gold-free pool provenance by offset.
    save(args.output/'sweep.json',dict(embedding_model=FrozenBGE.model if embedder else None,
        embedding_revision=FrozenBGE.revision if embedder else None, original=original,
        best_by_method=best_by_method,best_strict_500=best_small,rows=ordered))
    for key,row in [('original',original),*best_by_method.items()]:
        if row:
            print(key,row['id'],row['config']['strategy'],row['config']['chars'],
                  [x['pool']['hit'] for x in row['results'].values()],
                  round(row['macro_recall_at_2400'],3),round(row['macro_char_precision'],3),
                  row['mean_input_chars'],flush=True)


if __name__ == '__main__':
    main()

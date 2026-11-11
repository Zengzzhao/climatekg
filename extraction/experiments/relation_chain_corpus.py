"""Fixed-candidate corpus benchmark for relation-conditioned evidence retrieval."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from difflib import SequenceMatcher
from functools import partial
import hashlib
import itertools
import json
from pathlib import Path
import re
from statistics import mean
import numpy as np

from relation_retrieval import api_client, config_id, metrics as span_metrics, rank_budget, save
from evidence_chunking import blocks
from evidence_index import EvidenceUnit
from evidence_retrieval import (FrozenBGE, RELATION_CHAIN_350, RetrievalConfig, RetrievalIndex,
                                cosine_matrix, minmax, retrieval_query)
from evidence_schema import verify_units
from evidence_selection import select_evidence
from relation_evidence import annotate_pool, collect_evidence
from validation_agent import validate_relation_conditions

FORMAT = re.compile(r'\\(?:mathrm|mathbf|mathit|operatorname|left|right|text)\b')
JSON_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"', re.S)
ESCAPES = {'\\':'\\', 'n':'\n', '"':'"', '/':'/'}


def unpack_annotated_block(raw):
    """Read the labeled fields and evidence despite mixed JSON and TeX escaping."""
    fields = {name: re.search(fr'"{name}"\s*:\s*"([^"]+)"', raw)[1]
              for name in ('source','target','type')}
    array = re.search(r'"evidence"\s*:\s*\[([\s\S]*?)\]\s*,\s*"evdience_zh"', raw)[1]
    fields['evidence'] = [
        re.sub(r'\\(\\|n(?![a-zA-Z])|"|/)',lambda m: ESCAPES[m[1]],match[1])
        for match in JSON_STRING.finditer(array)
    ]
    return fields


def canonical(text):
    removed = [(m.start(), m.end()) for m in FORMAT.finditer(text)]
    i = 0
    chars, positions = [], []
    for pos, char in enumerate(text):
        if i < len(removed) and pos >= removed[i][1]:
            i += 1
        if i < len(removed) and removed[i][0] <= pos < removed[i][1]:
            continue
        if char.isalnum():
            chars.append(char.lower())
            positions.append(pos)
    return ''.join(chars), positions


def align(text, markdown, view, sections):
    key, _ = canonical(text)
    haystack, positions = view
    hits = [m.start() for m in re.finditer(re.escape(key),haystack)]
    if hits:
        ranked = [(1.,i,i+len(key)) for i in hits]
    else:
        # Vote on approximate starts using exact anchors, then align at those locations.
        candidates = set()
        n = len(key)
        for k in range(0,max(1,n-15),max(8,n//35)):
            token = key[k:k+16]
            if len(token)<10:
                continue
            candidates.update(max(0,m.start()-k) for m in re.finditer(re.escape(token),haystack))
        aligned = {}
        for start in sorted(candidates):
            padding = max(12,n//15)
            left = max(0,start-padding)
            right = min(len(haystack),start+n+padding)
            match = SequenceMatcher(None,key,haystack[left:right],autojunk=False)
            chunks = match.get_matching_blocks()[:-1]
            if not chunks:
                continue
            lo = left+chunks[0].b
            hi = left+chunks[-1].b+chunks[-1].size
            score = 2*sum(c.size for c in chunks)/(n+hi-lo)
            aligned[lo,hi] = max(score,aligned.get((lo,hi),0.))
        ranked = sorted(((score,lo,hi) for (lo,hi),score in aligned.items()),reverse=True)
    if not ranked:
        raise ValueError(f'No alignment: {text[:150]}')
    score,left,right = ranked[0]
    lo,hi = positions[left],positions[right-1]+1
    # Include formula delimiters or sentence punctuation immediately adjoining the quote.
    lo -= len(re.search(r'(?:\\(?:mathrm|mathbf|mathit)\{?|\$\$?|\{)?$',markdown[:lo])[0])
    ending = re.match(r'[.)!?,;}"”’\$]*',markdown[hi:])[0]
    hi += len(ending)
    section = next((s for a,b,s in sections if a<=lo<b),'front matter')
    other = next(((v,positions[a],positions[b-1]+1) for v,a,b in ranked[1:] if abs(a-left)>max(10,len(key)//2)),None)
    return dict(annotation=text,alignment=score,runner_up=other,
        unit=asdict(EvidenceUnit(f'gold-{lo}',lo,hi,section,markdown[lo:hi])))


def load_cases(annotation_path, corpus, overrides=None):
    papers = {}
    cases = {}
    overrides = overrides or {}
    annotated = annotation_path.read_text(encoding='utf-8-sig')
    for section in re.split(r'^# (?=\d)',annotated,flags=re.M)[1:]:
        paper = int(section.splitlines()[0])
        path = Path(re.search(r"'(/[^'\n]+\.(?:md|pdf))'",section)[1])
        if path.suffix=='.pdf':
            path = corpus/path.stem/'full.md'
        markdown = path.read_text(encoding='utf-8-sig')
        papers[paper] = dict(path=str(path),markdown=markdown,sha256=hashlib.sha256(markdown.encode()).hexdigest())
        view = canonical(markdown)
        sections = [(block.start(),block.end(),name) for block,name in blocks(markdown)]
        for number, raw in enumerate(re.findall(r'```json\s*([\s\S]*?)```',section),1):
            x = unpack_annotated_block(raw)
            cid = f'p{paper:02d}-{number}'
            gold = []
            for i, quote in enumerate(x['evidence'],1):
                row = align(quote,markdown,view,sections)
                manual = overrides.get(f'{cid}.{i}')
                if manual:
                    row['automatic'] = row.pop('unit')
                    row['parts'] = [dict(unit=asdict(EvidenceUnit(f'gold-{lo}',lo,hi,
                         row['automatic']['section'],markdown[lo:hi]))) for lo,hi in manual['spans']]
                    row['adjudication'] = manual['reason']
                gold.append(row)
            cases[cid] = dict(paper=paper,candidate=dict(
                source_entity_instance=x['source'],source_entity_type='Paper' if x['source']=='Paper' else 'Indicator',
                target_entity_instance=x['target'],target_entity_type='Indicator',
                relation=x['type'],ontology_conformed=True),gold=gold)
    if len(papers)!=8 or len(cases)!=12:
        raise ValueError(f'Expected eight papers and 12 candidates, got {len(papers)}, {len(cases)}')
    return papers,cases


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--annotations',type=Path,required=True)
    p.add_argument('--corpus',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--bge',type=Path)
    p.add_argument('--phase',choices=['align','frozen','sweep','live','final'],required=True)
    p.add_argument('--plan',type=Path)
    p.add_argument('--overrides',type=Path)
    p.add_argument('--tag',default='first')
    p.add_argument('--workers',type=int,default=6)
    args = p.parse_args()
    if (args.out/'alignment.json').exists() and args.phase!='align':
        data = json.loads((args.out/'alignment.json').read_text())
        papers = {int(k):v for k,v in data['papers'].items()}
        cases = data['cases']
        for paper in papers.values():
            paper['markdown'] = Path(paper['path']).read_text(encoding='utf-8-sig')
            if hashlib.sha256(paper['markdown'].encode()).hexdigest()!=paper['sha256']:
                raise ValueError(f"Corpus changed: {paper['path']}")
        if hashlib.sha256(args.annotations.read_bytes()).hexdigest()!=data['annotations_sha256']:
            raise ValueError('Annotation source changed')
    else:
        papers,cases = load_cases(args.annotations,args.corpus,
                                  json.loads(args.overrides.read_text()) if args.overrides else None)
        save(args.out/'alignment.json',dict(papers={k:{a:b for a,b in v.items() if a!='markdown'}
            for k,v in papers.items()},cases=cases,
            annotations_sha256=hashlib.sha256(args.annotations.read_bytes()).hexdigest()))
    if args.phase=='align':
        for cid, case in cases.items():
            print(cid,[(round(g['alignment'],4),[(z['unit']['start'],z['unit']['end']) for z in g.get('parts',[g])]) for g in case['gold']],flush=True)
    else:
        run(args,papers,cases)


def metrics(units,gold):
    parts = [g.get('parts',[g]) for g in gold]
    result = span_metrics(units,[span for group in parts for span in group])
    hits = iter(result['hit_mask'])
    grouped = [all(list(itertools.islice(hits,len(group)))) for group in parts]
    return dict(result,hit=sum(grouped),count=len(gold),recall=mean(grouped),hit_mask=grouped)


def configs(phase,plan):
    if plan:
        return json.loads(plan.read_text())
    old = RELATION_CHAIN_350
    rows = [dict(name='frozen_B',config=asdict(old))]
    if phase=='frozen':
        return rows
    layouts = [('legacy',2600,3),('char',350,2),('char',500,3),('sentence',500,2),
               ('window',500,3),('context',350,2),('context',500,3)]
    layouts += [('bounded',cap,n) for cap,n in ((220,1),(350,2),(500,2),(500,3),(800,4),(1200,5))]
    for strategy,chars,sentences in layouts:
        for method in ('sparse','bm25','dense','hybrid','facets','chains'):
            cfg = replace(old if method=='chains' else RetrievalConfig(method=method),
                          strategy=strategy,chars=chars,sentences=sentences)
            rows.append(dict(name=f'{method}_{strategy}_{chars}_{sentences}',config=asdict(cfg)))
    for cap,n in ((350,2),(500,2),(800,4)):
        base=replace(old,chars=cap,sentences=n)
        for dense,edge,anchor in itertools.product((0,.25,.5,.75,1.),(0,.5,1.,1.5,2.5),(0,.3,.65)):
            rows.append(dict(name=f'grid_{cap}_{dense}_{edge}_{anchor}',
                             config=asdict(replace(base,dense=dense,edge=edge,anchor=anchor))))
        for key,values in dict(temperature=(.025,.2),redundancy=(0,.6),beam=(4,8,32)).items():
            for v in values:
                rows.append(dict(name=f'abl_{cap}_{key}_{v}',config=asdict(replace(base,**{key:v}))))
        for k1,b in itertools.product((.6,1.2,1.8),(0,.4,.75)):
            rows.append(dict(name=f'bm25_tuned_{cap}_{k1}_{b}',config=asdict(
                RetrievalConfig(method='bm25',strategy='bounded',chars=cap,sentences=n,k1=k1,b=b))))
        for weight in (.15,.3,.7,.85):
            rows.append(dict(name=f'hybrid_tuned_{cap}_{weight}',config=asdict(
                RetrievalConfig(method='hybrid',strategy='bounded',chars=cap,sentences=n,dense=weight))))
    return rows


def aggregate(rows,stage='pool'):
    return dict(hit=sum(r[stage]['hit'] for r in rows),count=sum(r[stage]['count'] for r in rows),
                macro=mean(r[stage]['recall'] for r in rows),
                complete=sum(r[stage]['recall']==1 for r in rows),
                mean_chars=mean(r[stage]['input_chars'] for r in rows))


def offline(args,papers,cases):
    entries = configs(args.phase,args.plan)
    embedder = FrozenBGE(args.bge)
    results = {e['name']:dict(e,cases={}) for e in entries}
    for pid,paper in papers.items():
        index_by_layout = {}
        seen = {}
        for entry in entries:
            cfg = RetrievalConfig(**entry['config'])
            key = (cfg.strategy,cfg.chars,cfg.sentences)
            if key not in index_by_layout:
                index_by_layout[key] = RetrievalIndex(paper['markdown'],cfg,embedder)
            index = index_by_layout[key]
            index.config = cfg
            for cid,case in cases.items():
                if case['paper']!=pid:
                    continue
                cache_key=(cfg,cid)
                if cache_key not in seen:
                    c=case['candidate']
                    if c['relation']=='alternative_to' and cfg.method=='chains':
                        c=index.orient_alternative(c)
                    pool=[asdict(u) for _,u in index.rank(c,retrieval_query(c),limit=12)]
                    seen[cache_key]=dict(pool=metrics(pool,case['gold']),
                        top6=metrics(pool[:6],case['gold']),
                        at1200=metrics(rank_budget(pool,1200),case['gold']),
                        at2400=metrics(rank_budget(pool,2400),case['gold']),
                        spans=[(u['start'],u['end']) for u in pool],
                        max_unit=max(len(u['text']) for u in pool))
                results[entry['name']]['cases'][cid]=seen[cache_key]
        print('offline',pid,len(entries),flush=True)
    for row in results.values():
        row['overall']={k:aggregate(list(row['cases'].values()),k) for k in ('pool','top6','at1200','at2400')}
        row['new_papers']={k:aggregate([r for cid,r in row['cases'].items() if not cid.startswith('p01')],k)
                           for k in ('pool','top6','at1200','at2400')}
    save(args.out/f'{args.phase}-{args.tag}.json',dict(model=embedder.model,revision=embedder.revision,
        configs=list(results.values())))


def feedback_pool(client,model,candidate,index,query,excluded,feedback,pool_size):
    """Diversify a single supplemental retrieval by the judge's search questions."""
    questions = list(dict.fromkeys(feedback['search_queries']))[:6] or [query]
    endpoints = ' '.join(candidate[f'{k}_entity_instance'] for k in ('source','target'))
    texts = [q+' '+endpoints for q in questions]
    dense = cosine_matrix(index.embedder.encode(texts,query=True),
                          index.embedder.encode([u.text for u in index.units]))
    scores = [(.55*minmax(index.bm25(q,1.2,.75))+.45*minmax(d)) for q,d in zip(texts,dense)]
    orders = [np.argsort(-row,kind='stable') for row in scores]
    selected = []
    used = set(excluded)
    for choices in zip(*orders):
        for i in choices:
            unit = index.units[i]
            if len(selected)<pool_size and unit.id not in used:
                used.add(unit.id)
                selected.append((-float(len(selected)),unit))
        if len(selected)==pool_size:
            break
    ids = [u.id for _,u in selected]
    return selected,dict(queries=questions,shortlist_ids=ids,selected_ids=ids)


class ScopeChainIndex(RetrievalIndex):
    """Optional search for the base measurement window of a scope variant."""

    def rank(self,candidate,query,excluded=(),limit=12):
        if candidate['relation']!='variant_of' or excluded:
            return super().rank(candidate,query,excluded,limit)
        base = candidate['target_entity_instance']
        windows = [f'{base} cumulative totals counting period full time window years since publication',
                   f'{base} distinction early shorter window compared with overall readership or counts']
        feedback = {'search_queries':windows}
        reserve = min(4,limit//3)
        chain = super().rank(candidate,query,limit=limit-reserve)
        extras,_ = feedback_pool(None,None,candidate,self,query,{u.id for _,u in chain},feedback,reserve)
        return [(-float(i),u) for i,(_,u) in enumerate(chain+extras)]


def live(args,papers,cases):
    entries = configs('frozen',args.plan)
    embedder = FrozenBGE(args.bge)
    tasks = []
    for entry in entries:
        cfg = RetrievalConfig(**entry['config'])
        indexes = {}
        for cid in entry.get('cases',cases):
            case = cases[cid]
            pid = case['paper']
            if pid not in indexes:
                kind = ScopeChainIndex if entry.get('scope_queries') else RetrievalIndex
                indexes[pid] = kind(papers[pid]['markdown'],cfg,embedder)
            index = indexes[pid]
            candidate = dict(case['candidate'])
            if candidate['relation']=='alternative_to' and cfg.method=='chains':
                candidate = index.orient_alternative(candidate)
            index.rank(candidate,retrieval_query(candidate),limit=entry.get('pool_size',12))
            tasks.append((entry,cid,case,candidate,index))
    out = args.out/'live'/args.tag
    save(out/'plan.json',entries)

    def one(task):
        entry,cid,case,candidate,index = task
        dest = out/f"{entry['name']}-{cid}.json"
        if dest.exists():
            return json.loads(dest.read_text())
        client,model = api_client(args.out,args.tag)
        with client:
            result = collect_evidence(candidate,index,client,model,max_rounds=entry.get('rounds',0),
                pool_size=entry.get('pool_size',12),
                annotator=partial(annotate_pool,decomposed=True,task_scoped=True),
                selector=partial(select_evidence,decomposed=True,task_scoped=True,
                                 explicit_variant_scopes=entry.get('scope_queries',False),
                                 limit=entry.get('selection_limit',6)),
                validator=partial(validate_relation_conditions,client,model),
                retriever=feedback_pool)
        steps = result['evidence_completion']['trace']
        pool = [u for trace in steps for u in trace['retrieved_evidence']]
        assert all(index.markdown[u['start']:u['end']]==u['text'] for u in pool)
        row = dict(cid=cid,name=entry['name'],config=entry['config'],
                   first_pool=metrics(steps[0]['retrieved_evidence'],case['gold']),
                   pool=metrics(pool,case['gold']),steps=len(steps),
                   final=metrics(result['evidence'],case['gold']),verdict=steps[-1]['validator_feedback'],
                   valid=result['valid'],exact=verify_units(result,index.markdown),result=result)
        save(dest,row)
        print(entry['name'],cid,row['pool']['hit'],row['final']['hit'],row['valid'],
              row['verdict']['reason'][:200],flush=True)
        return row

    with ThreadPoolExecutor(max_workers=args.workers) as workers:
        rows = list(workers.map(one,tasks))
    save(out/'summary.json',[{k:v for k,v in row.items() if k!='result'} for row in rows])


def final(args,papers,cases):
    """Independently validate exactly the same original excerpts in the final arrays."""
    plan = json.loads(args.plan.read_text())
    if set(plan)!=set(cases):
        raise ValueError('Final plan must contain all 12 case IDs')
    out = args.out/'final'/args.tag
    save(out/'plan.json',plan)

    def one(item):
        cid, path = item
        row = json.loads(Path(path).read_text())
        if row['cid']!=cid:
            raise ValueError(f'Candidate and selected array mismatch: {cid}')
        case = cases[cid]
        original = row['result']
        triplet = dict(case['candidate'],evidence=original['evidence'])
        fields = ('source_entity_instance','target_entity_instance')
        observed = tuple(original[field] for field in fields)
        expected = tuple(triplet[field] for field in fields)
        if triplet['relation']=='alternative_to':
            observed,expected = sorted(observed),sorted(expected)
        if original['relation']!=triplet['relation'] or observed!=expected:
            raise ValueError(f'Changed candidate: {cid}')
        text = papers[case['paper']]['markdown']
        if not verify_units(triplet,text):
            raise ValueError(f'Invalid exact-source evidence: {cid}')
        client,model = api_client(args.out,'final-'+args.tag)
        with client:
            decision = validate_relation_conditions(client,model,triplet)
        result = dict(cid=cid,retrieval_config=row['config'],pool=row['pool'],
                      evidence_metrics=metrics(triplet['evidence'],case['gold']),
                      exact=True,validator=decision,source_run=str(path),triplet=triplet)
        save(out/f'{cid}.json',result)
        print(cid,decision['verdict'],result['evidence_metrics']['hit'],decision['reason'][:180],flush=True)
        return result

    with ThreadPoolExecutor(max_workers=args.workers) as workers:
        results=list(workers.map(one,plan.items()))
    save(out/'evidence_and_verdicts.json',results)


def run(args,papers,cases):
    if args.phase in {'frozen','sweep'}:
        offline(args,papers,cases)
    elif args.phase=='live':
        live(args,papers,cases)
    else:
        final(args,papers,cases)


if __name__=='__main__':
    main()

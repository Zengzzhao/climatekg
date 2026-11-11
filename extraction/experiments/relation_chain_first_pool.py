"""Evaluate compact, first-round evidence pools on the fixed corpus benchmark."""
import argparse
from dataclasses import asdict, replace
from functools import lru_cache
import hashlib
import itertools
import json
from pathlib import Path
import re
from statistics import mean
import time

from relation_chain_corpus import aggregate, metrics
from relation_retrieval import rank_budget, save
from evidence_chunking import MATH, atoms, sentence_ranges
from evidence_index import EvidenceIndex, EvidenceUnit
from evidence_retrieval import FrozenBGE, RetrievalConfig, RetrievalIndex, minmax, retrieval_query


@lru_cache(maxsize=12000)
def partition(text, start, end, cap):
    """Tile a contiguous region with few cuts, favoring sentences and intact math."""
    original = text[start:end]
    if len(original) <= cap:
        return [(start,end)]
    masked = MATH.sub(lambda m:'_'*len(m[0]),original)
    boundaries = {0:0., len(original):0.}
    for match in re.finditer(r'\s+',masked):
        cut = match.end()
        boundaries[cut] = .45 if original[match.start()-1:match.start()] in {',',':',';'} else .7
    boundaries.update({b:0. for _,b in sentence_ranges(original)})
    for match in MATH.finditer(original):
        boundaries[match.start()] = boundaries[match.end()] = .1
    # A rare equation or unbroken token longer than cap still has to fit.
    marks = sorted(boundaries)
    for a,b in zip(marks,marks[1:]):
        for cut in range(a+cap,b,cap):
            boundaries[cut] = 1.5
    marks = sorted(boundaries)
    best = {0:(0.,None)}
    for i,b in enumerate(marks[1:],1):
        choices = []
        for a in reversed(marks[:i]):
            if b-a>cap:
                break
            if a in best:
                choices.append((best[a][0]+2.2+boundaries[b]+.04*(1-(b-a)/cap),a))
        if choices:
            best[b] = min(choices)
    cuts = [len(original)]
    while cuts[-1]:
        cuts.append(best[cuts[-1]][1])
    result = []
    for a,b in zip(reversed(cuts[1:]),reversed(cuts[:-1])):
        span = original[a:b]
        if span.strip():
            result.append((start+a+len(span)-len(span.lstrip()),start+a+len(span.rstrip())))
    return result


class RegionIndex(RetrievalIndex):
    """Add short cross-paragraph regions, keeping original offsets."""
    def __init__(self, text, config, model, cap, stride, overlap):
        units = EvidenceIndex(text).units
        spans = atoms(text,split_tables=True)
        windows = set()
        for i in range(0,len(spans),stride):
            a,b,section = spans[i]
            if b-a>cap:
                continue
            for j in range(i+1,min(i+overlap+1,len(spans))):
                _,end,other = spans[j]
                if other!=section or end-a>cap:
                    break
                b=end
            if b-a>=cap*.32:
                windows.add((a,b,section))
        existing={(u.start,u.end) for u in units}
        for a,b,section in sorted(windows):
            if (a,b) not in existing:
                units.append(EvidenceUnit(f'r{len(units):04d}',a,b,section,text[a:b]))
        units.sort(key=lambda u:(u.start,u.end))
        super().__init__(text,config,model,units=units)


def rank_units(index, candidate, limit):
    if candidate['relation']=='alternative_to' and index.config.method=='chains':
        candidate=index.orient_alternative(candidate)
    return [u for _,u in index.rank(candidate,retrieval_query(candidate),limit=limit)]


def tiles_for(text, unit, cap):
    spans = partition(text,unit.start,unit.end,cap)
    return [EvidenceUnit(f'e{a}-{b}',a,b,unit.section,text[a:b]) for a,b in spans]


def overlap_ratio(a,b):
    return max(0,min(a.end,b.end)-max(a.start,b.start))/min(a.end-a.start,b.end-b.start)


def proposals(text, parents, children, cap, max_group, flat_weight, local, consensus=0.):
    """One proposal is one compact unit, or all tiles of a retrieved source region."""
    groups = {}
    for rank, parent in enumerate(parents):
        tiles = tiles_for(text,parent,cap)
        if not tiles or len(tiles)>max_group:
            continue
        key = tuple((u.start,u.end) for u in tiles)
        peak = max((overlap_ratio(parent,u)/(1+i/10) for i,u in enumerate(children[:16])),default=0)
        proximity = peak
        if consensus:
            affinity = [max((overlap_ratio(tile,u)/(1+i/10) for i,u in enumerate(children[:24])),default=0)
                        for tile in tiles]
            proximity = (1-consensus)*peak + consensus*mean(affinity)
        score = (1-flat_weight)/(1+rank/8)+flat_weight*proximity
        groups[key] = (score,tiles)
        if local and len(tiles)>1:
            for tile in tiles:
                k = ((tile.start,tile.end),)
                prior=max((overlap_ratio(tile,u)/(1+i/8) for i,u in enumerate(children[:16])),default=0)
                v=score*.70+prior*.30
                if k not in groups or groups[k][0]<v:
                    groups[k]=(v,[tile])
    for rank, unit in enumerate(children):
        for tile in tiles_for(text,unit,cap):
            key=((tile.start,tile.end),)
            score=flat_weight/(1+rank/8)
            if key not in groups or groups[key][0]<score:
                groups[key]=(score,[tile])
    return list(groups.values())


def pack(groups, *, budget, penalty, duplicate, strategy, seeds=()):
    """Jointly spend the 12 slots on distinct evidence, charging per added tile."""
    chosen = list(seeds)
    groups = list(groups)
    while len(chosen)<budget and groups:
        options = []
        for i,(score,units) in enumerate(groups):
            coverage=[max((overlap_ratio(u,v) for v in chosen),default=0.) for u in units]
            remaining=[u for u in units if not any(
                v.start<=u.start and u.end<=v.end for v in chosen)]
            if not remaining or len(chosen)+len(remaining)>budget:
                continue
            redundant = mean(coverage) if strategy=='novel' else max(coverage)
            gain = score * (1-duplicate*redundant) / len(remaining)**penalty
            if gain>0:
                options.append((gain,-i,i,remaining))
        if not options:
            break
        _,_,i,remaining=max(options,key=lambda v:v[:2])
        chosen.extend(remaining)
        groups.pop(i)
    return chosen


def stitched(text,units,cap):
    """Re-tile overlapping or whitespace-adjacent excerpts without dropping text."""
    ranges=[]
    for rank,u in sorted(enumerate(units),key=lambda x:(x[1].start,x[1].end)):
        if ranges and ranges[-1][2]==u.section and not text[ranges[-1][1]:u.start].strip():
            ranges[-1][1]=max(ranges[-1][1],u.end)
            ranges[-1][3]=min(ranges[-1][3],rank)
        else:
            ranges.append([u.start,u.end,u.section,rank])
    return [EvidenceUnit(f'e{a}-{b}',a,b,section,text[a:b])
            for lo,hi,section,_ in sorted(ranges,key=lambda r:(r[3],r[0]))
            for a,b in partition(text,lo,hi,cap)]


def pack_stitched(text,groups,cap,penalty,duplicate,seeds=()):
    selected=stitched(text,seeds,cap)
    groups=list(groups)
    while groups:
        options=[]
        for i,(score,units) in enumerate(groups):
            if all(any(v.start<=u.start and u.end<=v.end for v in selected) for u in units):
                continue
            length=sum(u.end-u.start for u in units)
            shared=sum(max(0,min(u.end,v.end)-max(u.start,v.start)) for u in units for v in selected)
            if shared>=length:
                continue
            packed=stitched(text,selected+units,cap)
            if len(packed)>12:
                continue
            cost=max(1,len(packed)-len(selected))
            gain=score*(1-duplicate*shared/length)/cost**penalty
            options.append((gain,-i,i,packed))
        if not options:
            break
        _,_,i,selected=max(options,key=lambda x:x[:2])
        groups.pop(i)
    if len(selected)<12:
        # Stitching may compact the last usable region. Add distinct child units.
        for _,units in sorted(groups,key=lambda row:-row[0]):
            for unit in units:
                if len(selected)==12:
                    return selected
                if not any((u.start,u.end)==(unit.start,unit.end) for u in selected):
                    selected.append(unit)
    return selected


def child_regions(text, paragraphs, children, limit, cap, fusion=0.):
    """Group around ranked children, with no embedding or scoring of long text."""
    regions={}
    for unit in children[:limit]:
        i=next((j for j,p in enumerate(paragraphs) if p.start<=unit.start<p.end),None)
        if i is None:
            continue
        match=paragraphs[i]
        if len(match.text)<=cap:
            regions.setdefault((match.start,match.end),match)
        for j,k in ((i-1,i),(i,i+1),(i-1,i+1)):
            if j<0 or k>=len(paragraphs):
                continue
            lo,hi=paragraphs[j].start,paragraphs[k].end
            if hi-lo>cap or paragraphs[j].section!=paragraphs[k].section:
                continue
            regions.setdefault((lo,hi),EvidenceUnit(f'g{lo}-{hi}',lo,hi,match.section,text[lo:hi]))
    proposals=list(regions.values())
    if not fusion:
        return proposals

    def priority(item):
        rank,region=item
        witnesses=[]
        for i,unit in enumerate(children[:limit]):
            if (overlap_ratio(unit,region)>=.5
                    and all(overlap_ratio(unit,v)<.5 for _,v in witnesses)):
                witnesses.append((1/(1+i/8),unit))
            if len(witnesses)==3:
                break
        return (1-fusion)/(1+rank/8)+fusion*sum(score/(i+1) for i,(score,_) in enumerate(witnesses))

    return [region for _,region in sorted(enumerate(proposals),key=priority,reverse=True)]


BEST_FIRST_POOL_CONFIG = RetrievalConfig(
    method='chains', strategy='bounded', chars=350, sentences=2, dense=.76,
    anchor=.2, edge=.45, role_profile='proof', endpoint_weight=.45,
    neighbor_weight=.4, math_weight=.3,
)
BEST_FIRST_POOL_PACK = dict(region=675, fusion=.65, weight=.4, duplicate=.68, max_group=4,
                            shortlist=48, region_seeds=36, region_limit=24, limit=12)


class CompactEvidenceIndex(RetrievalIndex):
    """Use the winning compact first pool and allow ordinary feedback retrieval."""

    def __init__(self, markdown, embedder):
        from evidence_chunking import chunk_units
        config = BEST_FIRST_POOL_CONFIG
        raw = chunk_units(markdown, config.strategy, config.chars, config.sentences)
        units = list({(v.start,v.end):v for u in raw
                      for v in tiles_for(markdown,u,config.chars)}.values())
        super().__init__(markdown,config,embedder,units=units)
        self.paragraphs = EvidenceIndex(markdown).units
        self.first_pools = {}

    def rank(self,candidate,query,excluded=(),limit=12):
        if excluded:
            existing = [self.by_id[key] for key in excluded]
            excluded = set(excluded) | {
                u.id for u in self.units if any(v.start<=u.start and u.end<=v.end for v in existing)
            }
            return super().rank(candidate,query,excluded,limit)
        key = (candidate['source_entity_instance'],candidate['relation'],
               candidate['target_entity_instance'],query,limit)
        if key not in self.first_pools:
            options = BEST_FIRST_POOL_PACK
            ranked = super().rank(candidate,query,limit=options['shortlist'])
            children = [u for _,u in ranked]
            regions = child_regions(self.markdown,self.paragraphs,children,
                options['region_seeds'],options['region'],options['fusion'])
            groups = proposals(self.markdown,regions[:options['region_limit']],children,
                self.config.chars,options['max_group'],options['weight'],False)
            pool = pack_stitched(self.markdown,groups,self.config.chars,0.,options['duplicate'])
            self.by_id.update((u.id,u) for u in pool)
            self.first_pools[key] = [(-float(i),u) for i,u in enumerate(pool[:limit])]
        return self.first_pools[key]


def load(out):
    source=json.loads(out.read_text())
    papers={int(k):Path(v['path']).read_text(encoding='utf-8-sig') for k,v in source['papers'].items()}
    for k,text in papers.items():
        if hashlib.sha256(text.encode()).hexdigest()!=source['papers'][str(k)]['sha256']:
            raise ValueError(f'Source changed: {k}')
    return papers,source['cases']


def evaluate(pool,gold):
    if len(pool)!=12 or max(len(u.text) for u in pool)>350:
        raise ValueError(f'Unexpected pool or cap: {len(pool)} units')
    units=list(map(asdict,pool))
    return dict(pool=metrics(units,gold),top6=metrics(units[:6],gold),
                at1200=metrics(rank_budget(units,1200),gold),
                at2400=metrics(rank_budget(units,2400),gold),
                spans=[(u.start,u.end) for u in pool],
                max_unit=max(len(u.text) for u in pool))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--alignment',type=Path,required=True)
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--bge',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--plan',type=Path)
    p.add_argument('--tag',default='pilot')
    args=p.parse_args()
    run(args)


def pilot_plan():
    rows = [dict(name='previous_350',mode='flat',cap=350)]
    for profile,cap,region,penalty,duplicate,weight in itertools.product(
            ('legacy','long'),(300,350),(0,950),(0.,.3,.65),(.45,.85),(.1,.38)):
        rows.append(dict(name=f'{profile}_{cap}_{region}_{penalty}_{duplicate}_{weight}',mode='pack',
            parent=profile,cap=cap,region=region,penalty=penalty,duplicate=duplicate,
            weight=weight,max_group=4,local=True,strategy='novel'))
    return rows


def run(args):
    papers,cases=load(args.alignment)
    old=json.loads(args.baseline.read_text())['configs']
    get=lambda name: RetrievalConfig(**next(r['config'] for r in old if r['name']==name))
    child=get('math_350_0.3')
    profiles={'legacy':get('math_2600_0.3'),'long':get('math_1400_0.3')}
    plan=json.loads(args.plan.read_text()) if args.plan else pilot_plan()
    args.out.mkdir(parents=True,exist_ok=True)
    save(args.out/f'plan-{args.tag}.json',plan)
    rows={r['name']:dict(r,cases={}) for r in plan}
    model=FrozenBGE(args.bge)
    started=time.monotonic()
    for pid,text in papers.items():
        paragraphs=EvidenceIndex(text).units
        indexes={}
        ranks={}
        grouped={}
        for cid,case in cases.items():
            if case['paper']!=pid:
                continue
            c=case['candidate']
            for row in plan:
                cap=row['cap']
                name=row['name']
                child_cfg=replace(child,chars=cap,sentences=row.get('sentences',2))
                if row.get('child_config'):
                    child_cfg=replace(child_cfg,**row['child_config'])
                strict=row.get('strict_child',False)
                child_index_key=child_cfg,strict
                if child_index_key not in indexes:
                    units=None
                    if strict:
                        from evidence_chunking import chunk_units
                        raw=chunk_units(text,child_cfg.strategy,cap,child_cfg.sentences)
                        units=list({(v.start,v.end):v for u in raw for v in tiles_for(text,u,cap)}.values())
                    indexes[child_index_key]=RetrievalIndex(text,child_cfg,model,units=units)
                ch_key=(child_index_key,cid)
                if ch_key not in ranks:
                    ranks[ch_key]=rank_units(indexes[child_index_key],c,48)
                kids=ranks[ch_key]
                if row['mode']=='flat':
                    pool=[u for u in kids if len(u.text)<=cap][:12]
                else:
                    if row['parent']=='child':
                        fusion=row.get('region_fusion',0.)
                        r_key=('child',ch_key,row['region'],fusion)
                        if r_key not in ranks:
                            ranks[r_key]=child_regions(text,paragraphs,kids,36,row['region'],fusion)
                    else:
                        parent_names=('legacy','long') if row['parent']=='fused' else (row['parent'],)
                        ranked_sources=[]
                        for parent_name in parent_names:
                            cfg=profiles[parent_name]
                            if row.get('parent_config'):
                                cfg=replace(cfg,**row['parent_config'])
                            p_key=cfg,row['region']
                            if p_key not in indexes:
                                indexes[p_key]=(RegionIndex(text,cfg,model,row['region'],2,6)
                                    if row['region'] else RetrievalIndex(text,cfg,model))
                            rank_key=p_key,cid
                            if rank_key not in ranks:
                                ranks[rank_key]=rank_units(indexes[p_key],c,36)
                            ranked_sources.append(rank_key)
                        r_key=tuple(ranked_sources)
                        if r_key not in ranks:
                            ranks[r_key]=list({(u.start,u.end):u for pair in zip(
                                *(ranks[key] for key in ranked_sources)) for u in pair}.values())
                    parents=ranks[r_key][:row.get('parent_limit',24)]
                    consensus=row.get('consensus',0.)
                    group_key=(ch_key,r_key,len(parents),cap,row['max_group'],row['weight'],row['local'],consensus)
                    if group_key not in grouped:
                        grouped[group_key]=proposals(text,parents,kids,cap,row['max_group'],
                                                     row['weight'],row['local'],consensus)
                    seeds=[]
                    for u in kids:
                        if len(seeds)>=row.get('seed',0):
                            break
                        if len(u.text)<=cap and all(overlap_ratio(u,v)<.55 for v in seeds):
                            seeds.append(u)
                    if row['strategy']=='stitched':
                        pool=pack_stitched(text,grouped[group_key],cap,row['penalty'],row['duplicate'],seeds)
                    else:
                        pool=pack(grouped[group_key],budget=12,penalty=row['penalty'],
                                  duplicate=row['duplicate'],strategy=row['strategy'],seeds=seeds)
                assert all(len(u.text)<=cap and text[u.start:u.end]==u.text for u in pool)
                rows[name]['cases'][cid]=evaluate(pool,case['gold'])
        print('paper',pid,len(plan),round(time.monotonic()-started,1),flush=True)
    for row in rows.values():
        all_rows=list(row['cases'].values())
        new_rows=[r for cid,r in row['cases'].items() if not cid.startswith('p01')]
        row['overall']={stage:aggregate(all_rows,stage) for stage in ('pool','top6','at1200','at2400')}
        row['new_papers']={stage:aggregate(new_rows,stage) for stage in ('pool','top6','at1200','at2400')}
    save(args.out/f'sweep-{args.tag}.json',dict(model=model.model,revision=model.revision,rows=list(rows.values())))
    for r in sorted(rows.values(),key=lambda r:(r['overall']['pool']['hit'],r['overall']['pool']['macro'],
                                               r['overall']['at1200']['macro']),reverse=True)[:12]:
        print(r['name'],r['overall']['pool']['hit'],round(r['overall']['pool']['macro'],3),
              round(r['overall']['at1200']['macro'],3),flush=True)


if __name__=='__main__':
    main()

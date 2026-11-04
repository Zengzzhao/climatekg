"""Alternative condition paths and budgeted marginal-coverage evidence selection.

Role support is binary, quote-grounded annotation, not a calibrated probability.
Greedy selection is a heuristic; no global optimum/approximation guarantee claimed.
"""
import re

ROLES = {
    'source_identity':'Explicit source indicator identity/name or bound alias.',
    'target_identity':'Explicit target indicator identity/name or bound alias.',
    'attribution':'The action is performed by the CURRENT paper, not merely attributed to a cited work.',
    'contribution':'Current authors explicitly introduce, name or formally define the target as their contribution.',
    'change':'Concrete change of formulation, weighting, normalization, scope or time window.',
    'actual_use':'Actual computation/use/comparison in this study, not background mention or a future plan.',
    'variant_claim':'Explicit directed statement that SOURCE is a variant/adaptation of TARGET, with identifiable endpoints.',
    'lineage':'Explicit direct source-to-target methodological/conceptual origin; shared framework or notation is insufficient.',
    'retained_core':'The same core measurement structure is retained while specific details change.',
    'derivation_claim':'Explicit directed derivation from TARGET with a substantive design extension, not only nesting or evaluation-level change.',
    'base_design':'Relevant construction of the TARGET, or explicit description of what is transformed.',
    'source_design':'Relevant construction of the SOURCE, not a neighboring third indicator.',
    'substantive_change':'An explicit substantive construction change from TARGET to SOURCE beyond parameter, weight or scope changes alone. An inferred change in evaluation level is insufficient.',
    'improvement_claim':'Explicit statement that SOURCE addresses a specified limitation of TARGET; not just higher scores/correlation.',
    'limitation':'A specific defect/bias of TARGET is identified.',
    'repair':'SOURCE explicitly addresses THAT defect of TARGET; a stated design objective suffices, but speculation by the extractor does not.',
    'substitutability':'Explicit substitutability for the same evaluation goal under stated conditions.',
    'direct_input':'SOURCE is a named quantitative score directly aggregated by TARGET, in this direction; not a transitive input or an algorithm.',
}
PATHS = {
    'proposes':[['contribution']], 'modifies':[['change']], 'applies':[['actual_use']],
    'variant_of':[['variant_claim'],['lineage','retained_core','change']],
    'derived_from':[['derivation_claim','substantive_change'],['lineage','base_design','source_design','substantive_change']],
    'improves_on':[['improvement_claim'],['lineage','limitation','repair']],
    'alternative_to':[['substitutability']], 'component_of':[['direct_input']],
}


def paths_for(candidate):
    # Paper action roles already require current-study attribution; avoid a duplicate
    # separately annotated condition that can mechanically reject the same evidence.
    common = ['target_identity'] + (['source_identity'] if candidate['source_entity_type']=='Indicator' else [])
    return [common+path for path in PATHS[candidate['relation']]]


def similarity(a,b):
    a,b = set(re.findall(r'\w+',a.lower())),set(re.findall(r'\w+',b.lower()))
    return len(a&b)/len(a|b) if a|b else 0


def select_evidence(candidate, pool, supports, *, method='cover', limit=6, max_chars=14000):
    """Select complete proof paths first, with gain/cost and redundancy control."""
    if method not in {'cover','topk'}:
        raise ValueError('Unknown selector')
    units = {u.id:u for _,u in pool}
    relevance = {u.id:score for score,u in pool}
    roles = {uid:set() for uid in units}
    for s in supports:
        roles[s['unit_id']].add(s['role'])
    paths = paths_for(candidate)
    def covered(ids):
        return set().union(*(roles[i] for i in ids)) if ids else set()
    if method=='topk':
        ids=[]
        for _,u in pool:
            if len(ids)<limit and sum(len(units[i].text) for i in ids)+len(u.text)<=max_chars:
                ids.append(u.id)
        best=max(paths,key=lambda p:len(set(p)&covered(ids))/len(p))
    else:
        choices=[]
        for path in paths:
            ids=[]
            while len(ids)<limit and not set(path)<=covered(ids):
                scored=[]
                for uid,u in units.items():
                    if uid in ids or sum(len(units[i].text) for i in ids)+len(u.text)>max_chars:
                        continue
                    gain=len((set(path)-covered(ids))&roles[uid])/len(path)
                    if gain==0:
                        continue
                    redundancy=max((similarity(u.text,units[i].text) for i in ids),default=0)
                    value=gain/(1+len(u.text)/1000)-.15*redundancy
                    scored.append((value,relevance[uid],-u.start,uid))
                if not scored:
                    break
                ids.append(max(scored)[-1])
            coverage=len(set(path)&covered(ids))/len(path)
            choices.append(((coverage==1,coverage,-sum(len(units[i].text) for i in ids),-len(ids)),path,ids))
        _,best,ids=max(choices,key=lambda c:c[0])
    missing=sorted(set(best)-covered(ids))
    return dict(ids=ids,path=best,missing=missing,complete=not missing,
                coverage=1-len(missing)/len(best),characters=sum(len(units[i].text) for i in ids))

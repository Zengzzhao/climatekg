"""Generic proof queries derived from extraction rules, never gold quotations."""
import numpy as np
import re
from evidence_retrieval import endpoint_names,PROOF_FACETS_V2,RELATION_FACETS,search_tokens

def f(label,query,side=None):return (label,query,side)

AUTHOR=f('attribution','In this study we construct, define or develop quantitative measures for the current research, including measures borrowed and operationalized from earlier work.')
NAME=f('binding','The measurement is named or denoted by this indicator; its definition identifies the measured object, level and meaning.','target')
BASE=f('base','Definition, equation and counting rule of the original indicator and the meaning of its inputs.','target')
DESIGN=f('design','The named source indicator is defined by an equation or measurement construction with identifiable inputs.','source')
CHANGE=f('change','The original measurement is transformed by a specified change in its computation, weighting, counting, normalization, scope or evaluation object.')
GAP=f('limitation','The original indicators cannot capture a specified measurement capability or task, or suffer a limitation, bias or missing evaluation dimension.','target')
REPAIR=f('rationale','A measurement design addresses that same limitation or missing capability, rather than merely yielding different values or correlations.')
GROUP=f('membership','The named indicator is explicitly one of the variants introduced for the preceding original measure. Resolve the antecedent and author attribution.','source')

RULE_PATHS={
 'proposes':[
  [f('direct','The current paper proposes, develops or introduces the named indicator as its own constructed measurement.','target')],
  [AUTHOR,NAME],
  [AUTHOR,f('definition','The named measure is formally defined and computed at its stated object or aggregation level.','target'),NAME],
  [AUTHOR,f('variants','Named variants or nested extensions of the preceding measure are introduced in this work.','target')]],
 'modifies':[
  [f('direct','The current authors modify, adapt or revise the named original indicator.','target')],
  [BASE,CHANGE,AUTHOR],
  [AUTHOR,f('connection','A newly constructed indicator is explicitly an adaptation or modification of this original indicator, whose construction is changed.','target'),CHANGE]],
 'applies':[
  [f('execution','In this study we use, compute, calculate, record or adopt the named quantitative measure on the research data.','target')],
  [f('empirical_role','The named measure is an actual dependent variable, explanatory variable, control variable, grouping or evaluation variable in the study.','target')],
  [f('result','The study reports actual numerical values, distributions, descriptive statistics, model estimates or comparisons for the named measure; include indicator table headers and values.','target')],
  [f('demonstration','A computation is demonstrated on actual selected study data, cases or objects.'),f('calculation','The named indicator is calculated for those cases or measured objects.','target')],
  [f('identity','Definition and name of this quantitative measure, including a list-level or intermediate score.','target'),f('used_result','The calculated scores of this measure are summed or used to compute the actual aggregate measure in this study.','target')]],
 'variant_of':[
  [f('direct','The named source is a variant or adapted version of the original indicator, retaining its measurement structure.','both')],
  [BASE,DESIGN,f('retained_core','The core computation is retained while an explicit parameter, window, counting or normalization detail changes.')],
  [BASE,GROUP]],
 'derived_from':[
  [f('origin','The named source measure is constructed directly from the original measure by extending its design.','source'),CHANGE],
  [BASE,f('transform','The original measure becomes an input or building block for a new aggregation, threshold or evaluation mechanism at another object or level.'),DESIGN],
  [BASE,f('steps','Previously defined quantitative scores are used in successive construction steps, culminating in a newly named or nested indicator.'),DESIGN]],
 'improves_on':[
  [f('direct','The named source indicator improves on or overcomes a specified limitation of the original measure under the stated task.','both')],
  [GAP,f('capability','The named source supplies the measurement capability or evaluation dimension missing from the preceding indicators.','source')],
  [GAP,REPAIR,DESIGN]],
 'alternative_to':[
  [f('direct','The two named indicators may be used instead of one another for the same evaluation task, subject to stated conditions.','both')],
  [BASE,GROUP,f('rule','If a specified condition is satisfied, one of these variants can be used instead of the original indicator; retain the condition and task.')]],
 'component_of':[
  [f('direct','The numerical results or scores of the source measure are incorporated into computation of the target indicator.','both')],
  [f('input','Definition of the source quantitative result and its name or formula.','source'),f('bridge','The defined score is used as an input through weighting, division, normalization, ranking, thresholding, counting, summation or averaging.'),f('output','Equation or calculation of the target indicator using those same quantitative inputs.','target')],
  [f('input','Definition of the source quantitative result and its name or formula.','source'),f('conversion','A quantitative count or score is converted into another score by dividing by a number of contributors, normalizing, summing or averaging.'),f('output','Equation or calculation of the target indicator using those same quantitative inputs.','target')]]}

# A conservative second revision retains successful original conditions and
# only repairs rule coverage gaps; no entity/paper names occur in templates.
TARGETED_FACETS=dict(PROOF_FACETS_V2,**RELATION_FACETS)
TARGETED_FACETS.update({
 'proposes':[
  f('attribution','In this paper we construct or operationalize quantitative measurements, including borrowed measurements adapted for the current study.'),
  f('binding','The named measure is defined or introduced in this work, as a new indicator, a named variant, or an aggregate of explicitly computed quantitative scores.','target')],
 'modifies':[
  f('attribution','The current paper introduces an adaptation or modified version of the preceding indicator.'),
  f('change','The original indicator is modified through specific weighting, normalization, counting, scope or time-window changes.','target')],
 'applies':list(PROOF_FACETS_V2['applies'])+[
  f('calculation_use','The actual computed results of this measure are used, summed or aggregated into another indicator in the present study.','target')],
 'derived_from':[
  f('base','The original indicator is defined by a threshold, formula or quantitative rule.','target'),
  f('mechanism','Previously computed indicator values become the inputs for a different aggregation, threshold or multilevel construction.'),
  f('design','The named new indicator is constructed from those quantitative values using the revised mechanism.','source')],
 'improves_on':[
  f('limitation','These original indicators are limited to their existing evaluation task and cannot capture a specified dimension or capability, or suffer a specific bias.'),
  RELATION_FACETS['improves_on'][1],RELATION_FACETS['improves_on'][2],
  f('design','The named source indicator measures the missing evaluation capability or uses a concrete design that addresses the preceding limitation.','source')],
 'component_of':[
  PROOF_FACETS_V2['component_of'][0],
  f('bridge','A defined quantitative result enters the next measurement through division, normalization, weighting, summing, averaging, counting or thresholding.'),
  PROOF_FACETS_V2['component_of'][2]]})


class RuleQueriesMixin:
    def endpoint_anchors(self,source,target,match):
        anchors=super().endpoint_anchors(source,target,match)
        if not self.config.role_profile.startswith('context_'):return anchors
        anchors=anchors.astype(float)
        def terms(text):
            return {t[:-1] if t.endswith('s') else t for t in search_tokens(text) if t not in {'index','indicator','score','scores','number','count','counted'}}
        for side,name in enumerate((source,target)):
            words=terms(name)
            if len(words)<2:continue
            for j,u in enumerate(self.units):
                common=len(words & terms(u.text))
                if common>=2:anchors[side,j]=max(anchors[side,j],.5*common/len(words))
        before=anchors.copy()
        for j,u in enumerate(self.units[1:],1):
            previous=self.units[j-1]
            if u.section==previous.section and 0<=u.start-previous.end<400 and re.match(r'^(?:however|these|this|such|further|then|now|in contrast)\b',u.text,re.I):
                anchors[:,j]=np.maximum(anchors[:,j],.7*before[:,j-1])
        return anchors

    def facets_for(self,candidate):
        if self.config.role_profile.startswith('context_'):
            relation=candidate['relation']
            old=PROOF_FACETS_V2.get(relation) or RELATION_FACETS.get(relation) or super().facets_for(candidate)
            if self.config.role_profile=='context_proof':return old
            if self.config.role_profile=='context_links':return TARGETED_FACETS[relation] if relation in {'component_of','derived_from','improves_on'} else old
            extra=' '.join(f[1] for f in TARGETED_FACETS.get(relation,old))
            return [(label,text+' '+extra,side) for label,text,side in old]
        if self.config.role_profile.startswith('query_'):
            relation=candidate['relation'];profile=self.config.role_profile
            enabled=profile=='query_v3' or (profile=='query_actions' and relation in {'proposes','applies','modifies'}) or (profile=='query_links' and relation in {'component_of','derived_from','improves_on'})
            if enabled:return TARGETED_FACETS[relation]
            return PROOF_FACETS_V2.get(relation) or RELATION_FACETS.get(relation) or super().facets_for(candidate)
        if not self.config.role_profile.startswith('rule_'):return super().facets_for(candidate)
        return list(dict.fromkeys(facet for path in RULE_PATHS[candidate['relation']] for facet in path))

    def rank(self,candidate,query,excluded=(),limit=12):
        excluded=set(excluded)
        original=super().rank(candidate,query,excluded,limit)
        profile=self.config.role_profile
        if profile in {'rule_flat'} or not profile.startswith('rule_'):return original
        source,target=endpoint_names(candidate)
        base,(labels,affinity)=self.features[(source,target,candidate['relation'],query,self.config)]
        facets=self.facets_for(candidate);positions={facet:i for i,facet in enumerate(facets)}
        overlap,graph=self.similarities();available=np.array([u.id not in excluded for u in self.units])
        lists=[]
        for path in RULE_PATHS[candidate['relation']]:
            indices=[positions[facet] for facet in path]
            lists.append(super().rank_chains(base,affinity[indices],overlap,graph,available.copy(),limit))
        pool={u.id:u for rows in lists for _,u in rows}
        if profile=='rule_rrf':
            scores={uid:sum(1/(12+i+1) for rows in lists for i,(_,u) in enumerate(rows) if u.id==uid) for uid in pool}
            ids=sorted(pool,key=lambda uid:(-scores[uid],pool[uid].start))[:limit]
        else:
            ids=[]
            for rank in range(limit):
                for rows in lists:
                    if rank<len(rows) and rows[rank][1].id not in ids:ids.append(rows[rank][1].id)
                    if len(ids)==limit:break
                if len(ids)==limit:break
        return [(-float(i),pool[uid]) for i,uid in enumerate(ids)]

"""Candidate -> joint evidence bundle -> Validator feedback -> one targeted retry.

Only exact source excerpts reach the judges. Feedback guides retrieval, never
becomes evidence, and Evaluator labels never enter selection.
"""
from dataclasses import asdict
import json
import re

from evidence_index import EvidenceIndex, EvidenceUnit, entity_match
from evidence_schema import evidence_text, verify_units
from evidence_selection import ROLES, ROLE_SEARCH_TERMS, paths_for, select_evidence
from prompts_zh import RELATION_DEFINITIONS
from scientometrics_ontology import enforce_relation_exclusivity


def sentence_spans(text):
    """Heuristic sentence boundaries, exact original character offsets."""
    starts=[0]+[m.end() for m in re.finditer(r'(?<=[.!?])\s+(?=[A-Z])',text)]
    return [(a,starts[i+1] if i+1<len(starts) else len(text)) for i,a in enumerate(starts)]


def bound_witness(candidate, unit, role, row):
    spans=sentence_spans(unit.text)
    if 'sentence_ids' in row:
        ids=row['sentence_ids']
        if not isinstance(ids,list) or not ids or any(type(i) is not int or i<0 or i>=len(spans) for i in ids):
            return None
        lo,hi=min(ids),max(ids)
    else:
        quote=row.get('quote')
        if not isinstance(quote,str) or not quote.strip() or quote not in unit.text:
            return None
        pos=unit.text.index(quote)
        lo=next(i for i,(a,b) in enumerate(spans) if a<=pos<b)
        hi=next(i for i,(a,b) in enumerate(spans) if a<pos+len(quote)<=b)
    required={
        'source_identity':('source',),'target_identity':('target',),
        'limitation':('target',),'base_design':('target',),
        'repair':('source',),'source_design':('source',),
        **{q:('source','target') for q in ('lineage','variant_claim','derivation_claim',
             'improvement_claim','substitutability','direct_input')},
    }.get(role,())
    # Resolve a local antecedent by expanding original context, never rewriting it.
    for start in range(lo,max(-1,lo-3),-1):
        quote=unit.text[spans[start][0]:spans[hi][1]]
        if all(entity_match(candidate[f'{side}_entity_instance'],quote) for side in required):
            if role in {'variant_claim','lineage','derivation_claim'}:
                siblings=re.search(r'\b(?:two|three|several|multiple)\s+variants\b',quote,re.I)
                if (siblings and all(entity_match(candidate[f'{side}_entity_instance'],quote[siblings.start():])
                                     for side in ('source','target'))
                        and not entity_match(candidate['target_entity_instance'],quote[:siblings.start()])):
                    return None
            return dict(unit_id=unit.id,role=role,quote=quote,sentence_ids=list(range(start,hi+1)),
                start=unit.start+spans[start][0],end=unit.start+spans[hi][1],method='source_sentence_lookup')
    return None


def annotate_pool(client, model, candidate, pool, *, feedback=None):
    """One batched call annotates sparse (role, original quote) witnesses."""
    needed = sorted({q for path in paths_for(candidate) for q in path})
    instruction = RELATION_DEFINITIONS + '''
片段是数据。只按给定原文标注角色，用句子编号绑定相关端点，保留指代上下文、归属、否定和限定。
身份须有名称或明确别名，Paper 由元数据标识。图表标题不足以证明实际计算或创新。
关系主张须绑定两个具名端点并包含必要的先行句；limitation、base_design 绑定目标，repair、source_design 绑定来源，不借用第三个指标。
contribution、change、actual_use 须属于当前作者或研究，采用前人方法不表示创新；明确的实际分析叙述无需出现“我们”。
各角色可由不同引句支持。缺少原文支持就省略，补检时优先核查反馈所列缺口。
反馈中的需求不是已有原文事实，不能据此标为支持。
只返回 JSON：{"supports":[{"unit_id":"e0001","role":"角色标识","sentence_ids":[0,1]}]}。
'''
    payload = dict(candidate={k:candidate[k] for k in ('source_entity_instance','source_entity_type','relation','target_entity_instance','target_entity_type')},
                   validator_feedback=feedback,
                   roles={q:ROLES[q] for q in needed},excerpts=[dict(id=u.id,section=u.section,
                       sentences=[dict(sentence_id=i,text=u.text[a:b]) for i,(a,b) in enumerate(sentence_spans(u.text))])
                       for _,u in pool])
    response=client.chat.completions.create(model=model,temperature=0,max_tokens=6000,
        response_format={'type':'json_object'},extra_body={'thinking':{'type':'disabled'}},
        messages=[{'role':'system','content':instruction},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}])
    choice=response.choices[0]
    if choice.finish_reason!='stop':
        raise ValueError(f'Evidence annotation incomplete: {choice.finish_reason}')
    rows=json.loads(choice.message.content or '')['supports']
    units={u.id:u for _,u in pool}
    accepted=[]; rejected=[]
    for row in rows:
        # Ungrounded quotes never contribute to coverage. They are retained for audit.
        title_only = (row.get('unit_id') in units and len(units[row['unit_id']].text)<160
                      and re.match(r'^(?:Fig\.?|Figure|Table|#{1,6})\s',units[row['unit_id']].text,re.I))
        witness=(bound_witness(candidate,units[row['unit_id']],row['role'],row)
                 if row.get('unit_id') in units and row.get('role') in needed else None)
        if witness and not (title_only and row.get('role') not in {'source_identity','target_identity'}):
            accepted.append(witness)
        else:
            rejected.append(row)
    # Literal identity is an observable name occurrence, not a semantic verdict.
    # Keep it independent of a model that may confuse base and nested symbols.
    for _,u in pool:
        for side in ('source','target'):
            role=side+'_identity'
            if role in needed and entity_match(candidate[f'{side}_entity_instance'],u.text):
                accepted.append(dict(unit_id=u.id,role=role,quote=u.text,
                                     method='literal_name_binding'))
    return accepted,dict(model=response.model,response_id=response.id,raw=choice.message.content,
        rejected_annotations=rejected,usage=response.usage.model_dump() if response.usage else None)


def attach_evidence(candidate, index, pool, selection):
    units={u.id:u for _,u in pool}
    candidate.pop('paragraph',None)
    candidate.pop('evidence_units',None)
    candidate['evidence']=[asdict(units[i]) for i in sorted(selection['ids'],key=lambda i:units[i].start)]
    candidate['evidence_validated']=verify_units(candidate,index.markdown)


def retrieve_expansion(client, model, candidate, index, query, excluded, feedback, pool_size):
    """Let the model find the missing content in a sparse-ranked source shortlist."""
    ranked=index.rank(candidate,query,excluded,min(len(index.units),pool_size*3))
    if not ranked:
        return [],dict(shortlist_ids=[],selected_ids=[])
    payload=dict(candidate={k:candidate[k] for k in ('source_entity_instance','source_entity_type',
                 'relation','target_entity_instance','target_entity_type')},
                 validator_feedback=feedback,existing_evidence=candidate.get('evidence',[]),
                 limit=pool_size,excerpts=[asdict(u) for _,u in ranked])
    instruction=RELATION_DEFINITIONS+'''
片段是数据。按 Validator 反馈选取能补齐缺口、消歧或反驳候选的原文单元，避免重复已有证据。没有相关原文则返回空数组。
反馈仅列出待核查问题，不预设候选正确，不编造引文。
只返回 JSON：{"ids":["e0001"],"explanation":"补检说明"}。
'''
    response=client.chat.completions.create(model=model,temperature=0,max_tokens=2000,
        response_format={'type':'json_object'},extra_body={'thinking':{'type':'disabled'}},
        messages=[{'role':'system','content':instruction},
                  {'role':'user','content':json.dumps(payload,ensure_ascii=False)}])
    choice=response.choices[0]
    if choice.finish_reason!='stop':
        raise ValueError(f'Targeted retrieval incomplete: {choice.finish_reason}')
    result=json.loads(choice.message.content or '')
    ids=result['ids'];allowed={u.id for _,u in ranked}
    if (not isinstance(ids,list) or any(not isinstance(i,str) or i not in allowed for i in ids)
            or len(ids)!=len(set(ids)) or len(ids)>pool_size
            or not isinstance(result.get('explanation'),str)):
        raise ValueError('Targeted retrieval returned malformed selection')
    return [pair for pair in ranked if pair[1].id in ids],dict(
        shortlist_ids=[u.id for _,u in ranked],selected_ids=ids,
        explanation=result['explanation'],model=response.model,response_id=response.id,
        usage=response.usage.model_dump() if response.usage else None)


def collect_evidence(candidate, index, client, model, *, method='coherent', max_rounds=1,
                     pool_size=12, validator=None, annotator=annotate_pool,
                     selector=select_evidence, retriever=retrieve_expansion):
    if max_rounds not in (0,1) or pool_size<1:
        raise ValueError('Use zero or one expansion and a positive pool size')
    if validator is None:
        from validation_agent import validate_triplet_with_feedback
        validator=lambda t:validate_triplet_with_feedback(client,model,t)
    roles=sorted({q for path in paths_for(candidate) for q in path})
    query=' '.join(roles)+' '+' '.join(ROLE_SEARCH_TERMS[q] for q in roles)
    pool=[]; supports=[]; trace=[];feedback=None;previous_ids=[];valid=False
    for round_no in range(max_rounds+1):
        ranked=index.rank(candidate,query,{u.id for _,u in pool},len(index.units)) if round_no==0 else []
        retrieval_log=None
        if round_no==0:
            new=ranked[:pool_size]
        else:
            new,retrieval_log=retriever(client,model,candidate,index,query,
                {u.id for _,u in pool},feedback,pool_size)
        if not new:
            if trace:
                trace[-1]['expansion_without_new_evidence']=retrieval_log
            break
        pool+=new
        # Re-sort pooled relevance; selection never treats retrieval score as factual support.
        pool.sort(key=lambda pair:(-pair[0],pair[1].start))
        additions,log=annotator(client,model,candidate,new,feedback=feedback)
        supports+=additions
        selected=selector(candidate,pool,supports,method=method,client=client,model=model,
                          feedback=feedback,previous_ids=previous_ids)
        attach_evidence(candidate,index,pool,selected)
        # Annotation coverage is diagnostic. The independent Validator reads the
        # exact selected originals, even when a role annotator missed a condition.
        validator_called=bool(candidate['evidence_validated'])
        decision=validator(candidate) if validator_called else False
        if isinstance(decision,dict):
            from validation_agent import parse_feedback
            feedback=parse_feedback(json.dumps(decision,ensure_ascii=False))
            valid=feedback['verdict']=='valid'
        elif type(decision) is bool:
            # Offline/diagnostic validators may retain the binary interface.
            valid=decision
            missing=selected.get('assessment',{}).get('missing_evidence') or [ROLES[q] for q in selected['missing']] or [ROLES[q] for q in roles]
            feedback=dict(verdict='valid' if valid else 'invalid',reason='Binary validator or no selected evidence',
                          missing_evidence=[] if valid else missing,
                          search_queries=[] if valid else [ROLE_SEARCH_TERMS[q] for q in selected['missing'] or roles])
        else:
            raise ValueError('Validator must return structured feedback or a boolean')
        trace.append(dict(round=round_no,selection=selected,annotation=log,validator=valid,
                          validator_called=validator_called,retrieved_ids=[u.id for _,u in new],
                          validator_feedback=feedback,
                          retrieval=retrieval_log,retrieval_query=query))
        if valid:
            break
        previous_ids=selected['ids']
        query=' '.join(feedback['missing_evidence']+feedback['search_queries'])
    if not trace:
        candidate['evidence']=[];candidate['evidence_validated']=False;valid=False
    candidate['valid']=valid
    candidate['evidence_status']='supported' if valid else 'insufficient'
    candidate['validation_response']='valid' if valid else 'invalid'
    candidate['evidence_completion']=dict(document_sha256=index.sha256,
        selector='coherent' if method=='cover' else method,
        max_expansions=max_rounds,trace=trace,
        supports=[s for s in supports if s['unit_id'] in {e['id'] for e in candidate['evidence']}],
        pool_supports=supports)
    return candidate


def collect_data(data,markdown,client,model,*,method='coherent',max_rounds=1):
    index=EvidenceIndex(markdown)
    for candidate in data['kg_extraction_triplets']:
        if not candidate.get('ontology_conformed'):
            candidate.update(evidence=[],evidence_validated=False,valid=False,
                             validation_response='not_in_ontology',evidence_status='not_in_ontology')
            candidate.pop('paragraph',None)
            candidate.pop('evidence_units',None)
            continue
        collect_evidence(candidate,index,client,model,method=method,max_rounds=max_rounds)
    enforce_relation_exclusivity(data)
    return len(data['kg_extraction_triplets']),sum(t['valid'] for t in data['kg_extraction_triplets'])

"""Model-only semantics; local code validates output protocol, never evidence meaning."""
import json
from semantic_prompts import judge_instruction

FIELDS=('source_entity_instance','source_entity_type','relation','target_entity_instance','target_entity_type')

def judge_triplet(client,model,triplet,role):
    """Return one model judgment; evidence expansion belongs to the caller."""
    candidate={k:str(triplet.get(k,'')) for k in FIELDS}
    for side in ('source','target'):
        if triplet.get(side+'_entity_instance_anaphora_resolved'):
            candidate[side+'_entity_instance']=triplet[side+'_entity_instance_anaphora_resolved']
    raw=triplet.get('evidence',triplet.get('evidence_units',triplet.get('paragraph',[])))
    if isinstance(raw,str):raw=[raw]
    units=[]
    for i,u in enumerate(raw):
        if isinstance(u,str):units.append(dict(id=f'e{i}',text=u))
        elif isinstance(u,dict) and isinstance(u.get('text'),str):units.append(dict(id=str(u.get('id',f'e{i}')),text=u['text']))
        else:raise ValueError('Judge evidence must contain text units, not nested alternatives')
    ids={u['id'] for u in units}
    if len(ids)!=len(units):raise ValueError('Duplicate evidence IDs')
    instruction=judge_instruction(candidate['relation'],role)
    for side in ('source','target'):
        if triplet.get(side+'_entity_symbol'):
            candidate[side+'_entity_symbol']=triplet[side+'_entity_symbol']
    if any(k.endswith('_entity_symbol') for k in candidate):
        instruction+='\n候选附带entity_symbol仅标识待核对的原文名称/层级，不是证据。必须从给定原文独立确认该符号的定义、对象和作用。若同一描述性全称用于多个层级，以候选标识所指的符号为待验证对象，不得换成另一个下标/版本；不得因元数据声明就认定关系成立。'
    payload=dict(evidence=units,candidate=candidate,task='只判断最后给出的唯一候选：'+'['+candidate['source_entity_type']+'] '+candidate['source_entity_instance']+' → '+candidate['relation']+' → ['+candidate['target_entity_type']+'] '+candidate['target_entity_instance']+'。不得换成证据支持的其他方向或其他端点。')
    response=client.chat.completions.create(model=model,temperature=0,max_tokens=2400,
        response_format={'type':'json_object'},extra_body={'thinking':{'type':'disabled'}},
        messages=[dict(role='system',content=instruction),dict(role='user',content=json.dumps(payload,ensure_ascii=False))])
    choice=response.choices[0]
    if choice.finish_reason!='stop':raise ValueError('Semantic judge incomplete')
    result=json.loads(choice.message.content or '')
    if result.get('verdict') not in {'valid','invalid'}:raise ValueError('Missing model verdict')
    for key in ('path','reason'):
        if not isinstance(result.get(key),str) or not result[key].strip():raise ValueError('Missing judge '+key)
    checks=result.get('checks')
    if not isinstance(checks,dict) or set(checks)!={'scope','identity','direction','sufficient_path'}:raise ValueError('Malformed judge checks')
    for row in checks.values():
        if (row.get('status') not in {'supported','missing','contradicted'} or not isinstance(row.get('ids'),list)
            or any(not isinstance(i,str) or i not in ids for i in row['ids'])
            or not isinstance(row.get('reason'),str) or not row['reason'].strip()):raise ValueError('Malformed judge check or evidence ID')
        if row['status']=='supported' and not row['ids']:raise ValueError('Supported check has no original evidence ID')
    for key in ('missing_evidence','search_queries'):
        if not isinstance(result.get(key),list) or any(not isinstance(x,str) or not x.strip() for x in result[key]):raise ValueError('Malformed '+key)
    # Contradictory output is a protocol error, never a semantic relabeling.
    if result['verdict']=='valid' and (any(r['status']!='supported' for r in checks.values()) or result['missing_evidence'] or result['search_queries']):
        raise ValueError('Model verdict conflicts with its own checks')
    result.update(model=response.model,response_id=response.id,usage=response.usage.model_dump() if response.usage else None,
                  judgment_mode='model_only',judge=role)
    return result

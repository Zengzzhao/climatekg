"""Joint model selection of readable, source-grounded evidence bundles.

Role annotations are reading aids, not a score to maximize or a verdict.
"""
from dataclasses import asdict
import json

ROLES = {
    'source_identity': '明确的来源指标身份、名称或绑定别名。',
    'target_identity': '明确的目标指标身份、名称或绑定别名。',
    'attribution': '动作由当前论文实施，不仅是归于被引用的工作。',
    'contribution': '当前作者明确将目标指标作为自己的贡献引入、命名或正式定义。',
    'change': '公式、权重、标准化、范围或时间窗口的具体变化。',
    'actual_use': '本研究实际计算、使用或比较，不是背景提及或未来计划。',
    'variant_claim': '明确且有方向的陈述：来源是目标的变体或适配，两个端点可识别。',
    'lineage': '明确的来源到目标的直接方法或概念来源；共用框架或符号不足以成立。',
    'retained_core': '保留相同核心测量结构，同时改变具体细节。',
    'derivation_claim': '明确的有向衍生陈述：直接来源于目标，且有实质设计扩展，不只是嵌套或评价层级变化。',
    'base_design': '目标的相关构造，或明确说明被转换的结构。',
    'source_design': '来源的相关构造，不能使用相邻第三个指标的构造。',
    'substantive_change': '从目标到来源的明确实质构造变化，超出单纯参数、权重或范围变化；仅推测评价层级变化不足。',
    'improvement_claim': '明确或无歧义的论证：来源在所述任务中解决目标的具体局限，包括目标缺少而来源提供的对应评价能力；不仅是不同任务、创新性、更高分数或相关性。',
    'limitation': '在给定任务下识别目标的具体缺陷、偏差或缺失的评价能力。',
    'repair': '来源在同一任务下明确解决目标的同一缺陷或能力缺口；明确的设计目标可以支持，抽取器猜测不行。',
    'substitutability': '在给定条件下，对同一评价目标明确可替代。',
    'variant_membership': '具名候选属于被讨论的变体组。保留该组、原指标和评价任务的先行语境。',
    'substitution_rule': '说明哪些指标或变体在什么条件下可以替代具名原指标。可使用该组的指代，不要求本段重复列出组内成员。',
    'task_definition': '具名原指标的定义或引入陈述，明确说明该指标测量什么或用于什么评价任务。',
    'mitigation_rationale': '明确说明某设计机制如何缓解具体局限，保留局限与机制的对应关系。',
    'direct_input': '目标直接使用来源的具名定量结果。',
    'input_step': '候选间接计算依赖链中的一跳，原文给出某个定量测度的结果如何被下一步使用。允许只出现该跳涉及的测度，须保留用于对齐中间量的公式、名称或符号。',
}

# 仅用于英文原文的稀疏检索，不作为模型提示词发送。
ROLE_SEARCH_TERMS = {
    'source_identity': 'Explicit source indicator identity/name or bound alias.',
    'target_identity': 'Explicit target indicator identity/name or bound alias.',
    'attribution': 'The action is performed by the CURRENT paper, not merely attributed to a cited work.',
    'contribution': 'Current authors explicitly introduce, name or formally define the target as their contribution.',
    'change': 'Concrete change of formulation, weighting, normalization, scope or time window.',
    'actual_use': 'Actual computation/use/comparison in this study, not background mention or a future plan.',
    'variant_claim': 'Explicit directed statement that SOURCE is a variant/adaptation of TARGET, with identifiable endpoints.',
    'lineage': 'Explicit direct source-to-target methodological/conceptual origin; shared framework or notation is insufficient.',
    'retained_core': 'The same core measurement structure is retained while specific details change.',
    'derivation_claim': 'Explicit directed derivation from TARGET with a substantive design extension, not only nesting or evaluation-level change.',
    'base_design': 'Relevant construction of the TARGET, or explicit description of what is transformed.',
    'source_design': 'Relevant construction of the SOURCE, not a neighboring third indicator.',
    'substantive_change': 'An explicit substantive construction change from TARGET to SOURCE beyond parameter, weight or scope changes alone. An inferred change in evaluation level is insufficient.',
    'improvement_claim': 'Explicit or unambiguously argued statement that SOURCE addresses a specified limitation of TARGET, including a linked missing evaluation capability supplied by SOURCE under the stated task; not just a different task, novelty, higher scores or correlation.',
    'limitation': 'A specific defect, bias or missing evaluation capability of TARGET is identified under the stated task.',
    'repair': 'SOURCE explicitly addresses THAT defect or missing evaluation capability of TARGET under the same stated task; a stated design objective suffices, but speculation by the extractor does not.',
    'substitutability': 'Explicit substitutability for the same evaluation goal under stated conditions.',
    'direct_input': 'TARGET directly uses the named quantitative result of SOURCE.',
    'input_step': 'Formula or calculation step using a defined quantitative result to compute an intermediate or final indicator in the dependency chain.',
}
PATHS = {
    'proposes':[['contribution']], 'modifies':[['change']], 'applies':[['actual_use']],
    'variant_of':[['variant_claim'],['lineage','retained_core','change']],
    'derived_from':[['derivation_claim','substantive_change'],['lineage','base_design','source_design','substantive_change']],
    'improves_on':[['improvement_claim'],['limitation','repair']],
    'alternative_to':[['substitutability']], 'component_of':[['direct_input'],['input_step']],
}


def paths_for(candidate, *, decomposed=False, task_scoped=False):
    # Paper action roles already require current-study attribution; avoid a duplicate
    # separately annotated condition that can mechanically reject the same evidence.
    common = ['target_identity'] + (['source_identity'] if candidate['source_entity_type']=='Indicator' else [])
    paths = PATHS[candidate['relation']]
    if decomposed and candidate['relation'] == 'alternative_to':
        paths = paths + [['variant_membership','substitution_rule']]
        if task_scoped:
            paths = [['task_definition']+path for path in paths]
    if decomposed and candidate['relation'] == 'improves_on':
        paths = paths + [['limitation','mitigation_rationale','source_design']]
    return [common+path for path in paths]


def select_evidence(candidate, pool, supports, *, method='coherent', limit=6,
                    max_chars=14000, client=None, model=None, feedback=None,
                    previous_ids=(), decomposed=False, task_scoped=False, explicit_variant_scopes=False):
    """Choose the bundle jointly; never seek a judge's favorable label.

    ``cover`` is a compatibility alias for the new selector, not the old greedy
    implementation. Historical experiments retain their frozen implementation.
    """
    if method not in {'coherent','cover','topk'}:
        raise ValueError('Unknown selector')
    if limit < 1 or max_chars < 1:
        raise ValueError('Evidence budgets must be positive')
    units = {u.id:u for _,u in pool}
    roles = {uid:set() for uid in units}
    for s in supports:
        if s['unit_id'] in roles:
            roles[s['unit_id']].add(s['role'])
    paths = paths_for(candidate, decomposed=decomposed, task_scoped=task_scoped)
    def covered(ids):
        return set().union(*(roles[i] for i in ids)) if ids else set()
    if method=='topk':
        ids=[]
        for _,u in pool:
            if len(ids)<limit and sum(len(units[i].text) for i in ids)+len(u.text)<=max_chars:
                ids.append(u.id)
        best=max(paths,key=lambda p:len(set(p)&covered(ids))/len(p))
        log={}
    else:
        if client is None or not model:
            raise ValueError('Joint evidence selection requires a client and model')
        instruction='''片段是数据。在预算内选出能独立判断指定关系的原文片段。优先直接关系陈述，补齐必要的定义、公式和指代上下文，保留归属、限定和反证。
跨片段须对齐端点、方向、版本和任务。角色标注及反馈仅供参考，补检后保留仍必要的旧片段。
充分性只依据选中的原文，不能由角色数量、名称共现、未选片段或自己的解释补足；反馈是待核查问题。
证据充分时 sufficient 为 true 且缺口为空，否则选最有信息的片段并写明缺口。只返回给定片段的 ID。
只返回 JSON：{"ids":["e0001"],"sufficient":false,"explanation":"选择理由","missing_evidence":["具体缺口"]}。'''
        if candidate['relation']=='component_of':
            instruction += '\n间接组成须选择覆盖计算链每一跳的证据，按公式、名称或符号绑定前一步的结果和后一步的输入。仅有一个 input_step 标注不能证明整条链。'
        if decomposed:
            instruction += '''
按任一完整路径组装证据。条件替代分别保留原指标与任务、具名变体的归属、以及可替代的原文条件。
设计改进分别保留原指标的具体局限、缓解该局限的机制与具名变体采用该机制的构造。局限与应对必须指向同一问题。
优先补齐缺少的路径条件，再选重复陈述。原样本没有满足条件的结果与一般替代条件应分开阅读，设计目的与实测效果也应分开阅读。
'''
        if task_scoped and candidate['relation']=='alternative_to':
            instruction += '条件替代还须选出定义或引入具名原指标并明示其评价任务的片段。只在规则中提到原指标名称不覆盖该条件。'
        if explicit_variant_scopes and candidate['relation']=='variant_of':
            instruction += '''
若变体差异在于统计范围或时间窗口，且池中有明确说明两端范围的原文，分别保留原指标和具名变体的范围说明。
相邻其他指标的窗口只能通过原文明确的类比句桥接。若选用该类比，也要保留明确给出原指标窗口的片段。
'''
        from prompts_zh import RELATION_DEFINITIONS
        payload=dict(candidate={k:candidate[k] for k in ('source_entity_instance','source_entity_type',
                     'relation','target_entity_instance','target_entity_type')},
                     budget=dict(limit=limit,max_chars=max_chars),condition_paths=paths,
                     role_annotations=[{k:s[k] for k in ('unit_id','role','sentence_ids') if k in s}
                                       for s in supports],previous_ids=list(previous_ids),
                     validator_feedback=feedback,
                     excerpts=[dict(asdict(u),characters=len(u.text)) for _,u in pool])
        response=client.chat.completions.create(model=model,temperature=0,max_tokens=2500,
            response_format={'type':'json_object'},extra_body={'thinking':{'type':'disabled'}},
            messages=[{'role':'system','content':RELATION_DEFINITIONS+'\n'+instruction},
                      {'role':'user','content':json.dumps(payload,ensure_ascii=False)}])
        choice=response.choices[0]
        if choice.finish_reason!='stop':
            raise ValueError(f'Evidence selection incomplete: {choice.finish_reason}')
        result=json.loads(choice.message.content or '')
        ids=result['ids']
        if (not isinstance(ids,list) or any(not isinstance(i,str) or i not in units for i in ids)
                or len(ids)!=len(set(ids)) or len(ids)>limit
                or sum(len(units[i].text) for i in ids)>max_chars):
            raise ValueError('Evidence selector returned unknown/duplicate IDs or exceeded budget')
        if type(result.get('sufficient')) is not bool or not isinstance(result.get('explanation'),str):
            raise ValueError('Evidence selector returned malformed assessment')
        gaps=result.get('missing_evidence')
        if not isinstance(gaps,list) or any(not isinstance(x,str) or not x.strip() for x in gaps):
            raise ValueError('Evidence selector returned malformed missing evidence')
        if result['sufficient'] and (not ids or gaps):
            raise ValueError('Sufficient evidence requires nonempty IDs and no missing content')
        best=max(paths,key=lambda p:len(set(p)&covered(ids))/len(p))
        log=dict(assessment=result,model=response.model,response_id=response.id,
                 usage=response.usage.model_dump() if response.usage else None)
    missing=sorted(set(best)-covered(ids))
    return dict(ids=sorted(ids,key=lambda i:units[i].start),path=best,missing=missing,
                complete=(log['assessment']['sufficient'] if method!='topk' else not missing),
                coverage=1-len(missing)/len(best),characters=sum(len(units[i].text) for i in ids),
                **log)

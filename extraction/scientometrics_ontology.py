"""Local type, endpoint and exclusivity checks for the scientific-metrics domain."""
PAPER_RELATIONS = {'proposes', 'modifies', 'applies'}
INDICATOR_RELATIONS = {'variant_of', 'derived_from', 'improves_on', 'alternative_to', 'component_of'}
RELATIONSHIPS = ({('Paper', 'Indicator', r) for r in PAPER_RELATIONS}
                 | {('Indicator', 'Indicator', r) for r in INDICATOR_RELATIONS})


def annotate_scientometrics(data):
    indicators = [e['entity_instance'] for e in data['entities_extracted'] if e['entity_type'] == 'Indicator']
    rank = {name: i for i, name in reversed(list(enumerate(indicators)))}
    paper = data.get('title') or data.get('doi')
    triplets = data['kg_extraction_triplets']
    for t in triplets:
        s, d, r = t['source_entity_instance'], t['target_entity_instance'], t['relation']
        st, dt = t['source_entity_type'], t['target_entity_type']
        valid = ((st, dt, r) in RELATIONSHIPS and d in rank
                 and ((st == 'Paper' and bool(paper) and s == paper)
                      or (st == 'Indicator' and s in rank and s != d)))
        if valid and r == 'alternative_to':
            valid = rank[s] < rank[d]
        t['ontology_conformed'] = bool(valid)
    # Retain competing claims, but flag mutual-exclusion violations instead of inventing a resolution.
    for t in triplets:
        peers = {x['relation'] for x in triplets
                 if x['source_entity_instance'] == t['source_entity_instance']
                 and x['target_entity_instance'] == t['target_entity_instance']
                 and x['source_entity_type'] == t['source_entity_type']
                 and x['target_entity_type'] == t['target_entity_type']}
        if t['relation'] in PAPER_RELATIONS and len(peers & PAPER_RELATIONS) > 1:
            t['ontology_conformed'] = False
        if t['relation'] in {'variant_of', 'derived_from'} and {'variant_of', 'derived_from'} <= peers:
            t['ontology_conformed'] = False
    return sum(t['ontology_conformed'] for t in triplets), sum(not t['ontology_conformed'] for t in triplets)

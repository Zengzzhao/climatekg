"""Local type and endpoint checks for the scientific-metrics domain."""
import unicodedata
import re


def _name_key(name):
    """Typography only: preserve letters, case, suffixes and numeric versions."""
    name = unicodedata.normalize('NFKC', name)
    # Math delimiters and braces on a simple sub/superscript are typography,
    # not semantic aliases. Keep the actual symbols, indices and suffixes.
    name = name.replace('$', '')
    for wrapper in (r'\(', r'\)', r'\[', r'\]'):
        name = name.replace(wrapper, '')
    name = re.sub(r'([_^])\{([A-Za-z0-9]+)\}', r'\1\2', name)
    return ''.join(name.translate(str.maketrans(
        {'‐': '-', '‑': '-', '–': '-', '−': '-'})).split())

PAPER_RELATIONS = {'proposes', 'modifies', 'applies'}
INDICATOR_RELATIONS = {'variant_of', 'derived_from', 'improves_on', 'alternative_to', 'component_of'}
EXCLUSIVE_RELATIONS = frozenset({'variant_of', 'derived_from'})
RELATIONSHIPS = ({('Paper', 'Indicator', r) for r in PAPER_RELATIONS}
                 | {('Indicator', 'Indicator', r) for r in INDICATOR_RELATIONS})


def annotate_scientometrics(data):
    indicators = [e['entity_instance'] for e in data['entities_extracted'] if e['entity_type'] == 'Indicator']
    rank = {name: i for i, name in reversed(list(enumerate(indicators)))}
    paper = data.get('title') or data.get('doi')
    triplets = data['kg_extraction_triplets']
    spellings = {}
    for name in rank:
        spellings.setdefault(_name_key(name), set()).add(name)
    for t in triplets:
        for side in ('source', 'target'):
            field = f'{side}_entity_instance'
            if t[f'{side}_entity_type'] != 'Indicator' or t[field] in rank:
                continue
            matches = spellings.get(_name_key(t[field]), set())
            if len(matches) == 1:
                t.setdefault('entity_name_normalization', {})[field] = t[field]
                t[field] = next(iter(matches))
        s, d, r = t['source_entity_instance'], t['target_entity_instance'], t['relation']
        st, dt = t['source_entity_type'], t['target_entity_type']
        valid = ((st, dt, r) in RELATIONSHIPS and d in rank
                 and ((st == 'Paper' and bool(paper) and s == paper)
                      or (st == 'Indicator' and s in rank and s != d)))
        if valid and r == 'alternative_to':
            # Canonical orientation of a symmetric edge, not a direction error.
            if rank[s] > rank[d]:
                t['source_entity_instance'], t['target_entity_instance'] = d, s
        t['ontology_conformed'] = bool(valid)
    # Candidate hypotheses may conflict; enforce exclusion after independent validation.
    return sum(t['ontology_conformed'] for t in triplets), sum(not t['ontology_conformed'] for t in triplets)


def enforce_relation_exclusivity(data):
    """Gate unresolved competing survivors without using Evaluator or preferring a label.

    Unioned discovery hypotheses are allowed to compete through evidence validation.
    If both exclusive labels survive for the same typed, ordered pair, neither is
    ontology-conformant for final retention. Preserve Validator/evidence verdicts
    and record the conflict for adjudication rather than treating either as false.
    Run after validation and before evaluation/final retention.
    """
    groups = {}
    for t in data['kg_extraction_triplets']:
        if t['relation'] in EXCLUSIVE_RELATIONS and all(
                t.get(f) for f in ('ontology_conformed', 'evidence_validated', 'valid')):
            key = tuple(t[f] for f in ('source_entity_type', 'source_entity_instance',
                                      'target_entity_type', 'target_entity_instance'))
            groups.setdefault(key, []).append(t)
    conflicts = 0
    for rows in groups.values():
        if {t['relation'] for t in rows} != EXCLUSIVE_RELATIONS:
            continue
        conflicts += 1
        for t in rows:
            t['ontology_conformed'] = False
            t['relation_constraint_conflict'] = dict(
                reason='mutually_exclusive_labels', labels=sorted(EXCLUSIVE_RELATIONS),
                resolution='unresolved; neither retained; requires evidence-based adjudication')
    return conflicts

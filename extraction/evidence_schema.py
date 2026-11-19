"""Array evidence I/O; legacy paragraphs are read-only compatibility inputs."""
import json
from rapidfuzz import fuzz

EVIDENCE_MATCH_CONFIG = dict(method='rapidfuzz.partial_ratio_alignment', threshold=80.0,
                             aggregation='all_units', reference='full_document')


def evidence_items(triplet):
    if 'evidence' in triplet:
        value = triplet['evidence']
        if not isinstance(value, list):
            raise ValueError('evidence must be an array')
        return value
    return triplet.get('evidence_units', []) or ([triplet['paragraph']] if triplet.get('paragraph') else [])


def evidence_text(triplet):
    # Legacy evaluators retain their exact original representation for frozen comparisons.
    if 'evidence' not in triplet:
        units = triplet.get('evidence_units')
        if not units:
            return triplet.get('paragraph', '')
        return '\n\n'.join(f"[{u['id']}; section={u['section']}; chars={u['start']}:{u['end']}]\n{u['text']}" for u in units)
    return json.dumps(evidence_items(triplet), ensure_ascii=False)


def verify_units(triplet, markdown):
    """Strict offset audit; independent of the pipeline's fuzzy acceptance gate."""
    units = evidence_items(triplet)
    return bool(units) and all(isinstance(u, dict) and type(u.get('start')) is int
        and type(u.get('end')) is int and 0 <= u['start'] < u['end'] <= len(markdown)
        and markdown[u['start']:u['end']] == u.get('text') for u in units)


def match_units(triplet, markdown, threshold=80.0):
    """Apply the frozen legacy extract.verify_evidence score to every array item.

    Offsets remain an audit field, not an additional exact-match acceptance gate.
    Never concatenate nonadjacent evidence into a synthetic paragraph.
    """
    if not 0 <= threshold <= 100:
        raise ValueError('Evidence threshold must be between 0 and 100')
    units = evidence_items(triplet)
    checks = []
    for i, unit in enumerate(units):
        text = unit.get('text') if isinstance(unit, dict) else None
        nonempty = isinstance(text, str) and bool(text.strip()) and bool(markdown.strip())
        score = 0.0
        if nonempty:
            alignment = fuzz.partial_ratio_alignment(text, markdown)
            # Same scoring convention as the frozen legacy helper.
            score = 100.0 if alignment is None else alignment.score
        checks.append(dict(index=i, id=unit.get('id') if isinstance(unit, dict) else None,
            score=score, passed=bool(nonempty and score >= threshold),
            exact_offset_match=verify_units({'evidence':[unit]},markdown)))
    return dict(method=EVIDENCE_MATCH_CONFIG['method'], threshold=threshold,
        aggregation='all_units', reference='full_document',
        passed=bool(checks) and all(c['passed'] for c in checks),
        exact_offset_match=verify_units(triplet,markdown), units=checks)

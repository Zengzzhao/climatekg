"""Array evidence I/O; legacy paragraphs are read-only compatibility inputs."""
import json


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
    units = evidence_items(triplet)
    return bool(units) and all(isinstance(u, dict) and type(u.get('start')) is int
        and type(u.get('end')) is int and 0 <= u['start'] < u['end'] <= len(markdown)
        and markdown[u['start']:u['end']] == u.get('text') for u in units)

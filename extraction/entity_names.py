"""Lossless display formatting and explicit-alias resolution; no fuzzy merging."""
import re
import unicodedata

_SUBS = str.maketrans('₀₁₂₃₄₅₆₇₈₉ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ', '0123456789aehijklmnoprstuvx')
_SUB_PATTERN = r'[₀₁₂₃₄₅₆₇₈₉ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ]+'


def canonical_spelling(name):
    """Preserve letter case, actual subscripts, superscripts and version content."""
    name = re.sub(_SUB_PATTERN, lambda m: '_' + m.group().translate(_SUBS), name)
    # NFC does not collapse superscripts into baseline digits as NFKC does.
    name = unicodedata.normalize('NFC', name).strip()
    name = name.replace(r'\_', '_').replace('$', '')
    name = re.sub(r'\\(?:mathrm|mathbf|mathit|pmb|text|operatorname)\s*\{([^{}]*)\}', r'\1', name)
    name = re.sub(r'([_^])\s*\{\s*([^{}]+?)\s*\}', lambda m:m[1]+re.sub(r'\s+', '', m[2]), name)
    name = re.sub(r'\\(?:mathrm|mathbf|mathit|pmb|text|operatorname)\s*\{([^{}]*)\}', r'\1', name)
    for wrapper in (r'\(', r'\)', r'\[', r'\]'):
        name = name.replace(wrapper, '')
    name = name.translate(str.maketrans({'‐':'-', '‑':'-', '–':'-', '−':'-'}))
    name = re.sub(r'\s*([_^])\s*', r'\1', name)
    name = re.sub(r'\s*-\s*', '-', name)
    name = re.sub(r'\s*\(\s*', '(', name)
    name = re.sub(r'\s*\)', ')', name)
    return re.sub(r'\s+', ' ', name).strip()


def normalize_inventory(data, text=None):
    """Use one spelling per identity. Ambiguous aliases never bind an endpoint."""
    bindings = {}; entities = {}; name_changes = []
    proposed={}
    if text:
        source_view=' '.join(unicodedata.normalize('NFC',text).split())
        for entity in data['entities_extracted']:
            raw=entity['entity_instance']
            # Formal -index names are already names, not abbreviations to expand.
            if ' ' in raw or raw.endswith('-index'):continue
            full=[]
            for alias in entity.get('aliases',[]):
                name=canonical_spelling(alias)
                if (len(name.split())>=2 and re.fullmatch(r"[A-Za-z][A-Za-z -]+",name)
                        and re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',source_view,re.I)):
                    full.append(re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',source_view,re.I).group())
            if full:proposed[raw]=sorted(set(full),key=lambda n:(-len(n),n))[0]
        collisions={name for name in proposed.values() if list(proposed.values()).count(name)>1}
        proposed={raw:name for raw,name in proposed.items() if name not in collisions}

    for original in data['entities_extracted']:
        raw = original['entity_instance']; name = canonical_spelling(proposed.get(raw,raw))
        aliases = list(dict.fromkeys([*original.get('aliases', []), *([raw] if raw != name else [])]))
        entity = entities.setdefault(name, dict(original, entity_instance=name, aliases=[]))
        entity['aliases'] = list(dict.fromkeys(entity['aliases'] + aliases))
        for alias in [name, raw, *aliases]:
            bindings.setdefault(canonical_spelling(alias), set()).add(name)
        if name != raw:name_changes.append(dict(original=raw, canonical=name))
    data['entities_extracted'] = [entities[k] for k in sorted(entities)]
    # An alias is not permission to erase a mathematical level. Keep symbol
    # variants separate, even when an inventory response mistakenly lists them
    # as aliases. Full-name/abbreviation pairs remain resolvable.
    symbol = re.compile(r'[A-Za-z]{1,6}(?:_[A-Za-z0-9]+)?\Z')
    for spelling, choices in bindings.items():
        for name in tuple(choices):
            if (spelling != name and symbol.fullmatch(spelling) and symbol.fullmatch(name)
                    and ('_' in spelling or '_' in name)):
                choices.remove(name)
                entities[name]['aliases'] = [a for a in entities[name]['aliases']
                                             if canonical_spelling(a) != spelling]
    def resolve(name):
        spelling = canonical_spelling(name)
        if spelling in entities:return spelling
        choices = bindings.get(spelling, set())
        return next(iter(choices)) if len(choices)==1 else name
    for detail in data.get('indicator_details', []):
        detail['indicator'] = resolve(detail['indicator'])
    for t in data.get('kg_extraction_triplets', []):
        for side in ('source', 'target'):
            field = side+'_entity_instance'
            if t[side+'_entity_type'] == 'Indicator':
                name = resolve(t[field])
                if name != t[field]:
                    t.setdefault('entity_name_normalization', {})[field] = t[field]
                    t[field] = name
    if name_changes:data['entity_name_normalization'] = name_changes
    return data

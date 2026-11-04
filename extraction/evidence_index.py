"""Exact-source evidence units and sparse relation-aware candidate retrieval."""
from dataclasses import asdict, dataclass
from collections import Counter
import hashlib
import math
import re


def reading_form(text):
    def normalize(m):
        text = re.sub(r'\\(?:mathrm|mathbf|mathit|left|right|text|operatorname)\b', '', m.group())
        return re.sub(r'[\s${}]', '', text)
    text = re.sub(r'\$[^$]*\$|\\\([^\n]*?\\\)', normalize, text)
    text = re.sub(r'([_^])\{([A-Za-z0-9]+)\}', r'\1\2', text)
    return text.translate(str.maketrans({'‐':'-', '‑':'-', '–':'-', '−':'-'})).lower()


def entity_match(name, text):
    name = reading_form(name)
    variants = [name] + ([name[:-6]] if name.endswith('-index') else [])
    return any(re.search(r'(?<!\w)' + re.escape(v) + r'(?![\w(])', reading_form(text)) for v in variants if v)


def tokens(text):
    return re.findall(r'\w+', reading_form(text))


@dataclass(frozen=True)
class EvidenceUnit:
    id: str
    start: int
    end: int
    section: str
    text: str


class EvidenceIndex:
    def __init__(self, markdown):
        if not markdown.strip():
            raise ValueError('Empty source document')
        self.markdown = markdown
        self.sha256 = hashlib.sha256(markdown.encode()).hexdigest()
        self.units = []
        section = 'front matter'
        for block in re.finditer(r'\S[\s\S]*?(?=\n[ \t]*\n|\Z)', markdown):
            text = block.group()
            heading = re.match(r'#{1,6}\s+([^\n]+)', text)
            if heading:
                section = heading[1]
            if re.match(r'^(?:\d+[.\s]*)?(references|bibliography)\b', section, re.I):
                continue
            for offset in range(0, len(text), 2000):
                start, end = block.start()+offset, min(block.end(), block.start()+offset+2600)
                self.units.append(EvidenceUnit(f'e{len(self.units):04d}', start, end, section, markdown[start:end]))
                if end == block.end():
                    break
        self.by_id = {u.id:u for u in self.units}
        self.counts = {u.id:Counter(tokens(u.text)) for u in self.units}
        df = Counter(t for c in self.counts.values() for t in c)
        self.idf = {t:math.log(1+(len(self.units)+1)/(n+1)) for t,n in df.items()}

    def rank(self, candidate, query, excluded=(), limit=12):
        names = [candidate['target_entity_instance']]
        if candidate['source_entity_type'] == 'Indicator':
            names.append(candidate['source_entity_instance'])
        qt = set(tokens(' '.join(names)+' '+query))
        ranked = []
        for u in self.units:
            if u.id in excluded:
                continue
            c = self.counts[u.id]
            lexical = sum(self.idf.get(t,0)*min(c[t],3) for t in qt)/math.sqrt(max(20,sum(c.values())))
            mentions = sum(entity_match(n,u.text) for n in names)
            structure = .25 if re.search('abstract|method|definition|conclusion',u.section,re.I) else 0
            ranked.append((lexical+2*mentions+structure,u))
        return sorted(ranked,key=lambda item:(-item[0],item[1].start))[:limit]

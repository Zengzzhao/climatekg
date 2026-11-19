"""Exact-source semantic units: complete sentences, bound formulas and whole tables."""
import re
import unicodedata
from evidence_index import EvidenceUnit

MATH = re.compile(r'\$\$[\s\S]*?\$\$|\$[^$\n]*\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]')
ABBREVIATION = re.compile(r'(?:\b(?:al|figs?|eqs?|refs?|vol|vs|cf|dr|mr|ms|prof)|\b(?:[a-z]\.)+[a-z])\.$', re.I)
REFERENCES = re.compile(r'^(?:\d+[.\s]*)?(references|bibliography)\b', re.I)
HEADING = re.compile(r'^#{1,6}\s+([^\n]+)')
IMAGE = re.compile(r'!\[[^\]\n]*\]\((?:[^()\n]|\([^()\n]*\))*\)|<img\b[^>]*>', re.I)
STRUCTURE = re.compile(
    r'<table\b[^>]*>[\s\S]*?</table>'
    r'|\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]'
    r'|^[ \t]*\|[^\n]*\|[ \t]*\n[ \t]*\|?[ \t]*:?-{3,}[^\n]*\n(?:[ \t]*\|[^\n]*(?:\n|$))*'
    r'|!\[[^\]\n]*\]\((?:[^()\n]|\([^()\n]*\))*\)|<img\b[^>]*>',
    re.I | re.M,
)
LEAD_IN = re.compile(
    r'(?:[:：]|(?:as follows|in the following way|given (?:by|as)|defined (?:by|as)|computed (?:by|as)|calculated (?:by|as)))\s*(?:\\\s*)?$', re.I)
GLOSS = re.compile(r'^(?:where|here|with)\b', re.I)
POLICY = dict(strategy='semantic', prose='complete_sentence',
    formula='lead_in+display_formula+variable_gloss', table='whole',
    fixed_character_cap=None, overlap=0, offsets='exact_source')

def sentence_ranges(text):
    """Return trimmed offsets, treating math, common abbreviations and decimals atomically."""
    masked = MATH.sub(lambda m: '_' * len(m.group()), text)
    # Closing parentheses can be inside inline math while the opener is prose.
    # Pair against the original text, rather than the math-masked copy.
    stack=[]
    parenthetical=[False]*len(text)
    for i,char in enumerate(text):
        if char in '(（':stack.append((i,char))
        elif char in ')）' and stack:
            lo,opener=stack[-1]
            if (opener,char) in {('(',')'),('（','）')}:
                stack.pop()
                parenthetical[lo:i+1]=[True]*(i+1-lo)
    boundaries = [0]
    for match in re.finditer(r'[.!?][\x22\x27”’)]*\s+(?=[A-Z0-9$\\])', masked):
        punctuation = match.start() + 1
        if not parenthetical[match.start()] and not ABBREVIATION.search(masked[:punctuation]):
            boundaries.append(match.end())
    boundaries.append(len(text))
    return [
        (lo + len(text[lo:hi]) - len(text[lo:hi].lstrip()), lo + len(text[lo:hi].rstrip()))
        for lo, hi in zip(boundaries, boundaries[1:]) if text[lo:hi].strip()
    ]



def atoms(markdown):
    """Return indivisible semantic spans, without character caps or sliding windows."""
    rows = []
    section = 'front matter'

    def prose(start, end):
        nonlocal section
        for block in re.finditer(r'\S[\s\S]*?(?=\n[ \t]*(?:\\[ \t]*)?\n|\Z)', markdown[start:end]):
            lo = start + block.start()
            text = block.group()
            heading = HEADING.match(text)
            if heading:
                body=unicodedata.normalize('NFKC',heading[1])
                lhs,sep,rhs=body.partition('=')
                formula_heading=(sep and re.fullmatch(r'[\w\s_^{}\\*-]{1,64}',lhs)
                    and len(lhs.split())<=6 and re.search(r'[+*/∕÷×−]',rhs)
                    and rows and rows[-1][2]==section
                    and LEAD_IN.search(markdown[rows[-1][0]:rows[-1][1]])
                    and not markdown[rows[-1][1]:lo].strip(' \t\r\n\\'))
                if formula_heading:
                    # Preserve the original heading markers and offsets; the
                    # usual formula-binding pass joins this to its lead-in.
                    end=lo+len(heading.group().rstrip())
                    rows.append((lo,end,section,'formula'))
                    tail=text.find('\n')
                    if tail>=0:
                        rows.extend((lo+tail+1+a,lo+tail+1+b,section,'sentence')
                                    for a,b in sentence_ranges(text[tail+1:]))
                    continue
                section = heading[1]
                # Heading is context metadata, not an evidence sentence.
                tail = text.find('\n')
                if tail < 0:
                    continue
                lo += tail + 1
                text = text[tail+1:]
            if REFERENCES.match(section):
                continue
            rows.extend((lo+a, lo+b, section, 'sentence') for a,b in sentence_ranges(text))

    cursor = 0
    for match in STRUCTURE.finditer(markdown):
        prose(cursor, match.start())
        if IMAGE.fullmatch(match.group()):
            # Image paths/alt text are not source evidence; following captions
            # remain ordinary original text with unchanged character offsets.
            cursor = match.end()
            continue
        if not REFERENCES.match(section):
            kind = 'table' if match.group().lstrip().startswith(('<', '|')) else 'formula'
            rows.append((match.start(), match.start()+len(match.group().rstrip()), section, kind))
        cursor = match.end()
    prose(cursor, len(markdown))
    packed = []
    for lo, hi, section, kind in rows:
        if kind == 'formula' and packed:
            a,b,s,k = packed[-1]
            if s == section and k in {'sentence','formula'} and not markdown[b:lo].strip(' \t\r\n\\'):
                if k == 'formula' or LEAD_IN.search(markdown[a:b]):
                    packed.pop()
                    lo = a
        elif kind == 'sentence' and packed and GLOSS.match(markdown[lo:hi]):
            a,b,s,k = packed[-1]
            if k == 'formula' and s == section and not markdown[b:lo].strip(' \t\r\n\\'):
                packed.pop()
                lo,kind = a,'formula'
        packed.append((lo,hi,section,kind))
    return [(lo,hi,section) for lo,hi,section,_ in packed]


def chunk_units(markdown, strategy='semantic', max_chars=None, max_sentences=None):
    # Budget arguments remain accepted for callers' configuration schema; they
    # never split a semantic unit. Old chunk strategies have been removed.
    if strategy != 'semantic':
        raise ValueError(f'Old chunk strategy {strategy!r} has been removed; use semantic')
    return [EvidenceUnit(f'e{lo}-{hi}',lo,hi,section,markdown[lo:hi])
            for lo,hi,section in atoms(markdown)]

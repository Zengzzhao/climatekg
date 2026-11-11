"""Sentence-aligned evidence chunks with exact Markdown coordinates."""
import re

from evidence_index import EvidenceUnit

BLOCK = re.compile(r'\S[\s\S]*?(?=\n[ \t]*\n|\Z)')
HEADING = re.compile(r'#{1,6}\s+([^\n]+)')
REFERENCES = re.compile(r'^(?:\d+[.\s]*)?(references|bibliography)\b', re.I)
MATH = re.compile(r'\$\$[\s\S]*?\$\$|\$[^$\n]*\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]')
DISPLAY = re.compile(r'^(?:\$\$|\\\[)')
TABLE_ROWS = re.compile(r'<tr\b[^>]*>[\s\S]*?</tr>', re.I)
BLOCK_BREAK = re.compile(r'\n[ \t]*\n')
ABBREVIATION = re.compile(r'(?:\b(?:al|figs?|eqs?|refs?|vol|vs|cf|dr|mr|ms|prof)|\b(?:[a-z]\.)+[a-z])\.$', re.I)
ANAPHOR = re.compile(
    r'^(?:this|these|those|such|it\b|they\b|the (?:former|latter)|'
    r'further\b|that is\b|where\b|here\b|also\b|however\b|therefore\b)', re.I
)


def sentence_ranges(text):
    """Return trimmed offsets, treating math, common abbreviations and decimals atomically."""
    masked = MATH.sub(lambda m: '_' * len(m.group()), text)
    boundaries = [0]
    for match in re.finditer(r'[.!?][\x22\x27”’)]*\s+(?=[A-Z0-9$\\])', masked):
        punctuation = match.start() + 1
        if not ABBREVIATION.search(masked[:punctuation]):
            boundaries.append(match.end())
    boundaries.append(len(text))
    return [
        (lo + len(text[lo:hi]) - len(text[lo:hi].lstrip()), lo + len(text[lo:hi].rstrip()))
        for lo, hi in zip(boundaries, boundaries[1:]) if text[lo:hi].strip()
    ]


def blocks(markdown):
    section = 'front matter'
    for block in BLOCK.finditer(markdown):
        heading = HEADING.match(block.group())
        if heading:
            section = heading[1]
        if not REFERENCES.match(section):
            yield block, section


def atoms(markdown, bind_formula=False, max_chars=500, split_tables=False):
    """Keep display formulas whole. Bind adjacent setup and variable glosses when they fit."""
    rows = []
    for block, section in blocks(markdown):
        text = block.group()
        if split_tables and text.lower().startswith('<table'):
            ranges = [row.span() for row in TABLE_ROWS.finditer(text)] or sentence_ranges(text)
        else:
            ranges = [(0, len(text.rstrip()))] if DISPLAY.match(text) else sentence_ranges(text)
        rows.extend((block.start()+lo, block.start()+hi, section) for lo, hi in ranges)
    if not bind_formula:
        return rows
    packed = []
    i = 0
    while i < len(rows):
        lo, hi, section = rows[i]
        if DISPLAY.match(markdown[lo:hi]):
            if packed and packed[-1][2] == section and hi - packed[-1][0] <= max_chars:
                prev_lo, prev_hi, _ = packed[-1]
                if markdown[prev_lo:prev_hi].endswith(':'):
                    lo = packed.pop()[0]
            if i+1 < len(rows):
                next_lo, next_hi, next_section = rows[i+1]
                if (next_section == section and next_hi-lo <= max_chars
                        and re.match(r'^(?:where\b|here\b|with\b)', markdown[next_lo:next_hi], re.I)):
                    hi = next_hi
                    i += 1
        packed.append((lo, hi, section))
        i += 1
    return packed


def bounded_ranges(text, lo, hi, max_chars):
    """Split oversize prose at a clause or word boundary outside inline math."""
    if hi-lo <= max_chars or DISPLAY.match(text[lo:hi]):
        yield lo, hi
        return
    masked = MATH.sub(lambda m: '_'*len(m.group()), text[lo:hi])
    boundaries = [(lo+m.start(), lo+m.end()) for m in re.finditer(r'\s+', masked)]
    clauses = {a for a,_ in boundaries if a>lo and text[a-1] in ',;:'}
    while hi-lo > max_chars:
        options = [(a,b) for a,b in boundaries if lo < a <= lo+max_chars]
        if not options:
            # A formula or token longer than the cap remains an indivisible atom.
            options = [(a,b) for a,b in boundaries if a>lo][:1]
        if not options:
            break
        end, start = next(((a,b) for a,b in reversed(options) if a in clauses and a>=lo+max_chars*.5),
                          options[-1])
        yield lo,end
        lo = start
    if lo < hi:
        yield lo,hi


def chunk_units(markdown, strategy, max_chars, max_sentences):
    if strategy not in {'char', 'sentence', 'window', 'context', 'bounded'}:
        raise ValueError(f'Unknown chunk strategy {strategy}')
    if max_chars < 1 or max_sentences < 1:
        raise ValueError('Chunk budgets must be positive')
    if strategy == 'char':
        step = max(1, 4*max_chars//5)
        ranges = []
        for block, section in blocks(markdown):
            for lo in range(block.start(), block.end(), step):
                hi = min(lo+max_chars, block.end())
                ranges.append((lo, hi, section))
                if hi == block.end():
                    break
    else:
        contextual = strategy in {'context','bounded'}
        spans = atoms(markdown, bind_formula=contextual, max_chars=max_chars,
                      split_tables=strategy == 'bounded')
        if strategy == 'bounded':
            spans = [(a,b,section) for lo,hi,section in spans
                     for a,b in bounded_ranges(markdown,lo,hi,max_chars)]
        ranges = []
        i = 0
        while i < len(spans):
            lo, hi, section = spans[i]
            if contextual and i and ANAPHOR.match(markdown[lo:hi]):
                prev_lo, prev_hi, prev_section = spans[i-1]
                if prev_section == section and not BLOCK_BREAK.search(markdown[prev_hi:lo]) and hi-prev_lo <= max_chars:
                    lo = prev_lo
            j = i + 1
            consumed = 2 if lo < spans[i][0] else 1
            while j < len(spans) and consumed < max_sentences:
                next_lo, next_hi, next_section = spans[j]
                if (next_section != section or next_hi-lo > max_chars
                        or BLOCK_BREAK.search(markdown[hi:next_lo])):
                    break
                hi = next_hi
                j += 1
                consumed += 1
            ranges.append((lo, hi, section))
            i = j if strategy == 'sentence' else i+1
    return [
        EvidenceUnit(f'e{i:04d}', lo, hi, section, markdown[lo:hi])
        for i, (lo, hi, section) in enumerate(sorted(set(ranges))) if markdown[lo:hi].strip()
    ]

"""Complete a truncated formula introduction using only its immediate continuation.

No fuzzy anchor matching, equation-number lookup, retrieval, or LLM calls. The
result remains one exact, contiguous substring of the supplied Markdown.
"""
import re


MAX_ADDED_CHARS = 2000
MAX_ADDED_LINES = 40
MAX_FORMULAS = 2
_CUE = re.compile(
    r"(?:\b(?:formula|equation|expression)s?\b[^.!?\n]{0,100}"
    r"\b(?:below|as\s+follows)\s*[:：.]?"
    r"|(?:公式|方程|表达式)(?:如下|见下式|见下文)\s*[:：。]?)\s*$",
    re.IGNORECASE,
)
_EXPLANATION = re.compile(r"(?:where\b|with\b|in\s+which\b|式中|其中)", re.IGNORECASE)
# Only display math immediately after the introductory sentence is eligible.
_MATH_DELIMITERS = (("$$", "$$"), (r"\[", r"\]"),
                    ("```math", "```"), ("```latex", "```"))


def _formula_end(text: str, start: int) -> int | None:
    for opening, closing in _MATH_DELIMITERS:
        if text.startswith(opening, start):
            content_start = start + len(opening)
            end = text.find(closing, content_start)
            if end != -1 and text[content_start:end].strip():
                return end + len(closing)
            return None
    return None


def complete_local_formula_evidence(data: dict, markdown: str) -> int:
    """Extend eligible triplet paragraphs in place; return the number extended.

    At most two adjacent display formulas and one adjacent variable-explanation
    paragraph are included, within fixed character/line limits. Missing,
    duplicated, malformed or nonadjacent anchors/formulas are left unchanged.
    """
    completed = 0
    for triplet in data.get("kg_extraction_triplets", []):
        if triplet.get("local_formula_completion"):
            continue
        paragraph = triplet.get("paragraph", "")
        if not isinstance(paragraph, str) or not _CUE.search(paragraph[-200:]):
            continue
        start = markdown.find(paragraph)
        if start == -1 or markdown.find(paragraph, start + 1) != -1:
            continue
        original_end = start + len(paragraph)
        # Reading is strictly bounded from the end of the matched paragraph.
        tail = markdown[original_end:original_end + MAX_ADDED_CHARS]
        end = 0
        for _ in range(MAX_FORMULAS):
            next_start = end + len(tail[end:]) - len(tail[end:].lstrip())
            formula_end = _formula_end(tail, next_start)
            if formula_end is None:
                break
            if tail[:formula_end].count("\n") > MAX_ADDED_LINES:
                break
            end = formula_end
        if not end:
            continue

        next_start = end + len(tail[end:]) - len(tail[end:].lstrip())
        if _EXPLANATION.match(tail, next_start):
            # One full paragraph only. Never truncate a definition to fit a cap.
            remaining = tail[next_start:]
            boundary = re.search(r"\n[ \t]*\n|\n(?=#{1,6}\s)", remaining)
            if boundary:
                explanation_end = original_end + next_start + boundary.start()
            elif original_end + len(tail) == len(markdown):
                explanation_end = len(markdown)
            else:
                explanation_end = original_end + end
            added = markdown[original_end:explanation_end]
            if (len(added) <= MAX_ADDED_CHARS
                    and added.count("\n") <= MAX_ADDED_LINES):
                end = explanation_end - original_end

        final_end = original_end + end
        triplet["paragraph_original"] = paragraph
        triplet["paragraph"] = markdown[start:final_end]
        triplet["local_formula_completion"] = {
            "rule": "adjacent_display_formula_v1",
            "char_start": start,
            "char_end": final_end,
        }
        # Existing decisions no longer describe the changed evidence. Do not
        # turn them true automatically; both LLM stages must judge it afresh.
        for flag in ("evidence_validated", "valid", "eval"):
            triplet.pop(flag, None)
        completed += 1
    return completed

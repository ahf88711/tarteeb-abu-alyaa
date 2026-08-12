"""Arabic name normalization for matching (conservative)."""

from __future__ import annotations

import re
import unicodedata

_TASHKEEL = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_TATWEEL = "\u0640"
_WHITESPACE = re.compile(r"\s+")

_ALEF_MAP = str.maketrans({
    "أ": "ا",
    "إ": "ا",
    "آ": "ا",
    "ٱ": "ا",
})

# Single-character OCR confusions for soft matching only
_SOFT_CHAR = str.maketrans({
    "ى": "ي",
    "ة": "ه",
    "ؤ": "و",
    "ئ": "ي",
    "گ": "ك",
    "ڤ": "ف",
    # common Vision confusions on scanned Naskh
    "ه": "ه",
})


def strip_tashkeel(text: str) -> str:
    return _TASHKEEL.sub("", text)


def normalize_arabic_name(text: str) -> str:
    """Conservative normalization for identity keys."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = strip_tashkeel(t)
    t = t.replace(_TATWEEL, "")
    t = t.translate(_ALEF_MAP)
    t = re.sub(r"[\u200c\u200d\u200e\u200f\u202a-\u202e\ufeff]", "", t)
    t = re.sub(r"^[\d٠-٩]+\s*[\-–.]\s*", "", t)
    t = re.sub(r"[^\u0600-\u06FF\u0750-\u077F\s()ـ\-]", " ", t)
    t = re.sub(r"\s+(عريف|مريف|هريف|جندي|رقيب)\s*$", "", t)
    # Strip parenthetical role markers for keying but keep content inside lightly
    t = _WHITESPACE.sub(" ", t).strip()
    return t


def soft_normalize_for_fuzzy(text: str) -> str:
    """More aggressive normalization for candidate generation only."""
    t = normalize_arabic_name(text)
    t = t.translate(_SOFT_CHAR)
    # collapse (مكلف) etc. for matching
    t = re.sub(r"\([^)]*\)", " ", t)
    # Common prefix/spacing variants
    t = t.replace("عبدال", "عبد ال")
    t = t.replace("عبد ال", "عبدال")  # normalize to compact form
    t = _WHITESPACE.sub(" ", t).strip()
    return t


def tokenize_name(text: str) -> list[str]:
    n = normalize_arabic_name(text)
    return [p for p in n.split(" ") if p]


def _char_similarity(a: str, b: str) -> float:
    """Normalized Levenshtein similarity for one token."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    previous = list(range(lb + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(
                min(
                    current[j - 1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (char_a != char_b),
                )
            )
        previous = current
    distance = previous[-1]
    return max(0.0, 1.0 - distance / max(la, lb))


def name_similarity(a: str, b: str) -> float:
    """
    Deterministic similarity in [0, 1].
    Used only for ranking candidates — never auto-forces identity alone.
    """
    if not a or not b:
        return 0.0
    na, nb = normalize_arabic_name(a), normalize_arabic_name(b)
    if na == nb:
        return 1.0
    sa, sb = soft_normalize_for_fuzzy(a), soft_normalize_for_fuzzy(b)
    if sa == sb:
        # Soft equality can collapse ة/ه or ى/ي. It is strong candidate
        # evidence, but deliberately below the automatic identity threshold.
        return 0.94

    la, lb = sa.split(), sb.split()
    if not la or not lb:
        return 0.0

    first = _char_similarity(la[0], lb[0])
    family = _char_similarity(la[-1], lb[-1])
    if len(la) == len(lb):
        aligned = [_char_similarity(x, y) for x, y in zip(la, lb)]
        middle = sum(aligned[1:-1]) / max(1, len(aligned) - 2)
        score = 0.32 * first + 0.32 * family + 0.36 * middle
        if len(la) == 2:
            score = 0.5 * first + 0.5 * family
        # Same first/family but a materially different patronymic is exactly
        # the dangerous سعد/سعيد case: always require human review.
        if len(la) >= 3 and first == 1.0 and family == 1.0 and min(aligned[1:-1]) < 0.75:
            score = min(score, 0.88)
    else:
        shorter, longer = (la, lb) if len(la) < len(lb) else (lb, la)
        # Ordered subsequence alignment. Missing name components are never
        # treated like exact agreement.
        cursor = 0
        similarities: list[float] = []
        for token in shorter:
            candidates = [
                (_char_similarity(token, other), index)
                for index, other in enumerate(longer[cursor:], cursor)
            ]
            if not candidates:
                break
            sim, index = max(candidates)
            similarities.append(sim)
            cursor = index + 1
        aligned_mean = sum(similarities) / max(1, len(longer))
        score = 0.28 * first + 0.28 * family + 0.44 * aligned_mean
        score *= max(0.55, 1.0 - 0.13 * abs(len(la) - len(lb)))

    if first < 0.6 or family < 0.6:
        score *= 0.72
    return max(0.0, min(0.99, score))

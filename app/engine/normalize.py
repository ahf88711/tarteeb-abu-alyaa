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
    # Family-name OCR drift (soft only)
    for a, b in (
        ("البنافي", "البناقي"),
        ("البناهي", "البناقي"),
        ("العازمي", "الحازمي"),
        ("الصلبى", "الصلبي"),
        ("المنزي", "العنزي"),
    ):
        t = t.replace(a, b)
    t = _WHITESPACE.sub(" ", t).strip()
    return t


def tokenize_name(text: str) -> list[str]:
    n = normalize_arabic_name(text)
    return [p for p in n.split(" ") if p]


def _char_similarity(a: str, b: str) -> float:
    """Simple character-level ratio for short tokens (family names)."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Levenshtein-lite for short strings
    if abs(len(a) - len(b)) > 2:
        return 0.0
    # longest common subsequence ratio
    la, lb = len(a), len(b)
    dp = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    lcs = dp[la][lb]
    return (2 * lcs) / (la + lb)


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
        return 0.96

    la, lb = sa.split(), sb.split()
    if not la or not lb:
        return 0.0

    ta, tb = set(la), set(lb)
    inter = len(ta & tb)
    union = len(ta | tb)
    jacc = inter / union if union else 0.0

    bonus = 0.0
    # First name match
    if la[0] == lb[0]:
        bonus += 0.12
    elif _char_similarity(la[0], lb[0]) >= 0.8:
        bonus += 0.06

    # Family name (last token) match — strong signal
    if la[-1] == lb[-1]:
        bonus += 0.15
    else:
        fam = _char_similarity(la[-1], lb[-1])
        if fam >= 0.85:
            bonus += 0.12
        elif fam >= 0.7:
            bonus += 0.06

    # Same length names: compare middle tokens
    if len(la) == len(lb) == 3:
        mid = _char_similarity(la[1], lb[1])
        if la[1] == lb[1]:
            bonus += 0.1
        elif mid >= 0.8:
            bonus += 0.05
        elif mid < 0.5 and la[0] == lb[0] and la[-1] == lb[-1]:
            # first+last same but middle different (سعد vs سعيد) — cap score
            return min(0.88, jacc + bonus)

    # first+last strong agreement even if OCR mangled middle of full string
    first_ok = la[0] == lb[0] or _char_similarity(la[0], lb[0]) >= 0.85
    last_ok = la[-1] == lb[-1] or _char_similarity(la[-1], lb[-1]) >= 0.85
    if first_ok and last_ok and min(len(la), len(lb)) >= 2:
        bonus += 0.08

    score = min(1.0, jacc + bonus)

    # Penalize very different token counts with low overlap
    if abs(len(la) - len(lb)) >= 2 and inter <= 1:
        score *= 0.7

    return score

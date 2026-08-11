"""Extract and verify target names from photographed/scanned list."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Optional

from .models import MasterPerson, NameStatus, TargetName, make_master_key
from .normalize import name_similarity, normalize_arabic_name, soft_normalize_for_fuzzy
from .ocr import load_image_any, ocr_image, OcrToken

_NOISE = re.compile(
    r"(وزارة|المملكة|الداخلية|حرس|الحدود|قيادة|قطاع|بيان|التكميل|اليومي|"
    r"أفراد|المنفذ|التوقيع|الرتبة|الأسم|الاسم|معد|الملاحظات|إجازة|اجازة|"
    r"رخصة|تأخير|تاخير|موجود|نضار|بسم|الله|اللّه|الرحمن|الرحيم|الموافق|"
    r"يوم|الثلاثاء|الاثنين|الأحد|السبت|الخميس|الجمعة|الأربعاء|"
    r"عنه|لمدة|ساعة|ساعات|أيام|اعتبارا|اعتبارًا|الموجود|العريف)"
)

_RANK_ONLY = re.compile(
    r"^(عريف|جندي\s*أول|جندي\s*اول|جندي|جندى|رقيب|و\.?\s*رقيب|وكيل|ملازم|نقيب)$"
)


def _is_name_candidate(text: str) -> bool:
    t = normalize_arabic_name(text)
    if not t or len(t) < 6:
        return False
    parts = [p for p in t.split() if p]
    if len(parts) < 2 or len(parts) > 5:
        return False
    if _RANK_ONLY.match(t):
        return False
    if _NOISE.search(t):
        return False
    # Reject if too many digits
    if sum(ch.isdigit() or ch in "٠١٢٣٤٥٦٧٨٩" for ch in t) > 2:
        return False
    # Reject glued rank lists
    if t.count("عريف") >= 1 and len(parts) > 3:
        return False
    arabic_parts = [p for p in parts if re.search(r"[\u0600-\u06FF]", p)]
    return len(arabic_parts) >= 2


def extract_name_tokens(tokens: list[OcrToken]) -> list[tuple[str, OcrToken]]:
    """Prefer individual OCR tokens that look like person names."""
    candidates: list[tuple[str, OcrToken]] = []
    for t in tokens:
        text = t.text.strip()
        text = re.sub(r"^(الأسم|الاسم)\s*:\s*", "", text)
        # Skip very wide tokens (often full-line garbage)
        if t.w > 0.45:
            continue
        if _is_name_candidate(text):
            candidates.append((text, t))

    seen: set[str] = set()
    unique: list[tuple[str, OcrToken]] = []
    for text, tok in candidates:
        key = make_master_key(text)
        if key in seen:
            continue
        seen.add(key)
        unique.append((text, tok))
    return unique


def match_to_master(
    ocr_name: str,
    master: dict[str, MasterPerson],
    *,
    high: float = 0.92,
    ambiguous_gap: float = 0.08,
) -> tuple[NameStatus, float, list[dict], Optional[str]]:
    """Match OCR name against master. Never force ambiguous identity."""
    if not master:
        return NameStatus.NEEDS_REVIEW, 0.5, [], None

    from .models import make_master_key

    ocr_key = make_master_key(ocr_name)

    # 1) Exact normalized key — definitive (no ambiguity possible for identity)
    if ocr_key and ocr_key in master:
        p = master[ocr_key]
        return (
            NameStatus.VERIFIED,
            1.0,
            [
                {
                    "name": p.original_name,
                    "normalized": p.normalized_name,
                    "confidence": 1.0,
                    "pages": p.pages,
                }
            ],
            p.original_name,
        )

    scored: list[tuple[float, MasterPerson]] = []
    for person in master.values():
        s = name_similarity(ocr_name, person.original_name)
        s2 = name_similarity(
            soft_normalize_for_fuzzy(ocr_name),
            soft_normalize_for_fuzzy(person.original_name),
        )
        s = max(s, s2 * 0.98)
        if s >= 0.55:
            scored.append((s, person))
    scored.sort(key=lambda x: -x[0])

    if not scored:
        return NameStatus.NOT_IN_MASTER, 0.2, [], None

    best_s, best_p = scored[0]
    candidates = [
        {
            "name": p.original_name,
            "normalized": p.normalized_name,
            "confidence": round(s, 4),
            "pages": p.pages,
        }
        for s, p in scored[:5]
    ]

    # 2) Two close high scores for different people → human review
    if len(scored) >= 2:
        s2, p2 = scored[1]
        if (
            best_p.normalized_name != p2.normalized_name
            and (best_s - s2) < ambiguous_gap
            and best_s >= 0.70
        ):
            return NameStatus.AMBIGUOUS, best_s, candidates, None

    # 3) High confidence unique winner
    if best_s >= high:
        return NameStatus.VERIFIED, best_s, candidates, best_p.original_name

    # 4) Medium → review (never auto-verify)
    if best_s >= 0.80:
        return NameStatus.NEEDS_REVIEW, best_s, candidates, best_p.original_name

    if best_s >= 0.62:
        return (
            NameStatus.NEEDS_REVIEW,
            best_s,
            candidates,
            best_p.original_name if best_s >= 0.72 else None,
        )

    if best_s >= 0.55:
        return NameStatus.NEEDS_REVIEW, best_s, candidates, None

    return NameStatus.NOT_IN_MASTER, best_s, candidates, None


def extract_target_names(
    path: Path,
    master: dict[str, MasterPerson],
) -> list[TargetName]:
    """High-risk extraction with master cross-check. No forced matches."""
    img = load_image_any(path)
    tokens = ocr_image(img, preprocess=True)
    raw_names = extract_name_tokens(tokens)

    results: list[TargetName] = []
    seen: set[str] = set()

    for text, tok in raw_names:
        key = make_master_key(text)
        if key in seen:
            continue
        seen.add(key)

        status, conf, candidates, matched = match_to_master(text, master)

        # Exact key in master → verified
        if key in master:
            status = NameStatus.VERIFIED
            conf = max(conf, 0.99)
            matched = master[key].original_name
            candidates = [
                {
                    "name": master[key].original_name,
                    "normalized": key,
                    "confidence": 0.99,
                    "pages": master[key].pages,
                }
            ] + [c for c in candidates if c["normalized"] != key]

        results.append(
            TargetName(
                id=str(uuid.uuid4())[:8],
                original_name=text.strip(),
                normalized_name=key,
                ocr_raw=tok.text,
                confidence=round(conf, 4),
                status=status,
                candidates=candidates,
                matched_master_name=matched,
                bbox={"x": tok.x, "y": tok.y, "w": tok.w, "h": tok.h},
            )
        )

    # Sort roughly top-to-bottom using Vision y (higher y = higher on page in our cy)
    results.sort(key=lambda t: (t.bbox or {}).get("y", 0), reverse=True)
    return results

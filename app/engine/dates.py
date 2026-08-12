"""Hijri date parsing, validation, and comparison."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

_DIGIT_MAP = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)

_DATE_PATTERNS = [
    re.compile(
        r"(?<!\d)(1[0-9]{3,4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})(?!\d)"
    ),
    re.compile(
        r"(?<!\d)(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(1[0-9]{3,4})(?!\d)"
    ),
]


@dataclass(frozen=True, order=True)
class HijriDate:
    year: int
    month: int
    day: int

    def __post_init__(self) -> None:
        if not (1 <= self.month <= 12):
            raise ValueError(f"invalid month: {self.month}")
        if not (1 <= self.day <= 30):
            raise ValueError(f"invalid day: {self.day}")
        if not (1300 <= self.year <= 1600):
            raise ValueError(f"year out of expected Hijri range: {self.year}")

    def iso(self) -> str:
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"

    def display(self) -> str:
        return f"{self.year:04d}/{self.month:02d}/{self.day:02d}"

    def display_ar(self) -> str:
        return self.display().translate(
            str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
        )


@dataclass
class ExtractedDate:
    normalized: HijriDate
    original_text: str
    page: int
    confidence: float
    verified: bool = False
    source_snippet: str = ""
    person_name: str = ""
    needs_review: bool = False
    review_reason: str = ""
    row_index: Optional[int] = None
    source_image: str = ""
    source_bbox: Optional[dict] = None
    ocr_agreement: int = 1
    row_association_confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "normalized_date": self.normalized.iso(),
            "display": self.normalized.display(),
            "display_ar": self.normalized.display_ar(),
            "original_text": self.original_text,
            "page": self.page,
            "confidence": self.confidence,
            "verified": self.verified,
            "source_snippet": self.source_snippet,
            "person_name": self.person_name,
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
            "row_index": self.row_index,
            "source_image": self.source_image,
            "source_bbox": self.source_bbox,
            "ocr_agreement": self.ocr_agreement,
            "row_association_confidence": self.row_association_confidence,
        }


def to_western_digits(text: str) -> str:
    return text.translate(_DIGIT_MAP)


def _fix_ocr_year(y: int) -> tuple[int, bool, str]:
    """
    Fix obvious OCR year glitches without inventing unrelated years.
    Returns (year, changed, reason).
    """
    # 11447 / 12447 → drop leading spurious 1
    s = str(y)
    if len(s) == 5 and s.startswith("1") and 1300 <= int(s[1:]) <= 1600:
        return int(s[1:]), True, "تصحيح سنة OCR (رقم زائد في البداية)"
    return y, False, ""


def parse_hijri_date(text: str) -> Optional[HijriDate]:
    if not text:
        return None
    t = to_western_digits(text.strip())
    # Strip Hijri markers carefully (do not blanket-delete every Arabic ه in long text)
    t = t.replace("هـ", " ").replace("هـ.", " ")
    t = re.sub(r"(?<=\d)\s*ه(?:ـ)?\b", " ", t)
    t = t.replace("H", " ").strip()

    m = _DATE_PATTERNS[0].search(t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return HijriDate(y, mo, d)
        except ValueError:
            return None

    m = _DATE_PATTERNS[1].search(t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # Ambiguous when both day and month ≤ 12 — refuse rather than guess
        if d <= 12 and mo <= 12:
            return None
        try:
            return HijriDate(y, mo, d)
        except ValueError:
            return None
    return None


def extract_all_dates(
    text: str,
    *,
    page: int = 0,
    confidence: float = 0.9,
    person_name: str = "",
    source_snippet: str = "",
    row_index: Optional[int] = None,
    expected_year_hint: Optional[int] = None,
    source_image: str = "",
    source_bbox: Optional[dict] = None,
    ocr_agreement: int = 1,
    row_association_confidence: float = 1.0,
) -> list[ExtractedDate]:
    if not text:
        return []
    western = to_western_digits(text)
    found: list[ExtractedDate] = []
    seen: set[str] = set()

    for pat_idx, pat in enumerate(_DATE_PATTERNS):
        for m in pat.finditer(western):
            raw = m.group(0)
            if pat_idx == 0:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if d <= 12 and mo <= 12:
                    continue

            y, year_fixed, year_reason = _fix_ocr_year(y)
            needs_review = False
            reason = ""
            conf = min(confidence, row_association_confidence)

            try:
                hd = HijriDate(y, mo, d)
            except ValueError:
                continue

            key = hd.iso()
            if key in seen:
                continue
            seen.add(key)

            if year_fixed:
                conf = min(conf, 0.82)
                needs_review = True
                reason = year_reason

            # Suspicious year vs document hint (e.g. 1417 when doc is 1447)
            if expected_year_hint and abs(y - expected_year_hint) >= 20:
                # Try common 4↔1 confusion in thousands: 1417 vs 1447
                if str(y)[0:2] == str(expected_year_hint)[0:2] or (
                    len(str(y)) == 4 and len(str(expected_year_hint)) == 4
                    and str(y)[2:] == str(expected_year_hint)[2:]
                ):
                    needs_review = True
                    conf = min(conf, 0.7)
                    reason = (
                        reason
                        or f"سنة مشبوهة ({y}) مقارنة بسياق المستند (~{expected_year_hint})"
                    )
                elif abs(y - expected_year_hint) >= 30:
                    needs_review = True
                    conf = min(conf, 0.65)
                    reason = reason or f"سنة بعيدة عن سياق المستند ({y})"

            if row_association_confidence < 0.85:
                needs_review = True
                conf = min(conf, 0.82)
                reason = reason or "ارتباط التاريخ بصف الشخص غير مؤكد هندسيًا"

            if ocr_agreement <= 1 and confidence < 0.95:
                # A single OCR observation is evidence, not automatic proof.
                conf = min(conf, 0.88)

            if conf < 0.85:
                needs_review = True
                reason = reason or "ثقة منخفضة في التعرف على التاريخ"

            found.append(
                ExtractedDate(
                    normalized=hd,
                    original_text=raw,
                    page=page,
                    confidence=conf,
                    verified=not needs_review and conf >= 0.93,
                    source_snippet=source_snippet or text[:200],
                    person_name=person_name,
                    needs_review=needs_review,
                    review_reason=reason,
                    row_index=row_index,
                    source_image=source_image,
                    source_bbox=source_bbox,
                    ocr_agreement=ocr_agreement,
                    row_association_confidence=row_association_confidence,
                )
            )
    return found


def unique_dates_newest_first(
    dates: list[ExtractedDate],
    *,
    only_verified: bool = True,
) -> list[HijriDate]:
    pool = [d for d in dates if (d.verified if only_verified else True)]
    best: dict[str, ExtractedDate] = {}
    for d in pool:
        k = d.normalized.iso()
        if k not in best or d.confidence > best[k].confidence:
            best[k] = d
    unique = [best[k].normalized for k in best]
    unique.sort(reverse=True)
    return unique


def document_year_hint(dates: list[ExtractedDate]) -> Optional[int]:
    """Most common year among extracted dates — for sanity checks."""
    if not dates:
        return None
    years = [d.normalized.year for d in dates]
    return Counter(years).most_common(1)[0][0]


def revalidate_dates_with_hint(
    dates: list[ExtractedDate], hint: int
) -> list[ExtractedDate]:
    """Flag outlier years relative to document mode year."""
    out = []
    for d in dates:
        dd = ExtractedDate(
            normalized=d.normalized,
            original_text=d.original_text,
            page=d.page,
            confidence=d.confidence,
            verified=d.verified,
            source_snippet=d.source_snippet,
            person_name=d.person_name,
            needs_review=d.needs_review,
            review_reason=d.review_reason,
            row_index=d.row_index,
            source_image=d.source_image,
            source_bbox=d.source_bbox,
            ocr_agreement=d.ocr_agreement,
            row_association_confidence=d.row_association_confidence,
        )
        y = dd.normalized.year
        if abs(y - hint) >= 20:
            dd.needs_review = True
            dd.verified = False
            dd.confidence = min(dd.confidence, 0.7)
            if not dd.review_reason:
                dd.review_reason = f"سنة مشبوهة ({y}) — سياق المستند ~{hint}"
        out.append(dd)
    return out

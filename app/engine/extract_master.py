"""Extract person rows and dates from the entire master PDF."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Optional

import pypdfium2 as pdfium

from .dates import (
    document_year_hint,
    extract_all_dates,
    revalidate_dates_with_hint,
)
from .models import MasterPerson, make_master_key
from .normalize import normalize_arabic_name
from .ocr import ocr_image, OcrToken

_RANK_EXACT = {
    "عريف", "جندي", "جندى", "جندي اول", "جندي أول", "جندى اول", "جندى أول",
    "رقيب", "و. رقيب", "و رقيب", "وكيل رقيب", "ملازم", "نقيب", "رائد",
    "مقدم", "عقيد", "عميد", "لواء",
}
_HEADER_RE = re.compile(
    r"(الملاحظات|الاسم|الأسم|الرتبة|سراء|تطبيق|الفرقة|صفحة|الصفحة)"
)
_DATE_RE = re.compile(r"[٠-٩0-9]{3,4}\s*[/\-.\s]\s*[٠-٩0-9]{1,2}")
_SEQ_RE = re.compile(r"^[٠-٩0-9]{1,3}[\-–.]?$")


def render_pdf_pages(pdf_path: Path, scale: float = 3.0) -> list[Path]:
    pdf = pdfium.PdfDocument(str(pdf_path))
    paths: list[Path] = []
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            pil = page.render(scale=scale).to_pil()
            out = Path(tempfile.mkstemp(prefix=f"master_p{i+1}_", suffix=".jpg")[1])
            pil.convert("RGB").save(out, "JPEG", quality=93)
            paths.append(out)
    finally:
        pdf.close()
    return paths


def _clean_name(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^[٠-٩0-9]+\s*[\-–.]\s*", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    for r in sorted(_RANK_EXACT, key=len, reverse=True):
        if t.endswith(" " + r) or t.endswith(r):
            t = t[: -len(r)].strip()
    t = t.replace("مريف", "").replace("هريف", "").strip()
    # Common OCR confusions on scanned Arabic tables (display cleaned)
    replacements = [
        ("مبدائه", "عبدالله"),
        ("مبداله", "عبدالله"),
        ("مبدالله", "عبدالله"),
        ("مبدالعزيز", "عبدالعزيز"),
        ("عبد العزيز", "عبدالعزيز"),
        ("اليصل", "فيصل"),
        ("ذايف", "نايف"),
        ("هجاج", "عجاج"),
        ("المنزي", "العنزي"),
        ("البناهي", "البناقي"),
        ("البنافي", "البناقي"),
        ("الصلير", "الصلبي"),
        ("الصلبى", "الصلبي"),
        ("الحازمى", "الحازمي"),
        ("العازمي", "الحازمي"),  # common OCR miss of leading ح
        ("امجد", "أمجد"),
    ]
    for a, b in replacements:
        t = t.replace(a, b)
    return t.strip()


def _is_rank(text: str) -> bool:
    n = normalize_arabic_name(text)
    if n in _RANK_EXACT or n in {"اول", "أول"}:
        return True
    if re.fullmatch(r"و\.?\s*رقيب", n):
        return True
    return n.startswith("جندي") or n.startswith("جندى")


def _is_name_token(text: str) -> bool:
    t = _clean_name(text)
    n = normalize_arabic_name(t)
    if len(n) < 6:
        return False
    parts = n.split()
    if len(parts) < 2 or len(parts) > 6:
        return False
    if _is_rank(n) or _HEADER_RE.search(n) or _DATE_RE.search(t):
        return False
    if _SEQ_RE.match(t):
        return False
    if sum(c.isdigit() or c in "٠١٢٣٤٥٦٧٨٩" for c in t) >= 4:
        return False
    if not re.search(r"[\u0600-\u06FF]{3,}", n):
        return False
    return True


def _cluster_rows(tokens: list[OcrToken], y_tol: float = 0.014) -> list[list[OcrToken]]:
    if not tokens:
        return []
    ordered = sorted(tokens, key=lambda t: t.cy)
    rows: list[list[OcrToken]] = []
    cur: list[OcrToken] = []
    cy: Optional[float] = None
    for t in ordered:
        if cy is None or abs(t.cy - cy) <= y_tol:
            cur.append(t)
            cy = t.cy if cy is None else (cy * 0.55 + t.cy * 0.45)
        else:
            if cur:
                rows.append(cur)
            cur = [t]
            cy = t.cy
    if cur:
        rows.append(cur)
    return rows


def parse_page_tokens(tokens: list[OcrToken], page_num: int) -> list[dict]:
    rows_out: list[dict] = []

    for idx, row_tokens in enumerate(_cluster_rows(tokens)):
        if not row_tokens:
            continue
        joined = " ".join(t.text for t in row_tokens)
        if _HEADER_RE.search(joined) and not _DATE_RE.search(joined):
            continue

        name_toks: list[OcrToken] = []
        rank = ""
        other_toks: list[OcrToken] = []

        for t in row_tokens:
            txt = t.text.strip()
            if not txt:
                continue
            if _is_rank(txt):
                if not rank:
                    rank = normalize_arabic_name(txt)
                continue
            if _SEQ_RE.match(txt.replace(" ", "")):
                continue
            if _is_name_token(txt) and t.x >= 0.52 and not _DATE_RE.search(txt):
                name_toks.append(t)
            elif t.x >= 0.70 and re.search(r"[\u0600-\u06FF]", txt) and not _DATE_RE.search(txt):
                # rightmost fragments may be name pieces
                if _is_name_token(_clean_name(txt)) or len(txt) >= 4:
                    name_toks.append(t)
                else:
                    other_toks.append(t)
            else:
                other_toks.append(t)

        name_toks_sorted = sorted(name_toks, key=lambda t: -t.x)
        candidate = ""
        name_x_min = 1.0
        for t in name_toks_sorted:
            cleaned = _clean_name(t.text)
            if _is_name_token(cleaned) and 2 <= len(cleaned.split()) <= 5:
                if len(cleaned) > len(candidate):
                    candidate = cleaned
                    name_x_min = min(name_x_min, t.x)

        if not candidate:
            continue

        # Notes = tokens to the LEFT of the name column (lower x), plus any
        # same-row tokens that contain date patterns. Never fall back to the
        # full row join (that can import a neighbor's dates).
        notes_parts: list[str] = []
        for t in sorted(other_toks, key=lambda t: -t.x):
            txt = t.text.strip()
            has_date = bool(_DATE_RE.search(txt))
            left_of_name = t.x < (name_x_min - 0.02) if name_x_min < 1.0 else t.x < 0.62
            # Exclude other person-name-like tokens from notes
            if _is_name_token(_clean_name(txt)) and not has_date:
                continue
            if has_date or left_of_name:
                notes_parts.append(txt)

        notes = " ".join(notes_parts)
        if not notes:
            # last resort: only date-bearing tokens from the row
            notes = " ".join(
                t.text for t in row_tokens if _DATE_RE.search(t.text or "")
            )

        dates = extract_all_dates(notes, page=page_num, person_name=candidate)
        conf = 0.93 if dates else 0.80
        if len(candidate.split()) >= 3:
            conf = min(0.98, conf + 0.03)
        # Too many dates on one row often means row bleed from neighbors
        if len(dates) >= 10:
            conf = min(conf, 0.72)

        rows_out.append(
            {
                "original_name": candidate,
                "rank": rank,
                "notes": notes,
                "page": page_num,
                "row_index": idx,
                "confidence": conf,
            }
        )

    # Prefer best notes per normalized name on page
    best: dict[str, dict] = {}
    for row in rows_out:
        key = make_master_key(row["original_name"])
        dc = len(extract_all_dates(row["notes"], page=page_num))
        prev = best.get(key)
        prev_dc = len(extract_all_dates(prev["notes"], page=page_num)) if prev else -1
        if key not in best or dc > prev_dc:
            best[key] = row
    return list(best.values())


def extract_master_pdf(pdf_path: Path) -> dict[str, MasterPerson]:
    """Process the ENTIRE master PDF; merge all occurrences per person."""
    page_images = render_pdf_pages(pdf_path)
    people: dict[str, MasterPerson] = {}
    all_dates_acc = []
    try:
        for page_idx, img_path in enumerate(page_images):
            page_num = page_idx + 1
            # Dual OCR: preprocessed + raw for better name recall
            tokens = ocr_image(img_path, preprocess=True)
            tokens_raw = ocr_image(img_path, preprocess=False)
            # merge unique tokens by text+approx position
            seen_keys = set()
            merged: list[OcrToken] = []
            for t in tokens + tokens_raw:
                k = (round(t.x, 2), round(t.y, 2), t.text.strip()[:40])
                if k in seen_keys:
                    continue
                seen_keys.add(k)
                merged.append(t)

            rows = parse_page_tokens(merged, page_num)

            for row in rows:
                key = make_master_key(row["original_name"])
                if not key:
                    continue
                if key not in people:
                    people[key] = MasterPerson(
                        original_name=row["original_name"],
                        normalized_name=key,
                    )
                people[key].add_occurrence(
                    page=page_num,
                    notes=row["notes"],
                    rank_title=row.get("rank") or "",
                    row_index=row["row_index"],
                    date_confidence=row["confidence"],
                )

            # Standalone name tokens on the right with nearby notes
            for t in merged:
                if t.x < 0.55 or not _is_name_token(t.text):
                    continue
                name = _clean_name(t.text)
                key = make_master_key(name)
                if key in people:
                    continue
                nearby = [
                    t2.text
                    for t2 in merged
                    if abs(t2.cy - t.cy) <= 0.02
                    and (_DATE_RE.search(t2.text) or t2.x < 0.6)
                ]
                notes = " ".join(nearby)
                people[key] = MasterPerson(original_name=name, normalized_name=key)
                if extract_all_dates(notes, page=page_num):
                    people[key].add_occurrence(
                        page=page_num, notes=notes, date_confidence=0.85
                    )
                else:
                    if page_num not in people[key].pages:
                        people[key].pages.append(page_num)

        # Document-level year sanity
        for p in people.values():
            all_dates_acc.extend(p.dates)
        hint = document_year_hint(all_dates_acc)
        if hint:
            for p in people.values():
                p.dates = revalidate_dates_with_hint(p.dates, hint)

        # Merge near-duplicate OCR identities (soft-normalized key)
        people = _merge_near_duplicate_people(people)
        # Flag persons with suspiciously many unique dates (row bleed)
        for p in people.values():
            uniq = {d.normalized.iso() for d in p.dates}
            if len(uniq) >= 12:
                for d in p.dates:
                    d.needs_review = True
                    d.verified = False
                    if not d.review_reason:
                        d.review_reason = (
                            "عدد تواريخ مرتفع — راجع ارتباط الصف/الملاحظات"
                        )
    finally:
        for p in page_images:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    return people


def _merge_near_duplicate_people(
    people: dict[str, MasterPerson],
) -> dict[str, MasterPerson]:
    """
    Merge records that only differ by OCR noise when soft-normalized forms
    collide, or first+last name soft keys collide with high similarity.
    Prefer the name with more dates/notes.
    """
    from .normalize import name_similarity, soft_normalize_for_fuzzy

    items = list(people.values())
    used = set()
    groups: list[list[MasterPerson]] = []

    for i, p in enumerate(items):
        if i in used:
            continue
        group = [p]
        used.add(i)
        sp = soft_normalize_for_fuzzy(p.original_name)
        tp = sp.split()
        for j, q in enumerate(items):
            if j in used:
                continue
            sq = soft_normalize_for_fuzzy(q.original_name)
            if sp == sq:
                group.append(q)
                used.add(j)
                continue
            tq = sq.split()
            sim = name_similarity(p.original_name, q.original_name)
            # first+last soft match (allow tiny OCR drift on family name)
            if (
                len(tp) >= 2
                and len(tq) >= 2
                and tp[0] == tq[0]
                and sim >= 0.86
            ):
                group.append(q)
                used.add(j)
                continue
            # high overall similarity alone
            if sim >= 0.92:
                group.append(q)
                used.add(j)
        groups.append(group)

    merged: dict[str, MasterPerson] = {}
    for group in groups:
        group.sort(
            key=lambda p: (len(p.dates), len(p.notes_texts), len(p.original_name)),
            reverse=True,
        )
        primary = group[0]
        for other in group[1:]:
            for notes in other.notes_texts:
                if notes not in primary.notes_texts:
                    primary.notes_texts.append(notes)
            for d in other.dates:
                if not any(x.normalized.iso() == d.normalized.iso() for x in primary.dates):
                    primary.dates.append(d)
            for page in other.pages:
                if page not in primary.pages:
                    primary.pages.append(page)
            if other.rank_title and not primary.rank_title:
                primary.rank_title = other.rank_title
        merged[primary.normalized_name] = primary
    return merged

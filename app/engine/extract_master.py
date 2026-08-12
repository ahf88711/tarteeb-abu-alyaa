"""Extract person rows and dates from the entire master PDF."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageOps

from .dates import extract_all_dates
from .models import MasterPerson, make_master_key
from .normalize import normalize_arabic_name
from .ocr import OcrToken, ocr_consensus, save_region_crop

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
            width, height = page.get_size()
            adaptive_scale = max(1.0, min(4.0, 3400.0 / max(width, height)))
            pil = page.render(scale=adaptive_scale).to_pil()
            out = Path(tempfile.mkstemp(prefix=f"master_p{i+1}_", suffix=".png")[1])
            pil.convert("RGB").save(out, "PNG")
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


@dataclass(frozen=True)
class TableGrid:
    """Detected table geometry in normalized top-origin coordinates."""

    row_boundaries: tuple[float, ...]
    vertical_lines: tuple[float, ...]
    name_left: float
    name_right: float
    notes_right: float


def _cluster_axis(
    indices: np.ndarray, size: int, *, merge_distance: float = 0.006
) -> list[float]:
    if indices.size == 0:
        return []
    clusters: list[list[int]] = [[int(indices[0])]]
    for value in indices[1:]:
        number = int(value)
        if number - clusters[-1][-1] <= max(3, round(size * merge_distance)):
            clusters[-1].append(number)
        else:
            clusters.append([number])
    return [float(np.mean(cluster)) / size for cluster in clusters]


def _select_roster_columns(
    vertical_lines: list[float],
) -> Optional[tuple[float, float, float]]:
    """Select notes/rank/name rules without trusting simple rightmost order.

    Arabic roster scans often align the right edge of every printed name. That
    repeated ink can look like a vertical rule in a projection profile. A
    valid layout must instead form plausible sequence, name, and rank column
    widths. Evaluating all right-side rule combinations removes those false
    text-alignment lines while preserving tables that include an attendance
    column to the left of rank.
    """
    if len(vertical_lines) < 5:
        return None
    lines = sorted(vertical_lines)
    left_border = lines[0]
    candidates: list[tuple[float, float, float, float]] = []
    for right_index in range(len(lines) - 1, 3, -1):
        right = lines[right_index]
        if right < 0.90:
            continue
        for name_right_index in range(2, right_index):
            name_right = lines[name_right_index]
            sequence_width = right - name_right
            if not 0.025 <= sequence_width <= 0.115:
                continue
            for name_left_index in range(1, name_right_index):
                name_left = lines[name_left_index]
                name_width = name_right - name_left
                if not 0.10 <= name_width <= 0.32:
                    continue
                for notes_index in range(name_left_index):
                    notes_right = lines[notes_index]
                    rank_width = name_left - notes_right
                    if not 0.025 <= rank_width <= 0.14:
                        continue
                    if notes_right - left_border < 0.30:
                        continue
                    score = (
                        abs(sequence_width - 0.055)
                        + 0.70 * abs(name_width - 0.17)
                        + 0.80 * abs(rank_width - 0.065)
                        + 0.20 * abs(right - lines[-1])
                    )
                    candidates.append((score, notes_right, name_left, name_right))
    if not candidates:
        return None
    _, notes_right, name_left, name_right = min(candidates)
    return notes_right, name_left, name_right


def detect_table_grid(image_path: Path) -> Optional[TableGrid]:
    """Detect table rules using projection profiles; fail closed if uncertain."""
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source.convert("L"))
        if image.width > 1800:
            ratio = 1800 / image.width
            image = image.resize(
                (1800, max(1, round(image.height * ratio))), Image.Resampling.LANCZOS
            )
        array = np.asarray(ImageOps.autocontrast(image, cutoff=1), dtype=np.uint8)
    # A low absolute threshold resists page shadows; autocontrast has already
    # normalized genuinely dark table rules close to black.
    dark = array < 110
    height, width = dark.shape
    central = dark[:, int(width * 0.01) : int(width * 0.99)]
    h_window = max(80, int(central.shape[1] * 0.24))
    h_sum = np.pad(central.astype(np.int32), ((0, 0), (1, 0))).cumsum(axis=1)
    horizontal_score = (
        h_sum[:, h_window:] - h_sum[:, :-h_window]
    ).max(axis=1) / h_window
    horizontal = _cluster_axis(
        np.flatnonzero(horizontal_score >= 0.60),
        height,
        merge_distance=0.004,
    )
    # Keep boundaries in the main document body and remove near-duplicates.
    horizontal = [value for value in horizontal if 0.005 <= value <= 0.995]
    if len(horizontal) < 5:
        return None
    table_top, table_bottom = horizontal[0], horizontal[-1]
    y0, y1 = int(table_top * height), max(int(table_bottom * height), 1)
    table_dark = dark[y0:y1, :]
    v_window = max(80, int(table_dark.shape[0] * 0.24))
    v_sum = np.pad(table_dark.astype(np.int32), ((1, 0), (0, 0))).cumsum(axis=0)
    vertical_score = (
        v_sum[v_window:, :] - v_sum[:-v_window, :]
    ).max(axis=0) / v_window
    vertical = _cluster_axis(
        np.flatnonzero(vertical_score >= 0.60),
        width,
        merge_distance=0.015,
    )
    vertical = [value for value in vertical if 0.002 <= value <= 0.998]
    if len(vertical) < 5:
        return None
    # The common Arabic roster structure ends with rank | name | sequence.
    # Select by plausible column geometry because aligned name text can create
    # a stronger projection than a faint/slanted table rule.
    columns = _select_roster_columns(vertical)
    if columns is None:
        return None
    notes_right, name_left, name_right = columns
    return TableGrid(
        row_boundaries=tuple(horizontal),
        vertical_lines=tuple(vertical),
        name_left=name_left,
        name_right=name_right,
        notes_right=notes_right,
    )


def _cell_text_options(tokens: list[OcrToken]) -> list[tuple[str, float, int]]:
    options: list[tuple[str, float, int]] = []
    for token in tokens:
        cleaned = _clean_name(token.text)
        if _is_name_token(cleaned):
            options.append((cleaned, token.confidence, token.agreement))
    for line in _cluster_rows(tokens, y_tol=0.01):
        ordered = sorted(line, key=lambda token: -token.x)
        combined = _clean_name(" ".join(token.text for token in ordered))
        if _is_name_token(combined):
            options.append(
                (
                    combined,
                    sum(token.confidence for token in ordered) / len(ordered),
                    min(token.agreement for token in ordered),
                )
            )
    return options


def _geometric_rows(
    tokens: list[OcrToken], grid: Optional[TableGrid]
) -> list[tuple[list[OcrToken], Optional[tuple[float, float]], float]]:
    if grid:
        rows: list[tuple[list[OcrToken], Optional[tuple[float, float]], float]] = []
        for top, bottom in zip(grid.row_boundaries, grid.row_boundaries[1:]):
            if bottom - top < 0.004 or bottom - top > 0.22:
                continue
            members = [token for token in tokens if top <= token.cy < bottom]
            if not members:
                continue
            name_centers: list[float] = []
            for token in sorted(
                (
                    item
                    for item in members
                    if grid.name_left <= item.cx <= grid.name_right
                    and re.search(r"[\u0600-\u06FF]", item.text)
                ),
                key=lambda item: item.cy,
            ):
                if not name_centers or abs(token.cy - name_centers[-1]) > 0.014:
                    name_centers.append(token.cy)
                else:
                    name_centers[-1] = (name_centers[-1] + token.cy) / 2
            if len(name_centers) <= 1:
                rows.append((members, (top, bottom), 0.98))
                continue
            # A missed/faint horizontal rule left multiple name baselines in
            # one geometric span. Split at their midpoints and lower the row
            # association confidence so dates still require corroboration.
            cuts = [top] + [
                (left + right) / 2
                for left, right in zip(name_centers, name_centers[1:])
            ] + [bottom]
            for sub_top, sub_bottom in zip(cuts, cuts[1:]):
                subset = [
                    token for token in members if sub_top <= token.cy < sub_bottom
                ]
                if subset:
                    rows.append((subset, (sub_top, sub_bottom), 0.86))
        if rows:
            return rows
    return [(row, None, 0.68) for row in _cluster_rows(tokens)]


def parse_page_tokens(
    tokens: list[OcrToken],
    page_num: int,
    *,
    grid: Optional[TableGrid] = None,
    image_path: Optional[Path] = None,
) -> list[dict]:
    rows_out: list[dict] = []
    for idx, (row_tokens, span, association_confidence) in enumerate(
        _geometric_rows(tokens, grid)
    ):
        joined = " ".join(token.text for token in row_tokens)
        if _HEADER_RE.search(joined) and not _DATE_RE.search(joined):
            continue

        if grid:
            name_tokens = [
                token
                for token in row_tokens
                if grid.name_left <= token.cx <= grid.name_right
                and not _DATE_RE.search(token.text)
            ]
            rank_tokens = [
                token
                for token in row_tokens
                if grid.notes_right <= token.cx < grid.name_left
            ]
            notes_tokens = [
                token for token in row_tokens if token.cx < grid.notes_right
            ]
        else:
            name_tokens = [
                token
                for token in row_tokens
                if token.cx >= 0.64 and not _DATE_RE.search(token.text)
            ]
            rank_tokens = [token for token in row_tokens if _is_rank(token.text)]
            # Fallback association is intentionally low-confidence.
            name_edge = min((token.x for token in name_tokens), default=0.64)
            notes_tokens = [token for token in row_tokens if token.x < name_edge - 0.02]

        options = _cell_text_options(name_tokens)
        if not options:
            continue
        candidate, name_confidence, name_agreement = max(
            options,
            key=lambda item: (
                item[2],
                item[1],
                2 <= len(item[0].split()) <= 5,
                len(item[0]),
            ),
        )
        rank = next(
            (normalize_arabic_name(token.text) for token in rank_tokens if _is_rank(token.text)),
            "",
        )
        notes_tokens = sorted(notes_tokens, key=lambda token: (token.cy, -token.x))
        notes = " ".join(token.text.strip() for token in notes_tokens if token.text.strip())
        date_tokens = [token for token in notes_tokens if _DATE_RE.search(token.text)]
        if date_tokens:
            token_confidence = sum(token.confidence for token in date_tokens) / len(date_tokens)
            date_agreement = min(token.agreement for token in date_tokens)
        else:
            token_confidence = 0.0
            date_agreement = 0
        confidence = min(association_confidence, token_confidence or name_confidence)
        if date_agreement >= 2 and association_confidence >= 0.95:
            confidence = min(0.99, confidence + 0.04)

        source_bbox = None
        source_image = ""
        if span and image_path:
            top, bottom = span
            source_bbox = {"x": 0.0, "top": top, "w": 1.0, "h": bottom - top}
            source_image = save_region_crop(
                image_path,
                x=0.0,
                top=top,
                w=1.0,
                h=bottom - top,
                prefix=f"master_p{page_num}_r{idx}",
                padding=0.002,
            )
        rows_out.append(
            {
                "original_name": candidate,
                "rank": rank,
                "notes": notes,
                "page": page_num,
                "row_index": idx,
                "confidence": confidence,
                "name_confidence": name_confidence,
                "ocr_agreement": min(name_agreement, date_agreement) if date_tokens else name_agreement,
                "row_association_confidence": association_confidence,
                "source_bbox": source_bbox,
                "source_image": source_image,
            }
        )
    return rows_out


def extract_master_pdf(pdf_path: Path) -> dict[str, MasterPerson]:
    """Process the ENTIRE master PDF; merge all occurrences per person."""
    page_images = render_pdf_pages(pdf_path)
    people: dict[str, MasterPerson] = {}
    try:
        for page_idx, img_path in enumerate(page_images):
            page_num = page_idx + 1
            # Three independent image passes are fused with disagreement kept
            # as evidence. A page failure aborts the run instead of silently
            # pretending that the entire PDF was searched.
            tokens = ocr_consensus(img_path)
            grid = detect_table_grid(img_path)
            rows = parse_page_tokens(
                tokens,
                page_num,
                grid=grid,
                image_path=img_path,
            )

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
                    source_image=row.get("source_image") or "",
                    source_bbox=row.get("source_bbox"),
                    name_confidence=row.get("name_confidence") or 0.0,
                    ocr_agreement=row.get("ocr_agreement") or 1,
                    row_association_confidence=row.get("row_association_confidence") or 0.0,
                )

        # Similar readings are flagged as aliases, never auto-merged.
        people = _merge_near_duplicate_people(people)
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
    Preserve every distinct conservative identity key.

    Near-duplicate OCR readings are only cross-referenced for review. Merging
    them automatically can combine two real people and is more dangerous than
    leaving a duplicate candidate visible.
    """
    from .normalize import name_similarity

    items = list(people.values())
    for index, person in enumerate(items):
        for other in items[index + 1 :]:
            similarity = name_similarity(person.original_name, other.original_name)
            if similarity < 0.88:
                continue
            person.identity_needs_review = True
            other.identity_needs_review = True
            if other.original_name not in person.aliases:
                person.aliases.append(other.original_name)
            if person.original_name not in other.aliases:
                other.aliases.append(person.original_name)
    return people

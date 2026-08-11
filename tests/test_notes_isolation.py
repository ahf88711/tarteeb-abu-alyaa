"""Notes/name column isolation — dates must not bleed from neighboring names."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine.extract_master import parse_page_tokens
from app.engine.ocr import OcrToken
from app.engine.dates import extract_all_dates


def _tok(text: str, x: float, y: float, w: float = 0.1, h: float = 0.02) -> OcrToken:
    # Vision coords: y from bottom; cy = 1 - (y + h/2)
    return OcrToken(text=text, x=x, y=y, w=w, h=h)


def test_neighbor_dates_not_imported_into_name_only_side():
    """
    Simulate one table row: notes (left, with dates) + name (right).
    A second name token on the far right must not pull the first person's notes
    into a wrong person if clustered separately — here same row with two names
    should only keep best candidate with its left-side notes.
    """
    # Same vertical band (y≈0.5)
    y = 0.50
    tokens = [
        _tok("تطبيق من 1447/08/10 و 1447/07/01", x=0.10, y=y, w=0.40),
        _tok("عريف", x=0.55, y=y, w=0.05),
        _tok("وليد وادي العنزي", x=0.75, y=y, w=0.18),
    ]
    rows = parse_page_tokens(tokens, page_num=1)
    assert len(rows) >= 1
    # The extracted person should be وليد and include the two dates
    row = next(r for r in rows if "وليد" in r["original_name"] or "وادي" in r["original_name"])
    dates = extract_all_dates(row["notes"], page=1)
    isos = {d.normalized.iso() for d in dates}
    assert "1447-08-10" in isos
    assert "1447-07-01" in isos


def test_full_row_join_not_used_when_no_notes():
    """If there are no left/date tokens, notes should not invent from empty."""
    y = 0.40
    tokens = [
        _tok("محمد فرحان العنزي", x=0.80, y=y, w=0.15),
        _tok("عريف", x=0.60, y=y, w=0.05),
    ]
    rows = parse_page_tokens(tokens, page_num=2)
    # May or may not extract depending on name heuristics; if extracted, notes empty/no dates
    for r in rows:
        if "فرحان" in r["original_name"]:
            assert extract_all_dates(r["notes"], page=2) == [] or r["notes"] == ""

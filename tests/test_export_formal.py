"""Export formal PDF / master index tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine.export import export_master_index, export_pdf_formal, export_pdf_simple


def test_formal_pdf_bytes():
    results = [
        {
            "rank": 1,
            "original_name": "وليد وادي العنزي",
            "latest_date": "1447/07/16",
            "previous_date": "1447/07/01",
            "date_count": 3,
            "status": "مرتّب",
            "explanation": "اختبار",
        },
        {
            "rank": 2,
            "original_name": "أمجد النشمي الصلبي",
            "latest_date": "1447/08/04",
            "previous_date": "1447/07/23",
            "date_count": 2,
            "status": "مرتّب",
            "explanation": "اختبار",
        },
    ]
    summary = {"ranked_successfully": 2, "tied": 0, "unresolved": 0}
    data = export_pdf_formal(results, summary, unit_title="اختبار الوحدة")
    assert data[:4] == b"%PDF"
    assert len(data) > 500


def test_simple_pdf_bytes():
    data = export_pdf_simple(
        [{"rank": 1, "original_name": "أ", "status": "مرتّب", "date_count": 1, "explanation": "x"}],
        {"ranked_successfully": 1},
    )
    assert data[:4] == b"%PDF"


def test_master_index_excel():
    data = export_master_index(
        [
            {
                "name": "وليد وادي العنزي",
                "rank_title": "عريف",
                "pages": [1, 2],
                "dates": ["1447/07/16", "1447/06/20"],
            }
        ]
    )
    assert data[:2] == b"PK"  # zip/xlsx

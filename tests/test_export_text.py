"""Text / audit export tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine.export import export_audit_json, export_ranking_text


def test_ranking_text_contains_names():
    results = [
        {
            "rank": 1,
            "original_name": "منيف جمعة البناقي",
            "latest_date": "1447/07/12",
            "date_count": 5,
            "status": "مرتّب",
        },
        {
            "rank": 2,
            "original_name": "وليد وادي العنزي",
            "latest_date": "1447/07/16",
            "date_count": 4,
            "status": "مرتّب",
        },
    ]
    text = export_ranking_text(results, {"ranked_successfully": 2, "tied": 0, "unresolved": 0})
    assert "ترتيب أبو علياء" in text
    assert "منيف جمعة البناقي" in text
    assert "وليد وادي العنزي" in text


def test_audit_json_roundtrip():
    raw = export_audit_json(
        [{"rank": 1, "original_name": "أ"}],
        {"ranked_successfully": 1},
        ["رسالة"],
    )
    data = json.loads(raw.decode("utf-8"))
    assert data["app"] == "ترتيب أبو علياء"
    assert data["rule"]["no_invented_tiebreakers"] is True
    assert data["results"][0]["original_name"] == "أ"

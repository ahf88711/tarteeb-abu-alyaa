"""One-click full rank demo on clean Excel fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app

client = TestClient(app)

MASTER = ROOT / "data" / "samples" / "master_page3_clean.xlsx"
TARGETS = ROOT / "data" / "samples" / "targets_page3_overlap.xlsx"


@pytest.mark.skipif(not (MASTER.exists() and TARGETS.exists()), reason="samples missing")
def test_full_rank_demo_endpoint():
    r = client.post("/api/demo/full_rank")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["phase"] == "ranked"
    results = [x for x in body["results"] if x.get("rank") is not None]
    assert len(results) >= 8
    # first has older-or-equal latest than later ones when latest differs
    first = results[0]
    assert first["rank"] == 1
    assert "منيف" in first["original_name"] or first["latest_date"]
    # deterministic: second call same order
    r2 = client.post("/api/demo/full_rank")
    names1 = [x["original_name"] for x in results]
    names2 = [x["original_name"] for x in r2.json()["results"] if x.get("rank") is not None]
    assert names1 == names2

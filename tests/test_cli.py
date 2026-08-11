"""CLI smoke tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.cli import main

MASTER = ROOT / "data" / "samples" / "master_page3_clean.xlsx"
TARGETS = ROOT / "data" / "samples" / "targets_page3_overlap.xlsx"


@pytest.mark.skipif(not (MASTER.exists() and TARGETS.exists()), reason="samples missing")
def test_cli_demo_exit_zero():
    assert main(["demo"]) == 0


@pytest.mark.skipif(not (MASTER.exists() and TARGETS.exists()), reason="samples missing")
def test_cli_rank_json(tmp_path: Path):
    out = tmp_path / "out.json"
    code = main(
        [
            "rank",
            "--master",
            str(MASTER),
            "--targets",
            str(TARGETS),
            "--json-out",
            str(out),
        ]
    )
    assert code == 0
    assert out.exists()
    import json

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["summary"]["ranked_successfully"] >= 8

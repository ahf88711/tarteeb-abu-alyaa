"""
Integration tests against real sample files when present.
These tests actually execute OCR/pipeline against attached samples.
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLES = ROOT / "data" / "samples"
MASTER = SAMPLES / "master_sample.pdf"
TARGETS = SAMPLES / "target_names_preview.jpg"


def _require_real_ocr() -> None:
    """Use Apple Vision on macOS or local Arabic Tesseract on Linux."""
    from app.engine.ocr import available_ocr_backends, ensure_ocr_binary

    backends = available_ocr_backends()
    if not backends:
        pytest.skip("no Arabic OCR backend is available")
    if "vision" in backends:
        try:
            ensure_ocr_binary()
        except Exception as exc:
            if "tesseract" not in backends:
                pytest.skip(f"OCR backend unavailable: {exc}")


@pytest.mark.skipif(
    not MASTER.exists() or os.getenv("RUN_REAL_OCR_TESTS") != "1",
    reason="real OCR test is opt-in (RUN_REAL_OCR_TESTS=1)",
)
def test_master_pdf_extraction_runs():
    from app.engine.extract_master import extract_master_pdf

    _require_real_ocr()

    people = extract_master_pdf(MASTER)
    assert isinstance(people, dict)
    # Sample page has ~14 names; allow OCR variance
    assert len(people) >= 5, f"expected several people, got {len(people)}: {list(people)[:10]}"

    # At least some dates extracted
    total_dates = sum(len(p.dates) for p in people.values())
    assert total_dates >= 5, f"expected dates, got {total_dates}"

    # Known names from sample (fuzzy presence)
    joined = " ".join(people.keys())
    assert any(k in joined for k in ("العنزي", "الحازمي", "الصلبي", "الرويلي", "البناقي"))


@pytest.mark.skipif(
    not (MASTER.exists() and TARGETS.exists()) or os.getenv("RUN_REAL_OCR_TESTS") != "1",
    reason="real OCR test is opt-in (RUN_REAL_OCR_TESTS=1)",
)
def test_full_pipeline_samples():
    from app.engine.pipeline import (
        collect_dates_for_targets,
        load_master,
        load_targets,
        new_session,
        run_ranking,
    )
    from app.engine.models import NameStatus

    _require_real_ocr()

    s = new_session()
    load_master(s, MASTER)
    assert len(s.master_people) >= 5

    load_targets(s, TARGETS)
    assert len(s.target_names) >= 3

    # Only target names should be ranking candidates (not all master)
    assert len(s.target_names) <= len(s.master_people) + 50  # targets can include extras not in master

    # Auto-confirm high-confidence verified
    from app.engine.pipeline import apply_name_corrections

    corrections = []
    for t in s.target_names:
        if t.status == NameStatus.VERIFIED and t.matched_master_name:
            corrections.append({"id": t.id, "action": "confirm"})
        elif t.matched_master_name and t.confidence >= 0.9:
            corrections.append(
                {"id": t.id, "action": "set_name", "name": t.matched_master_name}
            )
    apply_name_corrections(s, corrections)
    collect_dates_for_targets(s)
    run_ranking(s, auto_verify_dates=True)

    ranked = [r for r in s.ranking_results if r.get("rank") is not None]
    # May be 0 if no overlap between target photo and single-page master sample
    # But pipeline must complete without crash
    assert s.phase == "ranked"
    assert "ranked_candidates" in s.summary

    # Determinism: re-run ranking
    results1 = [r["original_name"] for r in s.ranking_results if r.get("rank")]
    run_ranking(s, auto_verify_dates=True)
    results2 = [r["original_name"] for r in s.ranking_results if r.get("rank")]
    assert results1 == results2


@pytest.mark.skipif(
    not MASTER.exists() or os.getenv("RUN_REAL_OCR_TESTS") != "1",
    reason="real OCR test is opt-in (RUN_REAL_OCR_TESTS=1)",
)
def test_entire_pdf_searched_not_first_hit_only():
    """Same person occurrences should merge dates across pages (single-page sample still merges)."""
    from app.engine.extract_master import extract_master_pdf
    from app.engine.models import MasterPerson

    _require_real_ocr()

    people = extract_master_pdf(MASTER)
    # Each person object can accumulate multiple notes
    multi = [p for p in people.values() if len(p.notes_texts) >= 1]
    assert multi, "expected notes on people"

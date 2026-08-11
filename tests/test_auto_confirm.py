"""Cautious auto-confirm of high-confidence names."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine.models import MasterPerson, NameStatus, TargetName, make_master_key
from app.engine.pipeline import auto_confirm_high_confidence, new_session


def test_auto_confirm_exact_and_skips_ambiguous():
    s = new_session()
    key = make_master_key("وليد وادي العنزي")
    s.master_people = {
        key: MasterPerson(original_name="وليد وادي العنزي", normalized_name=key)
    }
    s.target_names = [
        TargetName(
            id="a1",
            original_name="وليد وادي العنزي",
            normalized_name=key,
            ocr_raw="وليد وادي العنزي",
            confidence=0.99,
            status=NameStatus.VERIFIED,
            matched_master_name="وليد وادي العنزي",
            candidates=[{"name": "وليد وادي العنزي", "confidence": 0.99}],
        ),
        TargetName(
            id="a2",
            original_name="محمد سعد",
            normalized_name=make_master_key("محمد سعد"),
            ocr_raw="محمد سعد",
            confidence=0.88,
            status=NameStatus.AMBIGUOUS,
            matched_master_name=None,
            candidates=[
                {"name": "محمد سعد الحارثي", "confidence": 0.86},
                {"name": "محمد سعيد الحارثي", "confidence": 0.85},
            ],
        ),
        TargetName(
            id="a3",
            original_name="شخص غريب",
            normalized_name=make_master_key("شخص غريب"),
            ocr_raw="شخص غريب",
            confidence=0.2,
            status=NameStatus.NOT_IN_MASTER,
        ),
    ]
    auto_confirm_high_confidence(s, min_confidence=0.92)
    by_id = {t.id: t for t in s.target_names}
    assert by_id["a1"].status == NameStatus.VERIFIED
    assert by_id["a2"].status == NameStatus.AMBIGUOUS  # never auto
    assert by_id["a3"].status == NameStatus.NOT_IN_MASTER

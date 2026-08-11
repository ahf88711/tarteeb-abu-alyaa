"""Name matching and ambiguity safety tests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.extract_targets import match_to_master
from app.engine.models import MasterPerson, NameStatus, make_master_key


def _master(*names: str) -> dict[str, MasterPerson]:
    out = {}
    for n in names:
        key = make_master_key(n)
        out[key] = MasterPerson(original_name=n, normalized_name=key)
    return out


def test_exact_match_verified():
    m = _master("يوسف سعيد العنزي", "محمد سلامة الحازمي")
    st, conf, cands, matched = match_to_master("يوسف سعيد العنزي", m)
    assert st == NameStatus.VERIFIED
    assert matched == "يوسف سعيد العنزي"
    assert conf >= 0.92


def test_soft_teh_marbuta_can_match():
    m = _master("محمد سلامة الحازمي")
    st, conf, cands, matched = match_to_master("محمد سلامه الحازمي", m)
    assert conf >= 0.9
    assert matched == "محمد سلامة الحازمي" or st in (
        NameStatus.VERIFIED,
        NameStatus.NEEDS_REVIEW,
    )


def test_ambiguous_two_similar_names_no_guess():
    """FALSE MATCH worse than NO MATCH."""
    m = _master("محمد سعد الحارثي", "محمد سعيد الحارثي")
    st, conf, cands, matched = match_to_master("محمد سعد الحارثي", m)
    # Exact should verify the exact one
    assert st == NameStatus.VERIFIED
    assert matched == "محمد سعد الحارثي"

    # Ambiguous OCR between both
    st2, conf2, cands2, matched2 = match_to_master("محمد سع؟ الحارثي", m)
    # If not clear, should not force — either not found, review, or ambiguous
    if conf2 < 0.92:
        assert matched2 is None or st2 in (
            NameStatus.AMBIGUOUS,
            NameStatus.NEEDS_REVIEW,
            NameStatus.NOT_IN_MASTER,
        )


def test_unknown_name_not_forced():
    m = _master("وليد وادي العنزي")
    st, conf, cands, matched = match_to_master("شخص غير موجود بالمرة", m)
    assert st == NameStatus.NOT_IN_MASTER
    assert matched is None

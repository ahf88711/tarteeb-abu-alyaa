"""Ranking against clean digitized master data (no OCR)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine.dates import HijriDate, parse_hijri_date
from app.engine.normalize import normalize_arabic_name
from app.engine.ranking import RankPerson, rank_people

FIXTURE = ROOT / "tests" / "fixtures" / "page3_clean_people.json"

# Names that appear both in page-3 master and typical target lists
OVERLAP = [
    "وليد وادي العنزي",
    "أمجد النشمي الصلبي",
    "نايف أحمد الحازمي",
    "منيف جمعة البناقي",
]


def _load_people():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    people = []
    for row in data:
        dates = []
        for d in row["dates"]:
            hd = parse_hijri_date(d)
            assert hd is not None, d
            dates.append(hd)
        dates = sorted(set(dates), reverse=True)
        people.append(
            RankPerson(
                id=row["name"],
                original_name=row["name"],
                normalized_name=normalize_arabic_name(row["name"]),
                dates=dates,
            )
        )
    return people


def test_fixture_all_14_have_dates():
    people = _load_people()
    assert len(people) == 14
    assert all(p.dates for p in people)


def test_rank_overlap_subset_deterministic():
    all_people = {p.original_name: p for p in _load_people()}
    subset = [all_people[n] for n in OVERLAP]
    r1 = [e.person.original_name for e in rank_people(subset)]
    r2 = [e.person.original_name for e in rank_people(list(reversed(subset)))]
    assert r1 == r2
    # All should be ranked (have dates)
    assert len(r1) == 4
    # Older latest-date first among these
    latest = {p.original_name: p.dates[0] for p in subset}
    # Verify order respects older-latest rule for first position
    first = r1[0]
    for other in r1[1:]:
        # either first has older or equal latest, and if equal deeper rules apply
        if latest[first] != latest[other]:
            assert latest[first] < latest[other]


def test_full_page_ranking_reproducible():
    people = _load_people()
    a = [e.person.original_name for e in rank_people(people)]
    b = [e.person.original_name for e in rank_people(list(reversed(people)))]
    assert a == b
    assert len(a) == 14
    # مازن has fewest/oldest-ish latest among some — just ensure no crash and full coverage
    assert set(a) == {p.original_name for p in people}


def test_walid_before_amjad_by_latest():
    """Real rule check: وليد latest 07/16 older than أمجد 08/03."""
    all_people = {p.original_name: p for p in _load_people()}
    subset = [all_people["وليد وادي العنزي"], all_people["أمجد النشمي الصلبي"]]
    res = rank_people(subset)
    assert res[0].person.original_name == "وليد وادي العنزي"
    assert res[1].person.original_name == "أمجد النشمي الصلبي"

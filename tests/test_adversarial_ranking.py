"""Adversarial / edge-case tests for deterministic ranking (spec compliance)."""

from __future__ import annotations

import itertools
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine.dates import HijriDate, extract_all_dates, parse_hijri_date, unique_dates_newest_first
from app.engine.dates import ExtractedDate
from app.engine.normalize import normalize_arabic_name
from app.engine.ranking import RankPerson, RankStatus, compare_two, rank_people


def P(name: str, dates: list[tuple[int, int, int]]) -> RankPerson:
    seq = [HijriDate(*t) for t in dates]
    return RankPerson(
        id=name,
        original_name=name,
        normalized_name=normalize_arabic_name(name),
        dates=seq,  # intentionally may be unsorted; engine must sanitize
    )


class TestAdversarialRanking:
    def test_duplicate_dates_not_extra_levels(self):
        a = RankPerson(
            id="a",
            original_name="أ",
            normalized_name="ا",
            dates=[
                HijriDate(1447, 8, 10),
                HijriDate(1447, 8, 10),  # duplicate
                HijriDate(1447, 7, 1),
            ],
        )
        b = P("ب", [(1447, 8, 10), (1447, 7, 2)])
        res = rank_people([a, b])
        # After dedupe a: [08/10, 07/01], b: [08/10, 07/02] → a older second → a first
        assert res[0].person.original_name == "أ"
        assert res[1].person.original_name == "ب"
        assert len(res[0].person.dates) == 2

    def test_unsorted_input_dates_sanitized(self):
        # oldest first input — must still rank correctly
        a = RankPerson(
            id="a",
            original_name="أ",
            normalized_name="ا",
            dates=[HijriDate(1447, 5, 1), HijriDate(1447, 8, 1)],  # oldest first
        )
        b = P("ب", [(1447, 8, 15)])
        res = rank_people([a, b])
        # a latest 08/01 older than b 08/15 → a first
        assert res[0].person.original_name == "أ"
        assert res[0].person.dates[0] == HijriDate(1447, 8, 1)

    def test_unequal_length_after_equal_prefix_unresolved(self):
        a = P("A", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 5)])
        b = P("B", [(1447, 8, 10), (1447, 7, 20)])
        res = rank_people([a, b])
        assert res[0].rank == res[1].rank
        assert res[0].status == RankStatus.UNRESOLVED
        assert res[1].status == RankStatus.UNRESOLVED

    def test_three_person_unequal_length_all_unresolved(self):
        a = P("A", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 5)])
        b = P("B", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 4)])
        c = P("C", [(1447, 8, 10), (1447, 7, 20)])
        res = rank_people([a, b, c])
        assert all(e.status == RankStatus.UNRESOLVED for e in res)
        assert len({e.rank for e in res}) == 1

    def test_n_way_tie_then_nested_split(self):
        # 8 people same L1; then two date groups at L2; nested L3
        people = []
        for i in range(8):
            l2 = 10 if i < 5 else 20  # older 10 first
            l3 = i  # unique
            people.append(P(f"P{i}", [(1447, 8, 10), (1447, 7, l2), (1447, 6, l3 + 1)]))
        res = rank_people(people)
        names = [e.person.original_name for e in res]
        # First five (l2=10) before last three (l2=20)
        first5 = names[:5]
        last3 = names[5:]
        assert set(first5) == {f"P{i}" for i in range(5)}
        assert set(last3) == {f"P{i}" for i in range(5, 8)}
        # Within first5, ordered by older l3 (smaller day first)
        assert first5 == ["P0", "P1", "P2", "P3", "P4"]

    def test_depth_twelve_no_max(self):
        base = [(1447, 12 - i, 1) for i in range(11)]
        a = P("A", base + [(1446, 1, 1)])
        b = P("B", base + [(1446, 1, 2)])
        res = rank_people([a, b])
        assert res[0].person.original_name == "A"
        assert res[1].person.original_name == "B"

    def test_full_tie_five_people(self):
        dates = [(1447, 8, 10), (1447, 7, 5), (1447, 6, 1)]
        people = [P(f"T{i}", dates) for i in range(5)]
        res = rank_people(people)
        assert all(e.status == RankStatus.TIE and e.rank == 1 for e in res)

    def test_no_hidden_alpha_breaker_on_full_tie_ranks(self):
        dates = [(1447, 3, 3)]
        people = [P("يحيى", dates), P("أحمد", dates), P("بدر", dates)]
        res = rank_people(people)
        assert all(e.rank == 1 and e.status == RankStatus.TIE for e in res)

    def test_shuffle_preserves_ranks(self):
        base = [
            P("A", [(1447, 8, 15), (1447, 1, 1)]),
            P("B", [(1447, 8, 10), (1447, 7, 1)]),
            P("C", [(1447, 8, 10), (1447, 6, 5)]),
            P("D", [(1447, 9, 1)]),
            P("E", [(1447, 8, 10), (1447, 7, 1), (1447, 5, 1)]),
        ]
        ranks1 = {(e.person.original_name, e.rank, e.status) for e in rank_people(base)}
        for seed in range(30):
            sh = base[:]
            random.Random(seed).shuffle(sh)
            ranks2 = {(e.person.original_name, e.rank, e.status) for e in rank_people(sh)}
            assert ranks1 == ranks2

    def test_shuffle_preserves_full_order_when_total(self):
        """When all ranks unique, full order must be identical after sanitize sort."""
        base = [
            P("A", [(1447, 8, 1)]),
            P("B", [(1447, 8, 2)]),
            P("C", [(1447, 8, 3)]),
            P("D", [(1447, 8, 4)]),
        ]
        order1 = [e.person.original_name for e in rank_people(base)]
        for seed in range(20):
            sh = base[:]
            random.Random(seed).shuffle(sh)
            order2 = [e.person.original_name for e in rank_people(sh)]
            assert order1 == order2 == ["A", "B", "C", "D"]

    def test_pairwise_consistent_with_order(self):
        people = [
            P("A", [(1447, 8, 10), (1447, 7, 5)]),
            P("B", [(1447, 8, 12), (1447, 6, 1)]),
            P("C", [(1447, 8, 11), (1447, 7, 9)]),
            P("D", [(1447, 8, 10), (1447, 7, 4)]),
        ]
        res = rank_people(people)
        by_name = {p.original_name: p for p in people}
        idx = {e.person.original_name: i for i, e in enumerate(res)}
        rank_of = {e.person.original_name: e.rank for e in res}
        for a, b in itertools.combinations(by_name.keys(), 2):
            c = compare_two(by_name[a], by_name[b])
            if c["result"] == "a":
                assert rank_of[a] <= rank_of[b]
                if rank_of[a] != rank_of[b]:
                    assert idx[a] < idx[b]
            elif c["result"] == "b":
                assert rank_of[b] <= rank_of[a]
                if rank_of[a] != rank_of[b]:
                    assert idx[b] < idx[a]
            else:
                assert rank_of[a] == rank_of[b]

    def test_empty_and_single(self):
        assert rank_people([]) == []
        r = rank_people([P("Only", [(1447, 1, 1)])])
        assert len(r) == 1 and r[0].rank == 1 and r[0].status == RankStatus.RANKED

    def test_no_dates_trailing(self):
        a = P("A", [(1447, 8, 1)])
        b = RankPerson(id="b", original_name="B", normalized_name="b", dates=[])
        res = rank_people([b, a])
        assert res[0].person.original_name == "A"
        assert res[1].status == RankStatus.NO_DATES

    def test_spec_section_21(self):
        a = P("A", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 5)])
        b = P("B", [(1447, 8, 10), (1447, 7, 18), (1447, 6, 9)])
        c = P("C", [(1447, 8, 10), (1447, 7, 18), (1447, 6, 7)])
        d = P("D", [(1447, 8, 10), (1447, 7, 25), (1447, 6, 1)])
        res = rank_people([a, b, c, d])
        assert [e.person.original_name for e in res] == ["C", "B", "A", "D"]

    def test_spec_section_23_deep(self):
        a = P("A", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 15), (1447, 5, 5), (1447, 4, 1)])
        b = P("B", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 15), (1447, 5, 5), (1447, 4, 3)])
        c = P("C", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 15), (1447, 5, 7), (1447, 4, 1)])
        res = rank_people([a, b, c])
        assert [e.person.original_name for e in res] == ["A", "B", "C"]


class TestDateParsingIntegrity:
    def test_arabic_and_western(self):
        assert parse_hijri_date("١٤٤٧/٠٨/١٥") == HijriDate(1447, 8, 15)
        assert parse_hijri_date("1447-8-15") == HijriDate(1447, 8, 15)

    def test_hijri_marker(self):
        assert parse_hijri_date("1447/08/15هـ") == HijriDate(1447, 8, 15)

    def test_ambiguous_day_first_refused(self):
        # 05/06/1447 could be D/M or M/D — must not guess
        assert parse_hijri_date("05/06/1447") is None

    def test_unambiguous_day_first(self):
        assert parse_hijri_date("15/08/1447") == HijriDate(1447, 8, 15)

    def test_ocr_five_digit_year(self):
        assert parse_hijri_date("11447/08/15") == HijriDate(1447, 8, 15)

    def test_extract_dedupes(self):
        text = "من 1447/08/10 ومرة أخرى 1447/08/10 ثم 1447/07/01"
        ds = extract_all_dates(text)
        isos = [d.normalized.iso() for d in ds]
        assert isos.count("1447-08-10") == 1
        assert "1447-07-01" in isos

    def test_unique_dates_newest_first_ignores_unverified(self):
        dates = [
            ExtractedDate(
                normalized=HijriDate(1447, 8, 1),
                original_text="x",
                page=1,
                confidence=0.9,
                verified=True,
            ),
            ExtractedDate(
                normalized=HijriDate(1447, 9, 1),
                original_text="y",
                page=1,
                confidence=0.5,
                verified=False,
                needs_review=True,
            ),
        ]
        uniq = unique_dates_newest_first(dates, only_verified=True)
        assert uniq == [HijriDate(1447, 8, 1)]


class TestMatchingSafeguards:
    def test_ambiguous_close_scores(self):
        from app.engine.extract_targets import match_to_master
        from app.engine.models import MasterPerson, NameStatus, make_master_key

        m = {
            make_master_key("محمد سعد الحارثي"): MasterPerson(
                original_name="محمد سعد الحارثي",
                normalized_name=make_master_key("محمد سعد الحارثي"),
            ),
            make_master_key("محمد سعيد الحارثي"): MasterPerson(
                original_name="محمد سعيد الحارثي",
                normalized_name=make_master_key("محمد سعيد الحارثي"),
            ),
        }
        # OCR that is close to both should be ambiguous or careful
        st, conf, cands, matched = match_to_master("محمد سع الحارثي", m)
        if st == NameStatus.AMBIGUOUS:
            assert matched is None
            assert len(cands) >= 2
        else:
            # If not ambiguous, must not be auto-verified without clear gap
            assert st != NameStatus.VERIFIED or matched in (
                "محمد سعد الحارثي",
                "محمد سعيد الحارثي",
            )

    def test_exact_match_verified(self):
        from app.engine.extract_targets import match_to_master
        from app.engine.models import MasterPerson, NameStatus, make_master_key

        name = "وليد وادي العنزي"
        m = {
            make_master_key(name): MasterPerson(
                original_name=name, normalized_name=make_master_key(name)
            )
        }
        st, conf, cands, matched = match_to_master(name, m)
        assert st == NameStatus.VERIFIED
        assert matched == name

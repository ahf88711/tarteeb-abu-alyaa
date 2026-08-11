"""Mandatory deterministic ranking engine tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine.dates import HijriDate, extract_all_dates, parse_hijri_date, to_western_digits
from app.engine.normalize import name_similarity, normalize_arabic_name
from app.engine.ranking import RankPerson, RankStatus, compare_two, rank_people


def D(y: int, m: int, d: int) -> HijriDate:
    return HijriDate(y, m, d)


def P(name: str, dates: list[tuple[int, int, int]], id: str | None = None) -> RankPerson:
    seq = [D(*t) for t in dates]
    # ensure newest-first
    seq = sorted(seq, reverse=True)
    return RankPerson(
        id=id or name,
        original_name=name,
        normalized_name=normalize_arabic_name(name),
        dates=seq,
    )


class TestBasicRanking:
    def test_two_people_different_latest(self):
        """1. Older latest date ranks first."""
        ahmed = P("أحمد", [(1447, 8, 10)])
        mohammed = P("محمد", [(1447, 8, 15)])
        res = rank_people([ahmed, mohammed])
        assert res[0].person.original_name == "أحمد"
        assert res[1].person.original_name == "محمد"
        assert res[0].rank == 1
        assert res[1].rank == 2
        assert res[0].status == RankStatus.RANKED

    def test_tie_at_latest_resolved_at_second(self):
        """2. Tied latest, different second."""
        a = P("أ", [(1447, 8, 10), (1447, 7, 20)])
        b = P("ب", [(1447, 8, 10), (1447, 7, 18)])
        res = rank_people([a, b])
        # older second date wins: ب has 18, أ has 20 → ب first
        assert res[0].person.original_name == "ب"
        assert res[1].person.original_name == "أ"

    def test_deep_tie_six_levels(self):
        """3. Tied through five, separated at sixth."""
        a = P(
            "أ",
            [
                (1447, 8, 10),
                (1447, 7, 20),
                (1447, 6, 15),
                (1447, 5, 5),
                (1447, 4, 1),
                (1447, 3, 1),
            ],
        )
        b = P(
            "ب",
            [
                (1447, 8, 10),
                (1447, 7, 20),
                (1447, 6, 15),
                (1447, 5, 5),
                (1447, 4, 1),
                (1447, 3, 3),
            ],
        )
        res = rank_people([a, b])
        assert res[0].person.original_name == "أ"  # 03/01 older than 03/03
        assert res[1].person.original_name == "ب"

    def test_five_people_tied_at_latest(self):
        """4. Five people same latest date."""
        people = [
            P("A", [(1447, 8, 10), (1447, 7, 25)]),
            P("B", [(1447, 8, 10), (1447, 7, 20)]),
            P("C", [(1447, 8, 10), (1447, 7, 15)]),
            P("D", [(1447, 8, 10), (1447, 7, 10)]),
            P("E", [(1447, 8, 10), (1447, 7, 5)]),
        ]
        res = rank_people(people)
        names = [r.person.original_name for r in res]
        assert names == ["E", "D", "C", "B", "A"]

    def test_five_people_tied_two_levels(self):
        """5. Five tied at first two levels."""
        people = [
            P("A", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 5)]),
            P("B", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 4)]),
            P("C", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 3)]),
            P("D", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 2)]),
            P("E", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 1)]),
        ]
        res = rank_people(people)
        assert [r.person.original_name for r in res] == ["E", "D", "C", "B", "A"]

    def test_nested_subgroups_example_from_spec(self):
        """6. Nested subgroups — example section 21."""
        # A: [10/08, 20/07, 05/06]
        # B: [10/08, 18/07, 09/06]
        # C: [10/08, 18/07, 07/06]
        # D: [10/08, 25/07, 01/06]
        # Expected: C, B, A, D
        a = P("A", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 5)])
        b = P("B", [(1447, 8, 10), (1447, 7, 18), (1447, 6, 9)])
        c = P("C", [(1447, 8, 10), (1447, 7, 18), (1447, 6, 7)])
        d = P("D", [(1447, 8, 10), (1447, 7, 25), (1447, 6, 1)])
        res = rank_people([a, b, c, d])
        assert [r.person.original_name for r in res] == ["C", "B", "A", "D"]

    def test_complete_tie(self):
        """7. Complete tie — identical sequences."""
        a = P("A", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 15)])
        b = P("B", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 15)])
        res = rank_people([a, b])
        assert res[0].rank == res[1].rank
        assert res[0].status == RankStatus.TIE
        assert res[1].status == RankStatus.TIE

    def test_different_sequence_lengths_unresolved(self):
        """8. Different lengths after equal prefix → unresolved."""
        a = P("A", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 15), (1447, 5, 1)])
        b = P("B", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 15)])
        res = rank_people([a, b])
        assert res[0].rank == res[1].rank
        assert res[0].status == RankStatus.UNRESOLVED
        assert "غير محسوم" in res[0].status.value

    def test_person_with_no_valid_date(self):
        """9. Person with no dates."""
        a = P("A", [(1447, 8, 10)])
        b = RankPerson(id="b", original_name="B", normalized_name="b", dates=[])
        res = rank_people([a, b])
        assert res[0].person.original_name == "A"
        assert res[1].status == RankStatus.NO_DATES

    def test_duplicate_dates_deduped_before_rank(self):
        """11. Duplicate same date is not an extra level — ranking key is unique."""
        # Simulate unique_dates already applied: single occurrence
        a = P("A", [(1447, 8, 10), (1447, 7, 1)])
        b = P("B", [(1447, 8, 10), (1447, 7, 1)])
        res = rank_people([a, b])
        assert res[0].status == RankStatus.TIE

    def test_reproducible(self):
        people = [
            P("A", [(1447, 8, 15), (1447, 6, 1)]),
            P("B", [(1447, 8, 10), (1447, 7, 1)]),
            P("C", [(1447, 8, 10), (1447, 6, 5)]),
        ]
        r1 = [e.person.original_name for e in rank_people(people)]
        r2 = [e.person.original_name for e in rank_people(list(reversed(people)))]
        # B,C latest 08/10 before A 08/15; then C 06/05 older than B 07/01
        assert r1 == r2 == ["C", "B", "A"]

    def test_compare_two_audit(self):
        a = P("A", [(1447, 8, 10), (1447, 7, 18)])
        b = P("B", [(1447, 8, 10), (1447, 7, 20)])
        cmp = compare_two(a, b)
        assert cmp["result"] == "a"
        assert cmp["level"] == 1
        assert cmp["rule"] == "older_wins"


class TestDeepTieMandatory:
    def test_ten_people_deep_nested_ties(self):
        """
        38. At least 10 people:
        - all 10 tied at level 1
        - 7 remain tied at level 2
        - 5 of those at level 3
        - 3 of those at level 4
        - final separation at level 5
        """
        # Level1 all = 08/10
        # Level2: group of 7 has 07/20; three others split earlier with different L2
        # People:
        # Core7: L2=07/20
        #   X1,X2 have L2 different
        #   X3 has L2 different
        # Among Core7:
        #   Core5: L3=06/15
        #   Y1,Y2 different L3
        # Among Core5:
        #   Core3: L4=05/05
        #   Z1,Z2 different L4
        # Among Core3: different L5

        def make(name, l2, l3, l4, l5):
            return P(
                name,
                [
                    (1447, 8, 10),  # L1 all same
                    (1447, 7, l2),
                    (1447, 6, l3),
                    (1447, 5, l4),
                    (1447, 4, l5),
                ],
            )

        # 3 people break at L2 (older L2 ranks first among them vs core)
        # Core uses l2=20
        p_early = make("E1", 10, 1, 1, 1)  # oldest L2 → ranks first among all
        p_mid = make("E2", 15, 1, 1, 1)
        p_late_l2 = make("E3", 25, 1, 1, 1)  # newest L2 → after core

        # Core7 with L2=20
        # 2 break at L3
        y1 = make("Y1", 20, 10, 1, 1)  # older L3
        y2 = make("Y2", 20, 20, 1, 1)  # newer L3

        # Core5 L3=15
        # 2 break at L4
        z1 = make("Z1", 20, 15, 1, 1)  # older L4
        z2 = make("Z2", 20, 15, 10, 1)  # newer L4

        # Core3 L4=5, split at L5
        c1 = make("C1", 20, 15, 5, 1)  # oldest L5
        c2 = make("C2", 20, 15, 5, 2)
        c3 = make("C3", 20, 15, 5, 3)

        people = [p_early, p_mid, p_late_l2, y1, y2, z1, z2, c1, c2, c3]
        assert len(people) == 10

        res = rank_people(people)
        names = [r.person.original_name for r in res]

        # Order by older-wins lexicographic on (L1 equal, then L2 asc, L3 asc, ...)
        # L2: E1(10), E2(15), Core(20)..., E3(25)
        # Within core L3: Y1(10), Core5(15), Y2(20)
        # Within core5 L4: Z1(1), Core3(5), Z2(10)
        # Within core3 L5: C1(1), C2(2), C3(3)
        expected = ["E1", "E2", "Y1", "Z1", "C1", "C2", "C3", "Z2", "Y2", "E3"]
        assert names == expected

    def test_deeper_than_five(self):
        """No hard-coded max depth — separate at level 8."""
        dates_a = [(1447, 8, 10 - i) for i in range(7)] + [(1447, 1, 1)]
        dates_b = [(1447, 8, 10 - i) for i in range(7)] + [(1447, 1, 2)]
        # Build properly as year/month/day with same first 7
        base = [
            (1447, 12, 1),
            (1447, 11, 1),
            (1447, 10, 1),
            (1447, 9, 1),
            (1447, 8, 1),
            (1447, 7, 1),
            (1447, 6, 1),
        ]
        a = P("A", base + [(1447, 5, 1)])
        b = P("B", base + [(1447, 5, 2)])
        res = rank_people([a, b])
        assert res[0].person.original_name == "A"
        assert res[1].person.original_name == "B"


class TestDatesAndNormalize:
    def test_arabic_numerals(self):
        """12. Arabic numerals."""
        d = parse_hijri_date("١٤٤٧/٠٨/١٥")
        assert d == HijriDate(1447, 8, 15)

    def test_western_numerals(self):
        """13. Western numerals."""
        d = parse_hijri_date("1447/08/15")
        assert d == HijriDate(1447, 8, 15)

    def test_extract_multiple_from_notes(self):
        text = "تطبيق لمدة ٢٤ ساعة اعتبارًا من يوم الأحد ١٤٤٧/٠٣/٢٢هـ ولمدة ١٢ ساعة ١٤٤٧/٠٦/١٤هـ و١٤٤٧/٠٨/١١هـ"
        dates = extract_all_dates(text, page=3)
        assert len(dates) >= 3
        isos = {d.normalized.iso() for d in dates}
        assert "1447-03-22" in isos
        assert "1447-08-11" in isos

    def test_to_western(self):
        assert to_western_digits("١٤٤٧") == "1447"

    def test_normalize_alef(self):
        assert normalize_arabic_name("أحمد") == normalize_arabic_name("احمد")

    def test_name_similarity_soft_teh(self):
        # soft path should score high but exact path keeps distinction
        s = name_similarity("محمد سلامة الحازمي", "محمد سلامه الحازمي")
        assert s >= 0.9

    def test_ambiguous_similar_names_low_forced_match(self):
        """17. Similar but different — similarity not 1.0."""
        s = name_similarity("محمد سعد الحارثي", "محمد سعيد الحارثي")
        assert s < 0.95  # must not be treated as identical


class TestSpecDeepExample:
    def test_section_23(self):
        a = P("A", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 15), (1447, 5, 5), (1447, 4, 1)])
        b = P("B", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 15), (1447, 5, 5), (1447, 4, 3)])
        c = P("C", [(1447, 8, 10), (1447, 7, 20), (1447, 6, 15), (1447, 5, 7), (1447, 4, 1)])
        res = rank_people([a, b, c])
        assert [r.person.original_name for r in res] == ["A", "B", "C"]

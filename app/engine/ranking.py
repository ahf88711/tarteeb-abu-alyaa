"""
Deterministic historical ranking engine.

CRITICAL BUSINESS RULE (never simplify):
- Each person's date sequence is stored NEWEST → OLDEST.
- Ranking compares sequences lexicographically starting at the newest.
- At the first differing position, the OLDER date ranks first.
- Ties among any number of people recurse to the next historical level.
- No fixed maximum tie depth.
- No invented tie-breakers.
- Exhausted unequal-length sequences → "تعادل غير محسوم بالبيانات المتاحة".
- Identical full sequences → "تعادل".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence  # noqa: F401 used in annotate

from .dates import HijriDate


class RankStatus(str, Enum):
    RANKED = "مرتّب"
    TIE = "تعادل"
    UNRESOLVED = "تعادل غير محسوم بالبيانات المتاحة"
    NO_DATES = "بدون تواريخ صالحة"
    NOT_FOUND = "غير موجود في الملف الرئيسي"
    NEEDS_REVIEW = "يحتاج مراجعة"


@dataclass
class RankPerson:
    """Verified person ready for deterministic ranking."""

    id: str
    original_name: str
    normalized_name: str
    dates: list[HijriDate]  # unique, newest → oldest, verified only
    meta: dict = field(default_factory=dict)

    def date_at(self, level: int) -> Optional[HijriDate]:
        if 0 <= level < len(self.dates):
            return self.dates[level]
        return None


@dataclass
class Cluster:
    """A group of people that share the same rank band."""

    people: list[RankPerson]
    status: RankStatus
    resolution_level: Optional[int] = None  # level that separated this cluster from others
    resolution_date: Optional[HijriDate] = None
    equal_prefix_levels: int = 0  # how many leading dates matched before split/end


@dataclass
class RankEntry:
    rank: int  # shared rank for ties / unresolved groups
    person: RankPerson
    status: RankStatus
    explanation: str
    resolution_level: Optional[int] = None
    equal_prefix_levels: int = 0
    group_size: int = 1
    comparison_path: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "id": self.person.id,
            "original_name": self.person.original_name,
            "normalized_name": self.person.normalized_name,
            "status": self.status.value,
            "explanation": self.explanation,
            "resolution_level": self.resolution_level,
            "equal_prefix_levels": self.equal_prefix_levels,
            "group_size": self.group_size,
            "dates": [d.display() for d in self.person.dates],
            "dates_ar": [d.display_ar() for d in self.person.dates],
            "latest_date": self.person.dates[0].display() if self.person.dates else None,
            "previous_date": self.person.dates[1].display() if len(self.person.dates) > 1 else None,
            "date_count": len(self.person.dates),
            "comparison_path": self.comparison_path,
        }


def _partition(people: Sequence[RankPerson], level: int) -> list[Cluster]:
    """
    Recursively partition a tied group by historical level.
    No maximum depth. Older date within a level comes first.
    """
    people = list(people)
    if len(people) <= 1:
        return [
            Cluster(
                people=people,
                status=RankStatus.RANKED,
                resolution_level=None if not people else max(0, level - 1),
                equal_prefix_levels=level,
            )
        ]

    present: dict[HijriDate, list[RankPerson]] = {}
    missing: list[RankPerson] = []

    for p in people:
        d = p.date_at(level)
        if d is None:
            missing.append(p)
        else:
            present.setdefault(d, []).append(p)

    # Case: everyone exhausted at this level → complete tie (sequences equal so far)
    if missing and not present:
        return [
            Cluster(
                people=people,
                status=RankStatus.TIE,
                equal_prefix_levels=level,
            )
        ]

    # Case: some have a date at this level, some don't → unresolved (section 25)
    if missing and present:
        return [
            Cluster(
                people=people,
                status=RankStatus.UNRESOLVED,
                equal_prefix_levels=level,
            )
        ]

    # All have dates at this level
    assert present
    if len(present) == 1:
        # Still tied on this level → go deeper (only this group)
        return _partition(people, level + 1)

    # Split by date: older first
    clusters: list[Cluster] = []
    for d in sorted(present.keys()):  # older → newer
        group = present[d]
        if len(group) == 1:
            clusters.append(
                Cluster(
                    people=group,
                    status=RankStatus.RANKED,
                    resolution_level=level,
                    resolution_date=d,
                    equal_prefix_levels=level,
                )
            )
        else:
            # Nested subgroup continues independently
            sub = _partition(group, level + 1)
            clusters.extend(sub)
    return clusters


def _arabic_level_label(level: int) -> str:
    labels = {
        0: "أحدث تاريخ",
        1: "التاريخ الثاني (الأسبق)",
        2: "التاريخ الثالث",
        3: "التاريخ الرابع",
        4: "التاريخ الخامس",
    }
    return labels.get(level, f"التاريخ رقم {level + 1}")


def _build_explanation(
    person: RankPerson,
    cluster: Cluster,
    all_clusters: list[Cluster],
    cluster_index: int,
) -> tuple[str, list[dict]]:
    """Deterministic Arabic explanation from ranking data (no LLM)."""
    path: list[dict] = []
    parts: list[str] = []

    if cluster.status == RankStatus.NO_DATES:
        return "لا توجد تواريخ صالحة ومعتمدة لهذا الشخص.", path

    if not person.dates:
        return "لا توجد تواريخ صالحة ومعتمدة لهذا الشخص.", path

    # Describe own sequence briefly
    seq = " ← ".join(d.display_ar() for d in person.dates[:6])
    if len(person.dates) > 6:
        seq += " …"
    parts.append(f"تسلسل تواريخ «{person.original_name}» (من الأحدث للأقدم): {seq}.")

    if cluster.status == RankStatus.TIE:
        names = " و ".join(f"«{p.original_name}»" for p in cluster.people)
        parts.append(
            f"تعادل تام بين {names} على جميع المستويات التاريخية المتاحة "
            f"({cluster.equal_prefix_levels} مستوى متطابق). لم يُطبَّق أي كسر تعادل إضافي."
        )
        path.append(
            {
                "type": "full_tie",
                "levels_equal": cluster.equal_prefix_levels,
                "group": [p.original_name for p in cluster.people],
            }
        )
        return " ".join(parts), path

    if cluster.status == RankStatus.UNRESOLVED:
        names = " و ".join(f"«{p.original_name}»" for p in cluster.people)
        parts.append(
            f"تعادل غير محسوم بالبيانات المتاحة بين {names}: "
            f"تطابقت التواريخ حتى المستوى {_arabic_level_label(max(0, cluster.equal_prefix_levels - 1))} "
            f"ثم نفدت السجلات التاريخية لبعضهم دون فرق حاسم. لم يُختلق كسر تعادل."
        )
        path.append(
            {
                "type": "unresolved",
                "equal_prefix_levels": cluster.equal_prefix_levels,
                "group": [p.original_name for p in cluster.people],
            }
        )
        return " ".join(parts), path

    # RANKED: reconstruct why this person sits where they sit vs neighbors
    if cluster.resolution_level is not None and cluster.resolution_date is not None:
        lvl = cluster.resolution_level
        parts.append(
            f"حُسم ترتيب «{person.original_name}» عند {_arabic_level_label(lvl)} "
            f"بالتاريخ {cluster.resolution_date.display_ar()} "
            f"(الأقدم يتقدّم عند أول اختلاف)."
        )
        path.append(
            {
                "type": "resolved",
                "level": lvl,
                "date": cluster.resolution_date.display(),
                "rule": "older_wins_at_first_difference",
            }
        )
    else:
        parts.append(
            f"ترتيب «{person.original_name}» وفق مقارنة تسلسل التواريخ "
            f"(الأحدث أولاً للمقارنة، والأقدم يفوز عند أول اختلاف)."
        )
        path.append({"type": "resolved", "level": 0, "rule": "older_wins_at_first_difference"})

    # Mention equal prefix if any
    if cluster.equal_prefix_levels > 0 and cluster.resolution_level is not None:
        eq_dates = []
        for i in range(cluster.resolution_level):
            d = person.date_at(i)
            if d:
                eq_dates.append(d.display_ar())
        if eq_dates:
            parts.append(
                "تساوى مع آخرين في: " + "، ".join(
                    f"{_arabic_level_label(i)}={eq_dates[i]}" for i in range(len(eq_dates))
                ) + "."
            )

    return " ".join(parts), path


def _sanitize_person_dates(person: RankPerson) -> RankPerson:
    """
    Enforce unique dates, newest → oldest.
    Callers may accidentally pass duplicates or unsorted sequences;
    ranking must never treat a duplicate as an extra historical level.
    """
    if not person.dates:
        return person
    # set() dedupes HijriDate (frozen); sort newest first
    person.dates = sorted(set(person.dates), reverse=True)
    return person


def rank_people(people: Sequence[RankPerson]) -> list[RankEntry]:
    """
    Deterministically rank verified people.

    Returns RankEntry list ordered by rank ascending (1 = highest priority).
    People with no dates are appended after ranked groups with NO_DATES status.

    Within the same rank band (full/unresolved ties), members are ordered by
    normalized_name for stable output only — this does NOT break ties for ranking.
    """
    people = [_sanitize_person_dates(p) for p in people]
    with_dates = [p for p in people if p.dates]
    without = [p for p in people if not p.dates]
    # Stable input order for partition: sort by name so same-date bucket
    # insertion order is deterministic across shuffles (ranks unchanged).
    with_dates.sort(key=lambda p: (p.normalized_name, p.id))
    without.sort(key=lambda p: (p.normalized_name, p.id))

    clusters = _partition(with_dates, level=0) if with_dates else []

    results: list[RankEntry] = []
    current_rank = 1

    for idx, cluster in enumerate(clusters):
        # Stable member order within a band (presentation only)
        members = sorted(cluster.people, key=lambda p: (p.normalized_name, p.id))
        group_size = len(members)
        for person in members:
            explanation, path = _build_explanation(person, cluster, clusters, idx)
            status = cluster.status
            if group_size == 1:
                status = RankStatus.RANKED
            results.append(
                RankEntry(
                    rank=current_rank,
                    person=person,
                    status=status,
                    explanation=explanation,
                    resolution_level=cluster.resolution_level,
                    equal_prefix_levels=cluster.equal_prefix_levels,
                    group_size=group_size,
                    comparison_path=path,
                )
            )

        current_rank += group_size

    for person in without:
        results.append(
            RankEntry(
                rank=current_rank,
                person=person,
                status=RankStatus.NO_DATES,
                explanation="لا توجد تواريخ صالحة ومعتمدة؛ لا يدخل في المقارنة التاريخية.",
                group_size=len(without),
            )
        )
    if without:
        base = results[-len(without)].rank if results else 1
        for e in results[-len(without) :]:
            e.rank = base
            e.group_size = len(without)

    _annotate_vs_previous(results)
    return results


def _annotate_vs_previous(results: list[RankEntry]) -> None:
    """Append deterministic Arabic note comparing each entry to the one above."""
    prev: Optional[RankEntry] = None
    for e in results:
        if e.status == RankStatus.NO_DATES:
            prev = e
            continue
        if prev is None or prev.status == RankStatus.NO_DATES:
            if e.rank == 1 and e.status == RankStatus.RANKED:
                e.explanation += " (الأول في الترتيب حسب القاعدة)."
            prev = e
            continue
        if e.rank == prev.rank:
            # same band (tie / unresolved already explained)
            prev = e
            continue
        cmp = compare_two(prev.person, e.person)
        if cmp.get("result") == "a":
            e.explanation += (
                f" جاء بعد «{prev.person.original_name}» لأن عند "
                f"{_arabic_level_label(int(cmp.get('level') or 0))} "
                f"تاريخه {cmp.get('date_b')} أحدث من {cmp.get('date_a')}."
            )
            e.comparison_path.append(
                {
                    "type": "vs_previous",
                    "previous": prev.person.original_name,
                    "level": cmp.get("level"),
                    "prev_date": cmp.get("date_a"),
                    "this_date": cmp.get("date_b"),
                }
            )
        elif cmp.get("result") == "b":
            # should not happen if order is correct — flag in path only
            e.comparison_path.append({"type": "vs_previous_anomaly", "cmp": cmp})
        prev = e


def compare_two(a: RankPerson, b: RankPerson) -> dict:
    """
    Pairwise comparison for audit. Returns structured result:
    winner id or tie/unresolved.
    """
    level = 0
    while True:
        da, db = a.date_at(level), b.date_at(level)
        if da is None and db is None:
            return {
                "result": "tie",
                "status": RankStatus.TIE.value,
                "level": level,
                "message": "تعادل تام على جميع المستويات المتاحة",
            }
        if da is None or db is None:
            return {
                "result": "unresolved",
                "status": RankStatus.UNRESOLVED.value,
                "level": level,
                "message": "تعادل غير محسوم بالبيانات المتاحة",
            }
        if da < db:
            return {
                "result": "a",
                "winner": a.id,
                "loser": b.id,
                "level": level,
                "date_a": da.display(),
                "date_b": db.display(),
                "rule": "older_wins",
            }
        if db < da:
            return {
                "result": "b",
                "winner": b.id,
                "loser": a.id,
                "level": level,
                "date_a": da.display(),
                "date_b": db.display(),
                "rule": "older_wins",
            }
        level += 1


def summarize_results(entries: list[RankEntry]) -> dict:
    ranked = sum(1 for e in entries if e.status == RankStatus.RANKED)
    ties = sum(1 for e in entries if e.status == RankStatus.TIE)
    unresolved = sum(1 for e in entries if e.status == RankStatus.UNRESOLVED)
    no_dates = sum(1 for e in entries if e.status == RankStatus.NO_DATES)
    return {
        "total": len(entries),
        "ranked_successfully": ranked,
        "tied": ties,
        "unresolved": unresolved,
        "no_dates": no_dates,
    }

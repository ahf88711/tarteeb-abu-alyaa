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
    rank_min: Optional[int] = None
    rank_max: Optional[int] = None
    rank_exact: bool = True
    unresolved_with: list[str] = field(default_factory=list)
    strictly_before: list[str] = field(default_factory=list)
    strictly_after: list[str] = field(default_factory=list)

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
            "rank_min": self.rank_min if self.rank_min is not None else self.rank,
            "rank_max": self.rank_max if self.rank_max is not None else self.rank,
            "rank_exact": self.rank_exact,
            "rank_display": (
                str(self.rank_min if self.rank_min is not None else self.rank)
                if self.rank_exact
                else f"{self.rank_min}–{self.rank_max}"
            ),
            "unresolved_with": self.unresolved_with,
            "strictly_before": self.strictly_before,
            "strictly_after": self.strictly_after,
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
    # Never mutate caller-owned evidence while producing a ranking view.
    return RankPerson(
        id=person.id,
        original_name=person.original_name,
        normalized_name=person.normalized_name,
        dates=sorted(set(person.dates), reverse=True),
        meta=dict(person.meta),
    )


def rank_people(people: Sequence[RankPerson]) -> list[RankEntry]:
    """
    Rank verified histories as a mathematically explicit partial order.

    Unequal prefix histories are incomparable, not equal. We therefore retain
    every strict comparison that is still provable and expose an honest
    ``rank_min..rank_max`` interval instead of discarding subgroup information
    or inventing a total order.
    """
    people = [_sanitize_person_dates(p) for p in people]
    with_dates = [p for p in people if p.dates]
    without = [p for p in people if not p.dates]
    without.sort(key=lambda p: (p.normalized_name, p.id))
    results: list[RankEntry] = []

    # Identical full sequences form true tie classes.
    classes_by_sequence: dict[tuple[HijriDate, ...], list[RankPerson]] = {}
    for person in with_dates:
        classes_by_sequence.setdefault(tuple(person.dates), []).append(person)
    nodes = [
        {
            "sequence": sequence,
            "people": sorted(members, key=lambda p: (p.normalized_name, p.id)),
        }
        for sequence, members in classes_by_sequence.items()
    ]
    nodes.sort(
        key=lambda node: (
            node["sequence"],
            node["people"][0].normalized_name,
            node["people"][0].id,
        )
    )
    count = len(nodes)
    edges: list[set[int]] = [set() for _ in range(count)]
    incomparable: list[set[int]] = [set() for _ in range(count)]
    comparison_levels: dict[tuple[int, int], int] = {}

    for left in range(count):
        for right in range(left + 1, count):
            outcome = compare_two(nodes[left]["people"][0], nodes[right]["people"][0])
            level = int(outcome.get("level") or 0)
            comparison_levels[(left, right)] = level
            if outcome["result"] == "a":
                edges[left].add(right)
            elif outcome["result"] == "b":
                edges[right].add(left)
            elif outcome["result"] == "unresolved":
                incomparable[left].add(right)
                incomparable[right].add(left)

    # Transitive closure is cheap for the expected roster sizes and provides
    # machine-checkable precedence/position bounds.
    reach = [set(destinations) for destinations in edges]
    changed = True
    while changed:
        changed = False
        for source in range(count):
            expanded = set(reach[source])
            for destination in tuple(reach[source]):
                expanded.update(reach[destination])
            if expanded != reach[source]:
                reach[source] = expanded
                changed = True
    if any(index in reach[index] for index in range(count)):
        raise AssertionError("ranking relation produced a cycle")

    ancestors: list[set[int]] = [set() for _ in range(count)]
    for source, destinations in enumerate(reach):
        for destination in destinations:
            ancestors[destination].add(source)
    sizes = [len(node["people"]) for node in nodes]
    total = sum(sizes)
    rank_min = [1 + sum(sizes[item] for item in ancestors[index]) for index in range(count)]
    rank_max = [
        total - sum(sizes[item] for item in reach[index]) - sizes[index] + 1
        for index in range(count)
    ]

    # Connected incomparability components retain the legacy shared rank band,
    # while rank_display/rank_min/rank_max carry the precise partial order.
    component = list(range(count))

    def find(index: int) -> int:
        while component[index] != index:
            component[index] = component[component[index]]
            index = component[index]
        return index

    def union(a: int, b: int) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            component[root_b] = root_a

    for index, peers in enumerate(incomparable):
        for peer in peers:
            union(index, peer)
    component_rank: dict[int, int] = {}
    for index in range(count):
        root = find(index)
        component_rank[root] = min(component_rank.get(root, rank_min[index]), rank_min[index])

    # Deterministic topological presentation. Name/id is presentation-only for
    # incomparable nodes and is never reported as a business tie-breaker.
    remaining = set(range(count))
    order: list[int] = []
    while remaining:
        ready = [index for index in remaining if not (ancestors[index] & remaining)]
        if not ready:
            raise AssertionError("no acyclic presentation order")
        ready.sort(
            key=lambda index: (
                rank_min[index],
                nodes[index]["sequence"],
                nodes[index]["people"][0].normalized_name,
                nodes[index]["people"][0].id,
            )
        )
        for index in ready:
            order.append(index)
            remaining.remove(index)

    for index in order:
        members: list[RankPerson] = nodes[index]["people"]
        unresolved_names = sorted(
            person.original_name
            for peer in incomparable[index]
            for person in nodes[peer]["people"]
        )
        before_names = sorted(
            person.original_name
            for peer in reach[index]
            for person in nodes[peer]["people"]
        )
        after_names = sorted(
            person.original_name
            for peer in ancestors[index]
            for person in nodes[peer]["people"]
        )
        exact = rank_min[index] == rank_max[index]
        status = (
            RankStatus.UNRESOLVED
            if unresolved_names
            else RankStatus.TIE
            if len(members) > 1
            else RankStatus.RANKED
        )
        legacy_rank = (
            component_rank[find(index)] if unresolved_names else rank_min[index]
        )
        for person in members:
            sequence_ar = " ← ".join(date.display_ar() for date in person.dates)
            path: list[dict] = [
                {
                    "type": "partial_order_bounds",
                    "rank_min": rank_min[index],
                    "rank_max": rank_max[index],
                    "strictly_before": before_names,
                    "strictly_after": after_names,
                    "unresolved_with": unresolved_names,
                }
            ]
            if status == RankStatus.UNRESOLVED:
                explanation = (
                    f"تسلسل تواريخ «{person.original_name}» (من الأحدث للأقدم): {sequence_ar}. "
                    f"الموضع الممكن من {rank_min[index]} إلى {rank_max[index]}؛ تعذّر حسم المقارنة "
                    f"مع {' و '.join(f'«{name}»' for name in unresolved_names)} لأن أحد التسلسلين "
                    "انتهى بعد بادئة متطابقة. لم يُختلق كسر تعادل."
                )
                if before_names:
                    explanation += " يسبق حتمًا: " + "، ".join(before_names) + "."
                if after_names:
                    explanation += " يسبقه حتمًا: " + "، ".join(after_names) + "."
            elif status == RankStatus.TIE:
                names = " و ".join(f"«{member.original_name}»" for member in members)
                explanation = (
                    f"تعادل تام بين {names}: التسلسل التاريخي الموثق متطابق بالكامل. "
                    "لم يُطبَّق أي كسر تعادل إضافي."
                )
                path.append({"type": "full_tie", "group": [p.original_name for p in members]})
            else:
                explanation = (
                    f"تسلسل تواريخ «{person.original_name}» (من الأحدث للأقدم): {sequence_ar}. "
                    f"الموضع {rank_min[index]} محسوم بالمقارنة المعجمية التاريخية؛ الأقدم يتقدم "
                    "عند أول اختلاف."
                )
            results.append(
                RankEntry(
                    rank=legacy_rank,
                    person=person,
                    status=status,
                    explanation=explanation,
                    group_size=len(members) if not unresolved_names else 1 + len(unresolved_names),
                    comparison_path=path,
                    rank_min=rank_min[index],
                    rank_max=rank_max[index],
                    rank_exact=exact,
                    unresolved_with=unresolved_names,
                    strictly_before=before_names,
                    strictly_after=after_names,
                )
            )

    current_rank = total + 1
    for person in without:
        results.append(
            RankEntry(
                rank=current_rank,
                person=person,
                status=RankStatus.NO_DATES,
                explanation="لا توجد تواريخ صالحة ومعتمدة؛ لا يدخل في المقارنة التاريخية.",
                group_size=len(without),
                rank_min=current_rank,
                rank_max=current_rank,
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
        if e.status == RankStatus.UNRESOLVED or prev.status == RankStatus.UNRESOLVED:
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

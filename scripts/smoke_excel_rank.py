#!/usr/bin/env python3
"""Smoke: rank using clean Excel master without starting the web UI."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine.models import NameStatus, make_master_key
from app.engine.pipeline import (
    add_manual_target_names,
    apply_date_reviews,
    apply_name_corrections,
    load_master_many,
    new_session,
    run_ranking,
)

OVERLAP = [
    "وليد وادي العنزي",
    "أمجد النشمي الصلبي",
    "نايف أحمد الحازمي",
    "منيف جمعة البناقي",
    "يوسف سعيد العنزي",
    "مازن عجاج العنزي",
    "فيصل سعود العنزي",
    "عبدالعزيز معاشي العنزي",
]


def main() -> int:
    xlsx = ROOT / "data" / "samples" / "master_page3_clean.xlsx"
    if not xlsx.exists():
        print("missing", xlsx)
        return 1
    s = new_session()
    load_master_many(s, [xlsx])
    add_manual_target_names(s, OVERLAP)
    corrections = []
    for t in s.target_names:
        if t.matched_master_name or t.status == NameStatus.VERIFIED:
            corrections.append(
                {
                    "id": t.id,
                    "action": "set_name",
                    "name": t.matched_master_name or t.original_name,
                }
            )
    apply_name_corrections(s, corrections)
    reviews = []
    for t in s.target_names:
        if t.status != NameStatus.VERIFIED:
            continue
        key = make_master_key(t.display_name)
        if key in s.master_people:
            reviews.append({"master_key": key, "action": "verify_all"})
    apply_date_reviews(s, reviews)
    run_ranking(s, auto_verify_dates=False)
    print("=== ترتيب أبو علياء (Excel نظيف) ===")
    for r in s.ranking_results:
        if r.get("rank") is None:
            continue
        print(
            f"#{r['rank']:2d}  {r['original_name']:<28}  "
            f"أحدث={r['latest_date']}  عدد={r['date_count']}  {r['status']}"
        )
    print("summary:", s.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

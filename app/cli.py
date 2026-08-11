#!/usr/bin/env python3
"""
CLI for ترتيب أبو علياء — rank without the browser.

Examples:
  python3 -m app.cli rank --master data/samples/master_page3_clean.xlsx \\
      --targets data/samples/targets_page3_overlap.xlsx

  python3 -m app.cli rank --master /path/to/roster.pdf --names-file names.txt

  python3 -m app.cli demo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# allow `python3 -m app.cli` from project root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def cmd_demo(_: argparse.Namespace) -> int:
    from app.engine.models import NameStatus, make_master_key
    from app.engine.pipeline import (
        apply_date_reviews,
        apply_name_corrections,
        load_master_many,
        load_targets,
        new_session,
        run_ranking,
    )

    master = ROOT / "data" / "samples" / "master_page3_clean.xlsx"
    targets = ROOT / "data" / "samples" / "targets_page3_overlap.xlsx"
    if not master.exists() or not targets.exists():
        print("ملفات العينة غير موجودة", file=sys.stderr)
        return 1
    s = new_session()
    load_master_many(s, [master])
    load_targets(s, targets)
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
    reviews = [
        {"master_key": make_master_key(t.display_name), "action": "verify_all"}
        for t in s.target_names
        if t.status == NameStatus.VERIFIED
        and make_master_key(t.display_name) in s.master_people
    ]
    apply_date_reviews(s, reviews)
    run_ranking(s, auto_verify_dates=False)
    _print_results(s)
    return 0


def cmd_rank(args: argparse.Namespace) -> int:
    from app.engine.models import NameStatus, make_master_key
    from app.engine.pipeline import (
        add_manual_target_names,
        apply_date_reviews,
        apply_name_corrections,
        load_master_many,
        load_targets,
        new_session,
        run_ranking,
    )

    masters = [Path(p) for p in (args.master or [])]
    if not masters:
        print("حدد --master ملف واحد على الأقل", file=sys.stderr)
        return 2
    for m in masters:
        if not m.exists():
            print(f"غير موجود: {m}", file=sys.stderr)
            return 1

    s = new_session()
    load_master_many(s, masters)
    print(f"الفهرس: {len(s.master_people)} شخصًا من {len(masters)} ملف")

    if args.targets:
        tpath = Path(args.targets)
        if not tpath.exists():
            print(f"غير موجود: {tpath}", file=sys.stderr)
            return 1
        load_targets(s, tpath)
    if args.names_file:
        names = Path(args.names_file).read_text(encoding="utf-8").splitlines()
        add_manual_target_names(s, names)
    if args.name:
        add_manual_target_names(s, list(args.name))

    if not s.target_names:
        print("لا أسماء مطلوبة — استخدم --targets أو --names-file أو --name", file=sys.stderr)
        return 2

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
        elif args.force_unmatched:
            # still skip ranking unmatched
            pass
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
    _print_results(s)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(
                {"summary": s.summary, "results": s.ranking_results},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"JSON → {args.json_out}")
    return 0


def _print_results(s) -> None:
    print("\n=== ترتيب أبو علياء ===")
    for r in s.ranking_results:
        if r.get("rank") is None:
            continue
        print(
            f"#{r['rank']:2d}  {r['original_name']:<30}  "
            f"أحدث={r.get('latest_date') or '—'}  "
            f"n={r.get('date_count', 0)}  {r.get('status')}"
        )
    print(
        f"\nملخص: مرتّب={s.summary.get('ranked_successfully')} "
        f"تعادل={s.summary.get('tied')} "
        f"غير محسوم={s.summary.get('unresolved')}"
    )
    if s.summary.get("auto_export_dir"):
        print(f"تصدير: {s.summary['auto_export_dir']}")


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tarteeb", description="ترتيب أبو علياء CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="تشغيل العينة النظيفة")
    d.set_defaults(func=cmd_demo)

    r = sub.add_parser("rank", help="ترتيب من ملفات")
    r.add_argument("--master", action="append", help="PDF/Excel رئيسي (يتكرر)")
    r.add_argument("--targets", help="صورة/PDF/Excel بقائمة الأسماء")
    r.add_argument("--names-file", help="ملف نصي: اسم في كل سطر")
    r.add_argument("--name", action="append", help="اسم مطلوب (يتكرر)")
    r.add_argument("--json-out", help="حفظ النتائج JSON")
    r.add_argument(
        "--force-unmatched",
        action="store_true",
        help="لا يُرتّب غير المطابق (للتوافق فقط)",
    )
    r.set_defaults(func=cmd_rank)

    s = sub.add_parser("serve", help="تشغيل خادم الويب")
    s.add_argument("--host", default="0.0.0.0", help="0.0.0.0 للوصول من الجوال على الشبكة")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(func=cmd_serve)

    args = p.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    # Python 3.9 compat: list[str] | None needs from __future__ annotations (have it)
    raise SystemExit(main())

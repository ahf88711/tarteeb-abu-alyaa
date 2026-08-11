"""Tests for multi-source master merge and Excel import."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openpyxl import Workbook

from app.engine.dates import HijriDate
from app.engine.merge_master import extract_master_excel, merge_people_dicts, rename_master_person
from app.engine.models import MasterPerson, make_master_key
from app.engine.dates import ExtractedDate


def test_merge_people_dicts_combines_dates():
    a = MasterPerson(original_name="وليد وادي العنزي", normalized_name=make_master_key("وليد وادي العنزي"))
    a.dates.append(
        ExtractedDate(
            normalized=HijriDate(1447, 7, 16),
            original_text="1447/07/16",
            page=1,
            confidence=0.9,
            verified=True,
        )
    )
    b = MasterPerson(original_name="وليد وادي العنزي", normalized_name=make_master_key("وليد وادي العنزي"))
    b.dates.append(
        ExtractedDate(
            normalized=HijriDate(1447, 6, 20),
            original_text="1447/06/20",
            page=2,
            confidence=0.9,
            verified=True,
        )
    )
    merged = merge_people_dicts(
        {a.normalized_name: a},
        {b.normalized_name: b},
    )
    assert len(merged) == 1
    person = list(merged.values())[0]
    isos = {d.normalized.iso() for d in person.dates}
    assert "1447-07-16" in isos
    assert "1447-06-20" in isos


def test_excel_import(tmp_path: Path):
    path = tmp_path / "master.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["الاسم", "التواريخ", "الرتبة", "الصفحة"])
    ws.append(["أحمد علي العنزي", "1447/08/10|1447/07/01", "عريف", 1])
    ws.append(["محمد سعيد الحازمي", "1447/08/15", "عريف", 2])
    wb.save(path)

    people = extract_master_excel(path)
    assert len(people) == 2
    a = people[make_master_key("أحمد علي العنزي")]
    assert len(a.dates) >= 2
    assert a.rank_title == "عريف"


def test_rename_master_person():
    key = make_master_key("مبدالله جامع الرويلي")
    p = MasterPerson(original_name="مبدالله جامع الرويلي", normalized_name=key)
    people = {key: p}
    people = rename_master_person(people, key, "عبدالله جامع الرويلي")
    assert make_master_key("عبدالله جامع الرويلي") in people
    assert people[make_master_key("عبدالله جامع الرويلي")].original_name == "عبدالله جامع الرويلي"

"""Target list from Excel."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openpyxl import Workbook

from app.engine.models import MasterPerson, make_master_key
from app.engine.pipeline import _extract_targets_excel, new_session, load_master_many, add_manual_target_names


def test_target_excel_column(tmp_path: Path):
    path = tmp_path / "targets.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["الاسم"])
    ws.append(["وليد وادي العنزي"])
    ws.append(["أمجد النشمي الصلبي"])
    ws.append(["شخص غير موجود"])
    wb.save(path)

    master = {
        make_master_key("وليد وادي العنزي"): MasterPerson(
            original_name="وليد وادي العنزي",
            normalized_name=make_master_key("وليد وادي العنزي"),
        ),
        make_master_key("أمجد النشمي الصلبي"): MasterPerson(
            original_name="أمجد النشمي الصلبي",
            normalized_name=make_master_key("أمجد النشمي الصلبي"),
        ),
    }
    targets = _extract_targets_excel(path, master)
    assert len(targets) == 3
    verified = [t for t in targets if t.status.value == "مؤكد"]
    assert len(verified) == 2

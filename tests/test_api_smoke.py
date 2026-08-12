"""API smoke tests (no OCR — fast)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["name"] == "ترتيب أبو علياء"


def test_session_and_manual_without_master_fails_gracefully():
    r = client.post("/api/session")
    assert r.status_code == 200
    sid = r.json()["session_id"]
    r2 = client.post(
        "/api/names/manual",
        json={"session_id": sid, "names": ["فلان الفلاني"]},
    )
    # pipeline sets error phase if no master
    assert r2.status_code == 200
    assert r2.json()["phase"] in ("error", "names_extracted")


def test_index_arabic():
    r = client.get("/")
    assert r.status_code == 200
    assert "ترتيب أبو علياء" in r.text
    assert 'dir="rtl"' in r.text


def test_index_has_only_two_image_or_pdf_upload_choices():
    """The public UI exposes exactly the two lists requested by the operator."""
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    file_inputs = re.findall(r'<input\s+[^>]*type="file"[^>]*>', html, re.I | re.S)
    assert len(file_inputs) == 2
    assert "قائمة الأسماء والتواريخ" in html
    assert "قائمة الأسماء المراد ترتيبهم فقط" in html
    assert "نتيجة ترتيب أسماء القائمة الثانية" in html
    assert "/static/app.js?v=" in html
    assert "/static/styles.css?v=" in html
    for field in file_inputs:
        assert ".pdf" in field
        assert "image/*" in field
        assert ".xlsx" not in field

    # No folder, manual-name, search, or comparison fields remain in the UI.
    assert 'type="text"' not in html
    assert 'type="search"' not in html
    assert "<textarea" not in html
    assert "<select" not in html


def test_capabilities():
    r = client.get("/api/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "ترتيب أبو علياء"
    assert "deterministic_ranking" in body["features"]
    assert "demo_full_rank" in body["features"]

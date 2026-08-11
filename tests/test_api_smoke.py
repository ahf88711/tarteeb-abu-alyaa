"""API smoke tests (no OCR — fast)."""

from __future__ import annotations

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


def test_capabilities():
    r = client.get("/api/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "ترتيب أبو علياء"
    assert "deterministic_ranking" in body["features"]
    assert "demo_full_rank" in body["features"]

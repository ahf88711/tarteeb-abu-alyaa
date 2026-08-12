"""API smoke tests (no OCR — fast)."""

from __future__ import annotations

import asyncio
import re
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.engine.pipeline import new_session
from app.main import SESSIONS, app

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


def test_health_stays_responsive_during_blocking_ocr_upload(monkeypatch):
    """Long local OCR must run in FastAPI's worker pool, not its event loop."""
    import app.main as main_module

    session = new_session()
    SESSIONS[session.session_id] = session
    worker_started = threading.Event()

    def slow_master_load(current_session, paths):
        worker_started.set()
        time.sleep(1.0)
        current_session.phase = "master_loaded"
        return current_session

    monkeypatch.setattr(main_module, "load_master_many", slow_master_load)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
            upload = asyncio.create_task(
                async_client.post(
                    "/api/upload/master/multi",
                    params={"session_id": session.session_id},
                    files={
                        "files": (
                            "master.xlsx",
                            b"placeholder",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                    },
                )
            )
            assert await asyncio.to_thread(worker_started.wait, 2.0)
            health = await asyncio.wait_for(async_client.get("/api/health"), timeout=0.5)
            assert health.status_code == 200
            assert health.json()["ok"] is True
            assert (await upload).status_code == 200

    try:
        asyncio.run(scenario())
    finally:
        SESSIONS.pop(session.session_id, None)

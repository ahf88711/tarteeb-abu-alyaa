"""Safety tests for optional OpenAI cross-checking of local Arabic OCR."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine import extract_master, extract_targets
from app.engine.hybrid_ocr import (
    HybridRowInput,
    HybridRowReading,
    HybridVerificationReport,
    _request_body,
    _validate_readings,
    evidence_url_to_path,
    hybrid_ocr_capabilities,
    verify_arabic_rows,
)
from app.engine.models import NameStatus, TargetName, make_master_key
from app.engine.ocr import EVIDENCE_ROOT


def _evidence_crop(name: str) -> tuple[str, Path]:
    path = EVIDENCE_ROOT / name
    Image.new("RGB", (480, 96), "white").save(path, "JPEG")
    return f"/api/evidence/{name}", path


def test_hybrid_is_optional_and_never_exposes_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    image = tmp_path / "row.jpg"
    Image.new("RGB", (120, 40), "white").save(image)
    report = verify_arabic_rows([HybridRowInput("r1", image)])
    assert report.attempted_rows == 0
    assert report.readings == {}
    capabilities = hybrid_ocr_capabilities()
    assert capabilities["configured"] is False
    assert "key" not in str(capabilities).lower()


def test_responses_request_uses_original_detail_strict_schema_and_no_storage(tmp_path):
    image = tmp_path / "row.jpg"
    Image.new("RGB", (120, 40), "white").save(image)
    body = _request_body(
        [HybridRowInput("r1", image, candidates=("محمد سعد الحارثي",))]
    )
    assert body["store"] is False
    assert body["reasoning"] == {"effort": "low"}
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    image_parts = [
        part
        for part in body["input"][0]["content"]
        if part.get("type") == "input_image"
    ]
    assert image_parts[0]["detail"] == "original"
    assert image_parts[0]["image_url"].startswith("data:image/jpeg;base64,")


def test_structured_output_rejects_candidate_not_supplied(tmp_path):
    image = tmp_path / "row.jpg"
    Image.new("RGB", (120, 40), "white").save(image)
    row = HybridRowInput("r1", image, candidates=("محمد سعد الحارثي",))
    injected = {
        "rows": [
            {
                "row_id": "r1",
                "transcription": "اسم آخر",
                "selected_candidate": "اسم غير موجود في المرشحين",
                "dates": [],
                "confidence": 0.99,
                "legible": True,
            }
        ]
    }
    assert _validate_readings(injected, [row]) == {}


def test_target_is_confirmed_only_on_three_way_agreement(monkeypatch):
    crop_url, crop_path = _evidence_crop("hybrid_target_agree.jpg")
    target = TargetName(
        id="target1",
        original_name="محمد سعاد الحارثي",
        normalized_name=make_master_key("محمد سعاد الحارثي"),
        ocr_raw="محمد سعاد الحارثي",
        confidence=0.82,
        status=NameStatus.NEEDS_REVIEW,
        crop_path=crop_url,
        candidates=[
            {"name": "محمد سعد الحارثي", "confidence": 0.94},
            {"name": "محمد سعيد الحارثي", "confidence": 0.72},
        ],
        matched_master_name="محمد سعد الحارثي",
        bbox={"visual_confidence": 0.78, "ocr_agreement": 1},
    )
    reading = HybridRowReading(
        row_id="target1",
        transcription="محمد سعد الحارثي",
        selected_candidate="محمد سعد الحارثي",
        dates=(),
        confidence=0.98,
        legible=True,
    )
    monkeypatch.setattr(
        extract_targets,
        "verify_arabic_rows",
        lambda rows: HybridVerificationReport(
            readings={"target1": reading}, attempted_rows=1, completed_rows=1
        ),
    )
    try:
        extract_targets.apply_hybrid_target_verification([target])
        assert target.status == NameStatus.VERIFIED
        assert target.matched_master_name == "محمد سعد الحارثي"
        assert target.bbox["hybrid_verification"]["decision"] == "confirmed"
    finally:
        crop_path.unlink(missing_ok=True)


def test_target_disagreement_never_auto_confirms(monkeypatch):
    crop_url, crop_path = _evidence_crop("hybrid_target_disagree.jpg")
    target = TargetName(
        id="target2",
        original_name="محمد سعاد الحارثي",
        normalized_name=make_master_key("محمد سعاد الحارثي"),
        ocr_raw="محمد سعاد الحارثي",
        confidence=0.82,
        status=NameStatus.NEEDS_REVIEW,
        crop_path=crop_url,
        candidates=[
            {"name": "محمد سعد الحارثي", "confidence": 0.94},
            {"name": "محمد سعيد الحارثي", "confidence": 0.72},
        ],
        matched_master_name="محمد سعد الحارثي",
        bbox={"visual_confidence": 0.78, "ocr_agreement": 1},
    )
    reading = HybridRowReading(
        row_id="target2",
        transcription="محمد سعيد الحارثي",
        selected_candidate="محمد سعيد الحارثي",
        dates=(),
        confidence=0.99,
        legible=True,
    )
    monkeypatch.setattr(
        extract_targets,
        "verify_arabic_rows",
        lambda rows: HybridVerificationReport(readings={"target2": reading}),
    )
    try:
        extract_targets.apply_hybrid_target_verification([target])
        assert target.status == NameStatus.NEEDS_REVIEW
        assert target.bbox["hybrid_verification"]["decision"] == "disagreement"
    finally:
        crop_path.unlink(missing_ok=True)


def test_master_date_confidence_rises_only_when_both_readings_match(monkeypatch):
    crop_url, crop_path = _evidence_crop("hybrid_master_agree.jpg")
    row = {
        "original_name": "محمد سعد الحارثي",
        "notes": "اعتبارا من 1448/02/28",
        "page": 1,
        "row_index": 7,
        "confidence": 0.74,
        "name_confidence": 0.78,
        "ocr_agreement": 1,
        "row_association_confidence": 0.98,
        "source_image": crop_url,
        "source_bbox": {"x": 0.0, "top": 0.2, "w": 1.0, "h": 0.03},
    }
    reading = HybridRowReading(
        row_id="m1r7",
        transcription="محمد سعد الحارثي",
        selected_candidate="محمد سعد الحارثي",
        dates=("1448/02/28",),
        confidence=0.98,
        legible=True,
    )
    monkeypatch.setattr(
        extract_master,
        "verify_arabic_rows",
        lambda rows: HybridVerificationReport(readings={"m1r7": reading}),
    )
    try:
        extract_master.apply_hybrid_master_verification([row])
        assert row["confidence"] == 0.96
        assert row["ocr_agreement"] == 2
        assert row["source_bbox"]["hybrid_verification"]["decision"] == "confirmed"
    finally:
        crop_path.unlink(missing_ok=True)


def test_master_row_without_date_evidence_does_not_call_paid_verifier(monkeypatch):
    crop_url, crop_path = _evidence_crop("hybrid_master_no_date.jpg")
    row = {
        "original_name": "محمد سعد الحارثي",
        "notes": "حاضر",
        "page": 1,
        "row_index": 8,
        "confidence": 0.60,
        "name_confidence": 0.60,
        "ocr_agreement": 1,
        "row_association_confidence": 0.98,
        "source_image": crop_url,
        "source_bbox": {},
    }
    calls = []
    monkeypatch.setattr(
        extract_master,
        "verify_arabic_rows",
        lambda rows: calls.extend(rows) or HybridVerificationReport(),
    )
    try:
        extract_master.apply_hybrid_master_verification([row])
        assert calls == []
    finally:
        crop_path.unlink(missing_ok=True)


def test_evidence_path_resolution_rejects_traversal():
    assert evidence_url_to_path("/api/evidence/../secret.jpg") is None

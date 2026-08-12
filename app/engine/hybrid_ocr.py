"""Optional OpenAI vision cross-checks for uncertain local Arabic OCR rows.

Tesseract remains the primary OCR engine and the ranking engine remains fully
deterministic.  This module only supplies an independent reading of small,
auditable row crops.  Callers must still enforce agreement before accepting a
name or date.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import httpx

from .ocr import EVIDENCE_ROOT

LOGGER = logging.getLogger(__name__)
RESPONSES_URL = "https://api.openai.com/v1/responses"


def _enabled_by_configuration() -> bool:
    return os.getenv("HYBRID_OCR_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def hybrid_ocr_configured() -> bool:
    """Return whether the optional verifier can be called without exposing its key."""
    return _enabled_by_configuration() and bool(os.getenv("OPENAI_API_KEY", "").strip())


def hybrid_ocr_model() -> str:
    return os.getenv("OPENAI_OCR_MODEL", "gpt-5.6-terra").strip() or "gpt-5.6-terra"


def hybrid_ocr_capabilities() -> dict:
    return {
        "enabled": _enabled_by_configuration(),
        "configured": hybrid_ocr_configured(),
        "model": hybrid_ocr_model(),
        "mode": "low_confidence_row_verification",
        "stores_responses": False,
    }


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(low, min(high, value))


def _timeout_seconds() -> int:
    return _bounded_int("OPENAI_OCR_TIMEOUT_SECONDS", 90, 20, 180)


def _batch_size() -> int:
    return _bounded_int("OPENAI_OCR_BATCH_SIZE", 6, 1, 10)


def evidence_url_to_path(url: str) -> Optional[Path]:
    """Resolve only evidence URLs created by ``save_region_crop``."""
    prefix = "/api/evidence/"
    if not url.startswith(prefix):
        return None
    filename = url[len(prefix) :]
    if not filename or Path(filename).name != filename:
        return None
    path = EVIDENCE_ROOT / filename
    if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        return path
    return None


@dataclass(frozen=True)
class HybridRowInput:
    row_id: str
    image_path: Path
    candidates: tuple[str, ...] = ()
    local_name: str = ""
    local_dates: tuple[str, ...] = ()


@dataclass(frozen=True)
class HybridRowReading:
    row_id: str
    transcription: str
    selected_candidate: str
    dates: tuple[str, ...]
    confidence: float
    legible: bool


@dataclass
class HybridVerificationReport:
    readings: dict[str, HybridRowReading] = field(default_factory=dict)
    attempted_rows: int = 0
    completed_rows: int = 0
    failed_batches: int = 0


_ROW_SCHEMA = {
    "type": "object",
    "properties": {
        "row_id": {"type": "string"},
        "transcription": {"type": "string"},
        "selected_candidate": {"type": "string"},
        "dates": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "legible": {"type": "boolean"},
    },
    "required": [
        "row_id",
        "transcription",
        "selected_candidate",
        "dates",
        "confidence",
        "legible",
    ],
    "additionalProperties": False,
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {"type": "array", "items": _ROW_SCHEMA},
    },
    "required": ["rows"],
    "additionalProperties": False,
}

_INSTRUCTIONS = """You are a narrow, evidence-only Arabic OCR verifier.
Read only text visibly printed in each supplied row crop. Do not use world
knowledge, do not complete missing letters, and do not infer a date. Preserve
the visible Arabic person name in transcription. selected_candidate must be
an exact string from that row's candidate list only when the visible name is
the same person; otherwise return an empty string. dates must contain only
complete, explicitly visible Hijri dates, normalized as YYYY/MM/DD. Never
convert a Gregorian date or guess an obscured digit. If the crop is unclear,
set legible=false and confidence below 0.8. Return exactly one result for each
row_id and nothing outside the requested JSON schema."""


def _image_data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _request_body(rows: list[HybridRowInput]) -> dict:
    content: list[dict] = [
        {
            "type": "input_text",
            "text": (
                "Verify the following Arabic OCR row crops independently. "
                "A candidate is a closed list, not evidence."
            ),
        }
    ]
    for row in rows:
        candidates = json.dumps(list(row.candidates), ensure_ascii=False)
        content.append(
            {
                "type": "input_text",
                "text": f"row_id={row.row_id}; candidates={candidates}",
            }
        )
        content.append(
            {
                "type": "input_image",
                "image_url": _image_data_url(row.image_path),
                "detail": "original",
            }
        )
    return {
        "model": hybrid_ocr_model(),
        "instructions": _INSTRUCTIONS,
        "input": [{"role": "user", "content": content}],
        "reasoning": {"effort": "low"},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "arabic_ocr_row_verification",
                "strict": True,
                "schema": _OUTPUT_SCHEMA,
            }
        },
        "max_output_tokens": max(500, 180 * len(rows)),
        "store": False,
    }


def _response_output_text(payload: dict) -> str:
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def _validate_readings(payload: dict, rows: list[HybridRowInput]) -> dict[str, HybridRowReading]:
    allowed = {row.row_id: set(row.candidates) for row in rows}
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        return {}
    result: dict[str, HybridRowReading] = {}
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        row_id = raw.get("row_id")
        if not isinstance(row_id, str) or row_id not in allowed or row_id in result:
            continue
        transcription = raw.get("transcription")
        selected = raw.get("selected_candidate")
        dates = raw.get("dates")
        confidence = raw.get("confidence")
        legible = raw.get("legible")
        if not isinstance(transcription, str) or len(transcription) > 220:
            continue
        if not isinstance(selected, str) or (selected and selected not in allowed[row_id]):
            continue
        if not isinstance(dates, list) or not all(isinstance(value, str) for value in dates):
            continue
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            continue
        if not isinstance(legible, bool):
            continue
        result[row_id] = HybridRowReading(
            row_id=row_id,
            transcription=transcription.strip(),
            selected_candidate=selected,
            dates=tuple(dict.fromkeys(value.strip() for value in dates if value.strip())),
            confidence=max(0.0, min(1.0, float(confidence))),
            legible=legible,
        )
    return result


def _verify_batch(rows: list[HybridRowInput]) -> dict[str, HybridRowReading]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return {}
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "X-Client-Request-Id": str(uuid.uuid4()),
    }
    last_error: Optional[Exception] = None
    for attempt in range(2):
        try:
            with httpx.Client(timeout=_timeout_seconds()) as client:
                response = client.post(RESPONSES_URL, headers=headers, json=_request_body(rows))
            if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                time.sleep(1.0)
                continue
            response.raise_for_status()
            body = response.json()
            output_text = _response_output_text(body)
            if not output_text:
                return {}
            decoded = json.loads(output_text)
            return _validate_readings(decoded, rows)
        except (httpx.HTTPError, json.JSONDecodeError, OSError, ValueError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1.0)
                continue
    # No response body, key, image content, or document text is logged.
    LOGGER.warning("OpenAI OCR verification batch failed: %s", type(last_error).__name__)
    return {}


def verify_arabic_rows(rows: Iterable[HybridRowInput]) -> HybridVerificationReport:
    """Verify row crops in bounded batches; return empty evidence on any API issue."""
    report = HybridVerificationReport()
    if not hybrid_ocr_configured():
        return report
    valid = [row for row in rows if row.image_path.is_file()]
    report.attempted_rows = len(valid)
    size = _batch_size()
    for start in range(0, len(valid), size):
        batch = valid[start : start + size]
        readings = _verify_batch(batch)
        if not readings:
            report.failed_batches += 1
            continue
        report.readings.update(readings)
        report.completed_rows += len(readings)
    return report

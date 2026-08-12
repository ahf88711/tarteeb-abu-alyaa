"""ترتيب أبو علياء — FastAPI application."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, List, Optional

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import APP_NAME_AR, __version__
from app.engine.export import (
    export_audit_json,
    export_excel,
    export_master_index,
    export_pdf_formal,
    export_pdf_simple,
    export_ranking_text,
)
from app.engine.pipeline import (
    add_manual_target_names,
    apply_date_reviews,
    apply_name_corrections,
    auto_confirm_high_confidence,
    bulk_verify_safe_dates,
    collect_dates_for_targets,
    load_master,
    load_master_many,
    load_targets,
    new_session,
    rename_person,
    run_ranking,
)

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
STATIC = APP_DIR / "static"
SAMPLES = PROJECT_ROOT / "data" / "samples"
UPLOAD_ROOT = Path(tempfile.gettempdir()) / "tarteeb_abu_alyaa_uploads"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
from app.engine.ocr import EVIDENCE_ROOT

app = FastAPI(title=APP_NAME_AR, version=__version__)

# Reasonable upload ceiling (scanned pages can be large)
MAX_UPLOAD_BYTES = 80 * 1024 * 1024  # 80 MB per request total

SESSIONS: dict[str, Any] = {}


@app.middleware("http")
async def limit_upload_size(request, call_next):
    cl = request.headers.get("content-length")
    if cl:
        try:
            if int(cl) > MAX_UPLOAD_BYTES:
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    {"detail": "حجم الرفع أكبر من المسموح (80 ميجابايت)."},
                    status_code=413,
                )
        except ValueError:
            pass
    return await call_next(request)


@app.exception_handler(Exception)
async def unhandled_error(request, exc):
    from fastapi.responses import JSONResponse
    from fastapi import HTTPException as _HTTPException

    if isinstance(exc, _HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    # Don't leak internals to the Arabic UI
    return JSONResponse(
        {"detail": f"خطأ داخلي: {type(exc).__name__}"},
        status_code=500,
    )


class CorrectionsBody(BaseModel):
    session_id: str
    corrections: list[dict]


class DateReviewsBody(BaseModel):
    session_id: str
    reviews: list[dict]


class RankBody(BaseModel):
    session_id: str
    auto_verify_dates: bool = False


class ManualNamesBody(BaseModel):
    session_id: str
    names: list[str]


class RenameBody(BaseModel):
    session_id: str
    old_key: str
    new_name: str


class SessionOnly(BaseModel):
    session_id: str


class FolderBody(BaseModel):
    session_id: str
    folder_path: str


def _session(sid: str):
    s = SESSIONS.get(sid)
    if not s:
        raise HTTPException(404, detail="الجلسة غير موجودة. ابدأ من جديد.")
    return s


def _people_payload(s) -> list[dict]:
    people_sorted = sorted(
        s.master_people.values(),
        key=lambda p: (-len(p.dates), p.original_name),
    )
    return [
        {
            "name": p.original_name,
            "normalized": p.normalized_name,
            "rank_title": p.rank_title,
            "pages": p.pages,
            "date_count": len(p.dates),
            "dates": [
                d.normalized.display()
                for d in sorted(
                    {d.normalized: d for d in p.dates}.values(),
                    key=lambda d: d.normalized,
                    reverse=True,
                )
            ][:12],
            "needs_review_dates": sum(1 for d in p.dates if d.needs_review),
            "identity_needs_review": p.identity_needs_review,
            "aliases": p.aliases,
        }
        for p in people_sorted[:300]
    ]


def _set_background_error(s, message: str, exc: Exception) -> None:
    """Expose a useful Arabic failure through the existing progress endpoint."""
    detail = f"{message}: {exc}"
    s.phase = "error"
    s.messages.append(detail)
    s.summary = {
        **s.summary,
        "progress_pct": 0,
        "progress_message": detail,
    }


def _run_master_upload_job(s, paths: list[Path]) -> None:
    try:
        load_master_many(s, paths)
    except Exception as exc:
        _set_background_error(s, "فشل استخراج القائمة الأولى", exc)


def _run_targets_upload_job(s, paths: list[Path]) -> None:
    try:
        from app.engine.pipeline import load_targets_many

        load_targets_many(s, paths)
    except Exception as exc:
        _set_background_error(s, "فشل استخراج القائمة الثانية", exc)


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/evidence/{filename}")
def evidence_image(filename: str):
    """Serve generated audit crops without exposing arbitrary filesystem paths."""
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.lower().endswith((".jpg", ".jpeg", ".png")):
        raise HTTPException(400, detail="اسم ملف الدليل غير صالح.")
    path = EVIDENCE_ROOT / safe_name
    if not path.is_file():
        raise HTTPException(404, detail="صورة الدليل غير موجودة.")
    return FileResponse(path)


@app.get("/api/health")
def health():
    return {"ok": True, "name": APP_NAME_AR, "version": __version__}


@app.get("/api/capabilities")
def capabilities():
    """Describe what this deployment supports (for UI / ops)."""
    ocr_bin = PROJECT_ROOT / "bin" / "ocr_vision"
    from app.engine.ocr import available_ocr_backends

    ocr_backends = available_ocr_backends()
    return {
        "name": APP_NAME_AR,
        "version": __version__,
        "features": [
            "master_pdf_ocr",
            "master_excel",
            "master_multi_merge",
            "master_folder",
            "master_images_as_pages",
            "target_image_ocr",
            "target_excel",
            "target_manual",
            "auto_confirm_cautious",
            "deterministic_ranking",
            "deep_ties",
            "export_excel_pdf_text_json",
            "formal_pdf",
            "compare_audit",
            "desktop_auto_export",
            "demo_full_rank",
            "drag_drop_ui",
            "rank_chart",
            "master_search",
            "keyboard_shortcuts",
            "dark_mode",
        ],
        "ocr_binary_present": ocr_bin.exists(),
        "ocr_backends": ocr_backends,
        "arabic_ocr_available": bool(ocr_backends),
        "samples": {
            "master_pdf": (SAMPLES / "master_sample.pdf").exists(),
            "master_excel": (SAMPLES / "master_page3_clean.xlsx").exists(),
            "targets_excel": (SAMPLES / "targets_page3_overlap.xlsx").exists(),
            "targets_image": (SAMPLES / "target_names.png").exists(),
        },
        "endpoints": {
            "full_rank": "POST /api/demo/full_rank",
            "ui": "GET /",
        },
    }


@app.post("/api/session")
def create_session():
    s = new_session()
    SESSIONS[s.session_id] = s
    return {"session_id": s.session_id, "name": APP_NAME_AR}


@app.get("/api/session/{session_id}/progress")
def progress(session_id: str):
    s = _session(session_id)
    return {
        "session_id": session_id,
        "phase": s.phase,
        "progress_pct": s.summary.get("progress_pct", 0),
        "progress_message": s.summary.get("progress_message", ""),
        "messages": s.messages[-10:],
        "master_people_count": len(s.master_people),
        "target_count": len(s.target_names),
    }


@app.post("/api/upload/master/multi/start", status_code=202)
def start_master_upload(
    background_tasks: BackgroundTasks,
    session_id: str,
    files: List[UploadFile] = File(...),
):
    """Accept master files quickly, then run long local OCR outside the request."""
    s = _session(session_id)
    paths: list[Path] = []
    for index, file in enumerate(files):
        if not file.filename:
            continue
        suffix = Path(file.filename).suffix.lower()
        if suffix not in {
            ".pdf", ".xlsx", ".xlsm", ".png", ".jpg", ".jpeg", ".heic", ".heif"
        }:
            continue
        destination = UPLOAD_ROOT / f"{session_id}_master_async_{index}{suffix}"
        with destination.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        if suffix in {".png", ".jpg", ".jpeg", ".heic", ".heif"}:
            try:
                destination = _image_to_pdf(destination, session_id, index)
            except Exception as exc:
                raise HTTPException(500, detail=f"فشل تحويل صورة إلى PDF: {exc}") from exc
        paths.append(destination)
    if not paths:
        raise HTTPException(400, detail="لا ملفات PDF/صور صالحة في القائمة الأولى.")

    s.phase = "master_queued"
    s.summary = {
        **s.summary,
        "progress_pct": 1,
        "progress_message": "تم استلام القائمة الأولى وبدأ OCR المحلي.",
    }
    background_tasks.add_task(_run_master_upload_job, s, paths)
    return {"session_id": session_id, "phase": s.phase, "files": len(paths)}


@app.get("/api/upload/master/result")
def master_upload_result(session_id: str):
    s = _session(session_id)
    if s.phase == "error":
        raise HTTPException(500, detail=s.summary.get("progress_message", "فشل OCR."))
    if s.phase != "master_loaded":
        return JSONResponse(
            {"session_id": session_id, "phase": s.phase, "ready": False},
            status_code=202,
        )
    return {
        "session_id": session_id,
        "phase": s.phase,
        "ready": True,
        "master_people_count": len(s.master_people),
        "people": _people_payload(s),
        "messages": s.messages[-5:],
    }


@app.post("/api/upload/targets/multi/start", status_code=202)
def start_targets_upload(
    background_tasks: BackgroundTasks,
    session_id: str,
    files: List[UploadFile] = File(...),
):
    """Accept target files quickly and keep their local OCR independently pollable."""
    s = _session(session_id)
    paths: list[Path] = []
    for index, file in enumerate(files):
        if not file.filename:
            continue
        suffix = Path(file.filename).suffix.lower()
        if suffix not in {
            ".pdf", ".xlsx", ".xlsm", ".png", ".jpg", ".jpeg", ".heic", ".heif"
        }:
            continue
        destination = UPLOAD_ROOT / f"{session_id}_targets_async_{index}{suffix}"
        with destination.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        paths.append(destination)
    if not paths:
        raise HTTPException(400, detail="لا ملفات PDF/صور صالحة في القائمة الثانية.")

    s.phase = "targets_queued"
    s.summary = {
        **s.summary,
        "progress_pct": 1,
        "progress_message": "تم استلام القائمة الثانية وبدأ OCR المحلي.",
    }
    background_tasks.add_task(_run_targets_upload_job, s, paths)
    return {"session_id": session_id, "phase": s.phase, "files": len(paths)}


@app.get("/api/upload/targets/result")
def targets_upload_result(session_id: str):
    s = _session(session_id)
    if s.phase == "error":
        raise HTTPException(500, detail=s.summary.get("progress_message", "فشل OCR."))
    if s.phase != "names_extracted":
        return JSONResponse(
            {"session_id": session_id, "phase": s.phase, "ready": False},
            status_code=202,
        )
    return {
        "session_id": session_id,
        "phase": s.phase,
        "ready": True,
        "target_names": [target.to_dict() for target in s.target_names],
        "summary": s.summary,
        "messages": s.messages[-8:],
    }


@app.post("/api/upload/master")
def upload_master(session_id: str, file: UploadFile = File(...)):
    s = _session(session_id)
    if not file.filename:
        raise HTTPException(400, detail="الملف مطلوب.")
    suf = Path(file.filename).suffix.lower()
    if suf not in {".pdf", ".xlsx", ".xlsm"}:
        raise HTTPException(400, detail="صيغة الملف الرئيسي: PDF أو Excel فقط.")
    dest = UPLOAD_ROOT / f"{session_id}_master{suf}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        if suf == ".pdf":
            load_master(s, dest)
        else:
            load_master_many(s, [dest])
    except Exception as e:
        raise HTTPException(500, detail=f"فشل استخراج الملف الرئيسي: {e}") from e
    # Auto-save master index snapshot to Desktop
    try:
        from app.engine.export import export_master_index

        desk = Path.home() / "Desktop" / "ترتيب_أبو_علياء"
        desk.mkdir(parents=True, exist_ok=True)
        (desk / "فهرس_الملف_الرئيسي.xlsx").write_bytes(
            export_master_index(_people_payload(s))
        )
        s.messages.append(f"حُفظ فهرس الملف الرئيسي على سطح المكتب.")
    except Exception:
        pass
    return {
        "session_id": session_id,
        "phase": s.phase,
        "master_people_count": len(s.master_people),
        "people": _people_payload(s),
        "messages": s.messages[-5:],
    }


@app.post("/api/upload/master/multi")
def upload_master_multi(
    session_id: str, files: List[UploadFile] = File(...)
):
    """Upload multiple master PDFs/Excel files and merge into one index."""
    s = _session(session_id)
    if not files:
        raise HTTPException(400, detail="ارفع ملفًا واحدًا على الأقل.")
    paths: list[Path] = []
    for i, file in enumerate(files):
        if not file.filename:
            continue
        suf = Path(file.filename).suffix.lower()
        # Also accept images of roster pages as single-page masters via PDF conversion
        if suf not in {".pdf", ".xlsx", ".xlsm", ".png", ".jpg", ".jpeg", ".heic", ".heif"}:
            continue
        dest = UPLOAD_ROOT / f"{session_id}_master_{i}{suf}"
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        if suf in {".png", ".jpg", ".jpeg", ".heic", ".heif"}:
            # wrap image as 1-page PDF for the master pipeline
            try:
                dest = _image_to_pdf(dest, session_id, i)
            except Exception as e:
                raise HTTPException(500, detail=f"فشل تحويل صورة إلى PDF: {e}") from e
        paths.append(dest)
    if not paths:
        raise HTTPException(400, detail="لا ملفات PDF/Excel/صور صالحة.")
    try:
        load_master_many(s, paths)
    except Exception as e:
        raise HTTPException(500, detail=f"فشل دمج الملفات: {e}") from e
    return {
        "session_id": session_id,
        "phase": s.phase,
        "master_people_count": len(s.master_people),
        "files_merged": len(paths),
        "people": _people_payload(s),
        "messages": s.messages[-8:],
    }


def _image_to_pdf(image_path: Path, session_id: str, idx: int) -> Path:
    """Convert a roster page image into a single-page PDF."""
    from PIL import Image
    from app.engine.ocr import load_image_any

    img_path = load_image_any(image_path)
    im = Image.open(img_path).convert("RGB")
    out = UPLOAD_ROOT / f"{session_id}_master_img_{idx}.pdf"
    im.save(out, "PDF", resolution=150.0)
    return out


@app.post("/api/upload/targets")
def upload_targets(session_id: str, file: UploadFile = File(...)):
    s = _session(session_id)
    if not file.filename:
        raise HTTPException(400, detail="الملف مطلوب.")
    suf = Path(file.filename).suffix.lower() or ".bin"
    dest = UPLOAD_ROOT / f"{session_id}_targets{suf}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        load_targets(s, dest)
    except Exception as e:
        raise HTTPException(500, detail=f"فشل استخراج قائمة الأسماء: {e}") from e
    return {
        "session_id": session_id,
        "phase": s.phase,
        "target_names": [t.to_dict() for t in s.target_names],
        "summary": s.summary,
        "messages": s.messages[-5:],
    }


@app.post("/api/upload/targets/multi")
def upload_targets_multi(
    session_id: str, files: List[UploadFile] = File(...)
):
    """Multiple target lists (images/PDFs/Excel) merged into one candidate set."""
    from app.engine.pipeline import load_targets_many

    s = _session(session_id)
    paths: list[Path] = []
    for i, file in enumerate(files):
        if not file.filename:
            continue
        suf = Path(file.filename).suffix.lower() or ".bin"
        dest = UPLOAD_ROOT / f"{session_id}_targets_{i}{suf}"
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        paths.append(dest)
    if not paths:
        raise HTTPException(400, detail="لا ملفات صالحة.")
    try:
        load_targets_many(s, paths)
    except Exception as e:
        raise HTTPException(500, detail=f"فشل دمج قوائم الأسماء: {e}") from e
    return {
        "session_id": session_id,
        "phase": s.phase,
        "target_names": [t.to_dict() for t in s.target_names],
        "summary": s.summary,
        "files": len(paths),
        "messages": s.messages[-8:],
    }


@app.post("/api/demo/samples")
def load_demo_samples(mode: str = "pdf"):
    """
    mode=pdf  → scanned PDF + target photo (OCR path)
    mode=excel → clean Excel master (high-accuracy dates) + target photo
    mode=excel_targets → clean master Excel + clean target Excel (no OCR noise)
    """
    targets_img = SAMPLES / "target_names.png"
    targets_xlsx = SAMPLES / "targets_page3_overlap.xlsx"
    s = new_session()
    SESSIONS[s.session_id] = s
    try:
        if mode in ("excel", "excel_targets", "full"):
            xlsx = SAMPLES / "master_page3_clean.xlsx"
            if not xlsx.exists():
                raise HTTPException(404, detail="عينة Excel غير متوفرة.")
            load_master_many(s, [xlsx])
            note = "عينة Excel نظيفة (تواريخ عالية الدقة)."
        else:
            master = SAMPLES / "master_sample.pdf"
            if not master.exists():
                raise HTTPException(404, detail="عينة الملف الرئيسي غير متوفرة.")
            load_master(s, master)
            note = "عينات محلية (PDF ممسوح) — راجع الأسماء قبل الاعتماد."

        if mode in ("excel_targets", "full") and targets_xlsx.exists():
            load_targets(s, targets_xlsx)
            note += " + قائمة مطلوبين Excel."
        elif targets_img.exists() and mode not in ("excel_targets", "full"):
            load_targets(s, targets_img)
            if mode == "excel":
                note += " + صورة القائمة."
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=f"فشل تحميل العينات: {e}") from e
    return {
        "session_id": s.session_id,
        "phase": s.phase,
        "master_people_count": len(s.master_people),
        "people": _people_payload(s),
        "target_names": [t.to_dict() for t in s.target_names],
        "summary": s.summary,
        "messages": s.messages[-8:],
        "note": note,
    }


@app.post("/api/demo/full_rank")
def demo_full_rank():
    """
    One-click: clean Excel master + clean target list → auto-confirm → verify dates → rank.
    No guessing: only names present in both Excel files with exact match.
    """
    from app.engine.models import NameStatus, make_master_key
    from app.engine.pipeline import (
        apply_date_reviews,
        apply_name_corrections,
        collect_dates_for_targets,
    )
    from app.engine.session_store import save_session_snapshot

    master_xlsx = SAMPLES / "master_page3_clean.xlsx"
    targets_xlsx = SAMPLES / "targets_page3_overlap.xlsx"
    if not master_xlsx.exists() or not targets_xlsx.exists():
        raise HTTPException(404, detail="ملفات العينة النظيفة غير متوفرة.")

    s = new_session()
    SESSIONS[s.session_id] = s
    load_master_many(s, [master_xlsx])
    load_targets(s, targets_xlsx)

    corrections = []
    for t in s.target_names:
        if t.status == NameStatus.VERIFIED and t.matched_master_name:
            corrections.append({"id": t.id, "action": "confirm"})
        elif t.matched_master_name and t.confidence >= 0.92:
            corrections.append(
                {"id": t.id, "action": "set_name", "name": t.matched_master_name}
            )
    apply_name_corrections(s, corrections)
    collect_dates_for_targets(s)

    reviews = []
    for t in s.target_names:
        if t.status != NameStatus.VERIFIED:
            continue
        key = make_master_key(t.display_name)
        if key in s.master_people:
            reviews.append({"master_key": key, "action": "verify_all"})
    apply_date_reviews(s, reviews)
    run_ranking(s, auto_verify_dates=False)

    try:
        save_session_snapshot(
            s.session_id,
            {
                "phase": s.phase,
                "summary": s.summary,
                "results": s.ranking_results,
                "messages": s.messages[-20:],
            },
        )
    except Exception:
        pass

    return {
        "session_id": s.session_id,
        "phase": s.phase,
        "master_people_count": len(s.master_people),
        "people": _people_payload(s),
        "target_names": [t.to_dict() for t in s.target_names],
        "results": s.ranking_results,
        "summary": s.summary,
        "messages": s.messages[-10:],
        "note": "تشغيل كامل على بيانات Excel النظيفة — ترتيب حتمي بدون OCR.",
    }


@app.post("/api/names/manual")
def names_manual(body: ManualNamesBody):
    s = _session(body.session_id)
    add_manual_target_names(s, body.names)
    return {
        "phase": s.phase,
        "target_names": [t.to_dict() for t in s.target_names],
        "summary": s.summary,
        "messages": s.messages[-5:],
    }


class AutoConfirmBody(BaseModel):
    session_id: str
    min_confidence: float = 0.97


@app.post("/api/names/auto_confirm")
def names_auto_confirm(body: AutoConfirmBody):
    """Cautious auto-confirm: high confidence + clear candidate gap only."""
    s = _session(body.session_id)
    auto_confirm_high_confidence(s, min_confidence=body.min_confidence)
    return {
        "phase": s.phase,
        "target_names": [t.to_dict() for t in s.target_names],
        "summary": s.summary,
        "messages": s.messages[-5:],
    }


@app.post("/api/names/review")
def names_review(body: CorrectionsBody):
    s = _session(body.session_id)
    apply_name_corrections(s, body.corrections)
    collect_dates_for_targets(s)
    dates_payload = []
    from app.engine.models import make_master_key
    from app.engine.normalize import soft_normalize_for_fuzzy

    for t in s.target_names:
        if t.status.value != "مؤكد":
            continue
        key = make_master_key(t.display_name)
        person = s.master_people.get(key)
        if not person and t.matched_master_name:
            person = s.master_people.get(make_master_key(t.matched_master_name))
        if not person:
            soft = soft_normalize_for_fuzzy(t.display_name)
            for mp in s.master_people.values():
                if soft_normalize_for_fuzzy(mp.original_name) == soft:
                    person = mp
                    break
        if not person:
            continue
        dates_payload.append(
            {
                "id": t.id,
                "name": person.original_name,
                "master_key": person.normalized_name,
                "pages": person.pages,
                "dates": [d.to_dict() for d in person.dates],
                "notes": person.notes_texts,
            }
        )
    return {
        "phase": s.phase,
        "target_names": [t.to_dict() for t in s.target_names],
        "dates_for_review": dates_payload,
        "messages": s.messages[-5:],
    }


@app.post("/api/master/rename")
def master_rename(body: RenameBody):
    s = _session(body.session_id)
    rename_person(s, body.old_key, body.new_name)
    return {
        "phase": s.phase,
        "people": _people_payload(s),
        "master_people_count": len(s.master_people),
        "messages": s.messages[-3:],
    }


@app.post("/api/upload/master/folder")
def upload_master_folder(body: FolderBody):
    """
    Load all PDF/Excel/images from a local folder (e.g. all scanned roster pages).
    Path must be absolute and under the user's home for safety.
    """
    s = _session(body.session_id)
    folder = Path(body.folder_path).expanduser().resolve()
    home = Path.home().resolve()
    try:
        folder.relative_to(home)
    except ValueError:
        raise HTTPException(400, detail="المسار يجب أن يكون داخل مجلد المستخدم فقط.")
    if not folder.is_dir():
        raise HTTPException(400, detail="المجلد غير موجود.")

    allowed = {".pdf", ".xlsx", ".xlsm", ".png", ".jpg", ".jpeg", ".heic", ".heif"}
    files = sorted(
        [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in allowed],
        key=lambda p: p.name,
    )
    if not files:
        raise HTTPException(400, detail="لا ملفات مدعومة في المجلد.")

    paths: list[Path] = []
    for i, src in enumerate(files):
        suf = src.suffix.lower()
        dest = UPLOAD_ROOT / f"{body.session_id}_folder_{i}{suf}"
        shutil.copy2(src, dest)
        if suf in {".png", ".jpg", ".jpeg", ".heic", ".heif"}:
            dest = _image_to_pdf(dest, body.session_id, i)
        paths.append(dest)

    try:
        load_master_many(s, paths)
    except Exception as e:
        raise HTTPException(500, detail=f"فشل دمج المجلد: {e}") from e

    return {
        "session_id": body.session_id,
        "phase": s.phase,
        "files_merged": len(paths),
        "master_people_count": len(s.master_people),
        "people": _people_payload(s),
        "messages": s.messages[-8:],
    }


@app.post("/api/dates/review")
def dates_review(body: DateReviewsBody):
    s = _session(body.session_id)
    apply_date_reviews(s, body.reviews)
    return {"phase": s.phase, "messages": s.messages[-5:]}


@app.post("/api/dates/bulk_verify_safe")
def dates_bulk_safe(body: SessionOnly):
    s = _session(body.session_id)
    bulk_verify_safe_dates(s)
    return {"phase": s.phase, "messages": s.messages[-3:], "summary": s.summary}


@app.post("/api/rank")
def rank(body: RankBody):
    s = _session(body.session_id)
    collect_dates_for_targets(s)
    run_ranking(s, auto_verify_dates=body.auto_verify_dates)
    try:
        from app.engine.session_store import save_session_snapshot

        save_session_snapshot(
            s.session_id,
            {
                "phase": s.phase,
                "summary": s.summary,
                "results": s.ranking_results,
                "messages": s.messages[-20:],
                "targets": [t.to_dict() for t in s.target_names],
            },
        )
    except Exception:
        pass
    return {
        "phase": s.phase,
        "results": s.ranking_results,
        "summary": s.summary,
        "messages": s.messages[-8:],
    }


@app.get("/api/snapshots")
def snapshots():
    from app.engine.session_store import list_snapshots

    return {"snapshots": list_snapshots()}


@app.get("/api/snapshots/{session_id}")
def snapshot_detail(session_id: str):
    from app.engine.session_store import load_session_snapshot

    data = load_session_snapshot(session_id)
    if not data:
        raise HTTPException(404, detail="لا لقطة محفوظة لهذه الجلسة.")
    return data


@app.get("/api/session/{session_id}")
def get_session(session_id: str):
    return _session(session_id).to_dict()


@app.get("/api/export/excel")
def export_xlsx(session_id: str):
    s = _session(session_id)
    if not s.ranking_results:
        raise HTTPException(400, detail="لا توجد نتائج للتصدير.")
    data = export_excel(s.ranking_results, s.summary)
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=tarteeb-abu-alyaa.xlsx"},
    )


@app.get("/api/export/pdf")
def export_pdf(session_id: str):
    s = _session(session_id)
    if not s.ranking_results:
        raise HTTPException(400, detail="لا توجد نتائج للتصدير.")
    data = export_pdf_simple(s.ranking_results, s.summary)
    return Response(
        data,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=tarteeb-abu-alyaa.pdf"},
    )


@app.get("/api/export/pdf/formal")
def export_pdf_official(
    session_id: str,
    unit_title: str = "",
    prepared_by: str = "",
):
    s = _session(session_id)
    if not s.ranking_results:
        raise HTTPException(400, detail="لا توجد نتائج للتصدير.")
    data = export_pdf_formal(
        s.ranking_results,
        s.summary,
        unit_title=unit_title,
        prepared_by=prepared_by,
    )
    return Response(
        data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=tarteeb-abu-alyaa-formal.pdf"
        },
    )


@app.get("/api/export/master")
def export_master(session_id: str):
    s = _session(session_id)
    if not s.master_people:
        raise HTTPException(400, detail="لا يوجد فهرس رئيسي للتصدير.")
    payload = _people_payload(s)
    data = export_master_index(payload)
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=tarteeb-master-index.xlsx"
        },
    )


@app.get("/api/export/text")
def export_text(session_id: str):
    s = _session(session_id)
    if not s.ranking_results:
        raise HTTPException(400, detail="لا توجد نتائج للتصدير.")
    text = export_ranking_text(s.ranking_results, s.summary)
    return Response(
        text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=tarteeb.txt"},
    )


@app.get("/api/export/audit")
def export_audit(session_id: str):
    s = _session(session_id)
    if not s.ranking_results:
        raise HTTPException(400, detail="لا توجد نتائج للتصدير.")
    data = export_audit_json(s.ranking_results, s.summary, s.messages[-50:])
    return Response(
        data,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=tarteeb-audit.json"},
    )


@app.post("/api/session/reset")
def reset_session(body: SessionOnly):
    """Start a fresh empty session (keeps old snapshots on disk)."""
    old = body.session_id
    if old in SESSIONS:
        del SESSIONS[old]
    s = new_session()
    SESSIONS[s.session_id] = s
    return {"session_id": s.session_id, "phase": s.phase, "message": "جلسة جديدة."}


@app.post("/api/compare")
def compare_two_api(body: dict):
    """Audit pairwise comparison between two ranked people by id or name."""
    from app.engine.dates import HijriDate, parse_hijri_date
    from app.engine.models import make_master_key
    from app.engine.ranking import RankPerson, compare_two

    s = _session(body.get("session_id") or "")
    a_name = (body.get("a") or "").strip()
    b_name = (body.get("b") or "").strip()
    if not a_name or not b_name:
        raise HTTPException(400, detail="حدّد الاسمين للمقارنة.")

    def find_person(name: str) -> RankPerson:
        key = make_master_key(name)
        mp = s.master_people.get(key)
        if not mp:
            for p in s.master_people.values():
                if p.original_name == name or make_master_key(p.original_name) == key:
                    mp = p
                    break
        if not mp:
            raise HTTPException(404, detail=f"غير موجود: {name}")
        from app.engine.dates import unique_dates_newest_first

        dates = unique_dates_newest_first(mp.dates, only_verified=False)
        return RankPerson(
            id=mp.normalized_name,
            original_name=mp.original_name,
            normalized_name=mp.normalized_name,
            dates=dates,
        )

    pa, pb = find_person(a_name), find_person(b_name)
    result = compare_two(pa, pb)
    result["a_name"] = pa.original_name
    result["b_name"] = pb.original_name
    result["a_dates"] = [d.display() for d in pa.dates]
    result["b_dates"] = [d.display() for d in pb.dates]
    return result


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

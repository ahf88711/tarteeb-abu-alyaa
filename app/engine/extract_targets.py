"""Conservative extraction and verification of photographed target names."""

from __future__ import annotations

import re
import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional

import pypdfium2 as pdfium

from .models import MasterPerson, NameStatus, TargetName, make_master_key
from .normalize import name_similarity, normalize_arabic_name
from .hybrid_ocr import (
    HybridRowInput,
    evidence_url_to_path,
    hybrid_ocr_model,
    verify_arabic_rows,
)
from .ocr import (
    OcrToken,
    highlight_score,
    load_image_any,
    ocr_consensus,
    ocr_max_side,
    orient_document_image,
    save_region_crop,
)

_NOISE = re.compile(
    r"(وزارة|المملكة|الداخلية|حرس|الحدود|قيادة|قطاع|بيان|التكميل|اليومي|"
    r"أفراد|المنفذ|التوقيع|الرتبة|الأسم|الاسم|معد|الملاحظات|إجازة|اجازة|"
    r"رخصة|تأخير|تاخير|موجود|نضار|بسم|الله|اللّه|الرحمن|الرحيم|الموافق|"
    r"يوم|الثلاثاء|الاثنين|الأحد|السبت|الخميس|الجمعة|الأربعاء|"
    r"عنه|لمدة|ساعة|ساعات|أيام|اعتبارا|اعتبارًا|الموجود)"
)
_RANK = re.compile(
    r"\b(عريف|جندي\s*أول|جندي\s*اول|جندي|جندى|رقيب|و\.?\s*رقيب|وكيل\s*رقيب|ملازم|نقيب)\b"
)


def _strip_non_name_fields(text: str) -> str:
    text = re.sub(r"^(الأسم|الاسم)\s*:\s*", "", text.strip())
    text = _RANK.sub(" ", text)
    text = re.sub(r"^[٠-٩0-9]+\s*[-–.]?\s*", "", text)
    return re.sub(r"\s+", " ", text).strip(" -–.،")


def _is_name_candidate(text: str) -> bool:
    value = normalize_arabic_name(_strip_non_name_fields(text))
    if not value or len(value) < 6:
        return False
    parts = [part for part in value.split() if part]
    if len(parts) < 2 or len(parts) > 6:
        return False
    if _NOISE.search(value):
        return False
    if sum(char.isdigit() or char in "٠١٢٣٤٥٦٧٨٩" for char in value) > 0:
        return False
    return all(re.search(r"[\u0600-\u06FF]", part) for part in parts)


def _combine_tokens(tokens: list[OcrToken]) -> list[tuple[str, OcrToken]]:
    """Recover full names when an OCR backend emits one token per word."""
    rows: list[list[OcrToken]] = []
    for token in sorted(tokens, key=lambda item: (item.cy, -item.x)):
        group = next(
            (
                row
                for row in rows
                if abs(sum(item.cy for item in row) / len(row) - token.cy)
                <= max(0.012, token.h * 0.65)
            ),
            None,
        )
        if group is None:
            rows.append([token])
        else:
            group.append(token)

    out: list[tuple[str, OcrToken]] = []
    for row in rows:
        ordered = sorted(row, key=lambda item: -item.x)
        # Existing line-level observations remain valuable.
        for token in ordered:
            cleaned = _strip_non_name_fields(token.text)
            if _is_name_candidate(cleaned):
                out.append((cleaned, token))

        # Join spatially adjacent word observations. Split at large gaps so a
        # rank/notes cell cannot silently enter the name.
        segments: list[list[OcrToken]] = []
        for token in ordered:
            if not re.search(r"[\u0600-\u06FF]", token.text):
                continue
            if not segments:
                segments.append([token])
                continue
            previous = segments[-1][-1]
            gap = previous.x - (token.x + token.w)
            if gap <= max(0.035, 1.8 * max(previous.h, token.h)):
                segments[-1].append(token)
            else:
                segments.append([token])
        for segment in segments:
            if len(segment) < 2:
                continue
            text = _strip_non_name_fields(" ".join(token.text for token in segment))
            if not _is_name_candidate(text):
                continue
            left = min(token.x for token in segment)
            right = max(token.x + token.w for token in segment)
            top = min(token.cy - token.h / 2 for token in segment)
            bottom = max(token.cy + token.h / 2 for token in segment)
            confidence = sum(token.confidence for token in segment) / len(segment)
            combined = OcrToken(
                text=text,
                x=left,
                y=1.0 - bottom,
                w=right - left,
                h=bottom - top,
                confidence=confidence,
                alternatives=[alt for token in segment for alt in token.alternatives][:6],
                agreement=min(token.agreement for token in segment),
                source="combined",
            )
            out.append((text, combined))
    return out


def extract_name_tokens(tokens: list[OcrToken]) -> list[tuple[str, OcrToken]]:
    candidates = _combine_tokens(tokens)
    # Keep the strongest reading for each normalized identity.
    best: dict[str, tuple[str, OcrToken]] = {}
    for text, token in candidates:
        key = make_master_key(text)
        current = best.get(key)
        strength = (token.agreement, token.confidence, len(text))
        if current is None or strength > (
            current[1].agreement,
            current[1].confidence,
            len(current[0]),
        ):
            best[key] = (text, token)
    return list(best.values())


def match_to_master(
    ocr_name: str,
    master: dict[str, MasterPerson],
    *,
    high: float = 0.97,
    ambiguous_gap: float = 0.10,
) -> tuple[NameStatus, float, list[dict], Optional[str]]:
    """Generate identity candidates; only an exact conservative key auto-verifies."""
    if not master:
        return NameStatus.NEEDS_REVIEW, 0.0, [], None
    key = make_master_key(ocr_name)
    if key and key in master:
        person = master[key]
        return (
            NameStatus.VERIFIED,
            1.0,
            [{
                "name": person.original_name,
                "normalized": person.normalized_name,
                "confidence": 1.0,
                "pages": person.pages,
            }],
            person.original_name,
        )

    scored = sorted(
        (
            (name_similarity(ocr_name, person.original_name), person)
            for person in master.values()
        ),
        key=lambda item: (-item[0], item[1].normalized_name),
    )
    scored = [item for item in scored if item[0] >= 0.52]
    if not scored:
        return NameStatus.NOT_IN_MASTER, 0.0, [], None
    best_score, best_person = scored[0]
    candidates = [
        {
            "name": person.original_name,
            "normalized": person.normalized_name,
            "confidence": round(score, 4),
            "pages": person.pages,
        }
        for score, person in scored[:7]
    ]
    if len(scored) > 1 and best_score >= 0.68:
        second_score, second_person = scored[1]
        if (
            second_person.normalized_name != best_person.normalized_name
            and best_score - second_score < ambiguous_gap
        ):
            return NameStatus.AMBIGUOUS, best_score, candidates, None
    if best_score >= 0.62:
        # Even a very strong fuzzy result remains a proposal. OCR confidence
        # and master similarity are not independent proof of identity.
        return NameStatus.NEEDS_REVIEW, best_score, candidates, best_person.original_name
    return NameStatus.NOT_IN_MASTER, best_score, candidates, None


def _hybrid_target_limit() -> int:
    try:
        configured = int(os.getenv("OPENAI_OCR_MAX_TARGET_ROWS", "60"))
    except ValueError:
        configured = 60
    return max(1, min(200, configured))


def _candidate_gap(target: TargetName) -> float:
    candidates = target.candidates or []
    if len(candidates) < 2:
        return 1.0 if candidates else 0.0
    return float(candidates[0].get("confidence") or 0.0) - float(
        candidates[1].get("confidence") or 0.0
    )


def apply_hybrid_target_verification(targets: list[TargetName]) -> None:
    """Confirm uncertain target identities only when local and remote evidence agree.

    The model is never allowed to invent an identity: it can select only an
    exact master candidate already proposed by the deterministic matcher. A
    disagreement is retained in audit metadata and remains human-review work.
    """
    inputs: list[HybridRowInput] = []
    by_id: dict[str, TargetName] = {}
    for target in targets:
        if len(inputs) >= _hybrid_target_limit():
            break
        crop = evidence_url_to_path(target.crop_path or "")
        candidate_names = tuple(
            str(candidate.get("name") or "").strip()
            for candidate in (target.candidates or [])[:7]
            if str(candidate.get("name") or "").strip()
        )
        if crop is None or not candidate_names:
            continue
        # Exact, high-consensus readings need no paid verifier call.
        bbox = target.bbox or {}
        if (
            target.status == NameStatus.VERIFIED
            and float(bbox.get("visual_confidence") or 0.0) >= 0.90
            and int(bbox.get("ocr_agreement") or 0) >= 2
        ):
            continue
        inputs.append(
            HybridRowInput(
                row_id=target.id,
                image_path=crop,
                candidates=candidate_names,
                local_name=target.original_name,
            )
        )
        by_id[target.id] = target

    report = verify_arabic_rows(inputs)
    for row_id, reading in report.readings.items():
        target = by_id.get(row_id)
        if target is None:
            continue
        bbox = target.bbox or {}
        target.bbox = bbox
        local_proposal = target.matched_master_name or ""
        selected = reading.selected_candidate
        selected_score = next(
            (
                float(candidate.get("confidence") or 0.0)
                for candidate in target.candidates
                if candidate.get("name") == selected
            ),
            0.0,
        )
        independent_similarity = (
            name_similarity(reading.transcription, selected) if selected else 0.0
        )
        local_similarity = name_similarity(target.original_name, selected) if selected else 0.0
        exact_independent_reading = bool(
            selected and make_master_key(reading.transcription) == make_master_key(selected)
        )
        agreed = bool(
            reading.legible
            and reading.confidence >= 0.95
            and selected
            and selected == local_proposal
            and target.status != NameStatus.AMBIGUOUS
            and selected_score >= 0.78
            and _candidate_gap(target) >= 0.10
            and local_similarity >= 0.80
            and (exact_independent_reading or independent_similarity >= 0.95)
        )
        decision = "confirmed" if agreed else "disagreement"
        if not reading.legible:
            decision = "unreadable"
        bbox["hybrid_verification"] = {
            "model": hybrid_ocr_model(),
            "decision": decision,
            "confidence": round(reading.confidence, 4),
            "transcription": reading.transcription,
            "selected_candidate": selected,
            "local_proposal": local_proposal,
        }
        if not agreed:
            continue
        target.status = NameStatus.VERIFIED
        target.matched_master_name = selected
        target.normalized_name = make_master_key(selected)
        target.confidence = round(
            min(
                0.995,
                0.38 * max(target.confidence, local_similarity)
                + 0.32 * selected_score
                + 0.30 * reading.confidence,
            ),
            4,
        )


def _render_target_pdf(path: Path) -> list[Path]:
    document = pdfium.PdfDocument(str(path))
    images: list[Path] = []
    try:
        for index in range(len(document)):
            page = document[index]
            width, height = page.get_size()
            scale = max(1.0, min(4.0, ocr_max_side() / max(width, height)))
            image = page.render(scale=scale).to_pil().convert("RGB")
            destination = Path(tempfile.mkstemp(prefix=f"targets_{index + 1}_", suffix=".png")[1])
            image.save(destination, "PNG")
            images.append(destination)
    finally:
        document.close()
    return images


def _extract_page(
    path: Path,
    master: dict[str, MasterPerson],
    *,
    page_number: int,
) -> list[TargetName]:
    oriented_path = orient_document_image(path)
    remove_oriented = oriented_path != path
    try:
        tokens = ocr_consensus(oriented_path)
        highlighted = [
            (token, highlight_score(oriented_path, token)) for token in tokens
        ]
        highlighted_tokens = [token for token, score in highlighted if score >= 0.035]
        selection_mode = "highlighted" if highlighted_tokens else "all_names"
        source_tokens = highlighted_tokens or tokens
        raw_names = extract_name_tokens(source_tokens)

        results: list[TargetName] = []
        for text, token in raw_names:
            key = make_master_key(text)
            status, match_conf, candidates, matched = match_to_master(text, master)
            visual_conf = max(0.0, min(1.0, token.confidence))
            identity_conf = 0.58 * visual_conf + 0.42 * match_conf
            if status == NameStatus.VERIFIED and (
                visual_conf < 0.82 or token.agreement < 2
            ):
                status = NameStatus.NEEDS_REVIEW
            crop_path = save_region_crop(
                oriented_path,
                x=token.x,
                top=token.cy - token.h / 2,
                w=token.w,
                h=token.h,
                prefix=f"target_p{page_number}",
                padding=0.012,
            )
            score = (
                highlight_score(oriented_path, token)
                if selection_mode == "highlighted"
                else 0.0
            )
            results.append(
                TargetName(
                    id=str(uuid.uuid4())[:8],
                    original_name=text,
                    normalized_name=key,
                    ocr_raw=token.text,
                    confidence=round(identity_conf, 4),
                    status=status,
                    crop_path=crop_path,
                    candidates=candidates,
                    matched_master_name=matched,
                    bbox={
                        "x": token.x,
                        "y": token.y,
                        "w": token.w,
                        "h": token.h,
                        "page": page_number,
                        "visual_confidence": round(visual_conf, 4),
                        "ocr_agreement": token.agreement,
                        "ocr_alternatives": token.alternatives,
                        "selection_mode": selection_mode,
                        "highlight_score": round(score, 4),
                    },
                )
            )
        apply_hybrid_target_verification(results)
        return results
    finally:
        if remove_oriented:
            oriented_path.unlink(missing_ok=True)


def extract_target_names(
    path: Path,
    master: dict[str, MasterPerson],
) -> list[TargetName]:
    """Extract every target page; highlighted rosters select highlights only."""
    temporary: list[Path] = []
    if path.suffix.lower() == ".pdf":
        pages = _render_target_pdf(path)
        temporary.extend(pages)
    else:
        loaded = load_image_any(path)
        pages = [loaded]
        if loaded != path:
            temporary.append(loaded)

    results: list[TargetName] = []
    try:
        for page_number, page in enumerate(pages, 1):
            results.extend(_extract_page(page, master, page_number=page_number))
    finally:
        for item in temporary:
            item.unlink(missing_ok=True)

    best: dict[str, TargetName] = {}
    for target in results:
        current = best.get(target.normalized_name)
        if current is None or target.confidence > current.confidence:
            best[target.normalized_name] = target
    return sorted(
        best.values(),
        key=lambda target: (
            int((target.bbox or {}).get("page", 0)),
            -float((target.bbox or {}).get("y", 0.0)),
        ),
    )

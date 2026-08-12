"""Cross-platform, confidence-aware Arabic OCR and image preprocessing."""

from __future__ import annotations

import json
import math
import os
import platform
import re
import shutil
import subprocess
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OCR_BIN = PROJECT_ROOT / "bin" / "ocr_vision"
OCR_SOURCE = PROJECT_ROOT / "bin" / "ocr_vision.swift"
EVIDENCE_ROOT = Path(tempfile.gettempdir()) / "tarteeb_abu_alyaa_evidence"
EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)


def ocr_max_side() -> int:
    """Bound OCR rasters for the active runtime without weakening consensus.

    Render's free CPU cannot finish three Tesseract passes on 3400–3600 px
    pages within the per-pass safety timeout. 2400 px keeps a photographed A4
    page legible while reducing its pixel count by roughly half. Deployments
    with more CPU can override the value through ``OCR_MAX_SIDE``.
    """
    render_runtime = bool(os.getenv("RENDER_EXTERNAL_URL") or os.getenv("RENDER"))
    default = 2400 if render_runtime else 3600
    try:
        configured = int(os.getenv("OCR_MAX_SIDE", str(default)))
    except ValueError:
        configured = default
    return max(1800, min(4200, configured))


def ocr_timeout_seconds() -> int:
    """Per-pass OCR timeout, extended on resource-constrained Render CPUs."""
    render_runtime = bool(os.getenv("RENDER_EXTERNAL_URL") or os.getenv("RENDER"))
    default = 300 if render_runtime else 180
    try:
        configured = int(os.getenv("OCR_TIMEOUT_SECONDS", str(default)))
    except ValueError:
        configured = default
    return max(60, min(600, configured))


def tesseract_environment() -> dict[str, str]:
    """Keep Tesseract within the CPU/RAM available to the active runtime."""
    env = os.environ.copy()
    render_runtime = bool(os.getenv("RENDER_EXTERNAL_URL") or os.getenv("RENDER"))
    default_threads = "1" if render_runtime else "2"
    env.setdefault("OMP_THREAD_LIMIT", default_threads)
    env.setdefault("OMP_NUM_THREADS", default_threads)
    return env


@dataclass
class OcrToken:
    text: str
    x: float
    y: float
    w: float
    h: float
    # Vision coordinates: origin bottom-left, normalized 0-1.
    confidence: float = 0.0
    alternatives: list[dict] = field(default_factory=list)
    agreement: int = 1
    source: str = "unknown"

    @property
    def cy(self) -> float:
        """Center y from top (0=top, 1=bottom) for reading order."""
        return 1.0 - (self.y + self.h / 2)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2


def _compiler_environment() -> dict[str, str]:
    env = os.environ.copy()
    cache = Path(tempfile.gettempdir()) / "tarteeb_abu_alyaa_swift_cache"
    cache.mkdir(parents=True, exist_ok=True)
    env["CLANG_MODULE_CACHE_PATH"] = str(cache)
    env["SWIFT_MODULECACHE_PATH"] = str(cache)
    return env


def ensure_ocr_binary() -> Path:
    """Compile the macOS Vision helper when available and stale/missing."""
    if platform.system() != "Darwin":
        raise RuntimeError("Apple Vision OCR متاح على macOS فقط")
    compiler = shutil.which("swiftc")
    if not compiler:
        raise RuntimeError("مترجم Swift غير متوفر لتشغيل Apple Vision OCR")
    if not OCR_SOURCE.exists():
        raise RuntimeError("مصدر محرك Apple Vision OCR غير موجود")
    current = (
        OCR_BIN.exists()
        and os.access(OCR_BIN, os.X_OK)
        and OCR_BIN.stat().st_mtime >= OCR_SOURCE.stat().st_mtime
    )
    if current:
        return OCR_BIN
    OCR_BIN.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [compiler, "-O", str(OCR_SOURCE), "-o", str(OCR_BIN)],
        capture_output=True,
        text=True,
        env=_compiler_environment(),
        timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"تعذر بناء Apple Vision OCR: {proc.stderr[:600]}")
    return OCR_BIN


def _has_tesseract_arabic() -> bool:
    exe = shutil.which("tesseract")
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, "--list-langs"], capture_output=True, text=True, timeout=15
        )
        return proc.returncode == 0 and "ara" in proc.stdout.split()
    except Exception:
        return False


def available_ocr_backends() -> list[str]:
    out: list[str] = []
    if platform.system() == "Darwin" and shutil.which("swiftc"):
        out.append("vision")
    if _has_tesseract_arabic():
        out.append("tesseract")
    return out


def heic_to_png(path: Path) -> Path:
    """Convert HEIC via sips or pillow-heif without silently returning empties."""
    out = Path(tempfile.mkstemp(suffix=".png")[1])
    if shutil.which("sips"):
        proc = subprocess.run(
            ["sips", "-s", "format", "png", str(path), "--out", str(out)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0 and out.exists() and out.stat().st_size > 1024:
            return out
    try:
        from pillow_heif import register_heif_opener  # type: ignore

        register_heif_opener()
        with Image.open(path) as image:
            ImageOps.exif_transpose(image).save(out, "PNG")
        if out.stat().st_size <= 1024:
            raise RuntimeError("ناتج التحويل فارغ")
        return out
    except Exception as exc:
        out.unlink(missing_ok=True)
        raise RuntimeError(f"تعذر تحويل HEIC: {exc}") from exc


def load_image_any(path: Path) -> Path:
    suffix = path.suffix.lower()
    if suffix in {".heic", ".heif"}:
        return heic_to_png(path)
    if suffix in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}:
        return path
    raise RuntimeError(f"صيغة صورة غير مدعومة: {suffix}")


def _tesseract_osd_rotation(path: Path) -> Optional[int]:
    """Return the clockwise correction requested by Tesseract OSD."""
    exe = shutil.which("tesseract")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, str(path), "stdout", "--psm", "0", "-l", "osd"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception:
        return None
    output = f"{proc.stdout}\n{proc.stderr}"
    match = re.search(r"Rotate:\s*(0|90|180|270)\b", output)
    if not match:
        return None
    return int(match.group(1))


def orient_document_image(path: Path) -> Path:
    """Return an upright raster for OCR, preserving highlight coordinates.

    EXIF is applied first. For landscape phone photos, Tesseract OSD chooses
    the clockwise correction; a clockwise 90-degree fallback covers scanners
    that strip orientation metadata and OSD confidence. The returned temporary
    image is then used consistently for OCR, highlight scoring, and crops.
    """
    image_path = load_image_any(path)
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source.convert("RGB"))
        rotation = _tesseract_osd_rotation(image_path)
        if rotation is None:
            rotation = 90 if image.width > image.height * 1.08 else 0
        if rotation == 0:
            return image_path
        destination = Path(tempfile.mkstemp(prefix="oriented_", suffix=".png")[1])
        # PIL positive angles are counter-clockwise; OSD Rotate is clockwise.
        image.rotate(-rotation, expand=True, resample=Image.Resampling.BICUBIC).save(
            destination, "PNG"
        )
    return destination


def _resize_for_ocr(image: Image.Image, max_side: Optional[int] = None) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    max_side = max_side or ocr_max_side()
    width, height = image.size
    scale = min(1.0, max_side / max(width, height))
    if scale < 1.0:
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )
    return image


def preprocess_image(path: Path, *, preset: str = "enhanced") -> Path:
    """Create a bounded, lossless OCR variant while preserving page geometry."""
    with Image.open(path) as source:
        image = _resize_for_ocr(source.convert("RGB"))
        if preset == "raw":
            result = image
        else:
            gray = ImageOps.grayscale(image)
            gray = ImageOps.autocontrast(gray, cutoff=1)
            if preset == "binary":
                gray = gray.filter(ImageFilter.MedianFilter(size=3))
                # A conservative global threshold; uncertain digits are later
                # checked against the non-binary passes.
                result = gray.point(lambda value: 255 if value >= 176 else 0)
            else:
                gray = ImageEnhance.Contrast(gray).enhance(1.35)
                gray = ImageEnhance.Sharpness(gray).enhance(1.45)
                result = gray.filter(ImageFilter.UnsharpMask(radius=1, percent=115, threshold=3))
        out = Path(tempfile.mkstemp(suffix=".png")[1])
        result.save(out, "PNG", optimize=False)
    return out


def _vision_ocr(path: Path, *, source: str) -> list[OcrToken]:
    binary = ensure_ocr_binary()
    proc = subprocess.run(
        [str(binary), str(path)],
        capture_output=True,
        text=True,
        timeout=ocr_timeout_seconds(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"فشل Apple Vision OCR: {proc.stderr[:600]}")
    tokens: list[OcrToken] = []
    for line in proc.stdout.splitlines():
        try:
            obj = json.loads(line)
            text = str(obj.get("text") or "").strip()
            if not text:
                continue
            tokens.append(
                OcrToken(
                    text=text,
                    x=float(obj["x"]),
                    y=float(obj["y"]),
                    w=float(obj["w"]),
                    h=float(obj["h"]),
                    confidence=float(obj.get("confidence") or 0.0),
                    alternatives=list(obj.get("alternatives") or []),
                    source=source,
                )
            )
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    return tokens


def _tesseract_ocr(path: Path, *, source: str, psm: int = 6) -> list[OcrToken]:
    exe = shutil.which("tesseract")
    if not exe or not _has_tesseract_arabic():
        raise RuntimeError("Tesseract العربي غير متوفر")
    with Image.open(path) as image:
        width, height = image.size
    proc = subprocess.run(
        [
            exe,
            str(path),
            "stdout",
            "-l",
            "ara+eng",
            "--psm",
            str(psm),
            "tsv",
        ],
        capture_output=True,
        text=True,
        timeout=ocr_timeout_seconds(),
        env=tesseract_environment(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"فشل Tesseract OCR: {proc.stderr[:600]}")
    tokens: list[OcrToken] = []
    lines = proc.stdout.splitlines()
    if not lines:
        return tokens
    header = lines[0].split("\t")
    positions = {name: idx for idx, name in enumerate(header)}
    needed = {"left", "top", "width", "height", "conf", "text"}
    if not needed.issubset(positions):
        return tokens
    for row in lines[1:]:
        cols = row.split("\t")
        try:
            text = cols[positions["text"]].strip()
            conf = float(cols[positions["conf"]]) / 100.0
            left = int(cols[positions["left"]])
            top = int(cols[positions["top"]])
            box_w = int(cols[positions["width"]])
            box_h = int(cols[positions["height"]])
        except (ValueError, IndexError):
            continue
        if not text or conf < 0:
            continue
        tokens.append(
            OcrToken(
                text=text,
                x=left / width,
                y=1.0 - ((top + box_h) / height),
                w=box_w / width,
                h=box_h / height,
                confidence=max(0.0, min(1.0, conf)),
                source=source,
            )
        )
    return tokens


def _ocr_key(text: str) -> str:
    value = unicodedata.normalize("NFKC", text)
    value = value.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    value = re.sub(r"[\s\u200e\u200f]+", " ", value).strip()
    return value


def _geometry_match(a: OcrToken, b: OcrToken) -> bool:
    vertical = abs(a.cy - b.cy) <= max(0.012, 0.65 * max(a.h, b.h))
    horizontal = abs(a.cx - b.cx) <= max(0.025, 0.55 * max(a.w, b.w))
    return vertical and horizontal


def merge_ocr_observations(tokens: Iterable[OcrToken]) -> list[OcrToken]:
    """Fuse repeated OCR passes without hiding disagreements."""
    groups: list[list[OcrToken]] = []
    for token in sorted(tokens, key=lambda item: (item.cy, -item.x)):
        placed = False
        for group in groups:
            if _geometry_match(group[0], token) and (
                _ocr_key(group[0].text) == _ocr_key(token.text)
                or abs(len(group[0].text) - len(token.text)) <= 2
            ):
                group.append(token)
                placed = True
                break
        if not placed:
            groups.append([token])

    merged: list[OcrToken] = []
    for group in groups:
        votes: dict[str, list[OcrToken]] = {}
        for token in group:
            votes.setdefault(_ocr_key(token.text), []).append(token)
        winning_key, winning = max(
            votes.items(),
            key=lambda item: (
                len({token.source for token in item[1]}),
                sum(token.confidence for token in item[1]),
                len(item[0]),
            ),
        )
        best = max(winning, key=lambda token: token.confidence)
        sources = {token.source for token in winning}
        all_sources = {token.source for token in group}
        agreement = len(sources)
        mean_conf = sum(token.confidence for token in winning) / len(winning)
        consensus_ratio = agreement / max(1, len(all_sources))
        confidence = min(
            1.0,
            0.55 * best.confidence + 0.25 * mean_conf + 0.20 * consensus_ratio,
        )
        if agreement == 1 and len(all_sources) > 1:
            confidence *= 0.82
        alternatives: list[dict] = list(best.alternatives)
        for key, observations in votes.items():
            if key == winning_key:
                continue
            alternative = max(observations, key=lambda token: token.confidence)
            alternatives.append(
                {"text": alternative.text, "confidence": alternative.confidence}
            )
        merged.append(
            OcrToken(
                text=best.text,
                x=sum(token.x for token in winning) / len(winning),
                y=sum(token.y for token in winning) / len(winning),
                w=sum(token.w for token in winning) / len(winning),
                h=sum(token.h for token in winning) / len(winning),
                confidence=confidence,
                alternatives=alternatives[:6],
                agreement=agreement,
                source="consensus",
            )
        )
    return merged


def ocr_consensus(
    path: Path,
    *,
    presets: tuple[str, ...] = ("raw", "enhanced", "binary"),
) -> list[OcrToken]:
    """Run independent image/OCR passes and retain disagreement evidence."""
    image_path = load_image_any(path)
    backends = available_ocr_backends()
    if not backends:
        raise RuntimeError(
            "لا يوجد محرك OCR عربي. يلزم Apple Vision على macOS أو Tesseract بلغة ara."
        )
    observations: list[OcrToken] = []
    errors: list[str] = []
    for preset in presets:
        variant = preprocess_image(image_path, preset=preset)
        try:
            if "vision" in backends:
                try:
                    observations.extend(
                        _vision_ocr(variant, source=f"vision:{preset}")
                    )
                except Exception as exc:
                    errors.append(str(exc))
            if "tesseract" in backends:
                try:
                    psm = 6 if preset != "binary" else 11
                    observations.extend(
                        _tesseract_ocr(
                            variant, source=f"tesseract:{preset}:{psm}", psm=psm
                        )
                    )
                except Exception as exc:
                    errors.append(str(exc))
        finally:
            variant.unlink(missing_ok=True)
    if not observations:
        detail = " | ".join(dict.fromkeys(errors))[:1200]
        raise RuntimeError(f"فشلت جميع قراءات OCR. {detail}")
    return merge_ocr_observations(observations)


def ocr_image(path: Path, *, preprocess: bool = True) -> list[OcrToken]:
    """Compatibility entrypoint; uses at least two independent passes."""
    presets = ("enhanced", "binary") if preprocess else ("raw", "enhanced")
    return ocr_consensus(path, presets=presets)


def tokens_to_lines(tokens: list[OcrToken], y_tol: float = 0.012) -> list[str]:
    if not tokens:
        return []
    ordered = sorted(tokens, key=lambda token: (token.cy, -token.x))
    lines: list[list[OcrToken]] = []
    centers: list[float] = []
    for token in ordered:
        target: Optional[int] = None
        for index, center in enumerate(centers):
            if abs(token.cy - center) <= max(y_tol, token.h * 0.55):
                target = index
                break
        if target is None:
            lines.append([token])
            centers.append(token.cy)
        else:
            lines[target].append(token)
            centers[target] = sum(item.cy for item in lines[target]) / len(lines[target])
    return [
        " ".join(token.text for token in sorted(line, key=lambda item: -item.x))
        for line in lines
    ]


def ocr_full_text(path: Path) -> str:
    return "\n".join(tokens_to_lines(ocr_consensus(path)))


def save_region_crop(
    image_path: Path,
    *,
    x: float,
    top: float,
    w: float,
    h: float,
    prefix: str,
    padding: float = 0.01,
) -> str:
    """Persist an auditable source crop and return its safe API URL."""
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source.convert("RGB"))
        width, height = image.size
        left_px = max(0, int((x - padding) * width))
        top_px = max(0, int((top - padding) * height))
        right_px = min(width, int((x + w + padding) * width))
        bottom_px = min(height, int((top + h + padding) * height))
        if right_px <= left_px or bottom_px <= top_px:
            return ""
        crop = image.crop((left_px, top_px, right_px, bottom_px))
        filename = f"{prefix}_{uuid.uuid4().hex[:12]}.jpg"
        destination = EVIDENCE_ROOT / filename
        crop.save(destination, "JPEG", quality=94)
    return f"/api/evidence/{filename}"


def highlight_score(image_path: Path, token: OcrToken, padding: float = 0.006) -> float:
    """Measure yellow/green highlighter pixels behind a token region."""
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source.convert("HSV"))
        width, height = image.size
        left = max(0, int((token.x - padding) * width))
        top = max(0, int((token.cy - token.h / 2 - padding) * height))
        right = min(width, int((token.x + token.w + padding) * width))
        bottom = min(height, int((token.cy + token.h / 2 + padding) * height))
        if right <= left or bottom <= top:
            return 0.0
        region = image.crop((left, top, right, bottom))
        pixels = list(region.getdata())
    if not pixels:
        return 0.0
    colored = sum(
        1
        for hue, saturation, value in pixels
        if 24 <= hue <= 82 and saturation >= 70 and value >= 105
    )
    return colored / len(pixels)

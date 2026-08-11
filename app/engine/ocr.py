"""OCR utilities: macOS Vision CLI + preprocessing."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageEnhance, ImageOps

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OCR_BIN = PROJECT_ROOT / "bin" / "ocr_vision"


@dataclass
class OcrToken:
    text: str
    x: float
    y: float
    w: float
    h: float
    # Vision coords: origin bottom-left, normalized 0-1

    @property
    def cy(self) -> float:
        """Center y from top (0=top, 1=bottom) for easier reading order."""
        return 1.0 - (self.y + self.h / 2)


def ensure_ocr_binary() -> Path:
    """Compile Vision OCR helper if missing."""
    if OCR_BIN.exists() and os.access(OCR_BIN, os.X_OK):
        return OCR_BIN

    OCR_BIN.parent.mkdir(parents=True, exist_ok=True)
    src = PROJECT_ROOT / "bin" / "ocr_vision.swift"
    src.write_text(
        '''import Foundation
import Vision
import AppKit

let path = CommandLine.arguments[1]
let url = URL(fileURLWithPath: path)
guard let img = NSImage(contentsOf: url),
      let tiff = img.tiffRepresentation,
      let rep = NSBitmapImageRep(data: tiff),
      let cgImage = rep.cgImage else {
    fputs("failed to load image\\n", stderr)
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["ar", "en"]
request.usesLanguageCorrection = true

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
try handler.perform([request])

guard let observations = request.results else { exit(0) }
let sorted = observations.sorted { a, b in
    let aY = a.boundingBox.origin.y
    let bY = b.boundingBox.origin.y
    if abs(aY - bY) > 0.008 { return aY > bY }
    return a.boundingBox.origin.x > b.boundingBox.origin.x
}
for obs in sorted {
    if let candidate = obs.topCandidates(1).first {
        let box = obs.boundingBox
        print(String(format: "%.4f,%.4f,%.4f,%.4f|%@",
            box.origin.x, box.origin.y, box.size.width, box.size.height,
            candidate.string as NSString))
    }
}
''',
        encoding="utf-8",
    )
    subprocess.run(
        ["swiftc", "-O", str(src), "-o", str(OCR_BIN)],
        check=True,
        capture_output=True,
        text=True,
    )
    return OCR_BIN


def preprocess_image(path: Path, *, contrast: float = 1.6) -> Path:
    """Return path to enhanced JPEG (temp)."""
    im = Image.open(path)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    # Limit huge photos
    max_side = 2400
    w, h = im.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        im = im.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    gray = ImageOps.grayscale(im)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(contrast)
    gray = ImageEnhance.Sharpness(gray).enhance(1.4)
    out = Path(tempfile.mkstemp(suffix=".jpg")[1])
    gray.convert("RGB").save(out, "JPEG", quality=92)
    return out


def heic_to_png(path: Path) -> Path:
    """Convert HEIC via sips (macOS) or Pillow if available."""
    out = Path(tempfile.mkstemp(suffix=".png")[1])
    if shutil.which("sips"):
        r = subprocess.run(
            ["sips", "-s", "format", "png", str(path), "--out", str(out)],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return out
    try:
        from pillow_heif import register_heif_opener  # type: ignore

        register_heif_opener()
        im = Image.open(path)
        im.save(out, "PNG")
        return out
    except Exception as e:
        raise RuntimeError(f"تعذر تحويل HEIC: {e}") from e


def load_image_any(path: Path) -> Path:
    """Normalize any supported image/PDF page image path to PNG/JPEG."""
    suf = path.suffix.lower()
    if suf in {".heic", ".heif"}:
        return heic_to_png(path)
    if suf in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}:
        return path
    raise RuntimeError(f"صيغة غير مدعومة: {suf}")


def ocr_image(path: Path, *, preprocess: bool = True) -> list[OcrToken]:
    """Run Vision OCR; return tokens with bounding boxes."""
    ensure_ocr_binary()
    img_path = load_image_any(path)
    work = preprocess_image(img_path) if preprocess else img_path
    try:
        proc = subprocess.run(
            [str(OCR_BIN), str(work)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        if preprocess and work != img_path:
            try:
                work.unlink(missing_ok=True)
            except Exception:
                pass
    if proc.returncode != 0:
        raise RuntimeError(f"فشل OCR: {proc.stderr[:500]}")
    tokens: list[OcrToken] = []
    for line in proc.stdout.splitlines():
        if "|" not in line:
            continue
        meta, text = line.split("|", 1)
        text = text.strip()
        if not text:
            continue
        parts = meta.split(",")
        if len(parts) != 4:
            continue
        x, y, w, h = map(float, parts)
        tokens.append(OcrToken(text=text, x=x, y=y, w=w, h=h))
    return tokens


def tokens_to_lines(tokens: list[OcrToken], y_tol: float = 0.012) -> list[str]:
    """Cluster tokens into reading lines (top→bottom, RTL within line)."""
    if not tokens:
        return []
    ordered = sorted(tokens, key=lambda t: (round(t.cy / y_tol), -t.x))
    lines: list[list[OcrToken]] = []
    current: list[OcrToken] = []
    current_y: Optional[float] = None
    for t in ordered:
        if current_y is None or abs(t.cy - current_y) <= y_tol:
            current.append(t)
            current_y = t.cy if current_y is None else (current_y + t.cy) / 2
        else:
            lines.append(current)
            current = [t]
            current_y = t.cy
    if current:
        lines.append(current)

    result = []
    for line in lines:
        # RTL: higher x first (right side of page)
        line_sorted = sorted(line, key=lambda t: -t.x)
        result.append(" ".join(t.text for t in line_sorted))
    return result


def ocr_full_text(path: Path) -> str:
    return "\n".join(tokens_to_lines(ocr_image(path)))

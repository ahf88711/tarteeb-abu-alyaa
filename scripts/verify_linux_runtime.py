#!/usr/bin/env python3
"""Fail a Docker build unless the complete local Arabic OCR runtime works."""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _tesseract_languages() -> set[str]:
    proc = subprocess.run(
        ["tesseract", "--list-langs"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return {
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip() and not line.lower().startswith("list of available languages")
    }


def main() -> None:
    if platform.system() != "Linux":
        raise SystemExit("This verification gate must run inside the Linux image.")

    languages = _tesseract_languages()
    missing = {"ara", "eng"} - languages
    if missing:
        raise SystemExit(f"Missing Tesseract languages: {sorted(missing)}")

    # Verify native/image imports that are required by uploaded PDF/HEIC files.
    import numpy  # noqa: F401
    import pypdfium2  # noqa: F401
    from PIL import Image  # noqa: F401
    from pillow_heif import register_heif_opener

    register_heif_opener()

    from app.engine.export import _arabic_fonts
    from app.engine.ocr import available_ocr_backends, ocr_consensus

    backends = available_ocr_backends()
    if "tesseract" not in backends:
        raise SystemExit(f"Local Arabic Tesseract backend unavailable: {backends}")

    regular_font = Path("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf")
    if not regular_font.is_file():
        raise SystemExit(f"Arabic Noto font is missing: {regular_font}")
    if _arabic_fonts()[0] != "Ar":
        raise SystemExit("Arabic PDF export did not register the Noto font.")

    sample = ROOT / "data" / "samples" / "target_names_preview.jpg"
    if not sample.is_file():
        raise SystemExit(f"OCR verification sample is missing: {sample}")
    tokens = ocr_consensus(sample, presets=("raw", "enhanced"))
    combined = " ".join(token.text for token in tokens)
    arabic_count = len(re.findall(r"[\u0600-\u06ff]", combined))
    if len(tokens) < 5 or arabic_count < 10:
        raise SystemExit(
            "Arabic OCR produced insufficient output "
            f"(tokens={len(tokens)}, arabic_chars={arabic_count})."
        )

    print(
        json.dumps(
            {
                "platform": platform.platform(),
                "ocr_backend": "tesseract",
                "languages": sorted({"ara", "eng"} & languages),
                "tokens": len(tokens),
                "arabic_chars": arabic_count,
                "heic": "ready",
                "arabic_pdf_font": "NotoNaskhArabic",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

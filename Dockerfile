# syntax=docker/dockerfile:1

# Debian Bookworm keeps the Tesseract package set reproducible on Render/Linux.
FROM python:3.11-slim-bookworm AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# OCR is entirely local. ara/eng are required by app.engine.ocr, libheif1
# supports iPhone HEIC uploads, and Noto Naskh is used by Arabic PDF exports.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        fontconfig \
        fonts-noto-core \
        libheif1 \
        tesseract-ocr \
        tesseract-ocr-ara \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY bin ./bin
COPY data ./data
COPY scripts ./scripts
COPY pytest.ini Makefile README.md ./

# Force Docker/Render builds to execute the Linux OCR and full test gates.
FROM runtime-base AS test
COPY tests ./tests
RUN python3 scripts/verify_linux_runtime.py \
    && RENDER=true RUN_REAL_OCR_TESTS=1 python3 -m pytest tests -q \
    && touch /tmp/tarteeb-tests-passed

FROM runtime-base AS final
COPY --from=test /tmp/tarteeb-tests-passed /tmp/tarteeb-tests-passed

RUN addgroup --system app \
    && adduser --system --ingroup app --home /home/app app \
    && chown -R app:app /home/app

ENV HOME=/home/app
USER app

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8765') + '/api/health', timeout=3).read()" || exit 1

CMD ["sh", "-c", "exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8765}"]

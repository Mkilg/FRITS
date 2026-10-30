# FRITS dev image: the same Python, OCR engine and German language model on every machine.
FROM python:3.12-slim

# Tesseract reads photos of tickets and receipts; "deu" adds German language data.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*

# uv installs exactly the versions pinned in uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependencies first, code second: Docker caches this layer until uv.lock changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

# The German NER model used for redaction (about 570 MB, downloaded once at build time).
RUN uv pip install --python /opt/venv/bin/python \
    "de_core_news_lg @ https://github.com/explosion/spacy-models/releases/download/de_core_news_lg-3.8.0/de_core_news_lg-3.8.0-py3-none-any.whl"

COPY src ./src
COPY tests ./tests
RUN uv sync --frozen

ENTRYPOINT ["frits"]
CMD ["--help"]

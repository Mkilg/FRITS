# FRITS
Fantastisches Rechnungsstellungs IT System

Drop a note, email or photo into a folder and get an invoice draft back. Names, addresses and bank details are replaced on your own computer before any AI sees the text. Mascot: a French fry.

Status: phase 1 (command-line core pipeline). See the [plan](https://claude.ai/artifact/Y4geXZKysfhSEwjndteRQv) and the [phase 1 build spec](https://claude.ai/artifact/59NcqVbQoNgyqpGdYX7NgJ).

## For Fritz: the Mac app

A ready-to-run `FRITS.app` (no installing, works offline, also from a USB stick) is built automatically. See [packaging/macos](packaging/macos/README.md).

## Development with Docker

You need Docker Desktop on your Mac. Everything else (Python, the OCR engine, the German language model) lives inside the image.

```sh
docker compose build            # build the image (first time takes a few minutes)
docker compose run --rm app     # runs `frits --help`
docker compose run --rm --entrypoint pytest app -q   # run the tests
```

What the files do:

- `Dockerfile` is the recipe for the image: start from Python 3.12, add Tesseract, install the locked dependencies with uv, download the German spaCy model, copy the code.
- `compose.yaml` says how to run it: your `src/`, `tests/` and `workspace/` folders are mounted into the container, so code edits show up without a rebuild. Rebuild only when dependencies change.
- `docker compose --profile local up -d ollama` starts a local AI model server for testing the offline option.

API keys for development go in `.env` (copy `.env.example`). It is git-ignored and must never be committed.

## Without Docker

```sh
uv sync
uv run pytest -q
uv run frits --help
```

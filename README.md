# FRITS
Fantastisches Rechnungsschreibungs IT-System

Drop a note, email or photo into a folder and get an invoice draft back. Names, addresses and bank details are replaced on your own computer before any AI sees the text. Mascot: a French fry.

Status: v1 in progress. The core pipeline, the browser app and the setup assistant are built. See the [plan](https://claude.ai/artifact/Y4geXZKysfhSEwjndteRQv) and the [phase 1 build spec](https://claude.ai/artifact/59NcqVbQoNgyqpGdYX7NgJ).

## Try FRITS

### With Docker (easiest for development)

```sh
cp .env.example .env            # then put your Claude, OpenAI or OpenRouter key into .env
docker compose build            # first time takes a few minutes
docker compose up ui            # then open http://localhost:8765 in your browser
```

Your FRITS folder is `workspace/` in this repository: drop notes into `workspace/inbox/`.
Inside Docker, FRITS can't open Mail or Finder on your Mac (a container is a closed box),
so "In Mail öffnen" falls back to a normal email link. The API key comes from `.env`
because the container has no access to your Mac's Keychain.

### Directly on your Mac (how Fritz will run it)

```sh
brew install uv tesseract tesseract-lang
uv sync
uv pip install "de_core_news_lg @ https://github.com/explosion/spacy-models/releases/download/de_core_news_lg-3.8.0/de_core_news_lg-3.8.0-py3-none-any.whl"
uv run frits ui                 # opens the browser; the setup assistant starts
```

Your FRITS folder is `~/Documents/FRITS` (set `FRITS_HOME` to use another folder).
Here the key goes into the Keychain, and Mail opens ready-to-send messages.

### In the browser, without installing anything

FRITS also runs completely inside a browser tab: https://mkilg.github.io/FRITS/ (once GitHub Pages
is switched on). There is no FRITS server; your data stays in that browser on that device, so
download a backup now and then (button at the bottom). How it works: [browser/README.md](browser/README.md).

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

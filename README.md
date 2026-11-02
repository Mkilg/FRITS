# FRITS
Fantastisches Rechnungsschreibungs IT-System

Drop a note, email or photo into a folder and get an invoice draft back. Names, addresses and bank details are replaced on your own computer before any AI sees the text. Mascot: a French fry.

Status: v1 is done and in use; FRITS keeps growing (Steuer-Quest, Notfall-Chat, a small game around the paperwork). It is a free, non-commercial hobby project for freelancers in Germany, made by Marie Kilg as a private person. FRITS is not a project of the ARD or any of its broadcasters.

## Hinweis

FRITS ist ein kostenloses, nicht-kommerzielles Hobbyprojekt. Es hilft dir, deine **eigenen** Rechnungen und Unterlagen zu ordnen, und rechnet Zahlen für dich zusammen (Rechnungen, EÜR, Umsatzsteuer-Voranmeldung, Unterlagen für die Steuerberatung).

**FRITS ist keine Steuer-, Rechts- oder Finanzberatung und ersetzt sie nicht.** Prüf bitte alle Zahlen, bevor du sie ans Finanzamt schickst, und frag im Zweifel deine Steuerberatung. Du nutzt FRITS auf eigene Verantwortung. Es gilt die [MIT-Lizenz](LICENSE); für Schäden haftet die Autorin nur bei Vorsatz und grober Fahrlässigkeit.

*In English: FRITS helps you with your own paperwork. It is not tax, legal or financial advice. Check every number before you file it. Use at your own risk; see [LICENSE](LICENSE).*

## Datenschutz in FRITS

- **Alles bleibt auf deinem Computer.** Kunden, Rechnungen und Belege liegen in deinem FRITS-Ordner (`~/Documents/FRITS`). Es gibt keinen FRITS-Server, kein Konto und keine Statistik, die irgendwohin geht.
- **KI nur mit deinem eigenen Schlüssel**, bei einem Anbieter, den du wählst (Claude, OpenAI, OpenRouter), oder eine lokale KI auf deinem Mac, bei der nichts den Computer verlässt.
- **Vorher wird geschwärzt.** Namen, Adressen, IBANs, E-Mails, Telefon- und Steuernummern werden auf deinem Computer durch Platzhalter wie `[PERSON_1]` ersetzt, bevor eine KI den Text sieht.
- **Du siehst, was rausgeht.** Im Notfall-Chat zeigt FRITS den geschwärzten Text, und erst dein Klick schickt ihn ab. Die Fehler-Mail schickst du selbst, darin stehen nur Version und Computertyp.

Wenn du FRITS beruflich mit Kundendaten nutzt, bist du für diese Daten verantwortlich (DSGVO). Bei der Claude- und OpenAI-API ist ein Auftragsverarbeitungsvertrag Teil der Geschäftsbedingungen. Geschwärzter Text gilt rechtlich meist noch als personenbezogen, das Risiko ist aber viel kleiner.

## Lizenzen

FRITS steht unter der [MIT-Lizenz](LICENSE). Klänge und Maskottchen-Zeichnungen sind selbst gemacht (Code), die gemalten Bilder hat Marie mit einer Bild-KI erstellt. Die Prüfregeln für E-Rechnungen in `tests/fixtures/en16931/` stehen unter EUPL 1.2. Die Schrift DejaVu und die Bibliotheken haben eigene freie Lizenzen: Die Mac-App bringt sie alle in `LIZENZEN.txt` mit (erzeugt von `tools/third_party_licenses.py`).

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

### Directly on your Mac

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

## Mini-Paket „Erst schwärzen, dann fragen“

Ein kleines Paket zum Weitergeben, ohne FRITS: ein Kurs mit Fritte in sechs Etappen, die
Schwärz-Werkbank (Text rein, geschwärzt raus, Namen zurück) und die Prompts zum Kopieren. Es sind
nur ein paar HTML-Dateien: Doppelklick auf `index.html` genügt, vom USB-Stick oder auf einer
Webseite, ohne Internet. Die Seiten dürfen technisch keine Verbindung ins Netz aufbauen.

- Online: https://mkilg.github.io/FRITS/mini/
- Als ZIP: https://mkilg.github.io/FRITS/mini/erst-schwaerzen-dann-fragen.zip
- Quelle: [minipaket/](minipaket/), gepflegt mit `uv run python tools/minipaket.py` (holt die
  Prompts aus dem Kurs in FRITS und die Bilder aus `static/bilder`; `--zip X.zip` baut das Paket).
  Tests: `node --test minipaket/tests/*.test.cjs` und `node minipaket/tests/e2e.cjs`.

## The Mac app (no installing)

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

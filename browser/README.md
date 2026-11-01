# FRITS im Browser

The same FRITS, without installing anything: the real Python app runs inside the browser tab
(Pyodide = Python compiled to WebAssembly). There is no FRITS server. The web page is only the
program; every person's invoices, customers and receipts stay in their own browser.

```sh
uv run python browser/build.py --serve      # build into browser/dist/, open http://localhost:8800/
node --test browser/tests/*.test.mjs        # quick tests, no browser
node browser/tests/e2e.cjs                  # full walk-through in Chromium (needs Playwright)
```

On `main`, the workflow `Browser-Version` builds, tests and publishes it on GitHub Pages.

## How it fits together

```
index.html + shell.js   the page: loading screen, the app frame, backup buttons
   │  starts
   ├─ py-worker.js       Pyodide + FRITS (frits.browser.runtime). Files in IndexedDB (/frits),
   │                     the AI key in a separate store (/frits-geheim, never in a backup)
   └─ helper-worker.js   the two things Python can't do here: the NER model for redaction
                         (transformers.js) and text from photos (tesseract.js)
sw.js                    the service worker: every request of the app frame (…/app/…) goes to
                         FRITS in this tab instead of the network; adds the isolation headers
```

A click in the app frame: the browser asks for `/FRITS/app/invoices` → the service worker
passes it to the page → the page to the Python worker → `frits.browser.asgi.handle()` runs the
normal FastAPI app → the HTML goes back the same way. Links are written as `/invoices` in FRITS,
so the bridge adds the `/FRITS/app` prefix on the way out and removes it on the way in.

**Why Atomics.** FRITS's Python code is synchronous, but the model and Tesseract in the browser
are asynchronous. The Python worker therefore waits with `Atomics.wait` until the helper worker
has written its answer into shared memory. Shared memory needs cross-origin isolation (COOP/COEP
headers); GitHub Pages can't send them, so the service worker adds them. The first visit reloads
the page once for that.

**AI calls** go straight from the Python worker to the provider (the Anthropic SDK uses a
synchronous XMLHttpRequest in a worker). Claude needs the header
`anthropic-dangerous-direct-browser-access`, which `runtime._claude_from_the_browser()` adds.
The key is the user's own and only goes to the provider.

**Redaction** keeps all four layers and the leak check of `frits.redact`. Only the NER layer
changes: `Xenova/bert-base-multilingual-cased-ner-hrl` (PER/ORG/LOC, German among 10 languages,
quantized, about 180 MB, downloaded once from Hugging Face and kept by the browser) instead of
spaCy's `de_core_news_lg`. If the model can't load, FRITS sends nothing to an AI, exactly like
the Mac app without its model.

## What is different from the Mac app

| | Mac app | Browser |
|---|---|---|
| Install | 800 MB download, unzip, Gatekeeper | open a link |
| Data | `~/Documents/FRITS` | the browser's storage on this device; backup = zip download |
| AI key | macOS Keychain | browser storage, separate from the data, never in a backup |
| Inbox folder / "In Mail öffnen" | yes | no: drag files in or paste text |
| Start | 2-3 s | about 15 s (first visit also downloads about 35 MB) |
| Redaction model | spaCy, offline from the start | 180 MB once, then offline |

## Pinned inputs

`build.py` pins the Pyodide release; pure-Python wheels come from PyPI in the versions of
`uv.lock`; compiled ones (pydantic-core, lxml, Pillow, …) from the Pyodide release; JavaScript
parts from `package-lock.json`. Nothing is loaded from a CDN at run time except the model.

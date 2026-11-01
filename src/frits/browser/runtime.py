"""Start FRITS inside Pyodide and answer the app frame's requests.

The browser page calls, in this order:

    from frits.browser import runtime
    runtime.install_shims(services)     # before anything imports FastAPI or FRITS
    runtime.start(prefix="/FRITS/app")
    await runtime.handle(method, url, headers, body)   # once per request

`services` is a small JavaScript object with blocking calls: `ner(text)` returns the entities
of a German NER model as JSON, `ocr(png_bytes)` returns the text of a photo. Both run in a
helper worker; the Python side waits for them (see browser/README.md: "Warum Atomics").

What stays the same as on the Mac: the four redaction layers, the leak check before sending, the
workspace layout (customers as Markdown, invoices in SQLite). What differs: the files live in the
browser's storage, the API key in a separate store that is never part of a backup.
"""

from __future__ import annotations

import importlib
import io
import json
import sys
import types
import zipfile
from pathlib import Path

WORKSPACE = Path("/frits")
KEYS_FILE = Path("/frits-geheim/keys.json")

_app = None
_prefix = ""
_services = None


# --- shims: things a browser tab does not have ----------------------------------------------


def install_shims(services=None) -> None:
    global _services
    _services = services
    _no_threads()
    _cryptodome_alias()
    sys.modules["keyring"] = _keyring_module()
    sys.modules["keyring.errors"] = sys.modules["keyring"].errors
    sys.modules["pytesseract"] = _pytesseract_module()
    sys.modules.setdefault("pi_heif", _no_heic_module())
    _claude_from_the_browser()


def _no_heic_module() -> types.ModuleType:
    """iPhone photos (HEIC) need a decoder Pyodide doesn't ship. Say so instead of crashing."""
    mod = types.ModuleType("pi_heif")

    def register_heif_opener() -> None:
        from frits.ingest import IngestError

        raise IngestError(
            "HEIC-Fotos (vom iPhone) kann FRITS im Browser noch nicht lesen. Speichere das Foto "
            "als JPG (in Fotos: Exportieren) oder mach ein Bildschirmfoto davon."
        )

    mod.register_heif_opener = register_heif_opener
    return mod


def _claude_from_the_browser() -> None:
    """Claude answers a browser directly only when asked to (CORS). The key is the user's own."""
    try:
        import anthropic
    except ImportError:
        return
    original = anthropic.Anthropic.__init__

    def init(self, *args, default_headers=None, **kwargs):
        headers = {"anthropic-dangerous-direct-browser-access": "true", **(default_headers or {})}
        original(self, *args, default_headers=headers, **kwargs)

    anthropic.Anthropic.__init__ = init


def _no_threads() -> None:
    """A browser tab has no threads. FastAPI runs plain functions "in a thread": run them inline."""
    import anyio.to_thread

    async def run_sync(func, *args, abandon_on_cancel=False, cancellable=None, limiter=None):
        return func(*args)

    anyio.to_thread.run_sync = run_sync


def _cryptodome_alias() -> None:
    """pyzipper wants `Cryptodome`; Pyodide ships the same library under the name `Crypto`."""
    try:
        import Cryptodome  # noqa: F401

        return
    except ImportError:
        pass
    import Crypto

    sys.modules["Cryptodome"] = Crypto
    for sub in (
        "Cipher",
        "Cipher.AES",
        "Hash",
        "Hash.SHA1",
        "Hash.HMAC",
        "Protocol",
        "Protocol.KDF",
        "Random",
        "Util",
        "Util.Padding",
        "Util.Counter",
    ):
        try:
            sys.modules["Cryptodome." + sub] = importlib.import_module("Crypto." + sub)
        except ImportError:
            pass


def _keyring_module() -> types.ModuleType:
    """Instead of the macOS Keychain: a small file in its own browser store, never backed up."""
    mod = types.ModuleType("keyring")
    errors = types.ModuleType("keyring.errors")

    class KeyringError(Exception):
        pass

    errors.KeyringError = KeyringError
    mod.errors = errors

    def _load() -> dict:
        try:
            return json.loads(KEYS_FILE.read_text())
        except (OSError, ValueError):
            return {}

    def get_password(service: str, name: str) -> str | None:
        return _load().get(f"{service}:{name}")

    def set_password(service: str, name: str, value: str) -> None:
        data = _load()
        data[f"{service}:{name}"] = value
        KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEYS_FILE.write_text(json.dumps(data))

    def delete_password(service: str, name: str) -> None:
        data = _load()
        data.pop(f"{service}:{name}", None)
        KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEYS_FILE.write_text(json.dumps(data))

    mod.get_password = get_password
    mod.set_password = set_password
    mod.delete_password = delete_password
    return mod


def _pytesseract_module() -> types.ModuleType:
    """Instead of the Tesseract program: tesseract.js in the helper worker (same engine, WASM)."""
    mod = types.ModuleType("pytesseract")

    class TesseractNotFoundError(OSError):
        pass

    mod.TesseractNotFoundError = TesseractNotFoundError
    mod.pytesseract = types.SimpleNamespace(tesseract_cmd="tesseract.js")

    def image_to_string(img, lang: str = "deu+eng", **_) -> str:
        if _services is None:
            raise TesseractNotFoundError("Texterkennung ist im Browser nicht geladen.")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        answer = _call(_services.ocr, buf.getvalue(), lang)
        if answer.get("error"):
            raise TesseractNotFoundError(answer["error"])
        return answer.get("text", "")

    mod.image_to_string = image_to_string
    return mod


def _call(fn, *args) -> dict:
    """Blocking call into the helper worker. Answers are JSON text."""
    try:
        from pyodide.ffi import to_js

        args = tuple(to_js(a) if isinstance(a, bytes) else a for a in args)
    except ImportError:  # tests on a normal computer pass Python functions
        pass
    return json.loads(str(fn(*args)))


# --- NER: a spaCy-shaped answer from the browser model ----------------------------------------


class _Token:
    __slots__ = ("idx", "tag_", "text", "whitespace_")

    def __init__(self, idx: int, text: str, whitespace: str):
        self.idx, self.text, self.whitespace_ = idx, text, whitespace
        self.tag_ = "NE"  # the model only marks names, places and firms


class _Ent(list):
    """A span like spaCy's: its tokens, label_, text, start_char and end_char."""

    def __init__(self, tokens: list[_Token], label: str, text: str, start: int, end: int):
        super().__init__(tokens)
        self.label_, self.text, self.start_char, self.end_char = label, text, start, end


class _Doc:
    def __init__(self, ents: list[_Ent]):
        self.ents = ents


def doc_from_entities(text: str, entities: list[dict]) -> _Doc:
    """Turn [{"start", "end", "label": "PER"|"LOC"|"ORG"}] into what redact._ner_spans reads."""
    ents = []
    for e in entities:
        start, end, label = int(e["start"]), int(e["end"]), str(e["label"]).upper()
        label = {"PERSON": "PER", "B-PER": "PER", "I-PER": "PER"}.get(label, label)
        if label not in ("PER", "LOC", "ORG") or not (0 <= start < end <= len(text)):
            continue
        tokens, pos = [], start
        for word in text[start:end].split():
            idx = text.index(word, pos)
            after = idx + len(word)
            ws = text[after] if after < len(text) and text[after].isspace() else ""
            tokens.append(_Token(idx, word, ws))
            pos = after
        if tokens:
            ents.append(_Ent(tokens, label, text[start:end], start, end))
    return _Doc(ents)


class BrowserNER:
    """Callable like a spaCy pipeline: nlp(text).ents."""

    def __init__(self, services):
        self.services = services

    def __call__(self, text: str) -> _Doc:
        answer = _call(self.services.ner, text)
        if answer.get("error"):
            raise RuntimeError(
                "Das Sprachmodell zum Schwärzen ist in diesem Browser nicht geladen "
                f"({answer['error']}). Ohne es schickt FRITS aus Sicherheit nichts an eine KI."
            )
        return doc_from_entities(text, answer.get("entities", []))


# --- the app ----------------------------------------------------------------------------------


def start(prefix: str = "", today=None):
    global _app, _prefix
    from frits import umgebung
    from frits.settings import Workspace

    umgebung.IM_BROWSER = True
    from frits.web.app import create_app

    def nlp_loader():
        if _services is None:
            raise RuntimeError("Das Sprachmodell zum Schwärzen ist nicht geladen.")
        return BrowserNER(_services)

    kwargs = {"ws": Workspace(WORKSPACE), "nlp_loader": nlp_loader, "stop": lambda: None}
    if today is not None:
        kwargs["today"] = today
    _app = create_app(**kwargs)
    _prefix = prefix.rstrip("/")
    return _app


async def handle(method: str, url: str, headers, body) -> dict:
    from frits.browser.asgi import handle as asgi_handle

    if hasattr(headers, "to_py"):
        headers = headers.to_py()
    if hasattr(body, "to_py"):
        body = body.to_py()
    body = bytes(body or b"")
    return await asgi_handle(_app, method, url, [tuple(h) for h in headers], body, _prefix)


def has_any_key() -> bool:
    try:
        return bool(json.loads(KEYS_FILE.read_text()))
    except (OSError, ValueError):
        return False


# --- backup: the whole FRITS folder as one zip, same as on the Mac ------------------------------


def backup_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(WORKSPACE.rglob("*")):
            if path.is_file():
                zf.write(path, Path("FRITS") / path.relative_to(WORKSPACE))
    return buf.getvalue()


def restore_zip(data: bytes) -> int:
    """Replace the browser's FRITS folder with a backup. Returns the number of files restored."""
    import shutil

    if hasattr(data, "to_py"):
        data = data.to_py()
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            raise ValueError("Die Sicherung ist leer.")
        top = {n.split("/", 1)[0] for n in names}
        strip = len(top) == 1 and all("/" in n for n in names)
        for name in names:
            parts = Path(name).parts[1:] if strip else Path(name).parts
            if not parts or ".." in parts or Path(name).is_absolute():
                raise ValueError("Die Sicherung enthält einen ungültigen Dateinamen.")
        if WORKSPACE.exists():
            for child in WORKSPACE.iterdir():
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        count = 0
        for name in names:
            parts = Path(name).parts[1:] if strip else Path(name).parts
            target = WORKSPACE.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(name))
            count += 1
    start(_prefix)  # fresh app state (token, caches) for the restored folder
    return count

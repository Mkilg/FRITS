"""The FRITS app in the browser. It runs only on Fritz's computer (127.0.0.1) and only he can use it.

Every page is plain HTML rendered here. Every button that changes something is a form POST
that carries a secret token, so another website open in the same browser can't press
FRITS's buttons for him (that attack is called CSRF).
"""

from __future__ import annotations

import os
import re
import secrets
import signal
import threading
from collections.abc import Callable
from contextlib import ExitStack, asynccontextmanager
from dataclasses import replace
from datetime import date
from decimal import Decimal
from importlib.resources import as_file, files
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup
from PIL import Image
from starlette.exceptions import HTTPException as StarletteHTTPException

from frits import (
    __version__,
    about,
    aufgaben,
    emails,
    euer,
    expenses,
    gutschriften,
    haustier,
    setup,
    sicherung,
    store,
    system,
    umgebung,
    ustva,
)
from frits.billing import (
    CUSTOMER_TAX,
    DEFAULT_PATTERN,
    DOMESTIC_RATES,
    EARLIER_TAX,
    BillingError,
    Draft,
    add_earlier,
    approve,
    blockers,
    cancel,
    copy_invoice,
    discard_draft,
    draft_totals,
    limit_check,
    list_invoices,
    load_draft,
    mark_paid,
    new_draft,
    next_number,
    parse_amount,
    reviewed_line,
    save_draft,
    service_period,
    set_customer,
    unit_price,
    valid_pattern,
)
from frits.customers import (
    COUNTRIES,
    EU_COUNTRIES,
    Customer,
    load_customers,
    note_id,
    save_note,
    vat_place,
)
from frits.ingest import SUPPORTED, IngestError, extract_text
from frits.providers import KEY_ENV, ProviderError, get_api_key, make_provider, store_api_key
from frits.quotes import accept_quote, create_quote, decline_quote, list_quotes
from frits.reminders import create_reminder
from frits.render import eur, qty
from frits.settings import Settings, Workspace
from frits.spiel import stand as spiel_stand
from frits.web.haustier import fritte_datei, register_haustier

UNITS = ("Std", "km", "Stk", "pauschal")
PROVIDERS = {
    "claude": "Claude (Anthropic)",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "ollama": "Lokale KI (Ollama, ohne Internet)",
}
FALLBACK_LINES = {
    "draft_ready": ("done", "Dein Entwurf ist fertig. Schau ihn dir an, wenn es dir passt."),
    "needs_you": ("warning", "Hier brauche ich kurz deine Entscheidung. Keine Eile."),
    "welcome_back": (
        "welcome",
        "Schön, dass du da bist. Hier ist, was seit letztem Mal passiert ist.",
    ),
    "error_calm": (
        "warning",
        "Das hat nicht geklappt. Deine Daten sind sicher, nichts ist verloren.",
    ),
    "feierabend": ("feierabend", "Alles notiert, nichts geht verloren. Feierabend!"),
    "invoice_sent": ("done", "Erledigt! Die Rechnung ist raus. Ein Punkt weniger im Kopf."),
}


# ---------- mascot and sounds come from their own modules when installed ----------


def mascot_markup(mood: str) -> Markup:
    try:
        from frits.mascot import mascot_svg

        return Markup(mascot_svg(mood))
    except (ImportError, ValueError, FileNotFoundError):
        return Markup('<span class="fry-fallback" aria-hidden="true">🍟</span>')


# Painted pictures (static/bilder/<name>.png) replace the drawn ones once they are there.
MOOD_PICTURES = {
    "welcome": "fritte-willkommen",
    "thinking": "fritte-nachdenken",
    "done": "fritte-fertig",
    "warning": "fritte-achtung",
    "feierabend": "fritte-feierabend",
}


def painted_pictures(static: Path) -> dict[str, str]:
    """Picture name -> its address, e.g. "fritte-fertig" -> "/static/bilder/fritte-fertig.webp"."""
    folder = Path(static) / "bilder"
    if not folder.is_dir():
        return {}
    found = {}
    for path in sorted(folder.iterdir()):
        # Only plain names, so a file name can never break the page.
        if path.suffix in (".png", ".webp") and re.fullmatch(r"[a-z0-9-]+", path.stem):
            found.setdefault(path.stem, f"/static/bilder/{path.name}")
    return found


def picture_tag(static: Path, pictures: dict[str, str]):
    """<img> for a painted picture with its own width and height, so the page keeps the
    space for it while it loads (CSS sets the shown width, the browser keeps the ratio)."""

    def tag(name: str, css_class: str) -> Markup:
        url = pictures.get(name)
        if url is None:
            return Markup("")
        try:
            with Image.open(Path(static) / "bilder" / url.rsplit("/", 1)[1]) as im:
                width, height = im.size
        except OSError:
            return Markup('<img class="{}" src="{}" alt="">').format(css_class, url)
        return Markup('<img class="{}" src="{}" alt="" width="{}" height="{}">').format(
            css_class, url, width, height
        )

    return tag


NAV_SYMBOLS = {
    "/": "symbol-eingang",
    "/drafts": "symbol-entwuerfe",
    "/invoices": "symbol-rechnungen",
    "/quotes": "symbol-angebote",
    "/expenses": "symbol-ausgaben",
    "/customers": "symbol-kunden",
    "/steuer": "symbol-steuer",
    "/settings": "symbol-einstellungen",
}
STAMP_PICTURES = {"stempel-rund": ".stempel", "stempel-oval": ".stempel.stempel-fertig"}
# The painted stamps are a ring and an oval: the word sits in the middle.
STAMP_SHAPES = {
    "stempel-rund": "width: 5.4rem; height: 5.4rem; padding: 0; font-size: 0.62rem;"
    " letter-spacing: 0.05em; display: inline-flex; align-items: center; justify-content: center;",
    "stempel-oval": "width: 7.2rem; height: 4.4rem; padding: 0; font-size: 0.75rem;",
}


def picture_css(pictures: dict[str, str]) -> Markup:
    """CSS that puts the painted pictures in place. Without pictures FRITS looks the same,
    just with plain colours instead of painted ones."""

    def url(name: str) -> str:
        return f"url({pictures[name]})"

    rules = []
    if "schreibtisch-tag" in pictures:
        rules.append(f":root {{ --tisch-bild: {url('schreibtisch-tag')}; }}")
    if "feierabend-tisch" in pictures:
        rules.append(f":root {{ --feierabend-bild: {url('feierabend-tisch')}; }}")
    for href, name in NAV_SYMBOLS.items():
        if name in pictures:
            rules.append(
                f'nav a[href="{href}"]::before {{ display: inline-block; background-image: {url(name)}; }}'
            )
    for name, selector in STAMP_PICTURES.items():
        if name in pictures:
            rules.append(
                f"body {selector} {{ background: {url(name)} center / 100% 100% no-repeat;"
                f" border-color: transparent; {STAMP_SHAPES[name]} }}"
            )
    return Markup("\n".join(rules))


def mascot_or_picture(pictures: dict[str, str]):
    def mascot(mood: str) -> Markup:
        name = MOOD_PICTURES.get(mood)
        if name in pictures:
            # Fixed size in the HTML, so nothing jumps while the picture loads
            return Markup('<img class="fritte" src="{}" alt="" width="84" height="112">').format(
                pictures[name]
            )
        return mascot_markup(mood)

    return mascot


def mascot_line(key: str) -> tuple[str, str]:
    try:
        from frits.mascot import line

        data = line(key)
        return data["mood"], data["text"]
    except (ImportError, KeyError, FileNotFoundError):
        return FALLBACK_LINES.get(key, FALLBACK_LINES["welcome_back"])


def _sounds_dir():
    try:
        from frits.sounds import sounds_dir
    except ImportError:
        return None
    folder = sounds_dir()
    return folder if folder.is_dir() else None


def _music_moods() -> dict:
    try:
        from frits.sounds import MOODS
    except ImportError:
        return {}
    return MOODS


def _music_track(mood: str, path: str) -> str:
    try:
        from frits.sounds import track_for
    except ImportError:
        return "buero-block"
    return track_for(mood, path)


# ---------- formatting for templates ----------


def de_date(value: str | date | None) -> str:
    if not value:
        return ""
    day = date.fromisoformat(value[:10]) if isinstance(value, str) else value
    return day.strftime("%d.%m.%Y")


def de_number(value: Decimal | None) -> str:
    return "" if value is None else qty(value)


def _templates() -> Jinja2Templates:
    env = Environment(
        loader=PackageLoader("frits.web", "templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.filters.update(eur=lambda v: eur(Decimal(v)) if v not in (None, "") else "", qty=de_number)
    env.filters["de_date"] = de_date
    env.filters["price_in"] = price_in
    env.filters["elster"] = elster_amount
    env.globals.update(
        mascot=mascot_markup,
        units=UNITS,
        customer_tax=CUSTOMER_TAX,
        countries=COUNTRIES,
        providers=PROVIDERS,
        supported=",".join(sorted(SUPPORTED)),
        im_browser=lambda: umgebung.IM_BROWSER,
    )
    return Jinja2Templates(env=env)


# ---------- the app ----------


def _stop_server() -> None:
    # Ask uvicorn to shut down the same way Ctrl+C does, a moment after the page is sent.
    threading.Timer(0.5, os.kill, args=(os.getpid(), signal.SIGINT)).start()


def create_app(
    ws: Workspace | None = None,
    provider_factory: Callable[[Settings], object] | None = None,
    nlp_loader: Callable[[], object] | None = None,
    today: Callable[[], date] = date.today,
    stop: Callable[[], None] = _stop_server,
) -> FastAPI:
    ws = (ws or Workspace.default()).ensure()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.files.close()

    app = FastAPI(title="FRITS", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.token = secrets.token_urlsafe(32)
    app.state.flashes = []
    app.state.undo = None  # the last step that can still be taken back
    app.state.nlp = None
    app.state.tab_waiting = False  # a "FRITS ist aus" tab is waiting to come back
    app.state.files = ExitStack()
    templates = _templates()

    def settings() -> Settings:
        return Settings.load(ws.settings_file)

    def provider_for(current: Settings):
        if provider_factory:
            return provider_factory(current)
        if current.strict_local and current.provider != "ollama":
            raise ProviderError("Strikter Modus ist an: Es wird nur die lokale KI benutzt.")
        return make_provider(current.provider, current.models)

    def ai_ready(current: Settings) -> bool:
        """Can FRITS hand redacted text to an AI right now? (Ollama is checked when used.)"""
        if provider_factory or current.provider == "ollama":
            return True
        if current.strict_local or current.provider not in KEY_ENV:
            return False
        return bool(get_api_key(current.provider))

    def nlp():
        if app.state.nlp is None:
            from frits.redact import load_ner

            app.state.nlp = (nlp_loader or load_ner)()
        return app.state.nlp

    def db():
        return store.connect(ws.db)

    def customers() -> list[Customer]:
        return load_customers(ws.customers)

    def customer_by(value: str | None) -> Customer | None:
        """Find a customer by note id or by the name printed on an invoice."""
        return next((c for c in customers() if value in (c.id, c.display_name, c.name)), None)

    def flash(
        text: str,
        kind: str = "ok",
        cue: str | None = None,
        stamp: str | None = None,
        undo: dict | None = None,
        link: tuple[str, str] | None = None,
        aktion: dict | None = None,
        feld: str | None = None,
    ) -> None:
        """A short note on the next page. `stamp` puts an ink stamp on it, e.g. "Bezahlt".
        `undo` remembers how to take the step back and puts a "Rückgängig" button on the note:
        undo beats "Are you sure?" (people click OK by habit; a way back always works).
        `aktion` puts a way forward on the note (Steuer-Quest): {"url", "knopf", "felder",
        "link", "link_text"}; the note then stays until it is closed."""
        if undo:
            app.state.undo = undo
        note = {"text": text, "kind": kind, "cue": cue, "stamp": stamp, "undo": bool(undo)}
        if link:
            note["link"], note["link_text"] = link
        note["aktion"] = aktion
        note["feld"] = feld  # the form field this note is about: marked red and focused
        app.state.flashes.append(note)

    app.state.hinweise = []  # small notes other parts add to any page, e.g. Fritte's cards

    def page(request: Request, name: str, **context) -> HTMLResponse:
        current = settings()
        flashes, app.state.flashes = app.state.flashes, []
        for hinweis in app.state.hinweise:
            flashes += hinweis(request.url.path, current)
        nav = context.pop("nav", None) or request.url.path  # which menu entry is lit
        context.update(
            request=request,
            token=app.state.token,
            settings=current,
            flashes=flashes,
            sounds=app.state.sounds_url if current.sound else None,
            music=_music_track(current.music, request.url.path),
            moods=_music_moods(),
            nav=nav,
            ai_on=ai_ready(current),
        )
        return templates.TemplateResponse(request, name, context)

    def back(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    # Sounds and music, if that part of FRITS is installed
    app.state.sounds_url = None
    folder = _sounds_dir()
    if folder is not None:
        path = app.state.files.enter_context(as_file(folder))
        app.mount("/static/sounds", StaticFiles(directory=path), name="sounds")
        app.state.sounds_url = "/static/sounds/"
    static = app.state.files.enter_context(as_file(files("frits.web") / "static"))
    app.mount("/static", StaticFiles(directory=static), name="static")
    pictures = painted_pictures(static)
    templates.env.globals.update(
        mascot=mascot_or_picture(pictures),
        picture=pictures.get,
        picture_tag=picture_tag(static, pictures),
        picture_css=picture_css(pictures),
    )

    @app.middleware("http")
    async def only_this_computer(request: Request, call_next):
        # A web page can make the browser talk to 127.0.0.1 under a different name
        # (DNS rebinding). FRITS only answers when it's called by its real name.
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost", "testserver"):
            return HTMLResponse("FRITS antwortet nur auf diesem Computer.", status_code=403)
        return await call_next(request)

    @app.exception_handler(StarletteHTTPException)
    async def friendly_error(request: Request, exc: StarletteHTTPException):
        """Plain words instead of {"detail": "Not Found"}."""
        ziel = (exc.headers or {}).get("Location")
        if 300 <= exc.status_code < 400 and ziel:
            return RedirectResponse(ziel, status_code=exc.status_code)
        if exc.status_code == 404:
            titel, text = (
                "Diese Seite gibt es nicht",
                ("Vielleicht ist sie umgezogen, oder der Link war nicht ganz vollständig."),
            )
        elif exc.status_code == 403:
            titel, text = "Das hat nicht geklappt", str(exc.detail)
        else:
            titel, text = "Das hat nicht geklappt", "Versuch es gleich noch einmal."
        response = page(request, "fehler.html", titel=titel, text=text)
        response.status_code = exc.status_code
        return response

    @app.exception_handler(RequestValidationError)
    async def wrong_address(request: Request, exc: RequestValidationError):
        """A typo in an address (e.g. /steuer/abc) is a missing page, not a JSON error."""
        if request.method != "GET":
            return await request_validation_exception_handler(request, exc)
        response = page(
            request,
            "fehler.html",
            titel="Diese Seite gibt es nicht",
            text="Vielleicht ist sie umgezogen, oder der Link war nicht ganz vollständig.",
        )
        response.status_code = 404
        return response

    async def check_token(request: Request) -> None:
        form = await request.form()
        if not secrets.compare_digest(str(form.get("token", "")), app.state.token):
            raise HTTPException(403, "Ungültiges Formular. Bitte lade die Seite neu.")

    guarded = [Depends(check_token)]

    # ----- Heute -----

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        conn = db()
        current = settings()
        if not current.setup_done and not current.is_complete:
            return back("/setup/welcome")
        drafts = _open_drafts(conn, current)
        invoices = list_invoices(conn, today())
        overdue = [i for i in invoices if i["status"] == "überfällig"]
        waiting = _inbox_files(ws)
        check_me = _check_me_files(ws)
        receipts = open_receipts(conn)
        if not current.is_complete:
            moment = ("welcome", "Hallo! Bevor wir loslegen: Trag kurz deine Angaben ein.")
        elif drafts or check_me:
            moment = mascot_line("needs_you" if check_me else "draft_ready")
            ready = sum(1 for d in drafts if d["ready"])
            if not check_me and ready < len(drafts):
                moment = (
                    moment[0],
                    "Da liegt ein Entwurf, der noch etwas von dir braucht. Schau ihn dir an, "
                    "wenn es dir passt."
                    if len(drafts) == 1
                    else "Da liegen Entwürfe, die noch etwas von dir brauchen. Schau sie dir "
                    "an, wenn es dir passt."
                    if ready == 0
                    else f"{ready} von {len(drafts)} Entwürfen sind fertig, die anderen brauchen "
                    "noch etwas von dir.",
                )
            elif not check_me and len(drafts) > 1:
                moment = (
                    moment[0],
                    f"Deine {len(drafts)} Entwürfe sind fertig. Schau sie dir an, wenn es dir passt.",
                )
        else:
            moment = mascot_line("welcome_back")
        return page(
            request,
            "home.html",
            drafts=drafts,
            overdue=overdue,
            open_count=sum(1 for i in invoices if i["status"] in ("offen", "überfällig")),
            waiting=waiting,
            check_me=check_me,
            receipts=receipts,
            mood=moment[0],
            moment=moment[1],
            ai_ready=ai_ready(current),
            f=haustier.ansehen(conn, today(), fritte_datei(ws.root)) if current.haustier else None,
            liste=aufgaben.liste(conn, today()),
            liste_rest=_liste_rest(),
            heute=today().isoformat(),
            karten_uebrig=aufgaben.karten_uebrig(conn, today()) if current.haustier else 0,
            pro_tag=aufgaben.PRO_TAG,
        )

    # ----- Deine Liste (on Heute) -----

    app.state.liste_rest = ""  # points that didn't fit, shown again in the field once

    def _liste_rest() -> str:
        rest, app.state.liste_rest = app.state.liste_rest, ""
        return rest

    def _punkt_text(text: str) -> str:
        return text if len(text) <= 60 else text[:59].rstrip() + "…"

    @app.post("/aufgaben", dependencies=guarded)
    async def aufgaben_neu(request: Request):
        """Typed text becomes points: split here; one long sentence by the AI, redacted."""
        from frits.redact import LeakError

        text = str((await request.form()).get("text", "")).strip()
        punkte = aufgaben.aufteilen(text)
        if not punkte:
            flash("Schreib einfach auf, was ansteht. Jede Zeile wird ein Punkt.")
            return back("/#liste")
        current = settings()
        ki = ""
        if aufgaben.braucht_ki(punkte) and ai_ready(current):
            try:
                people = [*customers(), current.owner] if current.name else customers()
                punkte = aufgaben.ki_aufteilen(text, provider_for(current), people, nlp())
                ki = " Die KI hat deinen Satz aufgeteilt, die Namen waren vorher geschwärzt."
            except LeakError:
                ki = " Aufgeteilt habe ich ihn nicht: Da war etwas, das ich nicht sicher schwärzen konnte."
            except (ProviderError, RuntimeError, ValueError):
                ki = " Die KI war gerade nicht erreichbar, also steht er als ein Punkt da."
        conn = db()
        neu, rest = aufgaben.hinzufuegen(conn, punkte, today())
        if rest:
            app.state.liste_rest = "\n".join(rest)
            flash(
                (f"{neu} von {neu + len(rest)} Punkten stehen jetzt auf der Liste. " if neu else "")
                + f"Mehr als {aufgaben.MAX_OFFEN} offene Punkte auf einmal helfen nicht mehr. "
                "Hak erst ein paar ab oder streich, was nicht mehr wichtig ist; der Rest "
                "wartet unten im Feld." + ki
            )
        elif neu:
            flash(
                ("1 Punkt steht" if neu == 1 else f"{neu} Punkte stehen") + " auf der Liste." + ki
            )
        else:
            flash("Das steht schon auf deiner Liste.")
        return back("/#liste")

    @app.post("/aufgaben/{pid}/abhaken", dependencies=guarded)
    async def aufgabe_abhaken(request: Request, pid: int):
        ziel = str((await request.form()).get("back") or "")
        if not ziel.startswith("/") or ziel.startswith("//"):
            ziel = "/#liste"
        conn = db()
        current = settings()
        text = aufgaben.text_von(conn, pid)
        ergebnis = aufgaben.abhaken(conn, pid, today(), spiel=current.haustier)
        if ergebnis is None or text is None:
            return back(ziel)
        if ergebnis == "karte":
            uebrig = aufgaben.karten_uebrig(conn, today())
            nachsatz = (
                " Für Fritte liegt eine Karte bereit."
                if uebrig
                else " Für Fritte liegt eine Karte bereit, das war die letzte für heute."
            )
        elif ergebnis == "voll":
            nachsatz = (
                f" Fürs Spiel zählen {aufgaben.PRO_TAG} am Tag, der Rest ist einfach gut erledigt."
            )
        else:
            nachsatz = ""
        flash(f"Erledigt: {_punkt_text(text)}.{nachsatz}", cue="done")
        return back(ziel)

    @app.post("/aufgaben/{pid}/zurueck", dependencies=guarded)
    def aufgabe_zurueck(pid: int):
        conn = db()
        if aufgaben.zuruecknehmen(conn, pid):
            flash("Wieder offen. Eine Karte, die noch wartete, ist mit weg.")
        return back("/#liste")

    @app.post("/aufgaben/{pid}/streichen", dependencies=guarded)
    def aufgabe_streichen(pid: int):
        conn = db()
        aufgaben.vorbereiten(conn)
        row = conn.execute(
            "SELECT id, text, erstellt, erledigt FROM aufgaben WHERE id = ? AND erledigt IS NULL",
            (pid,),
        ).fetchone()
        if row:  # only open points: a ticked one is taken back with "noch nicht erledigt"
            aufgaben.streichen(conn, pid)
            flash(
                f"Gestrichen: {_punkt_text(row[1])}.", undo={"kind": "aufgabe", "row": tuple(row)}
            )
        return back("/#liste")

    @app.post("/inbox/process", dependencies=guarded)
    def process_inbox():
        return read_inbox("/")

    def read_inbox(target: str, after_upload: bool = False) -> RedirectResponse:
        """Read everything waiting in the inbox: redact here, then the AI sees placeholders only."""
        from frits.pipeline import process_file

        current = settings()
        files_waiting = _inbox_files(ws)
        if not files_waiting:
            flash("Der Eingang ist leer. Zieh eine Notiz, E-Mail oder ein Foto auf das Feld.")
            return back(target)
        try:
            provider = provider_for(current)
            model = nlp()
        except (ProviderError, RuntimeError) as exc:
            if after_upload:
                # Fritz just handed something over: keep him where he is, the file is safe.
                flash(f"{exc} Die Datei liegt sicher im Eingang.", "error")
                return back(target)
            flash(str(exc), "error")
            return back("/settings" if isinstance(exc, ProviderError) else target)
        counts = {"drafted": 0, "check-me": 0, "receipt": 0, "skipped": 0}
        drafted: list[int] = []
        for path in files_waiting:
            outcome = process_file(path, ws, current, provider, model)
            counts[outcome.status] += 1
            if outcome.draft_id:
                drafted.append(outcome.draft_id)
            if outcome.status == "check-me" and path.exists():
                flash(f"{path.name}: {outcome.message}", "error")
                break  # e.g. the AI isn't reachable: the next files would fail the same way
            if outcome.status == "check-me":
                flash(
                    f"{path.name}: {outcome.message}", link=("/check-me", "Ansehen und einordnen")
                )
            if outcome.status == "skipped" and outcome.sha256:
                text, link = where_known(db(), outcome.sha256)
                flash(f"{_upload_name(path)}: {text}", link=link)
        parts = []
        if counts["drafted"]:
            parts.append(
                "1 neuer Entwurf"
                if counts["drafted"] == 1
                else f"{counts['drafted']} neue Entwürfe"
            )
        if counts["receipt"]:
            parts.append("1 Beleg" if counts["receipt"] == 1 else f"{counts['receipt']} Belege")
        if counts["check-me"]:
            parts.append(f"{counts['check-me']} zum Prüfen")
        flash("Fertig gelesen: " + (", ".join(parts) or "nichts Neues") + ".", cue="done")
        if target == "/" and not counts["check-me"]:
            # Exactly one thing to do next? Then go there instead of making Fritz look for it.
            if len(drafted) == 1 and not counts["receipt"]:
                return back(f"/drafts/{drafted[0]}")
            if counts["receipt"] and not drafted and (waiting := open_receipts(db())):
                return back(f"/expenses?receipt={waiting[0]['id']}#neu")
        return back(target)

    @app.post("/open/{folder}", dependencies=guarded)
    async def open_folder(request: Request, folder: str):
        places = {
            "inbox": ws.inbox,
            "invoices": ws.invoices,
            "quotes": ws.quotes,
            "customers": ws.customers,
            "check-me": ws.check_me,
            "home": ws.root,
        }
        if folder not in places or not system.open_path(places[folder]):
            flash(f"Den Ordner findest du hier: {places.get(folder, ws.root)}")
        target = str((await request.form()).get("back") or "")
        if target.startswith("/") and not target.startswith("//"):
            return back(target)
        return back("/" if folder != "customers" else "/customers")

    @app.post("/inbox/upload", dependencies=guarded)
    async def inbox_upload(request: Request):
        """Files dropped or pasted into the browser land in the inbox folder, as if dragged
        there in Finder. Pasted text becomes a small note file."""
        form = await request.form()
        target = _local(form.get("back"))
        saved, problems = await _saved_uploads(form, ws.inbox)
        if saved and "lesen_merken" in form:
            # The "gleich lesen" box on Heute: whatever Fritz chose last time stays chosen.
            wants = form.get("lesen") == "on"
            if wants != settings().auto_read:
                replace(settings(), auto_read=wants).save(ws.settings_file)
            if wants and not problems:
                return read_inbox(target, after_upload=True)
        if saved:
            names = ", ".join(p.name for p in saved)
            if ai_ready(settings()):
                flash(f"Im Eingang: {names}. Drück „Jetzt lesen lassen“, wenn du so weit bist.")
            else:
                flash(
                    f"Im Eingang: {names}. Sobald die KI verbunden ist, lese ich es. Ein Beleg "
                    "geht auch ohne KI: Zieh ihn unter Ausgaben auf das Feld."
                )
        for problem in problems:
            flash(problem, "error")
        if not saved and not problems:
            flash("Es kam nichts an. Zieh eine Datei auf das Feld oder füg Text ein.", "error")
        return back(target)

    @app.get("/check-me", response_class=HTMLResponse)
    def check_me_page(request: Request):
        return page(request, "check_me.html", files=_check_me_files(ws))

    def _check_me_file(name) -> Path:
        path = (ws.check_me / Path(str(name or "")).name).resolve()
        if not path.is_file() or ws.check_me.resolve() not in path.parents:
            raise HTTPException(404)
        if path.name.endswith(".grund.txt"):
            raise HTTPException(404)
        return path

    @app.get("/check-me/datei")
    def check_me_file(name: str = ""):
        """Look at the file first, here on this computer, to decide where it belongs."""
        return FileResponse(_check_me_file(name), content_disposition_type="inline")

    def _check_me_done(path: Path, status: str) -> tuple[Path, int]:
        """Move a file out of "Zum Prüfen" into "erledigt" and note what it became."""
        from frits.pipeline import _move, file_hash

        conn = db()
        digest = file_hash(path)
        path.with_name(path.name + ".grund.txt").unlink(missing_ok=True)
        target = _move(path, ws.done)
        row = conn.execute("SELECT id FROM jobs WHERE sha256 = ?", (digest,)).fetchone()
        if row:
            conn.execute(
                "UPDATE jobs SET status = ?, filename = ? WHERE id = ?",
                (status, target.name, row["id"]),
            )
            return target, row["id"]
        return target, store.add_job(conn, digest, target.name, status)

    @app.post("/check-me/beleg", dependencies=guarded)
    async def check_me_receipt(request: Request):
        """It's a ticket or a receipt: enter it on Ausgaben. FRITS reads amount and date here,
        no AI involved, so nothing has to be redacted."""
        path = _check_me_file((await request.form()).get("name"))
        _, job_id = _check_me_done(path, "receipt")
        flash(
            "Als Beleg übernommen. Betrag und Datum lese ich hier auf deinem Computer, ganz "
            "ohne KI. Bitte prüfen und speichern, dann zählt er in der EÜR."
        )
        return back(f"/expenses?receipt={job_id}#neu")

    @app.post("/check-me/notfall", dependencies=guarded)
    async def check_me_chat(request: Request):
        """A letter or a notice: open it in the Notfall-Chat, redacted, to understand it.
        The file stays under "Zum Prüfen" until Fritz puts it away."""
        from frits import notfall
        from frits.redact import LeakError

        path = _check_me_file((await request.form()).get("name"))
        current = settings()
        known = list(customers()) + ([current.owner] if current.name else [])
        try:
            text = extract_text(path).text
            g = notfall.neues_gespraech()
            notfall.add_document(
                g, path.name, path.suffix.lstrip(".") or "text", text, known, nlp()
            )
        except IngestError as exc:
            flash(str(exc), "error")
            return back("/check-me")
        except LeakError:
            flash(
                "Ich konnte nicht alles sicher schwärzen, darum geht davon nichts raus. Im "
                "Notfall-Chat kannst du selbst Wörter zum Schwärzen eintragen: Zieh die Datei "
                "dort noch einmal auf das Feld.",
                "error",
            )
            return back("/notfall")
        except RuntimeError as exc:
            flash(str(exc), "error")
            return back("/check-me")
        chats = app.state.notfall
        chats[g.key] = g
        while len(chats) > notfall.KEEP:
            chats.popitem(last=False)
        return back(f"/notfall/{g.key}")

    @app.post("/check-me/weglegen", dependencies=guarded)
    async def check_me_away(request: Request):
        path = _check_me_file((await request.form()).get("name"))
        target, _ = _check_me_done(path, "check-me-ignored")
        flash(f"{target.name} liegt jetzt im Ordner „erledigt“ und zählt nirgends mit.")
        return back("/check-me")

    @app.post("/check-me/retry", dependencies=guarded)
    async def check_me_retry(request: Request):
        name = (await request.form()).get("name", "")
        path = ws.check_me / Path(str(name)).name
        if not path.is_file():
            raise HTTPException(404)
        from frits.pipeline import _move, file_hash

        conn = db()
        conn.execute(
            "DELETE FROM jobs WHERE sha256 = ? AND status = 'check-me'", (file_hash(path),)
        )
        path.with_name(path.name + ".grund.txt").unlink(missing_ok=True)
        _move(path, ws.inbox)
        flash(f"{path.name} liegt wieder im Eingang.")
        return back("/")

    # ----- Entwürfe -----

    @app.get("/drafts", response_class=HTMLResponse)
    def drafts_page(request: Request):
        return page(request, "drafts.html", drafts=_open_drafts(db(), settings()))

    @app.post("/drafts/new", dependencies=guarded)
    async def drafts_new(request: Request):
        form = await request.form()
        kind = "quote" if form.get("kind") == "quote" else "invoice"
        draft = Draft(None, None, None, [], [], [], "von Hand", kind=kind)
        set_customer(draft, customer_by(str(form.get("customer", ""))))
        return back(f"/drafts/{new_draft(db(), draft)}")

    @app.get("/drafts/{draft_id}", response_class=HTMLResponse)
    def draft_page(request: Request, draft_id: int):
        conn = db()
        try:
            draft, row = load_draft(conn, draft_id)
        except BillingError:
            raise HTTPException(404) from None
        current = settings()
        job = None
        original = None
        if row["job_id"]:
            job = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["job_id"],)).fetchone()
            source = ws.done / draft.source_file
            if source.is_file():
                try:
                    original = extract_text(source).text
                except IngestError:
                    original = None
        problems, limit_warnings = [], []
        if row["status"] == "draft":
            problems = blockers(draft, current)
            if draft.kind != "quote":
                extra, limit_warnings = limit_check(
                    conn, current, today().year, draft_totals(draft, current).net
                )
                problems += extra
        return page(
            request,
            "draft.html",
            draft=draft,
            row=row,
            sums=draft_totals(draft, current),
            period=service_period(draft.lines),
            problems=problems,
            limit_warnings=limit_warnings,
            vat_rates=DOMESTIC_RATES,
            draft_hints=_draft_hints(draft, customer_by(draft.customer_id)),
            customers=customers(),
            original=original,
            sent_text=job["sent_text"] if job else None,
        )

    @app.post("/drafts/{draft_id}", dependencies=guarded)
    async def draft_save(request: Request, draft_id: int):
        form = await request.form()
        conn = db()
        action = form.get("action", "save")
        if action == "answer":
            return draft_answer(draft_id, str(form.get("answers") or ""))
        try:
            draft, _ = load_draft(conn, draft_id)
            _apply_form(draft, form, customer_by, settings())
            save_draft(conn, draft_id, draft)
        except BillingError as exc:
            flash(str(exc), "error", feld=getattr(exc, "feld", None))
            return back(f"/drafts/{draft_id}")
        current = settings()
        if action == "approve":
            try:
                number, _ = approve(conn, draft_id, current, ws.invoices, today())
            except BillingError as exc:
                flash(str(exc), "error")
                return back(f"/drafts/{draft_id}")
            row = conn.execute("SELECT job_id FROM drafts WHERE id = ?", (draft_id,)).fetchone()
            if row["job_id"]:
                from frits.pipeline import forget_mapping

                forget_mapping(ws, row["job_id"])
            flash(
                f"Rechnung {number} ist fertig. Ein Punkt weniger im Kopf.",
                cue="rechnung",
                stamp="Fertig",
            )
            late = ustva.late_notice(ws.db, current, number, today())
            if late:
                flash(late, "error")
            return back(f"/invoices#r-{number}")
        if action == "quote":
            try:
                number, _ = create_quote(conn, draft, current, ws.quotes, today())
            except BillingError as exc:
                flash(str(exc), "error")
                return back(f"/drafts/{draft_id}")
            discard_draft(conn, draft_id)
            flash(f"Angebot {number} ist fertig.", cue="done", stamp="Fertig")
            return back(f"/quotes#a-{number}")
        flash("Gespeichert.")
        return back(f"/drafts/{draft_id}")

    def draft_answer(draft_id: int, answers: str):
        """The answers to "Fragen an dich", in Fritz's words: the AI reads the note again."""
        from frits.pipeline import AnswerError, answer_questions

        if not answers.strip():
            flash("Schreib deine Antworten in das Feld unter den Fragen.", "error")
            return back(f"/drafts/{draft_id}#fragen")
        current = settings()
        try:
            answer_questions(db(), ws, current, provider_for(current), nlp(), draft_id, answers)
        except (AnswerError, ProviderError, RuntimeError, BillingError) as exc:
            flash(str(exc), "error")
            return back(f"/drafts/{draft_id}#fragen")
        flash(
            "Neu vorgeschlagen, mit deinen Antworten. Bitte schau die Positionen noch einmal an.",
            cue="done",
        )
        return back(f"/drafts/{draft_id}")

    @app.post("/drafts/{draft_id}/discard", dependencies=guarded)
    def draft_discard(draft_id: int):
        try:
            discard_draft(db(), draft_id)
            flash(
                "Entwurf verworfen. Es wurde keine Nummer verbraucht.",
                undo={"kind": "draft", "id": draft_id},
            )
        except BillingError as exc:
            flash(str(exc), "error")
        return back("/drafts")

    # ----- Rechnungen -----

    @app.get("/invoices", response_class=HTMLResponse)
    def invoices_page(request: Request):
        return page(
            request,
            "invoices.html",
            invoices=list_invoices(db(), today()),
            credits=gutschriften.listing(db()),
            today_iso=today().isoformat(),
            earlier_tax=EARLIER_TAX,
            earlier_default="klein" if settings().tax_mode != "regelbesteuerung" else "19",
        )

    @app.post("/invoices/frueher", dependencies=guarded)
    async def invoice_earlier(request: Request):
        """An invoice Fritz wrote before FRITS: entered with its own number and date."""
        form = await request.form()
        try:
            issued = date.fromisoformat(str(form.get("issue_date", "")))
            served = str(form.get("service_date") or "")
            paid = str(form.get("paid") or "")
            upload = form.get("original")
            original = None
            if upload is not None and getattr(upload, "filename", ""):
                original = await upload.read(MAX_UPLOAD) or None
            number = add_earlier(
                db(),
                number=str(form.get("number", "")),
                customer=str(form.get("customer", "")),
                issue_date=issued,
                service_date=date.fromisoformat(served) if served else None,
                description=str(form.get("description", "")),
                net=parse_amount(str(form.get("net", ""))),
                tax=str(form.get("tax", "")),
                paid_date=date.fromisoformat(paid) if paid else None,
                original=original,
                invoices_dir=ws.invoices,
                settings=settings(),
                today=today(),
            )
        except BillingError as exc:
            flash(str(exc), "error")
            return back("/invoices#frueher")
        except ValueError:
            flash(
                "Bitte die Daten prüfen: Rechnungsdatum und Betrag fehlen oder passen nicht.",
                "error",
            )
            return back("/invoices#frueher")
        row = db().execute("SELECT gross FROM invoices WHERE number = ?", (number,)).fetchone()
        flash(
            f"Rechnung {number} ist nachgetragen: {eur(Decimal(row['gross']))} brutto. Bitte kurz mit "
            "dem Original vergleichen. Sie zählt jetzt in EÜR und Umsatzsteuer mit.",
            cue="done",
            stamp="Gebucht",
        )
        return back(f"/invoices#r-{number}")

    @app.get("/invoices/{number}/pdf")
    def invoice_pdf(number: str):
        row = db().execute("SELECT pdf_path FROM invoices WHERE number = ?", (number,)).fetchone()
        return _pdf(ws.find(row["pdf_path"]) if row else None, ws.invoices)

    @app.post("/invoices/{number}/paid", dependencies=guarded)
    async def invoice_paid(request: Request, number: str):
        form = await request.form()
        try:
            paid = _paid_day(form.get("paid"), today())
            row = (
                db()
                .execute("SELECT cancels, issue_date FROM invoices WHERE number = ?", (number,))
                .fetchone()
            )
            mark_paid(db(), number, paid)
            if row and row["cancels"]:
                flash(f"Erstattung zu {row['cancels']} ist eingetragen.")
            else:
                flash(f"Rechnung {number} ist bezahlt. Schön!", cue="done", stamp="Bezahlt")
            issued = date.fromisoformat(row["issue_date"]) if row else paid
            if paid.year < issued.year:
                flash(
                    f"Das Zahldatum {paid:%d.%m.%Y} liegt vor der Rechnung, im Jahr {paid.year}. "
                    f"So zählt das Geld in der EÜR {paid.year}. War das eine Vorauszahlung? "
                    "Sonst bitte unter Rechnungen prüfen.",
                    "error",
                )
        except BillingError as exc:
            flash(str(exc), "error")
        return back("/invoices")

    @app.post("/invoices/{number}/kopieren", dependencies=guarded)
    def invoice_copy(number: str):
        conn = db()
        row = conn.execute("SELECT customer FROM invoices WHERE number = ?", (number,)).fetchone()
        try:
            draft_id = copy_invoice(conn, number, customer_by(row["customer"]) if row else None)
        except BillingError as exc:
            flash(str(exc), "error")
            return back("/invoices")
        flash(f"Neuer Entwurf mit den Positionen von {number}. Trag noch die Daten ein.")
        return back(f"/drafts/{draft_id}")

    @app.post("/invoices/{number}/cancel", dependencies=guarded)
    def invoice_cancel(number: str):
        try:
            storno, _ = cancel(db(), number, settings(), ws.invoices, today())
            flash(f"Rechnung {number} ist storniert. Die Stornorechnung hat die Nummer {storno}.")
        except BillingError as exc:
            flash(str(exc), "error")
        return back("/invoices")

    @app.post("/invoices/{number}/email", dependencies=guarded)
    def invoice_email(number: str):
        conn = db()
        row = conn.execute("SELECT * FROM invoices WHERE number = ?", (number,)).fetchone()
        if row is None or not row["pdf_path"]:  # entered from before FRITS without a PDF
            raise HTTPException(404)
        customer = customer_by(row["customer"])
        subject, body = emails.invoice_email(dict(row), customer, settings())
        result = system.compose_email(
            customer.email if customer and customer.email else "",
            subject,
            body,
            ws.find(row["pdf_path"]) or Path(row["pdf_path"]),
        )
        return _after_compose(conn, "invoices", number, result, "/invoices")

    @app.post("/invoices/{number}/remind", dependencies=guarded)
    def invoice_remind(number: str):
        conn = db()
        row = conn.execute("SELECT * FROM invoices WHERE number = ?", (number,)).fetchone()
        if row is None:
            raise HTTPException(404)
        customer = customer_by(row["customer"])
        current = settings()
        try:
            reminder_id, pdf, new_due = create_reminder(
                conn,
                number,
                current,
                ws.invoices,
                customer.address if customer else None,
                today(),
            )
        except BillingError as exc:
            flash(str(exc), "error")
            return back("/invoices")
        level, previous = conn.execute(
            "SELECT level, (SELECT MAX(created) FROM reminders r2 WHERE r2.invoice_number ="
            " r.invoice_number AND r2.id < r.id) FROM reminders r WHERE id = ?",
            (reminder_id,),
        ).fetchone()
        subject, body = emails.reminder_email(
            dict(row),
            new_due,
            customer,
            current,
            level,
            date.fromisoformat(previous[:10]) if previous else None,
        )
        result = system.compose_email(
            customer.email if customer and customer.email else "", subject, body, pdf
        )
        return _after_compose(conn, "reminders", reminder_id, result, "/invoices")

    def _after_compose(conn, table: str, key, result, url: str):
        column = "id" if table == "reminders" else "number"
        if result.opened:
            conn.execute(
                f"UPDATE {table} SET emailed = ? WHERE {column} = ?",
                (today().isoformat(), key),
            )
            flash(
                "Die E-Mail ist in Mail geöffnet. Prüf sie kurz und drück dort auf Senden.",
                cue="sent",
            )
        else:
            app.state.mailto = result.mailto
            return back("/mailto")
        return back(url)

    # ----- Angebote -----

    @app.get("/quotes", response_class=HTMLResponse)
    def quotes_page(request: Request):
        current = settings()
        quotes = list_quotes(db())
        for quote in quotes:
            quote["gross"] = draft_totals(quote["draft"], current).gross
        return page(request, "quotes.html", quotes=quotes, customers=customers())

    @app.get("/quotes/{number}/pdf")
    def quote_pdf(number: str):
        row = db().execute("SELECT pdf_path FROM quotes WHERE number = ?", (number,)).fetchone()
        return _pdf(ws.find(row["pdf_path"]) if row else None, ws.quotes)

    @app.post("/quotes/{number}/accept", dependencies=guarded)
    def quote_accept(number: str):
        try:
            draft_id = accept_quote(db(), number)
        except BillingError as exc:
            flash(str(exc), "error")
            return back("/quotes")
        flash(
            f"Angebot {number} angenommen. Hier ist der Rechnungsentwurf dazu.",
            cue="done",
            stamp="Angenommen",
        )
        return back(f"/drafts/{draft_id}")

    @app.post("/quotes/{number}/decline", dependencies=guarded)
    def quote_decline(number: str):
        decline_quote(db(), number)
        flash(f"Angebot {number} ist als abgelehnt markiert.")
        return back("/quotes")

    @app.post("/quotes/{number}/email", dependencies=guarded)
    def quote_email(number: str):
        conn = db()
        row = conn.execute("SELECT * FROM quotes WHERE number = ?", (number,)).fetchone()
        if row is None:
            raise HTTPException(404)
        draft = Draft.from_json(row["data"])
        current = settings()
        customer = customer_by(row["customer_id"])
        subject, body = emails.quote_email(
            dict(row), draft_totals(draft, current).gross, customer, current
        )
        result = system.compose_email(
            customer.email if customer and customer.email else "",
            subject,
            body,
            ws.find(row["pdf_path"]) or Path(row["pdf_path"]),
        )
        return _after_compose(conn, "quotes", number, result, "/quotes")

    # ----- Kunden -----

    @app.get("/customers", response_class=HTMLResponse)
    def customers_page(request: Request):
        return page(request, "customers.html", customers=customers())

    @app.get("/customers/new", response_class=HTMLResponse)
    def customer_new(request: Request):
        target = request.query_params.get("back") or ""
        found = re.fullmatch(r"/drafts/(\d+)", target)
        prefill = _prefill_from_draft(int(found.group(1))) if found else {}
        return page(
            request, "customer.html", customer=None, back_to=target or None, prefill=prefill
        )

    @app.post("/customers/new/einlesen", dependencies=guarded)
    async def customer_read_contact(request: Request):
        """A pasted signature or a dragged contact card fills the new customer's fields."""
        from frits import kontakt

        form = await request.form()
        target = _local(form.get("back")) if form.get("back") else ""
        text = str(form.get("text") or "").strip()[:MAX_PASTED_TEXT]
        upload = next((u for u in form.getlist("files") if getattr(u, "filename", None)), None)
        try:
            if not text and upload is not None:
                text = await _upload_text(upload)
            found = kontakt.details(text, nlp()) if text else {}
        except (IngestError, RuntimeError) as exc:
            flash(str(exc), "error")
            found = {}
        else:
            if not text:
                flash("Zieh eine Visitenkarte oder einen Kontakt auf das Feld oder füg Text ein.")
            elif not found:
                flash("Darin habe ich keine Kontaktdaten gefunden. Trag sie einfach von Hand ein.")
        return page(
            request,
            "customer.html",
            customer=None,
            back_to=target or None,
            prefill=found,
            prefill_from="contact" if found else None,
        )

    def _prefill_from_draft(draft_id: int) -> dict[str, str]:
        """Suggest the new customer's details from the draft and its original document."""
        try:
            draft, _ = load_draft(db(), draft_id)
        except BillingError:
            return {}
        prefill = {"name": draft.customer_name or ""}
        source = ws.done / draft.source_file
        if source.is_file():
            try:
                found = setup.own_details(extract_text(source).text, None)
            except IngestError:
                found = {}
            mine = settings()
            for key in ("address", "email", "phone"):
                if found.get(key) and found[key] != getattr(mine, key):
                    prefill[key] = found[key]
        return {k: v for k, v in prefill.items() if v}

    @app.get("/customers/{customer_id}", response_class=HTMLResponse)
    def customer_page(request: Request, customer_id: str):
        customer = customer_by(customer_id)
        if customer is None:
            raise HTTPException(404)
        return page(
            request,
            "customer.html",
            customer=customer,
            back_to=None,
            credits=gutschriften.listing(db(), customer_id=customer.id),
            credit_rates=_credit_rates(customer),
            today_iso=today().isoformat(),
        )

    def _credit_rates(customer: Customer) -> list[str]:
        """Which VAT rates a Gutschrift from this customer can carry; [] means none asked."""
        if settings().tax_mode != "regelbesteuerung" or customer.umsatzsteuer in (
            "eu",
            "drittland",
        ):
            return []
        return ["19", "7", "0"]

    @app.post("/customers/{customer_id}/gutschriften", dependencies=guarded)
    async def credit_add(request: Request, customer_id: str):
        customer = customer_by(customer_id)
        if customer is None:
            raise HTTPException(404)
        form = await request.form()
        url = f"/customers/{customer.id}#gutschriften"
        try:
            gross = parse_amount(str(form.get("gross", "")))
            issued = date.fromisoformat(str(form.get("issue_date", "")))
            paid = _paid_day(form.get("paid"), today()) if form.get("paid") else None
            rates = _credit_rates(customer)
            regel = settings().tax_mode == "regelbesteuerung"
            rate = str(form.get("vat_rate", "19"))
            if rates and rate not in rates:
                raise BillingError("Bitte den Steuersatz von der Gutschrift wählen.")
            gutschriften.add(
                db(),
                customer_id=customer.id,
                customer=customer.display_name,
                their_number=str(form.get("number", "")),
                issue_date=issued,
                gross=gross or Decimal(0),
                vat_rate=Decimal(rate) if rates else (Decimal(0) if regel else None),
                kind=customer.umsatzsteuer or "inland",
                paid_date=paid,
            )
        except ValueError as exc:  # CreditError, a bad date
            text = str(exc) if isinstance(exc, gutschriften.CreditError) else None
            flash(text or "Bitte Datum und Betrag prüfen.", "error")
            return back(url)
        except BillingError as exc:
            flash(str(exc), "error")
            return back(url)
        flash(
            f"Gutschrift erfasst: {eur(gross)} von {customer.display_name}."
            + (" Sie zählt ab dem Zahltag in der EÜR." if paid is None else ""),
            cue="done",
            stamp="Gebucht",
        )
        return back(url)

    @app.post("/gutschriften/{credit_id}/bezahlt", dependencies=guarded)
    async def credit_paid(request: Request, credit_id: int):
        form = await request.form()
        try:
            gutschriften.mark_paid(db(), credit_id, _paid_day(form.get("paid"), today()))
            flash("Zahlung eingetragen. Schön!", cue="done", stamp="Bezahlt")
        except BillingError as exc:
            flash(str(exc), "error")
        return back(_local(form.get("back")))

    @app.post("/gutschriften/{credit_id}/streichen", dependencies=guarded)
    async def credit_strike(request: Request, credit_id: int):
        form = await request.form()
        if gutschriften.strike(db(), credit_id):
            flash(
                "Gutschrift gestrichen. Sie zählt nicht mehr, bleibt fürs Finanzamt aber im Archiv.",
                undo={"kind": "gutschrift", "id": credit_id, "back": _local(form.get("back"))},
            )
        return back(_local(form.get("back")))

    @app.post("/customers", dependencies=guarded)
    async def customer_save(request: Request):
        form = await request.form()
        name = str(form.get("name", "")).strip()
        if not name:
            flash("Der Kunde braucht einen Namen.", "error")
            return back("/customers/new")
        existing = str(form.get("id", "")).strip()
        try:
            rate = parse_amount(str(form.get("hourly_rate", "")))
        except BillingError as exc:
            flash(str(exc), "error")
            return back(f"/customers/{existing}" if existing else "/customers/new")
        company = str(form.get("company", "")).strip() or None
        customer_id = existing or note_id(company or name)
        if not existing and (ws.customers / f"{customer_id}.md").exists():
            customer_id = f"{customer_id}-{secrets.token_hex(2)}"
        land = str(form.get("land", "DE"))
        land = land if land in COUNTRIES else "DE"
        privat = form.get("privat") == "on"
        customer = Customer(
            id=Path(customer_id).name,
            name=name,
            company=company,
            address=_one_line(form.get("address")),
            email=str(form.get("email", "")).strip() or None,
            phone=str(form.get("phone", "")).strip() or None,
            hourly_rate=rate,
            aliases=tuple(a.strip() for a in str(form.get("aliases", "")).split(",") if a.strip()),
            umsatzsteuer=vat_place(land, privat),
            ust_id=_vat_id(form.get("ust_id"), land),
            land=land,
            referenz=str(form.get("referenz", "")).strip() or None,
            privat=privat,
            gutschrift=form.get("gutschrift") == "on",
        )
        save_note(ws.customers, customer)
        flash(f"{customer.display_name} ist gespeichert.")
        target = str(form.get("back") or "")
        if found := re.fullmatch(r"/drafts/(\d+)", target):
            _attach_customer(int(found.group(1)), customer)
            return back(target)
        return back("/customers")

    def _attach_customer(draft_id: int, customer: Customer) -> None:
        """Coming from a draft: the new customer goes straight onto it."""
        conn = db()
        try:
            draft, row = load_draft(conn, draft_id)
        except BillingError:
            return
        if row["status"] == "draft":
            set_customer(draft, customer)
            save_draft(conn, draft_id, draft)

    @app.post("/customers/{customer_id}/open", dependencies=guarded)
    def customer_open(customer_id: str):
        customer = customer_by(customer_id)
        if customer is None:
            raise HTTPException(404)
        path = ws.customers / f"{customer.id}.md"
        if not system.open_path(path):
            flash(f"Die Notiz liegt hier: {path}")
        return back(f"/customers/{customer.id}")

    # ----- Einstellungen -----

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        keys = {name: bool(get_api_key(name)) for name in KEY_ENV}
        current = settings()
        upcoming = next_number(
            db(), today().year, current.number_pattern, current.last_number_before
        )
        return page(
            request, "settings.html", keys=keys, folder=ws.root, next_invoice_number=upcoming
        )

    @app.post("/settings", dependencies=guarded)
    async def settings_save(request: Request):
        form = await request.form()
        try:
            updated = settings_from_form(settings(), form)
        except (BillingError, ValueError) as exc:
            flash(
                str(exc)
                if isinstance(exc, BillingError)
                else "Das Nummern-Schema braucht genau einen Zähler (NNN) und nur Buchstaben, Ziffern, "
                "- _ oder Punkt."
                if "pattern" in str(exc)
                else "Bitte nur Zahlen bei Preisen und Zahlungsziel.",
                "error",
                feld=getattr(exc, "feld", None)
                or ("number_pattern" if "pattern" in str(exc) else None),
            )
            return back("/settings")
        updated.save(ws.settings_file)
        key = str(form.get("api_key", ""))
        if (
            key.strip()
            and not key_problem(updated.provider, key)
            and save_key(updated.provider, key)
        ):
            flash(f"Gespeichert. Dein Schlüssel liegt jetzt {umgebung.schluessel_ort()}.")
        elif key.strip():
            flash("Die übrigen Einstellungen sind gespeichert.")
            key_not_saved(updated.provider, key)
        else:
            flash("Gespeichert.")
        return back("/settings")

    def save_key(provider: str, key: str) -> bool:
        key = key.strip()
        if not key or provider not in KEY_ENV:
            return False
        try:
            store_api_key(provider, key)
        except Exception:  # noqa: BLE001  (any keychain failure gets the same plain message)
            return False
        return True

    def key_not_saved(provider: str, key: str, next_step: str | None = None) -> None:
        """Say why a key wasn't kept, and offer the way on without AI."""
        reason = key_problem(provider, key) or (
            "Der Schlüsselbund dieses Macs hat ihn nicht angenommen. Versuch es gleich noch "
            "einmal, oder starte FRITS neu."
        )
        flash(
            f"Der Schlüssel ließ sich nicht speichern. {reason} FRITS funktioniert auch ohne "
            "KI, den Schlüssel kannst du später unter Einstellungen › KI eintragen.",
            "error",
            link=(f"/setup/{next_step}", "Ohne KI weitermachen") if next_step else None,
        )

    # ----- Ausgaben und EÜR -----

    def where_known(conn, digest: str) -> tuple[str, tuple[str, str] | None]:
        """A file FRITS has seen before: what became of it, and a link there."""
        job = conn.execute("SELECT * FROM jobs WHERE sha256 = ?", (digest,)).fetchone()
        if job is None:
            return "Diese Datei kenne ich schon.", None
        if job["status"] == "receipt":
            expenses.init_db(ws.db)
            booked = conn.execute(
                "SELECT id, date FROM expenses WHERE source_file = ? ORDER BY id LIMIT 1",
                (job["filename"],),
            ).fetchone()
            if booked:
                year = booked["date"][:4]
                return (
                    (
                        f"Diesen Beleg hast du schon eingetragen (Ausgaben {year}). "
                        "Du musst nichts mehr tun."
                    ),
                    (f"/expenses?year={year}#ausgabe-{booked['id']}", "Zum Beleg"),
                )
            return (
                "Den kenne ich schon. Er wartet aufs Eintragen.",
                (f"/expenses?receipt={job['id']}#neu", "Jetzt eintragen"),
            )
        draft = conn.execute(
            "SELECT id, status, invoice_number FROM drafts WHERE job_id = ? ORDER BY id DESC",
            (job["id"],),
        ).fetchone()
        if draft and draft["invoice_number"]:
            return (
                f"Daraus ist schon die Rechnung {draft['invoice_number']} geworden.",
                (f"/invoices#r-{draft['invoice_number']}", "Zur Rechnung"),
            )
        if draft and draft["status"] == "draft":
            return "Daraus gibt es schon einen Entwurf.", (f"/drafts/{draft['id']}", "Zum Entwurf")
        if job["status"] == "check-me":
            return "Die liegt schon bei „Bitte einmal ansehen“.", ("/check-me", "Ansehen")
        return "Diese Datei habe ich schon einmal gelesen. Du musst nichts mehr tun.", None

    def open_receipts(conn) -> list[dict]:
        """Receipts FRITS recognised that Fritz hasn't entered as an expense yet."""
        expenses.init_db(ws.db)
        rows = conn.execute(
            "SELECT id, filename, received FROM jobs WHERE status = 'receipt'"
            " AND filename NOT IN (SELECT source_file FROM expenses WHERE source_file IS NOT NULL)"
            " ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    @app.get("/expenses", response_class=HTMLResponse)
    def expenses_page(request: Request, year: int | None = None, receipt: int | None = None):
        year = year or today().year
        conn = db()
        receipts = open_receipts(conn)
        chosen = next((r for r in receipts if r["id"] == receipt), None)
        guessed = _receipt_guess(chosen["filename"]) if chosen else {}
        items = expenses.list_expenses(ws.db, year=year)
        return page(
            request,
            "expenses.html",
            year=year,
            expenses=items,
            struck=expenses.list_expenses(ws.db, year=year, struck=True),
            total=sum((e.gross for e in items), Decimal(0)),
            receipts=receipts,
            chosen=chosen,
            categories=expenses.CATEGORIES,
            today=today().isoformat(),
            guessed=guessed,
        )

    def _receipt_guess(filename: str) -> dict[str, str]:
        """Amount, date and shop read from the receipt here on this computer (OCR, no AI)."""
        from frits import beleg

        path = ws.done / Path(filename).name
        try:
            return beleg.guess(extract_text(path).text, today()) if path.is_file() else {}
        except IngestError:
            return {}

    @app.get("/receipts/{job_id}")
    def receipt_file(job_id: int):
        row = (
            db()
            .execute(
                "SELECT filename FROM jobs WHERE id = ? AND status IN ('receipt', 'receipt-ignored')",
                (job_id,),
            )
            .fetchone()
        )
        if row is None:
            raise HTTPException(404)
        path = (ws.done / Path(row["filename"]).name).resolve()
        if not path.is_file() or ws.done.resolve() not in path.parents:
            raise HTTPException(404)
        return FileResponse(path, content_disposition_type="inline")

    @app.post("/expenses/beleg", dependencies=guarded)
    async def receipt_upload(request: Request):
        """A receipt dropped or pasted on Ausgaben: kept in "done" and opened for entering.
        No AI involved: FRITS reads the amount and date on this computer."""
        from frits.pipeline import file_hash

        form = await request.form()
        saved, problems = await _saved_uploads(form, ws.done, prefix="beleg")
        for problem in problems:
            flash(problem, "error")
        conn = db()
        first = None
        for path in saved:
            digest = file_hash(path)
            known = conn.execute(
                "SELECT id, status FROM jobs WHERE sha256 = ?", (digest,)
            ).fetchone()
            if known and known["status"] == "check-me":
                # It waited under "Zum Prüfen": now Fritz says it's a receipt, so it is one.
                for old in ws.check_me.glob("*"):
                    waiting_there = old.is_file() and not old.name.endswith(".grund.txt")
                    if waiting_there and file_hash(old) == digest:
                        old.with_name(old.name + ".grund.txt").unlink(missing_ok=True)
                        old.unlink()
                conn.execute(
                    "UPDATE jobs SET status = 'receipt', filename = ? WHERE id = ?",
                    (path.name, known["id"]),
                )
                first = first or known["id"]
                continue
            if known:
                path.unlink()
                name = _upload_name(path)
                waiting = {r["id"] for r in open_receipts(conn)}
                if known["id"] in waiting:
                    flash(f"{name}: Den kenne ich schon. Er wartet hier aufs Eintragen.")
                    first = first or known["id"]
                else:
                    text, link = where_known(conn, digest)
                    flash(f"{name}: {text}", link=link)
                continue
            job_id = store.add_job(conn, digest, path.name, "receipt")
            first = first or job_id
        if first:
            return back(f"/expenses?receipt={first}#neu")
        if not saved and not problems:
            flash("Es kam nichts an. Zieh einen Beleg auf das Feld oder füg ihn ein.", "error")
        return back("/expenses")

    @app.post("/expenses", dependencies=guarded)
    async def expense_add(request: Request):
        form = await request.form()
        year = today().year
        try:
            gross = parse_amount(str(form.get("gross", "")))
            if gross is None:
                raise BillingError("Bitte den Betrag eintragen.")
            paid = date.fromisoformat(str(form.get("date") or today().isoformat()))
            year = paid.year
            rate = str(form.get("vat_rate", ""))
            expense = expenses.Expense(
                date=paid,
                vendor=str(form.get("vendor", "")).strip() or "unbekannt",
                description=str(form.get("description", "")).strip(),
                gross=gross,
                vat_rate=Decimal(rate) if rate else None,
                category=str(form.get("category", "sonstiges")),
                source_file=str(form.get("source_file", "")).strip() or None,
            )
        except (BillingError, ValueError, TypeError, ArithmeticError) as exc:
            flash(
                str(exc)
                if isinstance(exc, BillingError)
                else "Bitte Datum, Betrag und Kategorie prüfen.",
                "error",
            )
            return back("/expenses")
        twin = next(
            (
                e
                for e in expenses.list_expenses(ws.db, year=expense.date.year)
                if e.date == expense.date
                and e.gross == expense.gross
                and e.vendor.casefold().strip() == expense.vendor.casefold().strip()
            ),
            None,
        )
        expenses.add_expense(ws.db, expense)
        flash(
            f"Ausgabe gespeichert: {eur(expense.gross)} bei {expense.vendor}.",
            cue="done",
            stamp="Gebucht",
        )
        if twin:
            flash(
                "Eine Ausgabe mit demselben Tag, Laden und Betrag gab es schon. Wenn das "
                "derselbe Beleg war, lösch die neue unten in der Liste. Zwei gleiche Tickets "
                "dürfen natürlich bleiben.",
                "error",
            )
        return back(f"/expenses?year={year}")

    @app.post("/expenses/{expense_id}/delete", dependencies=guarded)
    def expense_delete(expense_id: int):
        gone = next((e for e in expenses.list_expenses(ws.db) if e.id == expense_id), None)
        if gone and expenses.delete_expense(ws.db, expense_id):
            flash(
                f"Ausgabe gelöscht: {eur(gone.gross)} bei {gone.vendor}."
                " Sie zählt nicht mehr, bleibt fürs Finanzamt aber im Archiv.",
                undo={"kind": "expense", "expense": gone},
            )
        return back(f"/expenses?year={gone.date.year}" if gone else "/expenses")

    @app.post("/expenses/{expense_id}/restore", dependencies=guarded)
    def expense_restore(expense_id: int):
        back_to = next(
            (e for e in expenses.list_expenses(ws.db, struck=True) if e.id == expense_id), None
        )
        if back_to and expenses.restore_expense(ws.db, expense_id):
            flash(f"Wieder da: {eur(back_to.gross)} bei {back_to.vendor}.")
        return back(f"/expenses?year={back_to.date.year}" if back_to else "/expenses")

    @app.get("/expenses/{expense_id}/beleg")
    def expense_receipt(expense_id: int):
        found = next((e for e in expenses.list_expenses(ws.db) if e.id == expense_id), None)
        if found is None or not found.source_file:
            raise HTTPException(404)
        path = ws.done / Path(found.source_file).name  # only the name: never leave "done"
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, filename=path.name, content_disposition_type="inline")

    @app.post("/rueckgaengig", dependencies=guarded)
    def undo_last():
        """Take the last step back (a deleted expense, a discarded draft)."""
        last, app.state.undo = app.state.undo, None
        if last and last["kind"] == "expense":
            expense = last["expense"]
            expenses.restore_expense(ws.db, expense.id)
            flash(f"Wieder da: {eur(expense.gross)} bei {expense.vendor}.")
            return back(f"/expenses?year={expense.date.year}")
        if last and last["kind"] == "gutschrift":
            gutschriften.restore(db(), last["id"])
            flash("Die Gutschrift ist wieder da.")
            return back(last["back"])
        if last and last["kind"] == "aufgabe":
            conn = db()
            aufgaben.vorbereiten(conn)
            conn.execute(
                "INSERT OR IGNORE INTO aufgaben (id, text, erstellt, erledigt) VALUES (?, ?, ?, ?)",
                last["row"],
            )
            flash("Der Punkt ist wieder auf der Liste.")
            return back("/#liste")
        if last and last["kind"] == "draft":
            db().execute(
                "UPDATE drafts SET status = 'draft' WHERE id = ? AND status = 'discarded'",
                (last["id"],),
            )
            flash("Der Entwurf ist wieder da.")
            return back(f"/drafts/{last['id']}")
        flash("Da gibt es nichts mehr rückgängig zu machen.")
        return back("/")

    @app.post("/receipts/{job_id}/ignore", dependencies=guarded)
    def receipt_ignore(job_id: int):
        db().execute(
            "UPDATE jobs SET status = 'receipt-ignored' WHERE id = ? AND status = 'receipt'",
            (job_id,),
        )
        flash("Beleg ausgeblendet. Die Datei liegt weiter im Ordner „done“.")
        return back("/expenses")

    @app.get("/euer", response_class=HTMLResponse)
    def euer_page(request: Request, year: int | None = None):
        year = year or today().year - (1 if today().month <= 3 else 0)
        expenses.init_db(ws.db)
        db()  # make sure the invoices table exists
        summary = euer.summarize(ws.db, year, settings().tax_mode)
        return page(request, "euer.html", year=year, summary=summary, this_year=today().year)

    @app.get("/euer/{year}.{kind}")
    def euer_export(year: int, kind: str):
        if kind not in ("csv", "pdf"):
            raise HTTPException(404)
        expenses.init_db(ws.db)
        db()
        summary = euer.summarize(ws.db, year, settings().tax_mode)
        target = ws.root / "euer" / f"EUER_{year}.{kind}"
        target.parent.mkdir(parents=True, exist_ok=True)
        (euer.write_csv if kind == "csv" else euer.write_pdf)(summary, target)
        return FileResponse(target, filename=target.name)

    @app.get("/euer/{year}/buchungsliste.csv")
    def euer_buchungsliste(year: int):
        from frits.export import buchungsliste

        expenses.init_db(ws.db)
        db()
        path = buchungsliste(ws, year, ws.root / "euer")
        return FileResponse(path, filename=path.name)

    @app.get("/euer/{year}/verfahrensdoku.pdf")
    def euer_verfahrensdoku(year: int):
        from frits.export import verfahrensdoku

        path = verfahrensdoku(ws, year, ws.root / "euer", today())
        return FileResponse(path, filename=path.name)

    # ----- Umsatzsteuer-Voranmeldung -----

    @app.get("/ustva", response_class=HTMLResponse)
    def ustva_page(request: Request, year: int | None = None, nr: int | None = None):
        current = settings()
        period = current.vat_period if current.vat_period in ustva.PERIODS else "quartal"
        basis = ustva.basis_of(current)
        if year is None or nr is None:
            year, nr = ustva.next_due(today(), period)
        try:
            ustva.bounds(year, period, nr)
        except ValueError:
            year, nr = ustva.next_due(today(), period)
        db()  # tables exist
        meldung = ustva.voranmeldung(ws.db, current, year, nr, period, basis)
        last = 4 if period == "quartal" else 12
        prev = (year, nr - 1) if nr > 1 else (year - 1, last)
        nxt = (year, nr + 1) if nr < last else (year + 1, 1)
        booked = next(
            (
                e
                for e in expenses.list_expenses(ws.db)
                if e.category == expenses.VAT_PAYMENT and e.description == _vat_label(meldung)
            ),
            None,
        )
        return page(
            request,
            "ustva.html",
            m=meldung,
            prev=prev,
            nxt=nxt,
            period=period,
            basis=basis,
            periods=ustva.PERIODS,
            bases=ustva.BASES,
            basis_chosen=current.vat_basis in ustva.BASES,
            booked=booked,
            klein=current.tax_mode != "regelbesteuerung",
            today_iso=today().isoformat(),
        )

    def _vat_label(meldung) -> str:
        return f"USt-Voranmeldung {meldung.title}"

    @app.post("/ustva/art", dependencies=guarded)
    async def ustva_kind(request: Request):
        form = await request.form()
        period, basis = str(form.get("period", "")), str(form.get("basis", ""))
        if period in ustva.PERIODS and basis in ustva.BASES:
            replace(settings(), vat_period=period, vat_basis=basis).save(ws.settings_file)
            flash("Gemerkt.")
        return back("/ustva")

    @app.post("/ustva/{year}/{nr}/bezahlt", dependencies=guarded)
    async def ustva_paid(request: Request, year: int, nr: int):
        form = await request.form()
        current = settings()
        try:
            meldung = ustva.voranmeldung(
                ws.db, current, year, nr, current.vat_period, ustva.basis_of(current)
            )
            paid = _paid_day(form.get("paid"), today())
        except (ValueError, BillingError) as exc:
            flash(str(exc), "error")
            return back("/ustva")
        if meldung.zahllast <= 0:
            flash("Für diesen Zeitraum ist nichts zu zahlen.", "error")
            return back(f"/ustva?year={year}&nr={nr}")
        expenses.add_expense(
            ws.db,
            expenses.Expense(
                date=paid,
                vendor="Finanzamt",
                description=_vat_label(meldung),
                gross=meldung.zahllast,
                vat_rate=None,
                category=expenses.VAT_PAYMENT,
            ),
        )
        flash(
            f"Eingetragen: {eur(meldung.zahllast)} ans Finanzamt. Das zählt in der EÜR als Ausgabe.",
            cue="done",
            stamp="Gebucht",
        )
        return back(f"/ustva?year={year}&nr={nr}")

    # ----- Einrichtung -----

    @app.get("/setup", response_class=HTMLResponse)
    def setup_start():
        return back("/setup/welcome")

    @app.get("/setup/{step}", response_class=HTMLResponse)
    def setup_route(request: Request, step: str):
        return setup_page(request, step)

    def setup_page(request: Request, step: str, **extra):
        if step not in setup.STEPS:
            raise HTTPException(404)
        current = settings()
        previous, following = setup.neighbours(step)
        context = {
            "step": step,
            "info": setup.step_info(step),
            "number": setup.STEPS.index(step) + 1,
            "total": len(setup.STEPS),
            "previous": previous,
            "following": following,
            "ai_choices": setup.AI_CHOICES,
            "key_links": setup.KEY_LINKS,
            "has_key": bool(get_api_key(current.provider))
            if current.provider in KEY_ENV
            else False,
            "billing_links": setup.BILLING_LINKS,
            "inbox": ws.inbox,
            "sample": setup.sample_note(current),
            "learning": step in setup.LEARNING_STEPS,
        }
        if step == "trick_document" and "demo" not in extra:
            try:  # no AI involved: show the result right away
                context["demo"] = setup.trick_demo(current, nlp())
            except RuntimeError:
                pass
        context.update(extra)
        return page(request, "setup.html", **context)

    @app.post("/setup/business_details/import", dependencies=guarded)
    async def setup_import(request: Request):
        """Read Fritz's details from an old invoice. The file is read here and then forgotten."""
        form = await request.form()
        upload = next(
            (u for u in [form.get("file"), *form.getlist("files")] if getattr(u, "filename", None)),
            None,
        )
        pasted = str(form.get("text") or "").strip()[:MAX_PASTED_TEXT]
        if not getattr(upload, "filename", None) and not pasted:
            flash("Zieh eine alte Rechnung auf das Feld oder füg ihren Text ein.", "error")
            return back("/setup/business_details")
        try:
            if pasted:
                text = pasted
            else:
                with TemporaryDirectory() as tmp:
                    saved = await _save_upload(upload, Path(tmp))
                    text = extract_text(saved).text
            details = setup.own_details(text, nlp())
        except (IngestError, RuntimeError) as exc:
            flash(str(exc), "error")
            return back("/setup/business_details")
        if not details:
            flash("In dieser Datei habe ich keine Angaben gefunden. Trag sie einfach von Hand ein.")
            return back("/setup/business_details")
        return setup_page(request, "business_details", imported=details)

    @app.post("/setup/{step}", dependencies=guarded)
    async def setup_save(request: Request, step: str):
        if step not in setup.STEPS:
            raise HTTPException(404)
        form = await request.form()
        action = str(form.get("action", "next"))
        _, following = setup.neighbours(step)
        current = settings()
        try:
            updated = settings_from_form(current, form)
        except (BillingError, ValueError) as exc:
            flash(
                str(exc)
                if isinstance(exc, BillingError)
                else "Das Nummern-Schema braucht genau einen Zähler (NNN)."
                if "pattern" in str(exc)
                else "Bitte nur Zahlen bei Preisen.",
                "error",
                feld=getattr(exc, "feld", None)
                or ("number_pattern" if "pattern" in str(exc) else None),
            )
            return back(f"/setup/{step}")
        if step == "done":
            updated = replace(updated, setup_done=True)
        updated.save(ws.settings_file)

        if step == "store_key" and str(form.get("api_key", "")).strip():
            key = str(form.get("api_key"))
            if not key_problem(updated.provider, key) and save_key(updated.provider, key):
                flash(f"Dein Schlüssel liegt jetzt {umgebung.schluessel_ort()}.")
            else:
                key_not_saved(updated.provider, key, following)
                return back(f"/setup/{step}")
        if action == "test":
            try:
                sent, result, usage = setup.connection_test(provider_for(updated), updated, nlp())
            except (ProviderError, RuntimeError) as exc:
                return setup_page(request, step, test_error=str(exc))
            return setup_page(request, step, test_sent=sent, test_result=result, test_usage=usage)
        if action == "redact":
            text = str(form.get("text", ""))[:5000]
            try:
                shown = setup.redaction_test(text, updated, nlp())
            except RuntimeError as exc:
                flash(str(exc), "error")
                return back(f"/setup/{step}")
            return setup_page(request, step, redaction=shown, sample=text)
        if action == "trick":
            try:
                demo = setup.trick_demo(updated, nlp())
            except RuntimeError as exc:
                flash(str(exc), "error")
                return back(f"/setup/{step}")
            return setup_page(request, step, demo=demo)
        if action == "skip_learning":
            return back("/setup/first_invoice")
        if action == "sample":
            (ws.inbox / "beispiel-notiz.txt").write_text(setup.FIRST_NOTE, encoding="utf-8")
            flash("Die Beispielnotiz liegt im Eingang. Auf „Heute“ kannst du sie lesen lassen.")
            return back(f"/setup/{following}")
        if step == "done":
            flash("Alles eingerichtet. Schön, dass du da bist.", cue="done")
            return back("/")
        return back(f"/setup/{following}")

    @app.post("/setup-spaeter", dependencies=guarded)
    def setup_later():
        """Leave the setup for now. Heute then shows what's still missing."""
        replace(settings(), setup_done=True).save(ws.settings_file)
        flash("Du kannst die Einrichtung jederzeit in den Einstellungen fortsetzen.")
        return back("/")

    @app.post("/sound", dependencies=guarded)
    async def sound_toggle(request: Request):
        current = settings()
        replace(current, sound=not current.sound).save(ws.settings_file)
        return back(str((await request.form()).get("back") or "/"))

    # ----- Datensicherung -----

    def _sicherung_page(request: Request, **extra):
        current = settings()
        # After an error the place Fritz chose comes back, so he needn't choose again.
        ziel = request.query_params.get("ziel") or current.backup_dir
        return page(
            request,
            "sicherung.html",
            ziel=ziel,
            letzte=sicherung.letzte(ziel),
            faellig=sicherung.faellig(ziel),
            vorschlaege=sicherung.vorschlaege(),
            zettel=request.query_params.get("zettel") == "1",
            zettel_neu=not (ziel and sicherung.letzte(ziel)),
            min_neu=sicherung.MIN_NEU,
            **extra,
        )

    def _zurueck_zur_sicherung(ziel: str, zettel: bool) -> RedirectResponse:
        params = {}
        if zettel:
            params["zettel"] = "1"
        if ziel:
            params["ziel"] = ziel
        return back(f"/sicherung?{urlencode(params)}#sichern")

    @app.get("/sicherung", response_class=HTMLResponse)
    def sicherung_page(request: Request):
        return _sicherung_page(request)

    @app.post("/sicherung", dependencies=guarded)
    async def sicherung_jetzt(request: Request):
        form = await request.form()
        ziel = str(form.get("anderer") or form.get("ziel") or settings().backup_dir).strip()
        if not ziel:
            flash("Wähl zuerst aus, wohin die Sicherung soll.", "error")
            return back("/sicherung")
        vom_zettel = str(form.get("passwort") or "").strip()
        try:
            if vom_zettel:
                sicherung.kennwort_merken(vom_zettel, ziel)
                passwort = vom_zettel
            else:
                passwort = sicherung.kennwort(ziel=ziel)
            path = sicherung.sichern(ws, Path(ziel), passwort)
        except sicherung.KennwortFehlt as exc:
            flash(str(exc), "error")
            return _zurueck_zur_sicherung(ziel, zettel=True)
        except sicherung.SicherungError as exc:
            flash(str(exc), "error")
            return _zurueck_zur_sicherung(ziel, zettel=bool(vom_zettel))
        if ziel != settings().backup_dir:
            replace(settings(), backup_dir=ziel).save(ws.settings_file)
        flash(f"Gesichert: {path.name} in {path.parent}.", cue="saved", stamp="Gesichert")
        if sicherung.selbe_platte(ws, Path(ziel)):
            flash(
                "Achtung: Diese Sicherung liegt auf derselben Festplatte wie FRITS. Geht der Mac "
                "kaputt, ist sie mit weg. Nimm besser einen USB-Stick, eine externe Festplatte "
                "oder iCloud Drive.",
                "error",
            )
        return back(str(form.get("back") or "/sicherung"))

    @app.post("/sicherung/waehlen", dependencies=guarded)
    def sicherung_waehlen():
        """The Mac's own folder window, so nobody has to type a path."""
        chosen = system.choose_folder("Wohin soll die FRITS-Sicherung?")
        if chosen is None:
            flash("Kein Ordner gewählt. Du kannst den Ort auch unten eintippen.")
            return back("/sicherung#sichern")
        replace(settings(), backup_dir=str(chosen)).save(ws.settings_file)
        flash(f"Die Sicherung kommt nach {chosen}. Drück jetzt „Jetzt sichern“.")
        return back("/sicherung#sichern")

    @app.post("/sicherung/kennwort", dependencies=guarded)
    def sicherung_kennwort(request: Request):
        try:
            shown = sicherung.kennwort(ziel=settings().backup_dir or None)
        except sicherung.KennwortFehlt as exc:
            flash(str(exc), "error")
            return back("/sicherung?zettel=1#sichern")
        except sicherung.SicherungError as exc:
            flash(str(exc), "error")
            return back("/sicherung")
        return _sicherung_page(request, kennwort=shown)

    @app.post("/sicherung/zurueck", dependencies=guarded)
    async def sicherung_zurueck(request: Request):
        form = await request.form()
        upload = form.get("datei")
        passwort = str(form.get("passwort") or "").strip()
        if not getattr(upload, "filename", "") or not passwort:
            flash("Bitte die Sicherung auswählen und ihr Passwort eingeben.", "error")
            return back("/sicherung")
        stamp = today().strftime("%d.%m.%Y")
        ziel = ws.root.parent / f"FRITS aus Sicherung {stamp}"
        n = 2
        while ziel.exists():
            ziel = ws.root.parent / f"FRITS aus Sicherung {stamp} ({n})"
            n += 1
        with TemporaryDirectory() as work:
            tmp = Path(work) / "sicherung.zip"
            tmp.write_bytes(await upload.read())
            try:
                sicherung.wiederherstellen(tmp, passwort, ziel)
            except sicherung.SicherungError as exc:
                flash(str(exc), "error")
                return back("/sicherung")
        return _sicherung_page(request, zurueck=ziel)

    # ----- Feierabend -----

    @app.post("/feierabend", dependencies=guarded)
    def feierabend():
        spiel_stand.feierabend(db(), today())
        flash("", cue="shutdown")
        return back("/feierabend")

    @app.get("/feierabend", response_class=HTMLResponse)
    def feierabend_page(request: Request):
        conn = db()
        day = today().isoformat()
        made = conn.execute(
            "SELECT COUNT(*) FROM invoices WHERE issue_date = ? AND cancels IS NULL"
            " AND number NOT IN (SELECT cancels FROM invoices WHERE cancels IS NOT NULL)",
            (day,),
        ).fetchone()[0]
        read = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE substr(received, 1, 10) = ?", (day,)
        ).fetchone()[0]
        invoices = list_invoices(conn, today())
        mood, text = mascot_line("feierabend")
        return page(
            request,
            "feierabend.html",
            made=made,
            read=read,
            drafts=len(_open_drafts(conn, settings())),
            overdue=sum(1 for i in invoices if i["status"] == "überfällig"),
            waiting=len(_inbox_files(ws)),
            mood=mood,
            text=text,
            karten=spiel_stand.abgleichen(conn, today(), fritte_datei(ws.root))
            if settings().haustier
            else 0,
            sicherung_ziel=settings().backup_dir,
            sicherung_faellig=sicherung.faellig(settings().backup_dir),
        )

    @app.get("/health")
    def health(request: Request):
        if request.query_params.get("reopen"):
            # The goodbye page found FRITS again: it takes the user back, no second tab needed.
            app.state.tab_waiting = True
        return HTMLResponse("FRITS")

    @app.post("/quit", dependencies=guarded)
    def quit_frits(request: Request):
        stop()
        return page(request, "quit.html", mac_app=os.environ.get("FRITS_APP") == "1")

    @app.get("/mailto")
    def mailto_page(request: Request):
        return page(request, "mailto.html", link=getattr(app.state, "mailto", ""))

    @app.get("/ueber")
    def about_page(request: Request):
        return page(
            request,
            "ueber.html",
            about=about.ABOUT,
            placeholder=about.is_placeholder,
            bug_address=about.BUG_ADDRESS,
            version=__version__,
            computer=about.system_info(),
        )

    @app.post("/ueber/fehler", dependencies=guarded)
    def about_bug_report(request: Request):
        subject, body = about.bug_email()
        result = system.compose_email(about.BUG_ADDRESS, subject, body, None)
        if not result.opened:
            return page(request, "mailto.html", link=result.mailto, attachment=False)
        flash(
            "Die Fehler-Mail ist in Mail geöffnet. Schreib kurz, was passiert ist, und drück auf Senden."
        )
        return back("/ueber")

    from frits.web.steuer import register_steuer

    register_steuer(
        app,
        ws=ws,
        page=page,
        back=back,
        flash=flash,
        settings=settings,
        guarded=guarded,
        today=today,
    )

    register_haustier(
        app,
        ws=ws,
        page=page,
        back=back,
        flash=flash,
        db=db,
        settings=settings,
        guarded=guarded,
        today=today,
        aufgeraeumt=lambda: not _inbox_files(ws) and not _open_drafts(db(), settings()),
    )

    def liste_hinweis(path: str, current) -> list[dict]:
        """Work just done that fits a point on "Deine Liste": offer to tick it off.
        Runs after Fritte's note, which has already turned the work into cards."""
        if not current.haustier or path.startswith(("/steuer", "/mailto", "/feierabend")):
            return []  # in the Steuer-Quest it waits for the next page
        return [
            {"text": f"Passt das zu „{_punkt_text(p.text)}“ auf deiner Liste?", "kind": "karte",
             "cue": None, "stamp": None, "undo": False, "abhaken": p.id}
            for p in aufgaben.vorschlaege(db())
        ]  # fmt: skip

    app.state.hinweise.append(liste_hinweis)

    from frits.web.lernen import register_lernen

    register_lernen(
        app,
        page=page,
        back=back,
        flash=flash,
        db=db,
        settings=settings,
        customers=customers,
        nlp=nlp,
        guarded=guarded,
    )

    from frits.web.notfall import register_notfall

    register_notfall(
        app,
        page=page,
        back=back,
        flash=flash,
        settings=settings,
        customers=customers,
        nlp=nlp,
        guarded=guarded,
        provider_for=provider_for,
        ai_ready=ai_ready,
        db=db,
    )
    return app


# ---------- helpers ----------

TEXT_FIELDS = ("name", "company", "email", "phone", "steuernummer", "ust_id", "bank", "iban", "bic")
NOTIZ_MAX = 1500
NUMBER_FIELDS = {
    "hourly_rate": ("Stundensatz", "90"),
    "km_rate": ("Kilometerpreis", "0,30"),
    "vat_rate": ("Steuersatz", "19"),
}
# What each provider's keys start with, so a key pasted into the wrong box is caught.
KEY_STARTS = {"claude": "sk-ant-", "openrouter": "sk-or-", "openai": "sk-"}


def key_problem(provider: str, key: str) -> str | None:
    """A plain reason why this can't be the provider's API key, or None."""
    key = key.strip()
    start = KEY_STARTS.get(provider)
    if any(ch.isspace() for ch in key):
        return "Im Schlüssel steht ein Leerzeichen. Kopier ihn bitte noch einmal ganz."
    if start and not key.startswith(start):
        if provider == "openai" and key.startswith("sk-ant-"):
            return "Das ist ein Schlüssel von Claude (Anthropic). Wähl oben Claude aus."
        return f"Ein Schlüssel für diesen Anbieter beginnt mit „{start}“. Kopier ihn bitte noch einmal."
    return None


CHECKBOXES = ("strict_local", "sound", "haustier", "auto_read")
EFFECTS = ("ruhig", "verspielt")


def _upload_name(path: Path) -> str:
    """The name Fritz gave the file, without the " (1)" FRITS added to avoid a clash."""
    return re.sub(r" \(\d+\)(?=\.[^.]*$|$)", "", Path(path).name)


class FeldFehler(BillingError):
    """A form value that can't be used, and which field it was, so the page can point at it."""

    def __init__(self, text: str, feld: str):
        super().__init__(text)
        self.feld = feld


def settings_from_form(current: Settings, form) -> Settings:
    """Apply the fields this form contains. Checkboxes count only if the form lists them,
    because an unticked box isn't sent at all."""
    changes: dict = {}
    for key in TEXT_FIELDS:
        if key in form:
            changes[key] = str(form.get(key, "")).strip()
    if "iban" in changes:
        changes["iban"] = changes["iban"].replace(" ", "").upper()
    if "address" in form:
        changes["address"] = _one_line(form.get("address")) or ""
    if "verfahren_notiz" in form:
        changes["verfahren_notiz"] = " ".join(str(form.get("verfahren_notiz", "")).split())[
            :NOTIZ_MAX
        ]
    for key, empty in (("hourly_rate", 0), ("km_rate", 0), ("vat_rate", 19)):
        if key in form:
            try:
                changes[key] = parse_amount(str(form.get(key))) or Decimal(empty)
            except (BillingError, ValueError, ArithmeticError) as exc:
                label, example = NUMBER_FIELDS[key]
                raise FeldFehler(
                    f"Beim {label} bitte nur eine Zahl eingeben, zum Beispiel {example}.", key
                ) from exc
    if "number_pattern" in form:
        pattern = str(form.get("number_pattern", "")).strip() or DEFAULT_PATTERN
        if not valid_pattern(pattern):
            raise ValueError("pattern")
        changes["number_pattern"] = pattern
    if "last_number_before" in form:
        changes["last_number_before"] = str(form.get("last_number_before", "")).strip()[:40]
    if "price_name" in form:
        changes["prices"] = _price_rows(form)
    if "payment_days" in form:
        try:
            days = int(str(form.get("payment_days") or 14).strip())
        except ValueError as exc:
            raise FeldFehler(
                "Beim Zahlungsziel bitte nur die Zahl der Tage eingeben, zum Beispiel 14.",
                "payment_days",
            ) from exc
        if not 0 <= days <= 120:
            raise FeldFehler(
                "Das Zahlungsziel muss zwischen 0 und 120 Tagen liegen.", "payment_days"
            )
        changes["payment_days"] = days
    if "tax_mode" in form:
        changes["tax_mode"] = (
            "regelbesteuerung" if form.get("tax_mode") == "regelbesteuerung" else "kleinunternehmer"
        )
    if form.get("provider") in PROVIDERS:
        changes["provider"] = form.get("provider")
    if form.get("effects") in EFFECTS:
        changes["effects"] = form.get("effects")
    if form.get("music") in _music_moods():
        changes["music"] = form.get("music")
    for key in form.getlist("checkboxes"):
        if key in CHECKBOXES:
            changes[key] = form.get(key) == "on"
    return replace(current, **changes)


MAX_UPLOAD = 30 * 1024 * 1024
MAX_PASTED_TEXT = 200_000


def _local(target) -> str:
    """Only addresses inside FRITS, so a form can't send Fritz to another website."""
    target = str(target or "/")
    return target if target.startswith("/") and not target.startswith("//") else "/"


def pasted_name(prefix: str = "notiz") -> str:
    """A readable name for pasted text, e.g. "notiz-2026-11-01-1403.txt"."""
    from datetime import datetime

    return f"{prefix}-{datetime.now():%Y-%m-%d-%H%M}.txt"


async def _saved_uploads(form, folder: Path, prefix: str = "notiz") -> tuple[list[Path], list[str]]:
    """Store every file of the form (field "files") and pasted text (field "text") in `folder`."""
    saved, problems = [], []
    for upload in form.getlist("files"):
        if not getattr(upload, "filename", None):
            continue
        try:
            saved.append(await _save_upload(upload, folder))
        except IngestError as exc:
            problems.append(f"{Path(upload.filename).name}: {exc}")
    text = str(form.get("text") or "").strip()
    if text:
        if len(text) > MAX_PASTED_TEXT:
            problems.append("Der eingefügte Text ist sehr lang. Bitte leg ihn als Datei ab.")
        else:
            saved.append(_save_text(text, folder, prefix))
    return saved, problems


async def _upload_text(upload) -> str:
    """The text inside one uploaded file, read here and then forgotten."""
    name = Path(str(upload.filename).replace("\\", "/")).name
    if name.lower().endswith(".vcf"):  # a contact dragged out of the Contacts app
        data = await upload.read(MAX_UPLOAD)
        return data.decode("utf-8", errors="replace")
    with TemporaryDirectory() as tmp:
        saved = await _save_upload(upload, Path(tmp))
        return extract_text(saved).text


def _save_text(text: str, folder: Path, prefix: str = "notiz") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    name = pasted_name(prefix)
    target, n = folder / name, 1
    while target.exists():
        target = folder / f"{Path(name).stem} ({n}).txt"
        n += 1
    target.write_text(text.replace("\r\n", "\n") + "\n", encoding="utf-8")
    return target


async def _save_upload(upload, folder: Path) -> Path:
    """Store one uploaded file under its own name (never a path), without overwriting anything."""
    name = Path(str(upload.filename).replace("\\", "/")).name.strip()
    if not name or name.startswith("."):
        raise IngestError("Dieser Dateiname geht nicht.")
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED:
        raise IngestError(f"FRITS kann Dateien vom Typ '{suffix or 'ohne Endung'}' nicht lesen.")
    data = await upload.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise IngestError("Die Datei ist größer als 30 MB.")
    folder.mkdir(parents=True, exist_ok=True)
    target, n = folder / name, 1
    while target.exists():
        target = folder / f"{Path(name).stem} ({n}){suffix}"
        n += 1
    target.write_bytes(data)
    return target


def _price_rows(form) -> list[dict]:
    """The price list table: one row per service, empty rows are skipped."""
    rows = []
    for name, unit, price in zip(
        form.getlist("price_name"),
        form.getlist("price_unit"),
        form.getlist("price_value"),
        strict=False,
    ):
        name = str(name).strip()
        if not name:
            continue
        amount = parse_amount(str(price))
        if amount is None or amount < 0:
            raise ValueError(f"no price for {name}")
        rows.append(
            {"name": name[:80], "unit": unit if unit in UNITS else "Stk", "price": str(amount)}
        )
    return rows


def _open_drafts(conn, current: Settings) -> list[dict]:
    rows = conn.execute("SELECT * FROM drafts WHERE status = 'draft' ORDER BY id DESC").fetchall()
    result = []
    for row in rows:
        draft = Draft.from_json(row["data"])
        result.append(
            {
                "id": row["id"],
                "created": row["created"],
                "draft": draft,
                "sums": draft_totals(draft, current),
                "ready": not blockers(draft, current),
            }
        )
    return result


def _inbox_files(ws: Workspace) -> list[Path]:
    return sorted(p for p in ws.inbox.iterdir() if p.is_file() and not p.name.startswith("."))


def _check_me_files(ws: Workspace) -> list[dict]:
    result = []
    for path in sorted(ws.check_me.iterdir()):
        if not path.is_file() or path.name.endswith(".grund.txt") or path.name.startswith("."):
            continue
        reason = path.with_name(path.name + ".grund.txt")
        result.append(
            {
                "name": path.name,
                "reason": reason.read_text(encoding="utf-8").strip() if reason.exists() else "",
            }
        )
    return result


def _apply_form(draft: Draft, form, customer_by, current: Settings) -> None:
    chosen = str(form.get("customer", ""))
    if chosen:
        set_customer(draft, customer_by(chosen))
    lines = []
    fields = zip(
        form.getlist("description"),
        form.getlist("date"),
        form.getlist("quantity"),
        form.getlist("unit"),
        form.getlist("price"),
        strict=False,
    )
    # The VAT column only shows with Regelbesteuerung; without it every line keeps the normal rate.
    rates = [str(v) for v in form.getlist("vat")]
    for index, (description, when, quantity, unit, price) in enumerate(fields):
        if not str(description).strip() and not str(quantity).strip():
            continue  # the empty row at the bottom
        what = str(description).strip() or "der neuen Position"
        try:
            amount = parse_amount(str(quantity))
        except BillingError:
            raise FeldFehler(
                f"Bei der Menge von „{what}“ bitte nur eine Zahl eingeben, zum Beispiel 2.",
                f"quantity:{index}",
            ) from None
        if amount is None:
            raise FeldFehler(f"Bei „{what}“ fehlt die Menge.", f"quantity:{index}")
        unit = str(unit) if unit in UNITS else "Stk"
        try:
            given = parse_amount(str(price))
        except BillingError:
            raise FeldFehler(
                f"Beim Preis von „{what}“ bitte nur eine Zahl eingeben, zum Beispiel 80.",
                f"price:{index}",
            ) from None
        if given is None:  # an empty price field takes the price list's price, if one fits
            customer = customer_by(draft.customer_id) if draft.customer_id else None
            given = unit_price(unit, customer, current, str(description))
        chosen_rate = rates[index] if index < len(rates) else ""
        rate = Decimal(chosen_rate) if chosen_rate in ("19", "7", "0") else None
        lines.append(reviewed_line(str(description), str(when), amount, unit, given, rate))
    draft.lines = lines
    if form.get("questions_done") == "on":
        draft.questions = []


def _paid_day(value, day: date) -> date:
    """The payment date from the form; empty means today. Never in the future."""
    text = str(value or "").strip()
    if not text:
        return day
    try:
        paid = date.fromisoformat(text)
    except ValueError:
        raise BillingError("Das Zahldatum verstehe ich nicht.") from None
    if paid > day:
        raise BillingError("Das Zahldatum liegt in der Zukunft.")
    return paid


def _local(url, default: str = "/invoices") -> str:
    """A page of FRITS to go back to; anything else (another site) goes to the default."""
    text = str(url or "")
    safe = text.startswith("/") and not text.startswith(("//", "/\\"))
    return text if safe else default


def elster_amount(value, whole: bool = True) -> str:
    """How ELSTER wants an amount typed: "1234" (whole euros) or "123,45", no dots."""
    amount = Decimal(value)
    if whole:
        return str(int(amount))
    return f"{amount:.2f}".replace(".", ",")


def price_in(value) -> str:
    """A price in an input field: "0,30" and "59,80", not "0,3"; more decimals stay."""
    if value in (None, ""):
        return ""
    value = Decimal(value)
    if value == value.quantize(Decimal("0.01")):
        return f"{value:.2f}".replace(".", ",")
    return de_number(value)


def _draft_hints(draft: Draft, customer: Customer | None) -> list[str]:
    """Things worth a second look before the number is fixed. They never block."""
    hints = []
    if customer and customer.gutschrift and draft.kind != "quote":
        hints.append(
            f"{customer.display_name} rechnet per Gutschrift ab. Stell dann keine eigene Rechnung, "
            "sonst gibt es zwei Rechnungen über dieselbe Leistung (§ 14c UStG). Trag die "
            "Gutschrift stattdessen beim Kunden ein, dann zählt das Geld trotzdem."
        )
    if customer and customer.privat and customer.land != "DE":
        hints.append(
            "Privatperson im Ausland: FRITS rechnet mit deutscher Umsatzsteuer. Bei manchen "
            "Leistungen, etwa Nutzungsrechten an Privatleute außerhalb der EU, ist das anders. "
            "Frag im Zweifel deine Steuerberatung."
        )
    for line in draft.lines:
        if line.unit in ("pauschal", "Stk") and line.unit_price and 0 < line.unit_price < 10:
            hints.append(
                f"Sehr kleiner Preis bei „{line.description}“: {eur(line.unit_price)}. "
                "Stimmt das? Tausend schreibst du als 1.400 oder 1400."
            )
    return hints


def _vat_id(value, land: str) -> str | None:
    """EU VAT IDs are written without spaces ("ATU12345678"); others stay as typed (Swiss UID)."""
    text = " ".join(str(value or "").split())
    if not text:
        return None
    return text.replace(" ", "").upper() if land in EU_COUNTRIES or land == "DE" else text


def _one_line(value) -> str | None:
    """Addresses are typed on several lines and stored as one: "Weg 1, 12345 Ort"."""
    parts = [p.strip() for p in str(value or "").replace("\r", "").split("\n")]
    return ", ".join(p for p in parts if p) or None


def _pdf(path: str | Path | None, folder: Path) -> FileResponse:
    if not path:
        raise HTTPException(404)
    pdf = Path(path).resolve()
    if not pdf.is_file() or folder.resolve() not in pdf.parents:
        raise HTTPException(404)
    return FileResponse(
        pdf, media_type="application/pdf", filename=pdf.name, content_disposition_type="inline"
    )

"""The FRITS app in the browser. It runs only on Fritz's computer (127.0.0.1) and only he can use it.

Every page is plain HTML rendered here. Every button that changes something is a form POST
that carries a secret token, so another website open in the same browser can't press
FRITS's buttons for him (that attack is called CSRF).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from contextlib import ExitStack, asynccontextmanager
from dataclasses import replace
from datetime import date
from decimal import Decimal
from importlib.resources import as_file, files
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup

from frits import emails, setup, store, system
from frits.billing import (
    BillingError,
    Draft,
    approve,
    blockers,
    cancel,
    discard_draft,
    list_invoices,
    load_draft,
    mark_paid,
    new_draft,
    parse_amount,
    reviewed_line,
    save_draft,
    set_customer,
    totals,
)
from frits.customers import Customer, load_customers, note_id, save_note
from frits.ingest import IngestError, extract_text
from frits.providers import KEY_ENV, ProviderError, get_api_key, make_provider, store_api_key
from frits.quotes import accept_quote, create_quote, decline_quote, list_quotes
from frits.reminders import create_reminder
from frits.render import eur, qty
from frits.settings import Settings, Workspace

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
    env.globals.update(mascot=mascot_markup, units=UNITS, providers=PROVIDERS)
    return Jinja2Templates(env=env)


# ---------- the app ----------


def create_app(
    ws: Workspace | None = None,
    provider_factory: Callable[[Settings], object] | None = None,
    nlp_loader: Callable[[], object] | None = None,
    today: Callable[[], date] = date.today,
) -> FastAPI:
    ws = (ws or Workspace.default()).ensure()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.files.close()

    app = FastAPI(title="FRITS", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.token = secrets.token_urlsafe(32)
    app.state.flashes = []
    app.state.nlp = None
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

    def flash(text: str, kind: str = "ok", cue: str | None = None) -> None:
        app.state.flashes.append({"text": text, "kind": kind, "cue": cue})

    def page(request: Request, name: str, **context) -> HTMLResponse:
        current = settings()
        flashes, app.state.flashes = app.state.flashes, []
        context.update(
            request=request,
            token=app.state.token,
            settings=current,
            flashes=flashes,
            sounds=app.state.sounds_url if current.sound else None,
            nav=request.url.path,
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

    @app.middleware("http")
    async def only_this_computer(request: Request, call_next):
        # A web page can make the browser talk to 127.0.0.1 under a different name
        # (DNS rebinding). FRITS only answers when it's called by its real name.
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost", "testserver"):
            return HTMLResponse("FRITS antwortet nur auf diesem Computer.", status_code=403)
        return await call_next(request)

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
        if not current.is_complete:
            moment = ("welcome", "Hallo! Bevor wir loslegen: Trag kurz deine Angaben ein.")
        elif drafts or check_me:
            moment = mascot_line("needs_you" if check_me else "draft_ready")
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
            mood=moment[0],
            moment=moment[1],
        )

    @app.post("/inbox/process", dependencies=guarded)
    def process_inbox():
        from frits.pipeline import process_file

        current = settings()
        files_waiting = _inbox_files(ws)
        if not files_waiting:
            flash("Der Eingang ist leer. Leg eine Notiz, E-Mail oder ein Foto hinein.")
            return back("/")
        try:
            provider = provider_for(current)
            model = nlp()
        except (ProviderError, RuntimeError) as exc:
            flash(str(exc), "error")
            return back("/settings" if isinstance(exc, ProviderError) else "/")
        counts = {"drafted": 0, "check-me": 0, "receipt": 0, "skipped": 0}
        for path in files_waiting:
            outcome = process_file(path, ws, current, provider, model)
            counts[outcome.status] += 1
            if outcome.status == "check-me" and path.exists():
                flash(f"{path.name}: {outcome.message}", "error")
                break  # e.g. the AI isn't reachable: the next files would fail the same way
        parts = []
        if counts["drafted"]:
            parts.append(f"{counts['drafted']} neue Entwürfe")
        if counts["receipt"]:
            parts.append(f"{counts['receipt']} Belege")
        if counts["check-me"]:
            parts.append(f"{counts['check-me']} zum Prüfen")
        if counts["skipped"]:
            parts.append(f"{counts['skipped']} schon bekannt")
        flash("Fertig gelesen: " + (", ".join(parts) or "nichts Neues") + ".", cue="done")
        return back("/")

    @app.post("/open/{folder}", dependencies=guarded)
    def open_folder(folder: str):
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
        return back("/" if folder != "customers" else "/customers")

    @app.get("/check-me", response_class=HTMLResponse)
    def check_me_page(request: Request):
        return page(request, "check_me.html", files=_check_me_files(ws))

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
        return page(
            request,
            "draft.html",
            draft=draft,
            row=row,
            sums=totals(draft.lines, current),
            problems=blockers(draft, current) if row["status"] == "draft" else [],
            customers=customers(),
            original=original,
            sent_text=job["sent_text"] if job else None,
        )

    @app.post("/drafts/{draft_id}", dependencies=guarded)
    async def draft_save(request: Request, draft_id: int):
        form = await request.form()
        conn = db()
        try:
            draft, _ = load_draft(conn, draft_id)
            _apply_form(draft, form, customer_by)
            save_draft(conn, draft_id, draft)
        except BillingError as exc:
            flash(str(exc), "error")
            return back(f"/drafts/{draft_id}")
        action = form.get("action", "save")
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
            flash(f"Rechnung {number} ist fertig. Ein Punkt weniger im Kopf.", cue="done")
            return back(f"/invoices#r-{number}")
        if action == "quote":
            try:
                number, _ = create_quote(conn, draft, current, ws.quotes, today())
            except BillingError as exc:
                flash(str(exc), "error")
                return back(f"/drafts/{draft_id}")
            discard_draft(conn, draft_id)
            flash(f"Angebot {number} ist fertig.", cue="done")
            return back(f"/quotes#a-{number}")
        flash("Gespeichert.")
        return back(f"/drafts/{draft_id}")

    @app.post("/drafts/{draft_id}/discard", dependencies=guarded)
    def draft_discard(draft_id: int):
        try:
            discard_draft(db(), draft_id)
            flash("Entwurf verworfen. Es wurde keine Nummer verbraucht.")
        except BillingError as exc:
            flash(str(exc), "error")
        return back("/drafts")

    # ----- Rechnungen -----

    @app.get("/invoices", response_class=HTMLResponse)
    def invoices_page(request: Request):
        return page(request, "invoices.html", invoices=list_invoices(db(), today()))

    @app.get("/invoices/{number}/pdf")
    def invoice_pdf(number: str):
        row = db().execute("SELECT pdf_path FROM invoices WHERE number = ?", (number,)).fetchone()
        return _pdf(row["pdf_path"] if row else None, ws.invoices)

    @app.post("/invoices/{number}/paid", dependencies=guarded)
    def invoice_paid(number: str):
        try:
            mark_paid(db(), number, today())
            flash(f"Rechnung {number} ist bezahlt. Schön!", cue="done")
        except BillingError as exc:
            flash(str(exc), "error")
        return back("/invoices")

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
        if row is None:
            raise HTTPException(404)
        customer = customer_by(row["customer"])
        subject, body = emails.invoice_email(dict(row), customer, settings())
        result = system.compose_email(
            customer.email if customer and customer.email else "",
            subject,
            body,
            Path(row["pdf_path"]),
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
        subject, body = emails.reminder_email(dict(row), new_due, customer, current)
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
            quote["gross"] = totals(quote["draft"].lines, current).gross
        return page(request, "quotes.html", quotes=quotes, customers=customers())

    @app.get("/quotes/{number}/pdf")
    def quote_pdf(number: str):
        row = db().execute("SELECT pdf_path FROM quotes WHERE number = ?", (number,)).fetchone()
        return _pdf(row["pdf_path"] if row else None, ws.quotes)

    @app.post("/quotes/{number}/accept", dependencies=guarded)
    def quote_accept(number: str):
        try:
            draft_id = accept_quote(db(), number)
        except BillingError as exc:
            flash(str(exc), "error")
            return back("/quotes")
        flash(f"Angebot {number} angenommen. Hier ist der Rechnungsentwurf dazu.", cue="done")
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
            dict(row), totals(draft.lines, current).gross, customer, current
        )
        result = system.compose_email(
            customer.email if customer and customer.email else "",
            subject,
            body,
            Path(row["pdf_path"]),
        )
        return _after_compose(conn, "quotes", number, result, "/quotes")

    # ----- Kunden -----

    @app.get("/customers", response_class=HTMLResponse)
    def customers_page(request: Request):
        return page(request, "customers.html", customers=customers())

    @app.get("/customers/new", response_class=HTMLResponse)
    def customer_new(request: Request):
        return page(
            request, "customer.html", customer=None, back_to=request.query_params.get("back")
        )

    @app.get("/customers/{customer_id}", response_class=HTMLResponse)
    def customer_page(request: Request, customer_id: str):
        customer = customer_by(customer_id)
        if customer is None:
            raise HTTPException(404)
        return page(request, "customer.html", customer=customer, back_to=None)

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
        customer = Customer(
            id=Path(customer_id).name,
            name=name,
            company=company,
            address=_one_line(form.get("address")),
            email=str(form.get("email", "")).strip() or None,
            phone=str(form.get("phone", "")).strip() or None,
            hourly_rate=rate,
            aliases=tuple(a.strip() for a in str(form.get("aliases", "")).split(",") if a.strip()),
        )
        save_note(ws.customers, customer)
        flash(f"{customer.display_name} ist gespeichert.")
        target = str(form.get("back") or "")
        return back(target if target.startswith("/drafts/") else "/customers")

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
        return page(request, "settings.html", keys=keys, folder=ws.root)

    @app.post("/settings", dependencies=guarded)
    async def settings_save(request: Request):
        form = await request.form()
        try:
            updated = settings_from_form(settings(), form)
        except (BillingError, ValueError):
            flash("Bitte nur Zahlen bei Preisen und Zahlungsziel.", "error")
            return back("/settings")
        updated.save(ws.settings_file)
        if save_key(updated.provider, str(form.get("api_key", ""))):
            flash("Gespeichert. Dein Schlüssel liegt jetzt im Schlüsselbund dieses Macs.")
        elif str(form.get("api_key", "")).strip():
            flash("Der Schlüssel ließ sich nicht im Schlüsselbund speichern.", "error")
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
            "inbox": ws.inbox,
            "sample": setup.sample_note(current),
        }
        context.update(extra)
        return page(request, "setup.html", **context)

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
        except (BillingError, ValueError):
            flash("Bitte nur Zahlen bei Preisen.", "error")
            return back(f"/setup/{step}")
        if step == "done":
            updated = replace(updated, setup_done=True)
        updated.save(ws.settings_file)

        if step == "store_key" and str(form.get("api_key", "")).strip():
            if save_key(updated.provider, str(form.get("api_key"))):
                flash("Dein Schlüssel liegt jetzt im Schlüsselbund dieses Macs.")
            else:
                flash("Der Schlüssel ließ sich nicht im Schlüsselbund speichern.", "error")
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
        if action == "sample":
            (ws.inbox / "beispiel-notiz.txt").write_text(setup.FIRST_NOTE, encoding="utf-8")
            flash("Die Beispielnotiz liegt im Eingang. Auf „Heute“ kannst du sie lesen lassen.")
            return back(f"/setup/{following}")
        if step == "done":
            flash("Alles eingerichtet. Schön, dass du da bist.", cue="done")
            return back("/")
        return back(f"/setup/{following}")

    @app.post("/sound", dependencies=guarded)
    async def sound_toggle(request: Request):
        current = settings()
        replace(current, sound=not current.sound).save(ws.settings_file)
        return back(str((await request.form()).get("back") or "/"))

    # ----- Feierabend -----

    @app.post("/feierabend", dependencies=guarded)
    def feierabend():
        flash("", cue="shutdown")
        return back("/feierabend")

    @app.get("/feierabend", response_class=HTMLResponse)
    def feierabend_page(request: Request):
        conn = db()
        day = today().isoformat()
        made = conn.execute(
            "SELECT COUNT(*) FROM invoices WHERE issue_date = ? AND cancels IS NULL", (day,)
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
        )

    @app.get("/mailto")
    def mailto_page(request: Request):
        return page(request, "mailto.html", link=getattr(app.state, "mailto", ""))

    return app


# ---------- helpers ----------

TEXT_FIELDS = ("name", "company", "email", "phone", "steuernummer", "ust_id", "bank", "iban", "bic")
CHECKBOXES = ("strict_local", "sound")


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
    for key, empty in (("hourly_rate", 0), ("km_rate", 0), ("vat_rate", 19)):
        if key in form:
            changes[key] = parse_amount(str(form.get(key))) or Decimal(empty)
    if "payment_days" in form:
        changes["payment_days"] = int(str(form.get("payment_days") or 14))
    if "tax_mode" in form:
        changes["tax_mode"] = (
            "regelbesteuerung" if form.get("tax_mode") == "regelbesteuerung" else "kleinunternehmer"
        )
    if form.get("provider") in PROVIDERS:
        changes["provider"] = form.get("provider")
    for key in form.getlist("checkboxes"):
        if key in CHECKBOXES:
            changes[key] = form.get(key) == "on"
    return replace(current, **changes)


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
                "sums": totals(draft.lines, current),
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


def _apply_form(draft: Draft, form, customer_by) -> None:
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
    for description, when, quantity, unit, price in fields:
        if not str(description).strip() and not str(quantity).strip():
            continue  # the empty row at the bottom
        amount = parse_amount(str(quantity))
        if amount is None:
            raise BillingError(f"Bei „{description}“ fehlt die Menge.")
        lines.append(
            reviewed_line(
                str(description),
                str(when),
                amount,
                str(unit) if unit in UNITS else "Stk",
                parse_amount(str(price)),
            )
        )
    draft.lines = lines
    if form.get("questions_done") == "on":
        draft.questions = []


def _one_line(value) -> str | None:
    """Addresses are typed on several lines and stored as one: "Weg 1, 12345 Ort"."""
    parts = [p.strip() for p in str(value or "").replace("\r", "").split("\n")]
    return ", ".join(p for p in parts if p) or None


def _pdf(path: str | None, folder: Path) -> FileResponse:
    if not path:
        raise HTTPException(404)
    pdf = Path(path).resolve()
    if not pdf.is_file() or folder.resolve() not in pdf.parents:
        raise HTTPException(404)
    return FileResponse(
        pdf, media_type="application/pdf", filename=pdf.name, content_disposition_type="inline"
    )

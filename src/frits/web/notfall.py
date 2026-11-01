"""The Notfall-Chat pages: drop any document in, see what the AI will see, then ask.

Chats live only in this process's memory (app.state.notfall). Nothing of them is written to
disk; the audit log gets one line per AI call with the provider, the token counts and a hash
of what was sent, as for every other AI call in FRITS.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from frits import notfall, store
from frits.customers import Customer
from frits.ingest import IngestError
from frits.providers import _LABELS, ProviderError
from frits.redact import LeakError

LEAK_TEXT = (
    "Ich konnte nicht alles sicher schwärzen. Darum geht davon nichts raus. "
    "Trag die Namen oder Nummern unten bei „Auch schwärzen“ ein und versuch es noch einmal."
)
GONE_TEXT = (
    "Dieser Chat ist nicht mehr da. Das ist Absicht: Notfall-Chats werden nie gespeichert "
    "und sind weg, sobald FRITS neu startet."
)


def register_notfall(
    app: FastAPI,
    *,
    page: Callable,
    back: Callable,
    flash: Callable,
    settings: Callable,
    customers: Callable[[], list[Customer]],
    nlp: Callable,
    guarded: list,
    provider_for: Callable,
    ai_ready: Callable,
    db: Callable,
) -> None:
    app.state.notfall = OrderedDict()
    chats: OrderedDict[str, notfall.Gespraech] = app.state.notfall

    def people() -> list[Customer]:
        current = settings()
        known = list(customers())
        if current.name:
            known.append(current.owner)
        return known

    def chat_or_404(key: str) -> notfall.Gespraech:
        g = chats.get(key)
        if g is None:
            raise HTTPException(404)
        chats.move_to_end(key)
        return g

    def ai_label(current) -> str:
        if current.strict_local:
            return "die lokale KI"
        return _LABELS.get(current.provider, current.provider)

    def show(
        request: Request, g: notfall.Gespraech | None, frei: bool = False, **extra
    ) -> HTMLResponse:
        current = settings()
        frei = frei or bool(g and g.frei)
        return page(
            request,
            "notfall.html",
            g=g,
            frei=frei,
            chats=list(reversed(chats.values())),
            ki=ai_label(current),
            ai_ready=ai_ready(current),
            vorschlaege=notfall.VORSCHLAEGE_FREI if frei else notfall.VORSCHLAEGE,
            max_docs=notfall.MAX_DOCS,
            system=g.system if g else notfall.SYSTEM,
            leicht=notfall.leicht,
            nav="/chat" if frei else "/notfall",
            **extra,
        )

    def keep(g: notfall.Gespraech) -> None:
        chats[g.key] = g
        while len(chats) > notfall.KEEP:
            chats.popitem(last=False)

    async def take_documents(g: notfall.Gespraech, form) -> list[str]:
        """Read every dropped file and the pasted text, here, and redact them. Returns problems."""
        from frits.web.app import _upload_text

        problems = []
        known = people()
        model = None
        items = []
        for upload in form.getlist("files"):
            if not getattr(upload, "filename", None):
                continue
            name = Path(str(upload.filename).replace("\\", "/")).name
            try:
                text = await _upload_text(upload)
            except IngestError as exc:
                problems.append(f"{name}: {exc}")
                continue
            suffix = Path(name).suffix.lower().lstrip(".") or "text"
            ocr = suffix in ("jpg", "jpeg", "png", "heic", "heif")
            items.append((name, "foto" if ocr else suffix, text, ocr))
        pasted = str(form.get("text") or "").strip()
        if pasted:
            items.append(("Eingefügter Text", "text", pasted, False))
        for name, art, text, ocr in items:
            if len(g.docs) >= notfall.MAX_DOCS:
                problems.append(
                    f"Mehr als {notfall.MAX_DOCS} Dokumente passen nicht in einen Chat."
                )
                break
            if not text.strip():
                problems.append(f"{name}: Darin habe ich keinen Text gefunden.")
                continue
            try:
                model = model or nlp()
                doc = notfall.add_document(g, name, art, text, known, model, ocr)
            except LeakError:
                problems.append(f"{name}: {LEAK_TEXT}")
                continue
            except RuntimeError as exc:  # the German language model is missing
                problems.append(str(exc))
                break
            if doc.cut:
                problems.append(
                    f"{name} ist sehr lang. Ich nehme nur die ersten "
                    f"{notfall.MAX_DOC:,} Zeichen.".replace(",", ".")
                )
        return problems

    def mit_datei(pfad: Path, name: str) -> tuple[str | None, list[str]]:
        """A new chat that starts with one file from this computer (a Steuer-Quest Beleg).
        Read and redacted here; nothing leaves until Fritz asks a question.
        Returns (chat key or None, problems)."""
        from frits.ingest import extract_text

        try:
            text = extract_text(pfad).text
        except IngestError as exc:
            return None, [f"{name}: {exc}"]
        if not text.strip():
            return None, [f"{name}: Darin habe ich keinen Text gefunden."]
        g = notfall.neues_gespraech()
        suffix = pfad.suffix.lower().lstrip(".") or "text"
        ocr = suffix in ("jpg", "jpeg", "png", "heic", "heif")
        try:
            doc = notfall.add_document(
                g, name, "foto" if ocr else suffix, text, people(), nlp(), ocr
            )
        except LeakError:
            return None, [f"{name}: {LEAK_TEXT}"]
        except RuntimeError as exc:
            return None, [str(exc)]
        keep(g)
        cut = [f"{name} ist sehr lang. Ich nehme nur den Anfang."] if doc.cut else []
        return g.key, cut

    app.state.notfall_mit_datei = mit_datei

    @app.get("/notfall", response_class=HTMLResponse)
    def notfall_start(request: Request):
        return show(request, None)

    @app.post("/notfall/neu", dependencies=guarded)
    async def notfall_neu(request: Request):
        form = await request.form()
        g = notfall.neues_gespraech()
        problems = await take_documents(g, form)
        for p in problems:
            flash(p, "error")
        if not g.docs:
            if not problems:
                flash("Zieh ein Dokument auf das Feld oder füg Text ein.", "error")
            return back("/notfall")
        keep(g)
        return back(f"/notfall/{g.key}")

    @app.get("/chat", response_class=HTMLResponse)
    def chat_start(request: Request):
        return show(request, None, frei=True)

    @app.post("/chat/neu", dependencies=guarded)
    async def chat_neu(request: Request):
        """Freier Chat: a question straight away; documents only if Fritz adds some."""
        form = await request.form()
        g = notfall.neues_gespraech()
        g.frei = True
        problems = await take_documents(g, form)
        for p in problems:
            flash(p, "error")
        question = str(form.get("vorschlag") or form.get("frage") or "").strip()
        if not question and not g.docs:
            if not problems:
                flash("Schreib kurz, was du wissen möchtest.", "error")
            return back("/chat")
        keep(g)
        if g.docs:  # first look at what the AI would see, then send
            g.entwurf = question
            return back(f"/notfall/{g.key}#unterlagen")
        return await send(g, question, str(form.get("action") or form.get("modus") or "senden"))

    @app.get("/notfall/{key}", response_class=HTMLResponse)
    def notfall_chat(request: Request, key: str):
        g = chats.get(key)
        if g is None:
            flash(GONE_TEXT)
            return back("/notfall")
        chats.move_to_end(key)
        return show(request, g)

    @app.post("/notfall/{key}/dokument", dependencies=guarded)
    async def notfall_dokument(request: Request, key: str):
        g = chat_or_404(key)
        form = await request.form()
        before = len(g.docs)
        for p in await take_documents(g, form):
            flash(p, "error")
        added = len(g.docs) - before
        if added:
            flash(
                "Dazugelegt und geschwärzt. Es geht mit deiner nächsten Frage raus."
                if added == 1
                else f"{added} Dokumente dazugelegt und geschwärzt."
            )
        return back(f"/notfall/{key}#unterlagen")

    @app.post("/notfall/{key}/dokument/{nr}/weg", dependencies=guarded)
    def notfall_dokument_weg(key: str, nr: int):
        g = chat_or_404(key)
        doc = next((d for d in g.docs if d.nr == nr), None)
        if doc and not doc.sent:
            g.docs.remove(doc)
            flash(f"{doc.name} ist wieder raus. Es wurde nichts davon verschickt.")
        return back(f"/notfall/{key}#unterlagen")

    @app.post("/notfall/{key}/schwaerzen", dependencies=guarded)
    async def notfall_schwaerzen(request: Request, key: str):
        g = chat_or_404(key)
        form = await request.form()
        try:
            g.set_extra(str(form.get("extra", "")), people(), nlp())
        except LeakError:
            flash(LEAK_TEXT, "error")
        except RuntimeError as exc:
            flash(str(exc), "error")
        else:
            flash("Neu geschwärzt. Schau unten nach, ob jetzt alles weg ist.")
        return back(f"/notfall/{key}#unterlagen")

    @app.post("/notfall/{key}/frage", dependencies=guarded)
    async def notfall_frage(request: Request, key: str):
        g = chat_or_404(key)
        form = await request.form()
        question = str(form.get("vorschlag") or form.get("frage") or "").strip()
        mode = str(form.get("action") or form.get("modus") or "senden")
        return await send(g, question, mode)

    async def send(g: notfall.Gespraech, question: str, mode: str):
        """Redact the question, then send it (or prepare it for copying by hand)."""
        key = g.key
        if not question:
            flash("Schreib kurz, was du wissen möchtest.", "error")
            return back(f"/notfall/{key}#frage")
        if g.waiting_for_answer:
            flash("Füg zuerst die Antwort auf deine letzte Frage ein, oder lass sie weg.", "error")
            return back(f"/notfall/{key}#antwort")
        try:
            notfall.ask(g, question, people(), nlp(), via="kopiert" if mode == "kopieren" else "ki")
        except LeakError:
            flash(LEAK_TEXT, "error")
            return back(f"/notfall/{key}#frage")
        except RuntimeError as exc:
            flash(str(exc), "error")
            return back(f"/notfall/{key}#frage")
        if mode == "kopieren":
            return back(f"/notfall/{key}#kopieren")

        current = settings()
        try:
            provider = provider_for(current)
            if not hasattr(provider, "chat"):
                raise ProviderError(
                    "Diese KI kann hier nicht chatten. Kopier die Frage lieber selbst."
                )
            messages = g.api_messages()
            text, usage = await run_in_threadpool(provider.chat, g.system, messages)
        except ProviderError as exc:
            notfall.take_back(g)
            g.entwurf = question  # kept for the field, not in the address (browser history)
            flash(f"{exc} Deine Frage ist nicht verloren, sie steht unten noch im Feld.", "error")
            return back(f"/notfall/{key}#frage")
        conn = db()
        store.log_ai_call(conn, None, usage, g.fingerprint())
        g.calls += 1
        g.cost_usd += usage.cost_usd or 0.0
        g.entwurf = ""
        notfall.answer(g, text, "ki", usage.model)
        return back(f"/notfall/{key}#ende")

    @app.post("/notfall/{key}/antwort", dependencies=guarded)
    async def notfall_antwort(request: Request, key: str):
        g = chat_or_404(key)
        form = await request.form()
        if form.get("action") == "verwerfen":
            notfall.take_back(g)
            flash("Die Frage ist wieder weg.")
            return back(f"/notfall/{key}#frage")
        pasted = str(form.get("antwort") or "").strip()
        if not pasted:
            flash("Füg die Antwort der KI in das Feld ein.", "error")
            return back(f"/notfall/{key}#antwort")
        if not g.waiting_for_answer:
            return back(f"/notfall/{key}#ende")
        try:
            notfall.answer(g, pasted, "kopiert", people=people(), nlp=nlp())
        except LeakError:
            flash(LEAK_TEXT, "error")
            return back(f"/notfall/{key}#antwort")
        return back(f"/notfall/{key}#ende")

    @app.post("/notfall/{key}/vergessen", dependencies=guarded)
    def notfall_vergessen(key: str):
        chats.pop(key, None)
        flash("Vergessen. Von diesem Chat ist auf deinem Computer nichts mehr übrig.", cue="done")
        return back("/notfall")

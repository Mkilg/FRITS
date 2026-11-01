"""The course "So funktionieren KI-Agenten" (/lernen): small bites, one per page, and the
redaction workbench.

Progress (the chosen tool, bites seen, stages finished) lives in the game's small key-value
table, so a finished stage can become a card for the next Kassensturz. The workbench keeps the
placeholder list only in this process's memory (never on disk, never sent), so the real names
can be put back into an AI's answer until FRITS is closed.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from frits import lernen
from frits.customers import Customer
from frits.redact import LeakError
from frits.spiel import stand

KEEP = 20  # workbench pieces kept in memory at once


def register_lernen(
    app: FastAPI,
    *,
    page: Callable,
    back: Callable,
    flash: Callable,
    db: Callable,
    settings: Callable,
    customers: Callable[[], list[Customer]],
    nlp: Callable,
    guarded: list,
) -> None:
    app.state.werkbank = OrderedDict()

    # ---------- progress ----------

    def conn():
        c = db()
        stand.vorbereiten(c)
        return c

    def werkzeug(c) -> str:
        chosen = stand.einstellung(c, lernen.SCHLUESSEL_WERKZEUG)
        return chosen if chosen in lernen.WERKZEUGE else ""

    def gesehen(c) -> set[str]:
        try:
            return set(json.loads(stand.einstellung(c, lernen.SCHLUESSEL_GESEHEN, "[]")))
        except (ValueError, TypeError):
            return set()

    def sehen(c, e: lernen.Etappe, h: lernen.Happen) -> None:
        seen = gesehen(c)
        key = f"{e.slug}/{h.slug}"
        if key not in seen:
            stand.setze(c, lernen.SCHLUESSEL_GESEHEN, json.dumps(sorted(seen | {key})))

    def geschafft(c) -> set[str]:
        rows = c.execute(
            "SELECT schluessel FROM spiel_einstellung WHERE schluessel LIKE ?",
            (lernen.GESCHAFFT_PREFIX + "%",),
        ).fetchall()
        return {r[0][len(lernen.GESCHAFFT_PREFIX) :] for r in rows}

    def karte(c) -> list[dict]:
        """The course map: every stage with its bites for the chosen tool and what is done."""
        tool = werkzeug(c) or lernen.STANDARD_WERKZEUG
        seen, done = gesehen(c), geschafft(c)
        stops = []
        for e in lernen.ETAPPEN:
            bites = e.fuer(tool)
            stops.append(
                {
                    "e": e,
                    "happen": bites,
                    "gesehen": sum(f"{e.slug}/{h.slug}" in seen for h in bites),
                    "geschafft": e.slug in done,
                }
            )
        return stops

    def weiter_wo(stops: list[dict]) -> str:
        for stop in stops:
            if not stop["geschafft"]:
                return f"/lernen/{stop['e'].slug}"
        return "/lernen/papierkram/spickzettel"

    # ---------- pages ----------

    def happen_page(request: Request, slug: str, happen: str | None = None, **extra):
        slug = lernen.ALTE_SLUGS.get(slug, slug)
        if slug not in lernen.SLUGS:
            raise HTTPException(404)
        e = lernen.etappe(slug)
        c = conn()
        chosen = werkzeug(c)
        tool = chosen or lernen.STANDARD_WERKZEUG
        bites = e.fuer(tool)
        if happen is None:
            h = bites[0]
        else:
            h = next((b for b in bites if b.slug == happen), None)
            if h is None:
                other = next((b for b in e.happen if b.slug == happen), None)
                if other is None:
                    raise HTTPException(404)
                # A bite for another tool (e.g. an old link): show it anyway, it is harmless.
                h = other
        sehen(c, e, h)
        vorher, nachher = lernen.nachbarn(e, h, tool)
        letzter = h in bites and bites.index(h) == len(bites) - 1
        context = {
            "titel": lernen.TITEL,
            "e": e,
            "h": h,
            "bites": bites,
            "nr": bites.index(h) + 1 if h in bites else 0,
            "etappe_nr": lernen.SLUGS.index(e.slug) + 1,
            "etappen": lernen.ETAPPEN,
            "vorher": vorher,
            "nachher": nachher,
            "letzter": letzter,
            "naechste": lernen.naechste_etappe(e),
            "werkzeug": lernen.WERKZEUGE[tool],
            "gewaehlt": bool(chosen),
            "werkzeuge": lernen.WERKZEUGE,
            "gesehen": gesehen(c),
            "geschafft": e.slug in geschafft(c),
            "prompts": lernen.PROMPTS,
            "quellen": lernen.QUELLEN,
            "stand": lernen.STAND,
            "beispiel": lernen.BEISPIEL,
        }
        context.update(extra)
        return page(request, "lernen.html", **context)

    def known_people() -> list[Customer]:
        current = settings()
        people = list(customers())
        if current.name:
            people.append(current.owner)
        return people

    @app.get("/lernen", response_class=HTMLResponse)
    def lernen_karte(request: Request):
        c = conn()
        stops = karte(c)
        chosen = werkzeug(c)
        return page(
            request,
            "lernen_karte.html",
            titel=lernen.TITEL,
            stops=stops,
            weiter=weiter_wo(stops),
            begonnen=bool(gesehen(c)),
            alles=all(s["geschafft"] for s in stops),
            werkzeug=lernen.WERKZEUGE[chosen or lernen.STANDARD_WERKZEUG],
            gewaehlt=bool(chosen),
            minuten=sum(len(s["happen"]) for s in stops),
        )

    @app.get("/lernen/{slug}", response_class=HTMLResponse)
    def lernen_etappe(request: Request, slug: str):
        return happen_page(request, slug)

    @app.get("/lernen/{slug}/{happen}", response_class=HTMLResponse)
    def lernen_happen(request: Request, slug: str, happen: str):
        return happen_page(request, slug, happen)

    @app.post("/lernen/wahl", dependencies=guarded)
    async def werkzeug_waehlen(request: Request):
        form = await request.form()
        tool = str(form.get("werkzeug", ""))
        if tool not in lernen.WERKZEUGE:
            raise HTTPException(400)
        c = conn()
        stand.setze(c, lernen.SCHLUESSEL_WERKZEUG, tool)
        weiter = str(form.get("weiter", ""))
        # Only ever back into the course: a form can't send Fritz somewhere else.
        if not weiter.startswith("/lernen") or "//" in weiter or "\\" in weiter:
            weiter = "/lernen/agent"
        # Back to a bite the new tool doesn't have: its stage instead.
        teile = weiter.strip("/").split("/")
        if (
            len(teile) == 3
            and teile[1] in lernen.SLUGS
            and teile[2] not in {h.slug for h in lernen.etappe(teile[1]).fuer(tool)}
        ):
            weiter = f"/lernen/{teile[1]}"
        # Picking the tool is what the first stage is about: that stage is done.
        if "start" not in geschafft(c):
            abschliessen(c, lernen.etappe("start"))
        else:
            flash(f"Du lernst jetzt mit {lernen.WERKZEUGE[tool].name}.")
        return back(weiter)

    def abschliessen(c, e: lernen.Etappe) -> bool:
        """Stamp a stage as done. True if that finished the whole course just now."""
        neu = e.slug not in geschafft(c)
        stand.setze(c, lernen.GESCHAFFT_PREFIX + e.slug, "1")
        alles = len(geschafft(c)) == len(lernen.ETAPPEN)
        if alles and neu:
            text = "Kurs geschafft! Du weißt jetzt, wie KI-Agenten arbeiten und wie du sie zähmst."
        else:
            text = f"Etappe {lernen.SLUGS.index(e.slug) + 1} geschafft: {e.title}."
        if neu and settings().haustier:
            text += " Beim nächsten Kassensturz gibt es Salzkörner dafür."
        flash(text, cue="done", stamp="Gelernt")
        return alles

    @app.post("/lernen/{slug}/geschafft", dependencies=guarded)
    def etappe_geschafft(slug: str):
        if slug not in lernen.SLUGS:
            raise HTTPException(404)
        e = lernen.etappe(slug)
        alles = abschliessen(conn(), e)
        nach = lernen.naechste_etappe(e)
        if nach is None or alles:
            return back("/lernen")
        return RedirectResponse(f"/lernen/{nach.slug}", status_code=303)

    @app.post("/lernen/werkbank", dependencies=guarded)
    async def werkbank(request: Request):
        form = await request.form()
        if form.get("action") == "zurueck":
            key = str(form.get("stueck", ""))
            stueck = app.state.werkbank.get(key)
            answer = str(form.get("antwort", ""))
            if stueck is None:
                return happen_page(
                    request,
                    "werkbank",
                    fehler="Die Zuordnung ist nicht mehr da (FRITS wurde neu gestartet). "
                    "Schwärz den Text bitte noch einmal.",
                )
            return happen_page(
                request,
                "werkbank",
                stueck=stueck,
                antwort=answer,
                zurueck=lernen.zuruecksetzen(answer, stueck.mapping),
                extra=str(form.get("extra", "")),
                aufgabe=str(form.get("aufgabe", "")),
            )

        text = str(form.get("text", ""))
        extra = str(form.get("extra", ""))
        task = str(form.get("aufgabe", ""))
        try:
            stueck = lernen.schwaerzen(text, known_people(), nlp(), extra=extra, task=task)
        except LeakError:
            return happen_page(
                request,
                "werkbank",
                fehler="Ich konnte nicht alles sicher schwärzen. Darum zeige ich lieber nichts an. "
                "Trag die Namen oben bei „Auch schwärzen“ ein und versuch es noch einmal.",
                text=text,
                extra=extra,
                aufgabe=task,
            )
        except RuntimeError as exc:  # German language model missing
            return happen_page(
                request, "werkbank", fehler=str(exc), text=text, extra=extra, aufgabe=task
            )
        app.state.werkbank[stueck.key] = stueck
        while len(app.state.werkbank) > KEEP:
            app.state.werkbank.popitem(last=False)
        return happen_page(request, "werkbank", stueck=stueck, text=text, extra=extra, aufgabe=task)

"""The KI-Werkstatt pages: a course in eight chapters and a redaction workbench.

The workbench keeps the placeholder list only in this process's memory (never on disk, never sent),
so the real names can be put back into an AI's answer until FRITS is closed.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from frits import lernen
from frits.customers import Customer
from frits.redact import LeakError

KEEP = 20  # workbench pieces kept in memory at once


def register_lernen(
    app: FastAPI,
    *,
    page: Callable,
    settings: Callable,
    customers: Callable[[], list[Customer]],
    nlp: Callable,
    guarded: list,
) -> None:
    app.state.werkbank = OrderedDict()

    def kapitel_page(request: Request, slug: str, **extra) -> HTMLResponse:
        if slug not in lernen.SLUGS:
            raise HTTPException(404)
        context = {
            "k": lernen.kapitel_info(slug),
            "kapitel": lernen.KAPITEL,
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
    def lernen_start(request: Request):
        return kapitel_page(request, "start")

    @app.get("/lernen/{slug}", response_class=HTMLResponse)
    def lernen_kapitel(request: Request, slug: str):
        return kapitel_page(request, slug)

    @app.post("/lernen/werkbank", dependencies=guarded)
    async def werkbank(request: Request):
        form = await request.form()
        if form.get("action") == "zurueck":
            key = str(form.get("stueck", ""))
            stueck = app.state.werkbank.get(key)
            answer = str(form.get("antwort", ""))
            if stueck is None:
                return kapitel_page(
                    request,
                    "werkbank",
                    fehler="Die Zuordnung ist nicht mehr da (FRITS wurde neu gestartet). "
                    "Schwärz den Text bitte noch einmal.",
                )
            return kapitel_page(
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
            return kapitel_page(
                request,
                "werkbank",
                fehler="Ich konnte nicht alles sicher schwärzen. Darum zeige ich lieber nichts an. "
                "Trag die Namen oben bei „Auch schwärzen“ ein und versuch es noch einmal.",
                text=text,
                extra=extra,
                aufgabe=task,
            )
        except RuntimeError as exc:  # German language model missing
            return kapitel_page(
                request, "werkbank", fehler=str(exc), text=text, extra=extra, aufgabe=task
            )
        app.state.werkbank[stueck.key] = stueck
        while len(app.state.werkbank) > KEEP:
            app.state.werkbank.popitem(last=False)
        return kapitel_page(
            request, "werkbank", stueck=stueck, text=text, extra=extra, aufgabe=task
        )

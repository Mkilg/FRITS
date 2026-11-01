"""The pages for Fritte als Haustier: her room, the Salzkörner and the little shop."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from frits import haustier


def fritte_datei(root: Path) -> Path:
    return Path(root) / "fritte.toml"


def register_haustier(
    app: FastAPI,
    *,
    ws,
    page: Callable,
    back: Callable,
    flash: Callable,
    db: Callable,
    settings: Callable,
    guarded: list,
    today: Callable[[], date],
) -> None:
    datei = fritte_datei(ws.root)

    @app.get("/fritte", response_class=HTMLResponse)
    def fritte_page(request: Request, neu: str = ""):
        f = haustier.ansehen(db(), datei, today())
        ding = haustier.DINGE.get(neu) if neu in f.gekauft else None
        if ding:
            f.bild = haustier.BILD["freut_sich"]
            f.satz = f"Danke! {_freude(ding)}"
        return page(
            request,
            "fritte.html",
            f=f,
            laden=haustier.LADEN,
            punkte=haustier.PUNKTE_TEXT,
            neu=ding.id if ding else "",
        )

    @app.post("/fritte/kaufen", dependencies=guarded)
    async def fritte_kaufen(request: Request):
        ding_id = str((await request.form()).get("ding", ""))
        try:
            ding = haustier.kaufen(db(), datei, ding_id)
        except haustier.LadenFehler as exc:
            flash(str(exc), "error")
            return back("/fritte#laden")
        flash(f"{ding.name} gekauft.", cue="done")
        return back(f"/fritte?neu={ding.id}")

    @app.post("/fritte/hut", dependencies=guarded)
    async def fritte_hut(request: Request):
        haustier.aufsetzen(datei, str((await request.form()).get("ding", "")))
        return back("/fritte#huete")


def _freude(ding: haustier.Ding) -> str:
    if ding.art == "hut":
        return f"{ding.artikel.capitalize()} {ding.name} steht mir gut, oder?"
    return (
        f"{ding.artikel.capitalize()} {ding.name} hat jetzt einen schönen Platz in meinem Zimmer."
    )

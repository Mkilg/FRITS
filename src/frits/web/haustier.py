"""The pages for Fritte and the everyday game: her room, the Kassensturz and the shop."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from frits import haustier, store
from frits.spiel import kassensturz, stand
from frits.spiel.gewuerze import GEWICHT, GEWUERZE, NACH_ID

MARKT_GROESSE = 3


def fritte_datei(root: Path) -> Path:
    """Where the first version kept its state; read once to move it into the game tables."""
    return Path(root) / "fritte.toml"


def wurf_preis(wuerfe: int) -> int:
    return 2 + wuerfe


def verkaufs_preis(preis: int) -> int:
    return max(1, preis // 2)


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
    aufgeraeumt: Callable[[], bool],
) -> None:
    datei = fritte_datei(ws.root)

    def fritte(conn) -> haustier.Fritte:
        return haustier.ansehen(conn, today(), datei)

    async def form_value(request: Request, name: str) -> str:
        return str((await request.form()).get(name, ""))

    @app.get("/fritte", response_class=HTMLResponse)
    def fritte_page(request: Request, neu: str = ""):
        conn = db()
        f = fritte(conn)
        ding = haustier.DINGE.get(neu) if neu in f.gekauft else None
        if ding:
            f.bild = haustier.BILD["freut_sich"]
            f.satz = f"Danke! {_freude(ding)}"
        karten, vorschau = kassensturz.vorschau(conn, today(), aufgeraeumt())
        return page(
            request,
            "fritte.html",
            f=f,
            neu=ding.id if ding else "",
            karten=karten,
            vorschau=vorschau,
            regal=kassensturz.regal(conn),
            plaetze=kassensturz.plaetze(conn),
            chronik=kassensturz.chronik(conn),
            arten=stand.ARTEN_TEXT,
            grund=stand.GRUND,
        )

    @app.post("/fritte/kassensturz", dependencies=guarded)
    def fritte_kassensturz():
        conn = db()
        stand.abgleichen(conn, today(), datei)
        try:
            nummer = kassensturz.durchfuehren(conn, today(), aufgeraeumt())
        except stand.SpielFehler as exc:
            flash(str(exc), "error")
            return back("/fritte")
        return back(f"/fritte/kassensturz/{nummer}")

    @app.get("/fritte/kassensturz/{nummer}", response_class=HTMLResponse)
    def fritte_kassensturz_show(request: Request, nummer: int):
        conn = db()
        ergebnis = kassensturz.laden(conn, nummer)
        if ergebnis is None:
            raise HTTPException(404)
        return page(
            request,
            "kassensturz.html",
            k=ergebnis,
            f=fritte(conn),
            guthaben=stand.guthaben(conn),
        )

    @app.get("/fritte/laden", response_class=HTMLResponse)
    def laden_page(request: Request):
        conn = db()
        f = fritte(conn)
        wuerfe = stand.wuerfe(conn, today())
        markt = stand.angebot(
            [g.id for g in GEWUERZE],
            [GEWICHT[g.seltenheit] for g in GEWUERZE],
            today(),
            wuerfe,
            MARKT_GROESSE,
        )
        shelf = kassensturz.regal(conn)
        return page(
            request,
            "fritte_laden.html",
            f=f,
            markt=[NACH_ID[i] for i in markt],
            regal=shelf,
            im_regal={g.id for g in shelf},
            plaetze=kassensturz.plaetze(conn),
            wurf_preis=wurf_preis(wuerfe),
            verkauf=verkaufs_preis,
            laden=haustier.LADEN,
            kassenbuch=stand.kassenbuch(conn),
        )

    @app.post("/fritte/kaufen", dependencies=guarded)
    async def fritte_kaufen(request: Request):
        try:
            ding = haustier.kaufen(db(), today(), await form_value(request, "ding"))
        except stand.SpielFehler as exc:
            flash(str(exc), "error")
            return back("/fritte/laden#einrichtung")
        flash(f"{ding.name} gekauft.", cue="done")
        return back(f"/fritte?neu={ding.id}")

    @app.post("/fritte/hut", dependencies=guarded)
    async def fritte_hut(request: Request):
        haustier.aufsetzen(db(), await form_value(request, "ding"))
        return back("/fritte/laden#huete")

    @app.post("/fritte/gewuerz/kaufen", dependencies=guarded)
    async def gewuerz_kaufen(request: Request):
        conn = db()
        g = NACH_ID.get(await form_value(request, "gewuerz"))
        try:
            if g is None:
                raise stand.SpielFehler("Dieses Gewürz gibt es nicht.")
            stand.vorbereiten(conn)
            with store.transaction(conn):
                if stand.besitzt(conn, g.id):
                    raise stand.SpielFehler(f"{g.name} steht schon im Regal.")
                if len(kassensturz.regal(conn)) >= kassensturz.plaetze(conn):
                    raise stand.SpielFehler(
                        "Das Regal ist voll. Verkauf ein Gewürz oder bau Frittes Haus aus."
                    )
                stand.bezahlen(conn, today().isoformat(), g.preis, f"{g.name} gekauft")
                stand.nimm(conn, g.id, "gewuerz", today().isoformat(), g.preis)
                stand.setze(conn, f"reife:{g.id}", "0")
        except stand.SpielFehler as exc:
            flash(str(exc), "error")
            return back("/fritte/laden#markt")
        flash(f"{g.name} steht jetzt im Gewürzregal.", cue="done")
        return back("/fritte/laden#regal")

    @app.post("/fritte/gewuerz/verkaufen", dependencies=guarded)
    async def gewuerz_verkaufen(request: Request):
        conn = db()
        g = NACH_ID.get(await form_value(request, "gewuerz"))
        if g is None:
            raise HTTPException(404)
        stand.vorbereiten(conn)
        try:
            with store.transaction(conn):
                stand.gib_ab(conn, g.id)
                preis = verkaufs_preis(g.preis)
                stand.buche(conn, today().isoformat(), preis, "verkauf", f"{g.name} verkauft")
                stand.setze(conn, f"reife:{g.id}", "0")
        except stand.SpielFehler as exc:
            flash(str(exc), "error")
            return back("/fritte/laden#regal")
        flash(f"{g.name} verkauft für {preis} Salzkörner.")
        return back("/fritte/laden#regal")

    @app.post("/fritte/gewuerz/links", dependencies=guarded)
    async def gewuerz_links(request: Request):
        """Move a spice one place to the left: Würze is applied left to right."""
        conn = db()
        gid = await form_value(request, "gewuerz")
        ids = [g.id for g in kassensturz.regal(conn)]
        if gid in ids and ids.index(gid) > 0:
            i = ids.index(gid)
            ids[i - 1], ids[i] = ids[i], ids[i - 1]
            kassensturz.regal_ordnen(conn, ids)
        return back("/fritte/laden#regal")

    @app.post("/fritte/markt/wuerfeln", dependencies=guarded)
    def markt_wuerfeln():
        conn = db()
        try:
            stand.neu_wuerfeln(conn, today(), wurf_preis(stand.wuerfe(conn, today())))
        except stand.SpielFehler as exc:
            flash(str(exc), "error")
        return back("/fritte/laden#markt")


def _freude(ding: haustier.Ding) -> str:
    if ding.art == "hut":
        return f"{ding.artikel.capitalize()} {ding.name} steht mir gut, oder?"
    return (
        f"{ding.artikel.capitalize()} {ding.name} hat jetzt einen schönen Platz in meinem Zimmer."
    )

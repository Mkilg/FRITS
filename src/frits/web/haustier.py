"""The pages for Fritte and the everyday game: her room, the Kassensturz and the shop."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from frits import haustier, store
from frits.spiel import album, ausbau, kassensturz, saison, stand
from frits.spiel.gewuerze import GEWICHT, GEWUERZE, NACH_ID

MARKT_GROESSE = 3
PREIS_PRO_STUFE = 0.5  # spices cost 1×, 1.5×, 2× … as the house grows


def fritte_datei(root: Path) -> Path:
    """Where the first version kept its state; read once to move it into the game tables."""
    return Path(root) / "fritte.toml"


def wurf_preis(conn, wuerfe: int) -> int:
    return (2 + wuerfe) * ausbau.stufe(conn).nummer


def preis(conn, g) -> int:
    """Spices cost more as the house grows, so there is always something to save up for."""
    return round(g.preis * (1 + PREIS_PRO_STUFE * (ausbau.stufe(conn).nummer - 1)))


def verkaufs_preis(bezahlt: int) -> int:
    return max(1, bezahlt // 2)


def markt(conn, heute: date) -> list:
    """This week's spices: the same all week, until Fritz rolls again. What is already on
    the shelf is not offered. When the house grows mid-week, the offer stays and one spice
    of the newly unlocked kind joins it."""
    stand.vorbereiten(conn)
    wurf = stand.wuerfe(conn, heute)
    key = f"markt:{stand.woche(heute)}:{wurf}"
    gespeichert = stand.einstellung(conn, key)
    owned = set(stand.besitz(conn, "gewuerz"))
    seltenheiten = ausbau.seltenheiten(conn)

    def ziehen(erlaubt: set[str], ohne: set[str], n: int, salz: int) -> list[str]:
        pool = [g for g in GEWUERZE if g.seltenheit in erlaubt and g.id not in ohne]
        return stand.angebot(
            [g.id for g in pool], [GEWICHT[g.seltenheit] for g in pool], heute, salz, n
        )

    if gespeichert:
        teile, _, alt = gespeichert.partition("|")
        ids = [i for i in teile.split(",") if i in NACH_ID]
        alte = set(alt.split(",")) if alt else set(seltenheiten)
    else:
        ids, alte = ziehen(set(seltenheiten), owned, MARKT_GROESSE, wurf), set(seltenheiten)
    neue = set(seltenheiten) - alte
    if neue:
        ids += ziehen(neue, owned | set(ids), 1, 1000 + wurf)
    if not gespeichert or neue:
        stand.setze(conn, key, f"{','.join(ids)}|{','.join(sorted(seltenheiten))}")
    return [NACH_ID[i] for i in ids]


def wirkung(conn, heute: date, g, aufgeraeumt: bool) -> tuple[int, int, str]:
    """What this spice would add, put at the right end of the shelf: with the cards
    waiting now, or else with the last Kassensturz. (extra, cards, "warten"|"letzter")."""
    karten, _ = kassensturz.vorschau(conn, heute, aufgeraeumt)
    if karten:
        dicts, quelle = [k.als_dict() for k in karten], "warten"
    else:
        letzte = kassensturz.chronik(conn, 1)
        alt = kassensturz.laden(conn, letzte[0]["nummer"]) if letzte else None
        if not alt or not alt.get("karten"):
            return 0, 0, ""
        dicts, quelle = alt["karten"], "letzter"
    shelf = [x for x in kassensturz.regal(conn) if x.id != g.id]
    umfeld = kassensturz.umfeld(conn, heute, aufgeraeumt)
    ohne = kassensturz.berechnen(dicts, shelf, umfeld)["summe"]
    mit = kassensturz.berechnen(dicts, [*shelf, g], umfeld)["summe"]
    return mit - ohne, len(dicts), quelle


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

    def album_pruefen(conn) -> None:
        neu = album.neue(conn, today())
        if neu:
            flash(
                "Neu in Frittes Album: " + ", ".join(f"{e.zeichen} {e.name}" for e in neu),
                cue="done",
            )

    def karten_hinweis(path: str, current) -> list[dict]:
        """Right after the work: a quiet note that Fritte got a card for it."""
        if not current.haustier or not (current.setup_done or current.is_complete):
            return []
        conn = db()
        stand.vorbereiten(conn)
        if not stand.einstellung(conn, "eingerichtet"):
            return []
        n = stand.abgleichen(conn, today(), datei)
        vorher = int(stand.einstellung(conn, "hinweis_wartend", "0") or 0)
        if n != vorher:
            stand.setze(conn, "hinweis_wartend", str(n))
        # Not in the Steuer-Quest: there nearly every step is a card, and the note would
        # cover the basket. Fritte counts them anyway and shows them on her own page.
        if n <= vorher or path == "/" or path.startswith(("/fritte", "/feierabend", "/steuer")):
            return []
        neu = n - vorher
        bereit = kassensturz.vorschau(conn, today(), aufgeraeumt=False)[1]["summe"]
        text = (
            f"Fritte hat {'eine neue Karte' if neu == 1 else f'{neu} neue Karten'}"
            f" für den Kassensturz: {n} {'wartet' if n == 1 else 'warten'},"
            f" das {'ist' if bereit == 1 else 'sind'} etwa +{bereit}"
            f" {'Salzkorn' if bereit == 1 else 'Salzkörner'}."
        )
        return [
            {"text": text, "kind": "karte", "cue": None, "stamp": None, "undo": False,
             "link": "/fritte#kassensturz", "link_text": "Zu Fritte"}
        ]  # fmt: skip

    app.state.hinweise.append(karten_hinweis)

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
        pro_karte = kassensturz.pro_karte(vorschau, len(karten))
        willkommen = stand.einstellung(conn, "willkommen") == "kassensturz" and not (
            kassensturz.chronik(conn, 1)
        )
        return page(
            request,
            "fritte.html",
            f=f,
            neu=ding.id if ding else "",
            karten=karten,
            pro_karte=pro_karte,
            vorschau=vorschau,
            willkommen=willkommen,
            saison=saison.jetzt(conn, today()),
            ort=ausbau.ort(conn),
            orte=ausbau.ORTE,
            naechster_ort=ausbau.naechster_ort(conn),
            umzug_preis=ausbau.umzug_preis(conn),
            ruf=ausbau.ruf(conn),
            bau_preis=lambda st: ausbau.preis(conn, st),
            stufen_text=lambda st: ausbau.stufen_text(conn, st),
            regal=kassensturz.regal(conn),
            plaetze=kassensturz.plaetze(conn),
            chronik=kassensturz.chronik(conn),
            arten=stand.ARTEN_TEXT,
            grund=stand.GRUND,
            stufen=ausbau.STUFEN,
            naechste=ausbau.naechste(conn),
        )

    @app.post("/fritte/ausbauen", dependencies=guarded)
    def fritte_ausbauen():
        try:
            ziel = ausbau.ausbauen(db(), today())
        except stand.SpielFehler as exc:
            flash(str(exc), "error")
            return back("/fritte#haus")
        flash(
            f"Die Baustelle für „{ziel.name}“ steht. Beim nächsten Kassensturz ist sie fertig.",
            cue="done",
        )
        return back("/fritte#haus")

    @app.post("/fritte/umziehen", dependencies=guarded)
    def fritte_umziehen():
        conn = db()
        try:
            ziel = ausbau.umziehen(conn, today())
        except stand.SpielFehler as exc:
            flash(str(exc), "error")
            return back("/fritte#haus")
        flash(
            f"Fritte eröffnet eine neue Bude: {ziel.name}. Ihre Sachen, das Gewürzregal und ihr"
            " guter Ruf ziehen mit.",
            cue="done",
        )
        album_pruefen(conn)
        return back("/fritte#haus")

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
        return page(request, "kassensturz.html", k=ergebnis, f=fritte(conn))

    @app.get("/fritte/album", response_class=HTMLResponse)
    def album_page(request: Request):
        conn = db()
        f = fritte(conn)
        seiten = album.seiten(conn, today())
        return page(
            request,
            "fritte_album.html",
            f=f,
            seiten=seiten,
            geschafft=sum(1 for s in seiten if s["seit"]),
        )

    @app.get("/fritte/laden", response_class=HTMLResponse)
    def laden_page(request: Request):
        conn = db()
        f = fritte(conn)
        wuerfe = stand.wuerfe(conn, today())
        shelf = kassensturz.regal(conn)
        bezahlt = stand.besitz_preise(conn)
        ware = markt(conn, today())
        sauber = aufgeraeumt()
        return page(
            request,
            "fritte_laden.html",
            f=f,
            markt=ware,
            wirkung={g.id: wirkung(conn, today(), g, sauber) for g in ware},
            preis=lambda g: preis(conn, g),
            regal=shelf,
            im_regal={g.id for g in shelf},
            plaetze=kassensturz.plaetze(conn),
            wurf_preis=wurf_preis(conn, wuerfe),
            verkauf=lambda g: verkaufs_preis(bezahlt.get(g.id) or g.preis),
            laden=haustier.LADEN,
            kassenbuch=stand.kassenbuch(conn),
            stufe=ausbau.stufe(conn),
            ruf=ausbau.ruf(conn),
        )

    @app.post("/fritte/kaufen", dependencies=guarded)
    async def fritte_kaufen(request: Request):
        conn = db()
        try:
            ding = haustier.kaufen(conn, today(), await form_value(request, "ding"))
        except stand.SpielFehler as exc:
            flash(str(exc), "error")
            return back("/fritte/laden#einrichtung")
        flash(f"{ding.name} gekauft.", cue="done")
        album_pruefen(conn)
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
                if g not in markt(conn, today()):
                    raise stand.SpielFehler(f"{g.name} gibt es diese Woche nicht auf dem Markt.")
                if len(kassensturz.regal(conn)) >= kassensturz.plaetze(conn):
                    raise stand.SpielFehler(
                        "Das Regal ist voll. Verkauf ein Gewürz oder bau Frittes Haus aus."
                    )
                kosten = preis(conn, g)
                stand.bezahlen(conn, today().isoformat(), kosten, f"{g.name} gekauft")
                stand.nimm(conn, g.id, "gewuerz", today().isoformat(), kosten)
                stand.setze(conn, f"reife:{g.id}", "0")
                stand.setze(conn, f"reifewoche:{g.id}", "")
        except stand.SpielFehler as exc:
            flash(str(exc), "error")
            return back("/fritte/laden#markt")
        flash(f"{g.name} steht jetzt im Gewürzregal.", cue="done")
        album_pruefen(conn)
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
                erloes = verkaufs_preis(stand.gib_ab(conn, g.id))
                stand.buche(conn, today().isoformat(), erloes, "verkauf", f"{g.name} verkauft")
                stand.setze(conn, f"reife:{g.id}", "0")
        except stand.SpielFehler as exc:
            flash(str(exc), "error")
            return back("/fritte/laden#regal")
        flash(f"{g.name} verkauft für {erloes} Salzkörner.")
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
            stand.neu_wuerfeln(conn, today(), wurf_preis(conn, stand.wuerfe(conn, today())))
        except stand.SpielFehler as exc:
            flash(str(exc), "error")
        return back("/fritte/laden#markt")


def _freude(ding: haustier.Ding) -> str:
    if ding.art == "hut":
        return f"{ding.artikel.capitalize()} {ding.name} steht mir gut, oder?"
    return (
        f"{ding.artikel.capitalize()} {ding.name} hat jetzt einen schönen Platz in meinem Zimmer."
    )

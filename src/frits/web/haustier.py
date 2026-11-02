"""The pages for Fritte and the everyday game: her room, the Kassensturz and the shop."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from frits import haustier, store
from frits.spiel import album, ausbau, kassensturz, saison, sammlung, stand, truhen
from frits.spiel.gewuerze import GEWICHT, GEWUERZE, NACH_ID

MARKT_GROESSE = 3
PREIS_PRO_STUFE = 0.5  # spices cost 1×, 1.5×, 2× … as the house grows


def fritte_datei(root: Path) -> Path:
    """Where the first version kept its state; read once to move it into the game tables."""
    return Path(root) / "fritte.toml"


def wurf_preis(conn, wuerfe: int) -> int:
    return (2 + wuerfe) * ausbau.preisstufe(conn)


def preis(conn, g) -> int:
    """Spices cost more as the house grows, so there is always something to save up for."""
    return round(g.preis * (1 + PREIS_PRO_STUFE * (ausbau.preisstufe(conn) - 1)))


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
    if not gespeichert and stand.einstellung(conn, "salbei_markt") != "1":
        # The very first market brings a "×" spice, so Würze is more than a 1 from the start.
        if "salbei" not in owned and "salbei" not in ids:
            ids = ["salbei", *ids[: MARKT_GROESSE - 1]]
        stand.setze(conn, "salbei_markt", "1")
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


def haus_sichtbar(conn) -> bool:
    """Frittes Haus with its stages comes into view after the third Kassensturz (Lena:
    five stages with prices up to 900 overwhelm someone with 128 grains), or as soon as
    something was built."""
    return (
        len(kassensturz.chronik(conn, 3)) >= 3
        or ausbau.stufe(conn).nummer > 1
        or ausbau.ruf(conn) > 0
        or ausbau.im_bau(conn) is not None
    )


def vorwahl(summen: dict[str, int], ohne: int) -> str:
    """Which dish is chosen already (Lena): the one that brings the most today, and only one
    that brings more than no dish (the first on a tie). If none does, Einkochen, which pays
    next time; without it, none ("")."""
    bringen = {i: n for i, n in summen.items() if i != "einkochen" and n > ohne}
    if bringen:
        return max(bringen, key=lambda i: bringen[i])
    return "einkochen" if "einkochen" in summen else ""


def drachen_serie(gewuerze: int, helfer: frozenset[str] = frozenset()) -> dict:
    """What Fritte's shelf does in the Steuer-Quest's dragon fight, with the quest's own
    numbers: each spice raises the gold streak at the start by one, up to a few spices.
    `helfer`: the quest's helpers at the table (Kriegstrommel, Sanduhr), so the shop says
    the same number as the fight (Lena saw ×2 here and ×4 there)."""
    from frits.steuer import spiel

    def start(n: int, mit: frozenset[str] = frozenset()) -> int:
        # The number the fight's banner shows before the first Beleg ("Serie ×N"):
        # a Beleg counts the streak plus two (steuer.drache_seite, combo["naechster"]).
        return spiel.multiplikator(spiel.start_serie(n) + 2, mit)

    return {
        "jetzt": start(gewuerze, helfer),
        "naechster": start(gewuerze + 1, helfer),
        "ohne": start(0, helfer),
        "max": spiel.GEWUERZ_START_MAX,
    }


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
    inbox_und_entwuerfe_leer = aufgeraeumt

    def aufgeraeumt() -> bool:
        # A draft thrown away this week still counts as open: discarding never pays off.
        return inbox_und_entwuerfe_leer() and not stand.kuerzlich_verworfen(db(), today())

    def fritte(conn) -> haustier.Fritte:
        return haustier.ansehen(conn, today(), datei)

    def album_pruefen(conn) -> None:
        neu = album.neue(conn, today())
        if neu:
            flash(
                "Neu in der Sammlung: " + ", ".join(f"{e.zeichen} {e.name}" for e in neu),
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
        if n < vorher:  # after a Kassensturz
            stand.setze(conn, "hinweis_bereit", "0")
        # Not in the Steuer-Quest: there nearly every step is a card, and the note would
        # cover the basket. Fritte counts them anyway and shows them on her own page.
        # Not while writing to a customer either.
        ruhe = ("/fritte", "/feierabend", "/steuer", "/mailto")
        if n <= vorher or path == "/" or path.startswith(ruhe):
            return []
        neu = n - vorher
        bereit = kassensturz.vorschau(conn, today(), aufgeraeumt=False)[1]["summe"]
        if bereit <= int(stand.einstellung(conn, "hinweis_bereit", "0") or 0):
            return []  # nothing more to look forward to (a correction, say)
        stand.setze(conn, "hinweis_bereit", str(bereit))
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
        sauber = aufgeraeumt()
        karten, vorschau = kassensturz.vorschau(conn, today(), sauber)
        pro_karte = kassensturz.pro_karte(vorschau, len(karten))
        willkommen = stand.einstellung(conn, "willkommen") == "kassensturz" and not (
            kassensturz.chronik(conn, 1)
        )
        regal = kassensturz.regal(conn)
        extras = kassensturz.vorab(conn, None)
        dicts = [k.als_dict() for k in karten]
        gerichte = [] if willkommen or not karten else kassensturz.tagesgerichte(conn, today())
        gericht_summe = {
            g.id: kassensturz.vorschau(conn, today(), sauber, g.id)[1]["summe"] for g in gerichte
        }
        gericht_vorwahl = vorwahl(gericht_summe, vorschau["summe"])
        geschaetzt = {d.id for d in haustier.SCHAETZE if d.id in f.gekauft}
        jetzt = saison.jetzt(conn, today())
        return page(
            request,
            "fritte.html",
            f=f,
            truhen_zu=truhen.geschlossen(conn),
            # what chests hold today; old saves keep their gold and jars only in the chest
            truhen_auf=[
                t
                for t in truhen.geoeffnete(conn, 24)
                if (t.inhalt or {}).get("art") in truhen.GEWICHTE
            ][:6],
            fuellung=truhen.fuellung(conn),
            pro_truhe=truhen.KARTEN_PRO_TRUHE,
            chancen=truhen.chancen(conn),
            schaetze=[(d, d.id in geschaetzt) for d in haustier.SCHAETZE],
            neu=ding.id if ding else "",
            karten=karten,
            pro_karte=pro_karte,
            vorschau=vorschau,
            willkommen=willkommen,
            stil_fragen=willkommen and stand.einstellung(conn, "stil_gefragt") != "1",
            saison=jetzt,
            streifen=saison.streifen(conn, jetzt, today()),
            haus_sichtbar=haus_sichtbar(conn),
            ort=ausbau.ort(conn),
            orte=ausbau.ORTE,
            naechster_ort=ausbau.naechster_ort(conn),
            umzug_preis=ausbau.umzug_preis(conn),
            ruf=ausbau.ruf(conn),
            bau_preis=lambda st: ausbau.preis(conn, st),
            stufen_text=lambda st: ausbau.stufen_text(conn, st),
            regal=regal,
            extras=extras,
            wirkt=kassensturz.wirkungen(conn, today(), sauber, dicts, [*extras, *regal]),
            gerichte=gerichte,
            gericht_summe=gericht_summe,
            gericht_vorwahl=gericht_vorwahl,
            plaetze=kassensturz.plaetze(conn),
            kassenbuch=stand.kassenbuch(conn),
            arten=stand.ARTEN_TEXT,
            grund=stand.GRUND,
            stufen=ausbau.STUFEN,
            naechste=ausbau.naechste(conn),
            fest_moeglich=saison.fest_moeglich(conn),
            geschenk=int(stand.einstellung(conn, "willkommen_geschenk", "0") or 0),
            fest_preis=saison.fest_preis(conn),
            fest_name=saison.FESTE[saison.quartal(today())[0]],
        )

    @app.post("/fritte/stil", dependencies=guarded)
    async def fritte_stil(request: Request):
        """Before the first Kassensturz: calm or playful, with or without sound.
        The same switches are in the settings; this just asks once, at the right moment."""
        from dataclasses import replace

        stil = await form_value(request, "stil")
        if stil in ("ruhig", "verspielt"):
            current = settings()
            replace(current, effects=stil, sound=stil == "verspielt").save(ws.settings_file)
            stand.setze(db(), "stil_gefragt", "1")
            flash(
                "Verspielt mit Klang. Leiser geht es jederzeit in den Einstellungen."
                if stil == "verspielt"
                else "Ruhig und ohne Klang. Mehr Trubel geht jederzeit in den Einstellungen."
            )
        return back("/fritte")

    @app.post("/fritte/feiern", dependencies=guarded)
    def fritte_feiern():
        conn = db()
        try:
            with store.transaction(conn):
                s = saison.feiern(conn, today())
        except stand.SpielFehler as exc:
            flash(str(exc), "error")
            return back("/fritte#saison")
        fest = saison.FESTE[saison.quartal(today())[0]]
        flash(f"Fritte feiert das {fest} {s.fest[0].lower()}{s.fest[1:]}. Schön war's!", cue="done")
        return back("/fritte#saison")

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
    async def fritte_kassensturz(request: Request):
        rezept = await form_value(request, "rezept") or None
        conn = db()
        stand.abgleichen(conn, today(), datei)
        try:
            nummer = kassensturz.durchfuehren(conn, today(), aufgeraeumt(), rezept)
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
            request, "kassensturz.html", k=ergebnis, f=fritte(conn),
            offene_truhen=len(truhen.geschlossen(conn)),
        )  # fmt: skip

    @app.post("/fritte/truhe/oeffnen", dependencies=guarded)
    def truhe_oeffnen():
        conn = db()
        try:
            t = truhen.oeffnen(conn, today())
        except stand.SpielFehler as exc:
            flash(str(exc), "error")
            return back("/fritte#truhen")
        if t is None:
            flash("Gerade wartet keine Truhe. Echter Papierkram füllt die nächste.")
            return back("/fritte#truhen")
        album_pruefen(conn)
        return back(f"/fritte/truhe/{t.id}?neu=1")

    @app.post("/fritte/truhe/alle", dependencies=guarded)
    def truhen_alle():
        """Many chests at once (after a big catch-up Kassensturz): open them all, then
        show the best find big and the others next to it, instead of a conveyor belt."""
        conn = db()
        offen = []
        for _ in range(truhen.ALLE_HOECHSTENS):
            t = truhen.oeffnen(conn, today())
            if t is None:
                break
            offen.append(t)
        if not offen:
            flash("Gerade wartet keine Truhe. Echter Papierkram füllt die nächste.")
            return back("/fritte#truhen")
        album_pruefen(conn)
        best = truhen.bester(offen)
        mit = ",".join(str(t.id) for t in offen if t.id != best.id)
        return back(f"/fritte/truhe/{best.id}?neu=1" + (f"&mit={mit}" if mit else ""))

    @app.get("/fritte/truhe/{tid}", response_class=HTMLResponse)
    def truhe_show(request: Request, tid: int, neu: str = "", mit: str = ""):
        conn = db()
        t = truhen.laden(conn, tid)
        if t is None or t.inhalt is None:
            return back("/fritte#truhen")
        fund = t.inhalt
        bild = ""
        if fund["art"] == "ding":
            bild = f"ding-{fund['id']}"
        elif fund["art"] == "gewuerz":
            bild = f"gewuerz-{fund['id']}"
        return page(
            request,
            "truhe.html",
            t=t,
            fund=fund,
            bild=bild,
            neu=neu == "1",
            f=fritte(conn),
            weitere=len(truhen.geschlossen(conn)),
            chancen=truhen.chancen(conn),
            mit=[
                x
                for i in mit.split(",")[: truhen.ALLE_HOECHSTENS]
                if i.isdigit()
                if (x := truhen.laden(conn, int(i))) and x.inhalt
            ],
        )

    @app.get("/fritte/album")
    def album_page():
        return RedirectResponse("/sammlung", status_code=303)  # old links keep working

    @app.get("/sammlung", response_class=HTMLResponse)
    def sammlung_page(request: Request):
        """One showcase for the Steuer-Quest's Orden, Fritte's treasures and stickers, the seasons."""
        conn = db()
        mit_fritte = settings().haustier
        faecher = sammlung.faecher(conn, today(), alltag=mit_fritte)
        hat, platz = sammlung.stand(faecher)
        return page(
            request,
            "sammlung.html",
            f=fritte(conn) if mit_fritte else None,
            faecher=faecher,
            zuletzt=sammlung.zuletzt(faecher),
            hat=hat,
            platz=platz,
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
        # One shop (Marie, 2026-11-02): the Steuer-Quest's helpers sit on their own shelf here.
        # The quest registers app.state.helfer_regal(conn) -> dict | None; buying stays there.
        insel = getattr(request.app.state, "helfer_regal", None)
        try:
            insel_regal = insel(conn) if insel else None
        except Exception:  # noqa: BLE001 — the shop must open even if the quest has a hiccup
            insel_regal = None
        return page(
            request,
            "fritte_laden.html",
            f=f,
            insel_regal=insel_regal,
            markt=ware,
            wirkung={g.id: wirkung(conn, today(), g, sauber) for g in ware},
            preis=lambda g: preis(conn, g),
            regal=shelf,
            im_regal={g.id for g in shelf},
            plaetze=kassensturz.plaetze(conn),
            wurf_preis=wurf_preis(conn, wuerfe),
            verkauf=lambda g: verkaufs_preis(bezahlt.get(g.id, g.preis)),
            laden=haustier.LADEN,
            schaetze=haustier.SCHAETZE,
            drachen_serie=drachen_serie(
                len(shelf),
                frozenset(
                    w["ware"].key for w in (insel_regal or {}).get("waren", []) if w.get("an")
                ),
            ),
            haus_sichtbar=haus_sichtbar(conn),
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

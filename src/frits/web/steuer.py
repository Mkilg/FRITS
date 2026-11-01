"""Die Seiten der Steuer-Quest in der FRITS-App.

Die Spielregeln stehen in frits.steuer; hier wird nur angezeigt, gespeichert und weitergeleitet.
Wie überall in FRITS: Jeder Knopf, der etwas ändert, ist ein POST mit dem geheimen Token.
"""

from __future__ import annotations

import math
import shutil
from collections.abc import Callable
from datetime import date
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from frits import store, system
from frits.settings import Workspace
from frits.steuer import beraterin, ernte, quest, spiel, truhe
from frits.steuer.katalog import FAMILIENSTAND, HAKEN, Profil, fragen, gebiet, reich

AKTIONEN = {"gibts_nicht", "fragen", "offen"}

BILDER = resources.files("frits.web") / "static" / "steuer"


def bild(name: str) -> str:
    """Adresse eines gemalten Bildes, oder "" wenn es (noch) fehlt. Dann malt CSS einen Ersatz."""
    for endung in ("webp", "png", "jpg"):
        if (BILDER / f"{name}.{endung}").is_file():
            return f"/static/steuer/{name}.{endung}"
    return ""


# Für Provinzen ohne eigenes Bild leihen wir uns ein passendes, leicht umgefärbt.
LEIHGABEN = {
    "rathaus": ("gilde", "hue-rotate(-28deg) saturate(1.05)"),
    "kaserne": ("burg", "saturate(.7) brightness(.95)"),
    "handelskontor": ("werkstatt", "hue-rotate(18deg)"),
    "aussenposten": ("hafen", "hue-rotate(-14deg) saturate(.9)"),
    "kontorhaus": ("gilde", "saturate(.75) brightness(1.04)"),
    "ferienhaus": ("gehoeft", "hue-rotate(-18deg) brightness(1.05)"),
    "schatzkammer": ("burg", "sepia(.25) saturate(1.2)"),
    "jahrmarkt": ("werkstatt", "hue-rotate(-24deg) saturate(1.15)"),
    "altenteil": ("gehoeft", "saturate(.7)"),
    "brunnen": ("gehoeft", "hue-rotate(22deg) saturate(.9)"),
    "dorf": ("gehoeft", "hue-rotate(10deg) saturate(1.1)"),
}
# Stufe 0 bis 3 auf die drei gemalten Bilder: Fundament, gebaut, blühend
BILD_ZU_STUFE = {0: 1, 1: 2, 2: 2, 3: 3}


def provinzbild(key: str, stufe: int) -> tuple[str, str]:
    """(Adresse, CSS-Filter) des Bildes einer Provinz in dieser Stufe, oder ("", "")."""
    eigenes = bild(f"{key}-{BILD_ZU_STUFE[stufe]}")
    if eigenes:
        return eigenes, ""
    if key in LEIHGABEN:
        andere, filter_ = LEIHGABEN[key]
        geliehen = bild(f"{andere}-{BILD_ZU_STUFE[stufe]}")
        if geliehen:
            return geliehen, filter_
    return "", ""


def inselplan(anzahl: int) -> list[tuple[float, float]]:
    """Wo die Provinzen auf der schwebenden Insel stehen, in Prozent (links, oben).

    Das Rathaus steht in der Mitte, die anderen gleichmäßig im Kreis darum herum.
    Ab neun Provinzen gibt es einen inneren und einen äußeren Ring.
    So sieht die Insel bei 2 Provinzen genauso aufgeräumt aus wie bei 20.
    """
    if anzahl <= 0:
        return []
    if anzahl == 2:
        return [(34.0, 45.0), (66.0, 45.0)]
    lagen = [(50.0, 45.0)]
    rest = anzahl - 1
    ringe = [(rest, 34.0, 31.0)] if rest <= 8 else [(6, 21.0, 18.0), (rest - 6, 40.0, 35.0)]
    for menge, rx, ry in ringe:
        for i in range(menge):
            start = -math.pi / 2 + (math.pi / menge if menge <= 3 else 0)
            winkel = start + (2 * math.pi * i / menge) + (0.3 if menge > 6 else 0)
            lagen.append(
                (round(50 + rx * math.cos(winkel), 1), round(45 + ry * math.sin(winkel), 1))
            )
    return lagen


def wiesenplan(anzahl: int) -> list[tuple[float, float]]:
    """Wie inselplan(), aber für die gemalte Insel: Reihen auf der Wiese, leicht versetzt
    wie in einem Dorf, die hinteren Reihen etwas schmaler (Perspektive)."""
    if anzahl <= 0:
        return []
    spalten = max(2, math.ceil(math.sqrt(anzahl * 1.6)))
    reihen = math.ceil(anzahl / spalten)
    abstand = min(100 / spalten, 30.0)
    lagen = []
    for i in range(anzahl):
        reihe, spalte = divmod(i, spalten)
        in_reihe = min(spalten, anzahl - reihe * spalten)
        versatz = (abstand / 4 if reihe % 2 else 0) + (reihe / max(1, reihen - 1)) * 3
        x = 50 + (spalte - (in_reihe - 1) / 2) * abstand + versatz
        y = (reihe + 0.5) / reihen * 100
        lagen.append((round(min(95.0, max(5.0, x)), 1), round(y, 1)))
    return lagen


def fritte_tipp(daten: dict, lager: list) -> tuple[str, str]:
    """Was Fritte, die Kartografin, gerade sagt: (Stimmung, Satz)."""
    if daten["drache"].besiegt and daten["stuecke"]:
        return "done", "Der Drache ist besiegt! Die Schatztruhe wartet auf dich."
    if lager:
        n = len(lager)
        zettel = "ein Zettel" if n == 1 else f"{n} Zettel"
        return "welcome", f"Im Eingangskorb liegen {zettel}. Zieh sie auf die Insel, ich stempel."
    fehlt_vorjahr = [v for v in daten["vergleich"] if v["jetzt"] == "offen"]
    if fehlt_vorjahr:
        name = fehlt_vorjahr[0]["name"]
        satz = f"Letztes Jahr hattest du schon „{name}“. Schau mal, ob es wieder da ist."
        return "thinking", satz
    offen = [p for p in daten["provinzen"] if not p.stand.siegel]
    if offen:
        naechste = min(offen, key=lambda p: (p.stand.offen, p.gebiet.id))
        rest = naechste.stand.offen
        fehlt = "fehlt" if rest == 1 else "fehlen"
        return "welcome", f"Wie wär's mit {naechste.gebiet.titel}? Da {fehlt} nur noch {rest}."
    return "done", "Alle Provinzen tragen ein Siegel. Der Drache zittert schon."


def register_steuer(
    app: FastAPI,
    *,
    ws: Workspace,
    page: Callable,
    back: Callable,
    flash: Callable,
    settings: Callable,
    guarded: list,
    today: Callable[[], date],
) -> None:
    app.state.steuer_combo = {}
    app.state.steuer_notausgang = set()
    app.state.steuer_passwort = {}

    def conn():
        return quest.ensure_schema(store.connect(ws.db))

    def profil(jahr: int) -> Profil | None:
        return Profil.load(quest.profil_datei(ws.root, jahr))

    def profil_oder_404(jahr: int) -> Profil:
        p = profil(jahr)
        if p is None:
            raise HTTPException(404, "Für dieses Jahr gibt es noch keinen Fragebogen.")
        return p

    def seite(request: Request, name: str, **context):
        """Wie page(), plus was alle Quest-Seiten brauchen (Bilder, Klang)."""
        return page(
            request, name, bild=bild, provinzbild=provinzbild, klang=settings().sound, **context
        )

    def lage(jahr: int):
        """Alles, was die Seiten über den Spielstand wissen müssen."""
        p = profil_oder_404(jahr)
        c = conn()
        gebiete = reich(p)
        alle = quest.stuecke(c, jahr)
        staende = spiel.provinzen(gebiete, alle)
        gesamt = spiel.gesamtstand(alle)
        vergleich = quest.vergleich(c, jahr)
        erfolge = spiel.erfolge(alle, staende, vergleich, quest.hat_notiz(c, jahr, "brief"))
        return {
            "jahr": jahr,
            "profil": p,
            "gebiete": gebiete,
            "stuecke": alle,
            "provinzen": staende,
            "gesamt": gesamt,
            "ressourcen": spiel.ressourcen(alle, staende),
            "zeitalter": spiel.zeitalter(gesamt.anteil),
            "naechstes": spiel.naechstes_zeitalter(gesamt.anteil),
            "drache": spiel.drache(alle),
            "vergleich": vergleich,
            "erfolge": erfolge,
            "chronik": quest.chronik(c, jahr),
        }

    def belohnen(jahr: int, vorher: dict) -> None:
        """Nach jeder Änderung: neue Siegel, neues Zeitalter und neue Erfolge feiern."""
        nachher = lage(jahr)
        c = conn()
        alt_siegel = {p.gebiet.id for p in vorher["provinzen"] if p.stand.siegel}
        for p in nachher["provinzen"]:
            if p.stand.siegel and p.gebiet.id not in alt_siegel:
                text = f"Gold-Siegel! {p.gebiet.titel} ist komplett."
                quest.notiere(c, jahr, "siegel", text)
                flash(text, "siegel", cue="done")
        if nachher["zeitalter"][0] > vorher["zeitalter"][0]:
            text = f"Aufstieg ins {nachher['zeitalter'][1]}!"
            quest.notiere(c, jahr, "zeitalter", text)
            flash(text, "zeitalter", cue="done")
        for e in spiel.neue_erfolge(vorher["erfolge"], nachher["erfolge"]):
            quest.notiere(c, jahr, "erfolg", e.name)
            flash(f"Erfolg freigeschaltet: {e.name}. {e.beschreibung}", "erfolg")

    def stueck_im_jahr(jahr: int, stueck_id: int) -> dict:
        try:
            s = quest.stueck(conn(), stueck_id)
        except quest.QuestError as exc:
            raise HTTPException(404, str(exc)) from exc
        if s["jahr"] != jahr:
            raise HTTPException(404)
        return s

    def standard_jahr() -> int:
        # Im Herbst 2026 sammelt man meistens für 2025.
        return today().year - 1

    async def hochladen(jahr: int, stueck_id: int, uploads: list[UploadFile]) -> int:
        """Speichert hochgeladene Dateien und sammelt sie ein. Gibt die Zahl neuer Funde zurück."""
        neu = 0
        c = conn()
        ablage = quest.lager(ws.root)
        ablage.mkdir(parents=True, exist_ok=True)
        for upload in uploads:
            name = Path(upload.filename or "").name
            if not name:
                continue
            tmp = ablage / f".upload-{name}"
            with tmp.open("wb") as fh:
                shutil.copyfileobj(upload.file, fh)
            fund = quest.sammeln(c, ws.root, stueck_id, tmp, original=name, verschieben=True)
            if fund.neu:
                neu += 1
                quest.notiere(c, jahr, "fund", f"{fund.stueck['name']}: {name}")
        return neu

    # ----- Einstieg und Fragebogen -----

    @app.get("/steuer")
    def steuer_start():
        return back(f"/steuer/{standard_jahr()}")

    @app.get("/steuer/{jahr}", response_class=HTMLResponse)
    def karte(request: Request, jahr: int):
        if profil(jahr) is None:
            return back(f"/steuer/{jahr}/fragebogen")
        daten = lage(jahr)
        lager = quest.lager_dateien(ws.root)
        stimmung, tipp = fritte_tipp(daten, lager)
        return seite(
            request,
            "steuer_karte.html",
            lager=lager,
            ernte=ernte.finden(conn(), ws, jahr, settings().tax_mode),
            ernte_ziele=ernte.ziele(daten["gebiete"]),
            plan=(wiesenplan if bild("insel") else inselplan)(len(daten["provinzen"])),
            fritte=stimmung,
            tipp=tipp,
            **daten,
        )

    def _zahl(value, hoechstens: int = 20) -> int:
        try:
            return max(0, min(hoechstens, int(str(value or 0))))
        except ValueError:
            return 0

    def entwurf(jahr: int) -> Profil:
        """Der Fragebogen, wie er gerade ausgefüllt wird (Vorlage: dieses Jahr oder das Vorjahr)."""
        p = app.state.steuer_entwurf.get(jahr)
        if p is None:
            p = profil(jahr) or profil(jahr - 1)
            if p is None:
                p = Profil(jahr=jahr, name=settings().name)
                # Hat FRITS in dem Jahr Rechnungen geschrieben, gibt es sicher einen Betrieb.
                if ernte.hat_rechnungen(ws, jahr):
                    p.mengen["freiberuflich"] = [1, 0]
                if settings().tax_mode == "regelbesteuerung":
                    p.umsatzsteuer = "quartal"
            p.jahr = jahr
            app.state.steuer_entwurf[jahr] = p
        return p

    app.state.steuer_entwurf = {}

    @app.get("/steuer/{jahr}/fragebogen", response_class=HTMLResponse)
    def fragebogen(request: Request, jahr: int, teil: int = 1):
        teil = teil if teil in (1, 2, 3) else 1
        p = entwurf(jahr)
        return seite(
            request,
            "steuer_fragebogen.html",
            jahr=jahr,
            profil=p,
            teil=teil,
            familienstaende=FAMILIENSTAND,
            provinz_fragen=fragen(),
            haken=HAKEN,
        )

    @app.post("/steuer/{jahr}/fragebogen", dependencies=guarded)
    async def fragebogen_speichern(request: Request, jahr: int):
        form = await request.form()
        p = entwurf(jahr)
        teil = _zahl(form.get("teil"), 3) or 1
        if teil == 1:
            p.name = str(form.get("name") or "").strip()
            stand = str(form.get("familienstand") or "ledig")
            p.familienstand = stand if stand in FAMILIENSTAND else "ledig"
            p.zusammen = p.familienstand == "verheiratet" and form.get("zusammen") == "on"
            p.partner = str(form.get("partner") or "").strip() if p.zusammen else ""
            p.kinder = _zahl(form.get("kinder"))
            p.beraterin_email = str(form.get("beraterin_email") or "").strip()
            p.beraterin_termin = str(form.get("beraterin_termin") or "").strip()
        elif teil == 2:
            mengen = {}
            for provinz in fragen():
                werte = []
                for person in (0, 1):
                    roh = form.get(f"m_{provinz.frage}_{person}")
                    werte.append(1 if roh == "on" else _zahl(roh))
                mengen[provinz.frage] = werte
            p.mengen = mengen
            ust = str(form.get("umsatzsteuer") or "keine")
            p.umsatzsteuer = ust if ust in ("keine", "monatlich", "quartal") else "keine"
        else:
            p.haken = [h for h in form.getlist("haken") if h in HAKEN]
        if teil < 3:
            return back(f"/steuer/{jahr}/fragebogen?teil={teil + 1}")

        app.state.steuer_entwurf.pop(jahr, None)
        neu = profil(jahr) is None
        p.save(quest.profil_datei(ws.root, jahr))
        gebiete = quest.start(conn(), p)
        quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
        if neu:
            quest.notiere(conn(), jahr, "start", f"Reich gegründet mit {len(gebiete)} Provinzen")
            flash(f"Dein Reich steht: {len(gebiete)} Provinzen warten auf dich.", "erfolg")
        else:
            flash("Fragebogen gespeichert. Die Karte ist aktualisiert.")
        return back(f"/steuer/{jahr}")

    # ----- Provinzen und Sammeln -----

    @app.get("/steuer/{jahr}/gebiet/{gebiet_id}", response_class=HTMLResponse)
    def provinz(request: Request, jahr: int, gebiet_id: str):
        g = gebiet(profil_oder_404(jahr), gebiet_id)
        if g is None:
            raise HTTPException(404)
        daten = lage(jahr)
        c = conn()
        eigene = [s for s in daten["stuecke"] if s["gebiet"] == g.id]
        for s in eigene:
            s["datei_liste"] = quest.dateien(c, jahr, s["id"])
        vorjahr = [v for v in daten["vergleich"] if v["gebiet"] == g.id]
        stand = next(p for p in daten["provinzen"] if p.gebiet.id == g.id)
        return seite(
            request,
            "steuer_gebiet.html",
            g=g,
            eigene=eigene,
            vorjahr=vorjahr,
            provinzstand=stand,
            lager=quest.lager_dateien(ws.root),
            **daten,
        )

    @app.post("/steuer/{jahr}/stueck/{stueck_id}/status", dependencies=guarded)
    async def status(request: Request, jahr: int, stueck_id: int):
        form = await request.form()
        s = stueck_im_jahr(jahr, stueck_id)
        aktion = str(form.get("status", ""))
        if aktion not in AKTIONEN:
            raise HTTPException(400)
        vorher = lage(jahr)
        frage = str(form.get("frage") or "").strip() if aktion == "fragen" else ""
        quest.setze_status(conn(), stueck_id, aktion, frage)
        if aktion == "gibts_nicht":
            flash(f"Notiert: {s['name']} gibt es dieses Jahr nicht. Zählt trotzdem!", "beute")
        elif aktion == "fragen":
            flash("Kommt auf die Fragenliste für deine Beraterin. Fragen ist klug.", "beute")
        belohnen(jahr, vorher)
        return back(str(form.get("back") or f"/steuer/{jahr}/gebiet/{s['gebiet']}"))

    @app.post("/steuer/{jahr}/stueck/{stueck_id}/datei", dependencies=guarded)
    async def datei_hochladen(request: Request, jahr: int, stueck_id: int):
        form = await request.form()
        s = stueck_im_jahr(jahr, stueck_id)
        uploads = [f for f in form.getlist("datei") if hasattr(f, "filename") and f.filename]
        if not uploads:
            flash("Da war keine Datei dabei. Zieh eine Datei in das Feld oder wähle eine aus.")
            return back(f"/steuer/{jahr}/gebiet/{s['gebiet']}")
        vorher = lage(jahr)
        neu = await hochladen(jahr, stueck_id, uploads)
        if neu:
            flash(
                f"+{neu} Beleg{'e' if neu > 1 else ''}! {s['name']} ist gesichert.", "beute", "done"
            )
        else:
            flash("Diese Datei hattest du schon gesammelt. Doppelt zählt nicht, aber gut gemerkt!")
        belohnen(jahr, vorher)
        return back(str(form.get("back") or f"/steuer/{jahr}/gebiet/{s['gebiet']}"))

    @app.post("/steuer/{jahr}/lager/zuordnen", dependencies=guarded)
    async def lager_zuordnen(request: Request, jahr: int):
        form = await request.form()
        name = Path(str(form.get("name", ""))).name
        quelle = quest.lager(ws.root) / name
        if not name or not quelle.is_file():
            raise HTTPException(404)
        try:
            stueck_id = int(str(form.get("stueck_id", "")))
        except ValueError:
            flash("Wähle aus, wohin die Datei gehört.")
            return back(f"/steuer/{jahr}")
        s = stueck_im_jahr(jahr, stueck_id)
        vorher = lage(jahr)
        c = conn()
        fund = quest.sammeln(c, ws.root, stueck_id, quelle, verschieben=True)
        if fund.neu:
            quest.notiere(c, jahr, "fund", f"{s['name']}: {name}")
            flash(f"+1 Beleg! {name} → {s['name']}", "beute", "done")
        else:
            flash(f"{name} war schon gesammelt, ich habe die doppelte Kopie weggeräumt.")
        belohnen(jahr, vorher)
        return back(str(form.get("back") or f"/steuer/{jahr}"))

    @app.post("/steuer/{jahr}/datei/{datei_id}/entfernen", dependencies=guarded)
    async def datei_entfernen(request: Request, jahr: int, datei_id: int):
        form = await request.form()
        quest.entferne_datei(conn(), datei_id)
        flash("Die Datei liegt wieder im Lager. Nichts ist verloren.")
        return back(str(form.get("back") or f"/steuer/{jahr}"))

    @app.post("/steuer/{jahr}/lager/oeffnen", dependencies=guarded)
    def lager_oeffnen(jahr: int):
        ordner = quest.lager(ws.root)
        ordner.mkdir(parents=True, exist_ok=True)
        if not system.open_path(ordner):
            flash(f"Dein Lager findest du hier: {ordner}")
        return back(f"/steuer/{jahr}")

    # ----- FRITS-Ernte: Rechnungen, Belege und EÜR aus dem normalen FRITS -----

    @app.post("/steuer/{jahr}/ernte", dependencies=guarded)
    async def ernte_einbringen(request: Request, jahr: int):
        form = await request.form()
        daten = lage(jahr)
        ziele = {g.id: g for g in ernte.ziele(daten["gebiete"])}
        gebiet_id = str(form.get("gebiet") or "")
        if gebiet_id not in ziele:
            flash("Wähle aus, zu welchem Betrieb die Rechnungen gehören.")
            return back(f"/steuer/{jahr}")
        c = conn()
        neu = ernte.einbringen(c, ws, jahr, gebiet_id, settings().tax_mode)
        teile = []
        if neu["rechnungen"]:
            teile.append(f"{neu['rechnungen']} Rechnung{'en' if neu['rechnungen'] > 1 else ''}")
        if neu["belege"]:
            teile.append(f"{neu['belege']} Beleg{'e' if neu['belege'] > 1 else ''}")
        if neu["euer"]:
            teile.append("die EÜR")
        if teile:
            text = f"FRITS-Ernte eingebracht: {', '.join(teile)} → {ziele[gebiet_id].titel}"
            quest.notiere(c, jahr, "fund", text)
            flash(text + "!", "beute", "done")
        else:
            flash("Aus FRITS ist schon alles da. Nichts doppelt.")
        belohnen(jahr, daten)
        return back(f"/steuer/{jahr}")

    # ----- Mail der Beraterin -----

    @app.get("/steuer/{jahr}/beraterin", response_class=HTMLResponse)
    def beraterin_seite(request: Request, jahr: int):
        return seite(request, "steuer_beraterin.html", ergebnis=None, text="", **lage(jahr))

    @app.post("/steuer/{jahr}/beraterin", dependencies=guarded)
    async def beraterin_lesen(request: Request, jahr: int):
        form = await request.form()
        text = str(form.get("text") or "")
        daten = lage(jahr)
        wuensche = beraterin.zuordnen(text, daten["gebiete"], daten["stuecke"])
        if not wuensche:
            flash("Ich habe in der Mail keine Wünsche gefunden. Aufzählungen klappen am besten.")
            return seite(request, "steuer_beraterin.html", ergebnis=None, text=text, **daten)
        c = conn()
        titel = {g.id: g.titel for g in daten["gebiete"]}
        ergebnis = []
        for w in wuensche:
            if w.stueck_id:
                quest.markiere_wunsch(c, w.stueck_id)
                ergebnis.append({"text": w.text, "wo": titel[w.gebiete[0]], "neu": False})
            else:
                for gid in w.gebiete:
                    quest.neues_stueck(
                        c, jahr, gid, w.text[:120], hinweis="Wunsch deiner Beraterin"
                    )
                ergebnis.append(
                    {"text": w.text, "wo": ", ".join(titel[g] for g in w.gebiete), "neu": True}
                )
        vorher = daten
        quest.notiere(c, jahr, "brief", f"{len(wuensche)} Aufträge der Beraterin")
        flash(f"Botschaft entschlüsselt: {len(wuensche)} Aufträge auf deiner Karte.", "erfolg")
        belohnen(jahr, vorher)
        return seite(request, "steuer_beraterin.html", ergebnis=ergebnis, text="", **lage(jahr))

    # ----- Der Papierdrache -----

    @app.get("/steuer/{jahr}/drache", response_class=HTMLResponse)
    def drache_seite(request: Request, jahr: int):
        daten = lage(jahr)
        combo = dict(app.state.steuer_combo.get(jahr, {"combo": 0, "ruf": spiel.RUFE[0]}))
        frisch = app.state.steuer_combo.get(jahr, {}).pop("frisch", False)
        return seite(request, "steuer_drache.html", combo=combo, frisch=frisch, **daten)

    @app.post("/steuer/{jahr}/drache/schlag", dependencies=guarded)
    async def drache_schlag(request: Request, jahr: int):
        form = await request.form()
        s = stueck_im_jahr(jahr, int(str(form.get("stueck_id") or 0)))
        aktion = str(form.get("aktion") or "")
        vorher = lage(jahr)
        kritisch = False
        if aktion == "datei":
            uploads = [f for f in form.getlist("datei") if hasattr(f, "filename") and f.filename]
            if not uploads:
                flash("Wähle eine Datei aus, dann trifft der Schlag.")
                return back(f"/steuer/{jahr}/drache")
            kritisch = await hochladen(jahr, s["id"], uploads) > 0
            if not kritisch:
                quest.setze_status(conn(), s["id"], "gesammelt")
        elif aktion in ("gibts_nicht", "fragen"):
            frage = str(form.get("frage") or "").strip() if aktion == "fragen" else ""
            quest.setze_status(conn(), s["id"], aktion, frage)
        else:
            raise HTTPException(400)
        alt = app.state.steuer_combo.get(jahr, {"combo": 0})
        combo, ruf = spiel.treffer(alt["combo"], kritisch)
        app.state.steuer_combo[jahr] = {
            "combo": combo,
            "ruf": ruf,
            "frisch": "kritisch" if kritisch else "treffer",
        }
        quest.notiere(conn(), jahr, "treffer", f"{s['name']} ({ruf})")
        belohnen(jahr, vorher)
        if spiel.drache(quest.stuecke(conn(), jahr)).besiegt:
            quest.notiere(conn(), jahr, "sieg", "Der Papierdrache ist besiegt")
            app.state.steuer_combo.pop(jahr, None)
            flash("SIEG! Der Drache faltet sich zu einer Schatztruhe zusammen.", "sieg", "sent")
            return back(f"/steuer/{jahr}/sieg")
        return back(f"/steuer/{jahr}/drache")

    @app.post("/steuer/{jahr}/drache/notausgang", dependencies=guarded)
    def notausgang(jahr: int):
        app.state.steuer_notausgang.add(jahr)
        flash("Kein Problem. Was fehlt, steht in der Mail. Der Drache wartet geduldig.")
        return back(f"/steuer/{jahr}/sieg")

    # ----- Sieg, Schatztruhe und Mail -----

    def truhe_pfad(jahr: int, p: Profil) -> Path:
        name = f"-{quest._slug(p.name)}" if p.name else ""
        return quest.ordner(ws.root, jahr) / f"Steuer-{jahr}{name}.zip"

    @app.get("/steuer/{jahr}/sieg", response_class=HTMLResponse)
    def sieg(request: Request, jahr: int):
        daten = lage(jahr)
        if not daten["drache"].besiegt and jahr not in app.state.steuer_notausgang:
            flash("Erst der Drache, dann die Beute. Oder nimm den Notausgang, wenn es eilt.")
            return back(f"/steuer/{jahr}/drache")
        betreff, text = truhe.mail(daten["profil"], daten["gebiete"], daten["stuecke"])
        zip_pfad = truhe_pfad(jahr, daten["profil"])
        return seite(
            request,
            "steuer_sieg.html",
            betreff=betreff,
            mailtext=text,
            zip_pfad=zip_pfad if zip_pfad.exists() else None,
            passwort=app.state.steuer_passwort.get(jahr),
            dateien=quest.dateien(conn(), jahr),
            **daten,
        )

    @app.post("/steuer/{jahr}/truhe", dependencies=guarded)
    def truhe_packen(jahr: int):
        daten = lage(jahr)
        if not daten["drache"].besiegt and jahr not in app.state.steuer_notausgang:
            return back(f"/steuer/{jahr}/drache")
        kennwort = truhe.passwort()
        ziel = truhe.packen(
            truhe_pfad(jahr, daten["profil"]),
            daten["profil"],
            daten["gebiete"],
            daten["stuecke"],
            quest.dateien(conn(), jahr),
            kennwort,
        )
        app.state.steuer_passwort[jahr] = kennwort
        quest.notiere(conn(), jahr, "truhe", ziel.name)
        flash("Die Schatztruhe ist gepackt und verschlossen.", "erfolg", "done")
        return back(f"/steuer/{jahr}/sieg")

    @app.post("/steuer/{jahr}/mail", dependencies=guarded)
    async def mail_oeffnen(request: Request, jahr: int):
        form = await request.form()
        p = profil_oder_404(jahr)
        zip_pfad = truhe_pfad(jahr, p)
        if not zip_pfad.exists():
            flash("Pack zuerst die Schatztruhe, dann hänge ich sie an.")
            return back(f"/steuer/{jahr}/sieg")
        betreff = str(form.get("betreff") or f"Steuerunterlagen {jahr}")
        text = str(form.get("text") or "")
        result = system.compose_email(p.beraterin_email, betreff, text, zip_pfad)
        if result.opened:
            flash("Die Mail ist in deinem Mail-Programm. Du drückst dort auf Senden.", cue="sent")
            return back(f"/steuer/{jahr}/sieg")
        app.state.mailto = result.mailto
        flash(f"Hänge die Truhe bitte selbst an: {zip_pfad}")
        return back("/mailto")

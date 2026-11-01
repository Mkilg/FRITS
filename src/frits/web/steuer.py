"""Die Seiten der Steuer-Quest in der FRITS-App.

Die Spielregeln stehen in frits.steuer; hier wird nur angezeigt, gespeichert und weitergeleitet.
Wie überall in FRITS: Jeder Knopf, der etwas ändert, ist ein POST mit dem geheimen Token.
"""

from __future__ import annotations

import math
import re
import shutil
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from frits import store, system
from frits.settings import Workspace
from frits.steuer import beraterin, ernte, quest, spiel, truhe
from frits.steuer.katalog import (
    FAMILIENSTAND,
    HAKEN,
    MACHT_BERATERIN,
    Profil,
    fehlt_nie,
    fragen,
    gebiet,
    ist_kern,
    macht_beraterin,
    reich,
    warum,
)

AKTIONEN = {"gibts_nicht", "fragen", "offen"}
MAX_LAGER = 30 * 1024 * 1024  # wie beim Eingang von FRITS

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


MONATE = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)


def schoenes_datum(iso: str) -> str:
    """ "2026-12-15" wird zu "15. Dezember 2026"; alles andere bleibt, wie es ist."""
    try:
        d = date.fromisoformat(str(iso))
    except ValueError:
        return str(iso)
    return f"{d.day}. {MONATE[d.month - 1]} {d.year}"


def textname(text: str) -> str:
    """Ein eingefügter Text heißt nach seinen ersten Wörtern, damit man drei Zettel unterscheiden kann.

    Wörter mit Ziffern (Steuernummer, IBAN, Beträge) und Wörter wie "Steuernummer" bleiben weg:
    Dateinamen stehen auch in einer verschlüsselten Zip lesbar da."""
    heikel = {"steuernummer", "steuer-id", "iban", "identifikationsnummer", "steuer-nr", "konto"}
    woerter = [
        w
        for w in re.findall(r"[^\W_][\w.-]*", text)
        if not any(z.isdigit() for z in w) and w.lower().strip(".:") not in heikel
    ][:6]
    name = " ".join(woerter)[:48].strip(" .-")
    return f"{name or 'Eingefügter Text'}.txt"


def fritte_tipp(daten: dict, lager: list) -> tuple[str, str]:
    """Was Fritte, die Kartografin, gerade sagt: (Stimmung, Satz)."""
    if daten["drache"].besiegt and daten["stuecke"]:
        return "done", "Der Drache ist besiegt! Die Schatztruhe wartet auf dich."
    if daten["auftrag"].art == "drache":
        lp = daten["drache"].lp
        return (
            "welcome",
            f"Der Drache ist wach und hat noch {lp} Karte{'n' if lp != 1 else ''}. Ich komm mit!",
        )
    if lager:
        n = len(lager)
        if n == 1:
            return (
                "welcome",
                "Im Eingangskorb liegt ein Zettel. Zieh ihn auf die Insel, ich stempel.",
            )
        return "welcome", f"Im Eingangskorb liegen {n} Zettel. Zieh sie auf die Insel, ich stempel."
    fehlt_vorjahr = [v for v in daten["vergleich"] if v["jetzt"] == "offen"]
    if fehlt_vorjahr:
        name = fehlt_vorjahr[0]["name"]
        satz = f"Letztes Jahr hattest du schon „{name}“. Schau mal, ob es wieder da ist."
        return "thinking", satz
    auftrag = daten["auftrag"]
    if auftrag.art == "provinz":
        if auftrag.erledigt:
            return (
                "welcome",
                "Du bist mittendrin! Ich hab dir den Weg mit dem Leuchtfeuer markiert.",
            )
        if not any(p.stand.erledigt for p in daten["provinzen"]):
            return (
                "welcome",
                "Ich hab dir die erste Provinz markiert. Klick auf das Leuchtfeuer, ich zeig dir alles.",
            )
        return "welcome", "Weiter geht's! Folge dem Leuchtfeuer zur nächsten Provinz."
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
    # Jahre, deren Drache gerade gefallen ist: dort die Falt-Animation zeigen
    app.state.steuer_finale = set()
    # Der letzte Zettel aus der Hand, damit man ihn zurücklegen kann
    app.state.steuer_wurf = {}

    def conn():
        return quest.ensure_schema(store.connect(ws.db))

    def profil(jahr: int) -> Profil | None:
        return Profil.load(quest.profil_datei(ws.root, jahr))

    def profil_oder_404(jahr: int) -> Profil:
        p = profil(jahr)
        if p is None:
            # Kein JSON-Fehler: freundlich zum Anfang der Quest für dieses Jahr
            raise HTTPException(303, headers={"Location": f"/steuer/{jahr}/intro"})
        return p

    def seite(request: Request, name: str, **context):
        """Wie page(), plus was alle Quest-Seiten brauchen (Bilder, Klang)."""
        return page(
            request,
            name,
            bild=bild,
            provinzbild=provinzbild,
            klang=settings().sound,
            kern=ist_kern,
            fehlt_nie=fehlt_nie,
            warum=warum,
            macht_beraterin=macht_beraterin,
            MACHT_BERATERIN=MACHT_BERATERIN,
            datum=schoenes_datum,
            vermute=spiel.vermute,
            ahnung=spiel.ahnung,
            ware=spiel.ware,
            **context,
        )

    def quest_jahre() -> list[int]:
        """Die Jahre, für die es schon eine Quest (einen Fragebogen) gibt."""
        basis = ws.root / "steuer"
        if not basis.is_dir():
            return []
        return sorted(
            int(d.name)
            for d in basis.iterdir()
            if d.name.isdigit() and (d / "fragebogen.toml").exists()
        )

    def entwurf_datei(jahr: int) -> Path:
        return quest.ordner(ws.root, jahr) / ".frits" / "fragebogen-entwurf.toml"

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
        drache = spiel.drache(alle)
        zettel = len(quest.lager_dateien(ws.root))
        zweifel = spiel.zweifel(alle)
        wach = spiel.drache_wach(alle) or jahr in app.state.steuer_notausgang
        besitz = quest.besitz(c, jahr)
        return {
            "jahr": jahr,
            "profil": p,
            "gebiete": gebiete,
            "stuecke": alle,
            "provinzen": staende,
            "gesamt": gesamt,
            "ressourcen": replace(spiel.ressourcen(alle, staende), gold=quest.gold(c, jahr)),
            "besitz": besitz,
            "helfer": frozenset(k for k, an in besitz.items() if an),
            "zierde": [w for w in spiel.MARKT if w.art == "zierde" and w.key in besitz],
            # Das Goldene Zeitalter kommt erst mit dem Sieg über den Drachen
            "zeitalter": spiel.zeitalter(
                gesamt.anteil if drache.besiegt else min(gesamt.anteil, 0.99),
                silber=bool(zweifel),
            ),
            "naechstes": spiel.naechstes_zeitalter(gesamt.anteil),
            "drache": drache,
            "drache_wach": wach,
            "auftrag": spiel.auftrag(jahr, staende, drache, zettel, alle, wach),
            "tagwerk": spiel.tagwerk(alle, date.today()),
            "zweifel": zweifel,
            "burg_fehlt": not any(g.provinz.key == "burg" for g in gebiete),
            "jahre": quest_jahre(),
            "vergleich": vergleich,
            "erfolge": erfolge,
            "chronik": quest.chronik(c, jahr),
        }

    def beraterin_merken(p: Profil, form) -> None:
        """Übernimmt, was im Formular über die Beraterin steht. Felder, die fehlen, bleiben."""
        if "beraterin_email" in form:
            p.beraterin_email = str(form.get("beraterin_email") or "").strip()
        if "beraterin_name" in form:
            p.beraterin_name = str(form.get("beraterin_name") or "").strip()[:80]
        if form.get("anrede") in ("du", "sie"):
            p.anrede = str(form.get("anrede"))
        if "beraterin_termin" in form:
            termin = str(form.get("beraterin_termin") or "").strip()
            try:
                p.beraterin_termin = date.fromisoformat(termin).isoformat() if termin else ""
            except ValueError:
                p.beraterin_termin = ""

    def titel_von(jahr: int, gebiet_id: str) -> str:
        p = profil_oder_404(jahr)
        g = gebiet(p, gebiet_id)
        return g.titel if g else gebiet_id

    def doppelt_text(jahr: int, fund: quest.Fund | None, karte: dict) -> str:
        """Ehrlich sagen, warum eine Datei nicht (noch einmal) zählt."""
        if fund is None or fund.stueck["id"] == karte["id"]:
            return "Diese Datei hattest du schon gesammelt. Doppelt zählt nicht, aber gut gemerkt!"
        wo = f"„{fund.stueck['name']}“ ({titel_von(jahr, fund.stueck['gebiet'])})"
        return (
            f"Diese Datei liegt schon bei {wo}. Eine Datei zählt nur für eine Karte,"
            " außer bei derselben Unterlage für mehrere Wohnungen oder Firmen."
        )

    def belohnen(
        jahr: int, vorher: dict, ferne: bool = True, mult: int = 1, auftraege: bool = False
    ) -> bool:
        """Nach jeder Änderung: neue Siegel, neues Zeitalter, neue Orden und den Sieg feiern.

        Jedes Fest gibt es nur einmal: Wer einen Stempel zurücknimmt und neu setzt, bekommt
        beim zweiten Mal höchstens einen kleinen Zettel. Gibt True zurück, wenn der Drache
        gerade gefallen ist; dann geht es immer zur Drachenseite und ihrem Falt-Moment,
        egal ob der letzte Treffer im Kampf oder in einer Provinz kam (ferne=True).

        auftraege=True: Neue Wünsche der Beraterin sind dazugekommen. Das ist keine Strafe:
        Ein Siegel, das dadurch wieder offen ist, behält sein Gold, und der Drache wartet nur.
        """
        nachher = lage(jahr)
        c = conn()
        helfer = nachher["helfer"]
        # Gold: jede Karte zahlt einmal aus, im Kampf mal der Serie (Balatro: Chips × Mult)
        # Gold nur für die letzten Steuerjahre (sonst ließe es sich mit erfundenen Jahren farmen)
        goldjahr = spiel.gold_jahr(jahr, today())
        basis, faktor, plus, minus, minus_siegel = 0, 1, 0, 0, 0
        wuensche = quest.wunsch_funde(c, jahr)
        for v in spiel.verdienste(vorher["stuecke"], nachher["stuecke"], mult, helfer):
            if not goldjahr:
                break
            if ":wunsch:" in v.grund and v.grund.startswith("fund:"):
                if wuensche >= spiel.WUNSCH_DECKEL:
                    continue  # Wünsche zahlen nur bis zum Deckel (man kann sie selbst erfinden)
                wuensche += 1
            if quest.verdiene(c, jahr, v.grund, v.betrag, v.text):
                basis, faktor, plus = basis + v.basis, v.mult, plus + v.betrag
        # Wieder offen: Das Gold dafür geht zurück (sonst lohnt ein Fehlwurf bei ×8)
        for grund, text in spiel.rueckbuchungen(vorher["stuecke"], nachher["stuecke"]):
            minus += quest.storniere(c, jahr, grund, text)
        neu_siegel = {p.gebiet.id for p in nachher["provinzen"] if p.stand.siegel}
        wartend = [
            p.gebiet.titel
            for p in vorher["provinzen"]
            if p.stand.siegel and p.gebiet.id not in neu_siegel
        ]
        if auftraege and wartend:
            flash(
                f"Neue Aufträge für {', '.join(wartend)}. Dein Siegel wartet, bis du sie erledigt"
                " hast. Das Gold dafür behältst du.",
                "hinweis",
            )
        for p in vorher["provinzen"] if not auftraege else []:
            if p.stand.siegel and p.gebiet.id not in neu_siegel:
                minus_siegel += quest.storniere(
                    c, jahr, f"siegel:{p.gebiet.id}", f"Siegel {p.gebiet.titel}"
                )
        if minus or minus_siegel:
            teile = []
            if minus:
                teile.append(f"{minus} für die Karte")
            if minus_siegel:
                teile.append(f"{minus_siegel} für das Siegel")
            text = f"−{minus + minus_siegel} Gold zurückgebucht"
            text += f" ({' und '.join(teile)})." if minus and minus_siegel else "."
            if minus_siegel and not minus:
                text = f"−{minus_siegel} Gold zurückgebucht: Das Siegel ist wieder offen."
            flash(text + " Wer es neu verdient, bekommt es wieder.", "hinweis")
            for name in quest.decke_schulden(c, jahr):
                flash(
                    f"Das Gold war schon ausgegeben, darum ist {name} zurück im Hafen."
                    " Du kannst es wieder kaufen, sobald du das Gold neu verdient hast.",
                    "hinweis",
                )
        alt_siegel = {p.gebiet.id for p in vorher["provinzen"] if p.stand.siegel}
        for p in nachher["provinzen"]:
            if p.stand.siegel and p.gebiet.id not in alt_siegel:
                titel = p.gebiet.titel
                schon = quest.hat_notiz(
                    c, jahr, "siegel", f"Gold-Siegel! {titel} ist komplett."
                ) or quest.hat_notiz(c, jahr, "siegel", f"Silber-Siegel! {titel} ist komplett.")
                text = f"{'Silber' if p.silber else 'Gold'}-Siegel! {titel} ist komplett."
                funde = sum(1 for x in p.stuecke if x["status"] == "gesammelt")
                if goldjahr:
                    plus += quest.verdiene(
                        c,
                        jahr,
                        f"siegel:{p.gebiet.id}",
                        spiel.siegel_gold(funde),
                        f"Siegel: {titel}",
                    )
                if schon:
                    flash(f"{titel} ist wieder komplett.", "beute")  # kein zweites Fest
                    continue
                quest.notiere(c, jahr, "siegel", text)
                if p.silber:
                    text += " Deine Beraterin prüft noch etwas."
                flash(text, "siegel", cue="done")
        drache_vorher, drache_nachher = vorher["drache"], nachher["drache"]
        sieg = drache_nachher.besiegt and not drache_vorher.besiegt and bool(nachher["stuecke"])
        if nachher["drache_wach"] and not vorher["drache_wach"] and not drache_nachher.besiegt:
            lp = drache_nachher.lp
            text = f"Er hat nur noch {lp} Karte{'n' if lp != 1 else ''}. Jetzt bist du stark genug."
            if quest.hat_notiz(c, jahr, "erwachen"):
                flash(f"Der Drache ist wieder wach. {text}", "treffer")  # kein zweites Fest
            else:
                quest.notiere(c, jahr, "erwachen", "Der Papierdrache ist erwacht")
                flash(text, "erwachen", cue="done")
        elif vorher["drache_wach"] and not nachher["drache_wach"] and auftraege:
            flash("Der Drache döst, bis du die neuen Aufträge angesehen hast.", "hinweis")
        elif vorher["drache_wach"] and not nachher["drache_wach"]:
            flash("Der Drache ist wieder eingeschlafen. Er wacht bei 40 % wieder auf.", "hinweis")
        elif (
            ferne
            and not sieg
            and vorher["drache_wach"]
            and drache_nachher.lp < drache_vorher.lp
            and not drache_nachher.besiegt
        ):
            lp = drache_nachher.lp
            flash(
                f"Treffer aus der Ferne! Der Drache hat noch {lp} Karte{'n' if lp != 1 else ''}.",
                "treffer",
            )
        heute, ziel = nachher["tagwerk"]
        tag = f"tagwerk:{date.today()}"
        if vorher["tagwerk"][0] < ziel <= heute and not sieg and not quest.hat_gold(c, jahr, tag):
            # Einmal am Tag: Wer zurücklegt und neu zuordnet, bekommt kein zweites Fest
            quest.notiere(c, jahr, "tagwerk", "Tagwerk geschafft")
            lohn = spiel.tagwerk_gold(helfer) if goldjahr else 0
            if lohn:
                lohn = quest.verdiene(c, jahr, tag, lohn, "Tagwerk")
            plus += lohn
            flash(
                f"Tagwerk geschafft: {ziel} Karten heute"
                + (f", +{lohn} Gold." if lohn else ".")
                + " Mehr muss nicht sein, aber du darfst.",
                "tagwerk",
            )
        if nachher["zeitalter"][0] > vorher["zeitalter"][0]:
            text = spiel.aufstieg(nachher["zeitalter"][1]) + "!"
            if not quest.hat_notiz(c, jahr, "zeitalter", text):
                quest.notiere(c, jahr, "zeitalter", text)
                flash(text, "zeitalter", cue="done")
        for e in spiel.neue_erfolge(vorher["erfolge"], nachher["erfolge"]):
            if quest.hat_notiz(c, jahr, "erfolg", e.name):
                continue  # den Orden gab es schon einmal
            quest.notiere(c, jahr, "erfolg", e.name)
            flash(f"Neuer Orden: {e.name}. {e.beschreibung}", "orden")
        gold = (
            vorher["zweifel"]
            and not nachher["zweifel"]
            and drache_nachher.besiegt
            and drache_vorher.besiegt
        )
        if gold:
            # Wer Silber nachträglich aufwertet, soll das spüren
            text = "Aus Silber wird Gold! Alle wichtigen Unterlagen sind geklärt."
            quest.notiere(c, jahr, "gold", text)
            flash(text, "gold", cue="done")
        if plus and not ferne and jahr in app.state.steuer_combo:
            app.state.steuer_combo[jahr]["beute"] = {"basis": basis, "mult": faktor, "plus": plus}
        if sieg:
            quest.notiere(c, jahr, "sieg", "Der Papierdrache ist besiegt")
            app.state.steuer_combo.pop(jahr, None)
            app.state.steuer_finale.add(jahr)
            if nachher["zweifel"]:
                flash(
                    "Der Drache faltet sich zur Schatztruhe. Ein paar wichtige Unterlagen"
                    " soll deine Beraterin noch prüfen, darum Silber.",
                    "sieg",
                    "sent",
                )
            else:
                flash("Der Drache faltet sich zu einer Schatztruhe zusammen.", "sieg", "sent")
            if plus and not ferne:
                # Der größte Treffer des Kampfes soll man sehen, auch wenn der Sieg ihn verdrängt
                flash(f"Letzter Treffer: {basis} × {faktor} = +{basis * faktor} Gold.", "sieg")
        return sieg

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

    async def hochladen(jahr: int, stueck_id: int, uploads: list[UploadFile]) -> list[quest.Fund]:
        """Speichert hochgeladene Dateien und sammelt sie ein. Gibt alle Funde zurück (neu oder
        schon da), damit die Seite ehrlich sagen kann, was passiert ist."""
        funde = []
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
            funde.append(fund)
            if fund.neu:
                quest.notiere(c, jahr, "fund", f"{fund.stueck['name']}: {name}")
        return funde

    # ----- Einstieg und Fragebogen -----

    @app.get("/steuer")
    def steuer_start():
        return back(f"/steuer/{standard_jahr()}")

    @app.get("/steuer/{jahr}", response_class=HTMLResponse)
    def karte(request: Request, jahr: int, neu: str = ""):
        if profil(jahr) is None:
            return back(f"/steuer/{jahr}/intro")
        daten = lage(jahr)
        lager = quest.lager_dateien(ws.root)
        stimmung, tipp = fritte_tipp(daten, lager)
        return seite(
            request,
            "steuer_karte.html",
            lager=lager,
            ernte=ernte.finden(conn(), ws, jahr, settings().tax_mode),
            ernte_ziele=ernte.ziele(daten["gebiete"]),
            plan=(wiesenplan if bild("insel") else inselplan)(
                len(daten["provinzen"]) + (1 if daten["burg_fehlt"] else 0)
            ),
            fritte=stimmung,
            tipp=tipp,
            neu_zier=neu,
            wiese=spiel.WIESE,
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
            p = Profil.load(entwurf_datei(jahr)) or profil(jahr) or profil(jahr - 1)
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

    def ausweg(jahr: int) -> tuple[str, str]:
        """Wohin "Abbrechen" führt: zur nächsten Quest, die es schon gibt, sonst nach Hause."""
        andere = [j for j in quest_jahre() if j != jahr]
        if andere:
            ziel = min(andere, key=lambda j: (abs(j - jahr), -j))
            return f"/steuer/{ziel}", f"Zurück zu {ziel}"
        return "/", "Abbrechen"

    @app.get("/steuer/{jahr}/intro", response_class=HTMLResponse)
    def intro(request: Request, jahr: int):
        if profil(jahr) is not None:
            return back(f"/steuer/{jahr}")
        weg, weg_text = ausweg(jahr)
        # Wer schon angefangen hat, sieht nicht den ganzen Film noch einmal
        angefangen = jahr in app.state.steuer_entwurf or entwurf_datei(jahr).exists()
        return seite(
            request,
            "steuer_intro.html",
            jahr=jahr,
            ausweg=weg,
            ausweg_text=weg_text,
            kurz=bool(quest_jahre()) or angefangen,
            angefangen=angefangen,
        )

    @app.get("/steuer/{jahr}/fragebogen", response_class=HTMLResponse)
    def fragebogen(request: Request, jahr: int, teil: int = 1):
        teil = teil if teil in (1, 2, 3) else 1
        p = entwurf(jahr)
        weg, weg_text = ausweg(jahr)
        if profil(jahr) is not None:
            weg, weg_text = f"/steuer/{jahr}", "Zurück zur Insel"
        return seite(
            request,
            "steuer_fragebogen.html",
            ausweg=weg,
            ausweg_text=weg_text,
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
            if "beraterin_email" in form:
                p.beraterin_email = str(form.get("beraterin_email") or "").strip()
            if "beraterin_termin" in form:
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
        if form.get("merken"):
            # Zwischenspeichern beim Tippen: nichts geht verloren, auch nicht beim Tab-Wechsel.
            p.save(entwurf_datei(jahr))
            return Response(status_code=204)
        ziel = str(form.get("weiter") or "")
        if ziel.isdigit() and int(ziel) in (1, 2, 3) and int(ziel) != teil:
            p.save(entwurf_datei(jahr))
            return back(f"/steuer/{jahr}/fragebogen?teil={ziel}")
        if teil < 3:
            p.save(entwurf_datei(jahr))
            return back(f"/steuer/{jahr}/fragebogen?teil={teil + 1}")

        app.state.steuer_entwurf.pop(jahr, None)
        entwurf_datei(jahr).unlink(missing_ok=True)
        neu = profil(jahr) is None
        p.save(quest.profil_datei(ws.root, jahr))
        gebiete = quest.start(conn(), p)
        quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
        if neu:
            quest.notiere(conn(), jahr, "start", f"Reich gegründet mit {len(gebiete)} Provinzen")
            flash(
                f"Deine Insel hat {len(gebiete)} Provinzen. Fritte hat dir den Weg markiert.",
                "gruendung",
            )
        else:
            flash("Fragebogen gespeichert. Die Karte ist aktualisiert.")
        return back(f"/steuer/{jahr}")

    # ----- Provinzen und Sammeln -----

    @app.get("/steuer/{jahr}/gebiet/{gebiet_id}", response_class=HTMLResponse)
    def provinz(request: Request, jahr: int, gebiet_id: str):
        g = gebiet(profil_oder_404(jahr), gebiet_id)
        if g is None:
            flash("Diese Provinz gibt es auf deiner Insel nicht (mehr).", "hinweis")
            return back(f"/steuer/{jahr}")
        daten = lage(jahr)
        c = conn()
        eigene = [s for s in daten["stuecke"] if s["gebiet"] == g.id]
        for s in eigene:
            s["datei_liste"] = quest.dateien(c, jahr, s["id"])
            s["verweis_liste"] = [
                {**v, "bei_titel": titel_von(jahr, v["bei_gebiet"])}
                for v in quest.verweise(c, jahr, s["id"])
            ]
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
        if aktion == "gibts_nicht" and fehlt_nie(s["gebiet"], s["key"]):
            aktion = "fragen"  # die Steuer-ID hat jede:r, also wird daraus eine Frage
        vorher = lage(jahr)
        frage = str(form.get("frage") or "").strip() if aktion == "fragen" else ""
        quest.setze_status(conn(), stueck_id, aktion, frage)
        if aktion == "offen":
            flash(f"Stempel zurückgenommen: {s['name']} ist wieder offen.")
        elif aktion == "gibts_nicht":
            flash(f"Notiert: {s['name']} gibt es dieses Jahr nicht. Zählt trotzdem!", "beute")
        elif aktion == "fragen" and frage == MACHT_BERATERIN:
            flash(f"Notiert: {s['name']} macht deine Beraterin. Zählt als erledigt.", "beute")
        elif aktion == "fragen":
            flash("Kommt auf die Fragenliste für deine Beraterin. Fragen ist klug.", "beute")
        if belohnen(jahr, vorher):
            return back(f"/steuer/{jahr}/drache")
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
        funde = await hochladen(jahr, stueck_id, uploads)
        neu = sum(1 for f in funde if f.neu)
        mit = next((f.verweis for f in funde if f.neu and f.verweis), None)
        if mit and neu == 1:
            flash(
                f"+1 Beleg! {s['name']}: dieselbe Datei wie bei {titel_von(jahr, mit['gebiet'])}."
                " Sie zählt für beide, im Zip liegt sie einmal.",
                "beute",
                "done",
            )
        elif neu:
            flash(f"+{neu} Beleg{'e' if neu > 1 else ''}! Gesichert: {s['name']}", "beute", "done")
        else:
            flash(doppelt_text(jahr, funde[0] if funde else None, s))
        if belohnen(jahr, vorher):
            return back(f"/steuer/{jahr}/drache")
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
            wie = ""
            if fund.verweis:
                wie = f" (dieselbe Datei wie bei {titel_von(jahr, fund.verweis['gebiet'])})"
            flash(f"+1 Beleg! {name} → {s['name']}{wie}", "beute", "done")
        else:
            flash(doppelt_text(jahr, fund, s))
        if belohnen(jahr, vorher):
            return back(f"/steuer/{jahr}/drache")
        return back(str(form.get("back") or f"/steuer/{jahr}"))

    @app.post("/steuer/{jahr}/lager/vorschlaege", dependencies=guarded)
    async def lager_vorschlaege(request: Request, jahr: int):
        """Spürnase: alle Zettel mit einem Kartenvorschlag auf einmal abstempeln."""
        form = await request.form()
        zurueck = str(form.get("back") or f"/steuer/{jahr}")
        c = conn()
        if "spuernase" not in quest.helfer(c, jahr):
            flash("Das kann nur die Spürnase. Du findest sie im Hafenmarkt.")
            return back(zurueck)
        vorher = lage(jahr)
        alle = quest.stuecke(c, jahr)
        gestempelt = 0
        for quelle in quest.lager_dateien(ws.root):
            karte = spiel.ahnung(quelle.name, alle).stueck
            if karte is None:
                continue
            fund = quest.sammeln(c, ws.root, karte["id"], quelle, verschieben=True)
            if fund.neu:
                quest.notiere(c, jahr, "fund", f"{karte['name']}: {quelle.name}")
                gestempelt += 1
        if gestempelt:
            flash(
                f"Die Spürnase hat {gestempelt} Zettel abgestempelt. "
                "Falsch gelandet? Bei der Karte „Zurücklegen“, nichts ist verloren.",
                "beute",
                "done",
            )
        else:
            flash("Die Spürnase hat keinen Zettel gefunden, bei dem sie sicher ist.")
        if belohnen(jahr, vorher):
            return back(f"/steuer/{jahr}/drache")
        return back(zurueck)

    @app.post("/steuer/{jahr}/datei/{datei_id}/entfernen", dependencies=guarded)
    async def datei_entfernen(request: Request, jahr: int, datei_id: int):
        form = await request.form()
        vorher = lage(jahr)
        quest.entferne_datei(conn(), datei_id)
        # Wer zurücklegt, verliert die Serie: sonst ließe sich die Combo hochtreiben
        if jahr in app.state.steuer_combo:
            app.state.steuer_combo[jahr] = {"combo": 0, "ruf": spiel.RUFE[0]}
        flash("Die Datei liegt wieder im Eingangskorb. Nichts ist verloren.")
        lp = lage(jahr)["drache"].lp
        if vorher["drache_wach"] and lp > vorher["drache"].lp:
            flash(f"Der Drache hat wieder {lp} Karte{'n' if lp != 1 else ''}.", "treffer")
        belohnen(jahr, vorher, ferne=False)
        return back(str(form.get("back") or f"/steuer/{jahr}"))

    @app.post("/steuer/{jahr}/gebiet/{gebiet_id}/name", dependencies=guarded)
    async def gebiet_name(request: Request, jahr: int, gebiet_id: str):
        """Wohnungen, Firmen und Depots bekommen einen eigenen Namen ("Lindenstraße"). Der
        steht dann in der Mail und im Inhaltsverzeichnis, damit die Beraterin weiß, was gemeint ist."""
        form = await request.form()
        p = profil_oder_404(jahr)
        if gebiet(p, gebiet_id) is None:
            raise HTTPException(404)
        name = " ".join(str(form.get("name") or "").split())[:40]
        if name:
            p.namen[gebiet_id] = name
        else:
            p.namen.pop(gebiet_id, None)
        p.save(quest.profil_datei(ws.root, jahr))
        if request.headers.get("x-frits-leise"):
            return JSONResponse({"ok": True})
        flash(f"Gemerkt: {name}." if name else "Der eigene Name ist weg.")
        return back(f"/steuer/{jahr}/gebiet/{gebiet_id}")

    @app.post("/steuer/{jahr}/verweis/{verweis_id}/loesen", dependencies=guarded)
    async def verweis_loesen(request: Request, jahr: int, verweis_id: int):
        form = await request.form()
        vorher = lage(jahr)
        if not any(v["id"] == verweis_id for v in quest.verweise(conn(), jahr)):
            raise HTTPException(404)
        quest.entferne_verweis(conn(), verweis_id)
        flash("Die Karte benutzt die Datei nicht mehr mit. Die Datei selbst bleibt, wo sie ist.")
        belohnen(jahr, vorher, ferne=False)
        return back(str(form.get("back") or f"/steuer/{jahr}"))

    @app.post("/steuer/{jahr}/lager/hochladen", dependencies=guarded)
    async def lager_hochladen(request: Request, jahr: int):
        """Dateien, die auf den Eingangskorb fallen, landen im Lager wie im Finder hineingelegt."""
        form = await request.form()
        ablage = quest.lager(ws.root)
        ablage.mkdir(parents=True, exist_ok=True)
        gelegt, zu_gross = [], []
        for upload in [*form.getlist("files"), *form.getlist("datei")]:
            name = Path(str(getattr(upload, "filename", "") or "").replace("\\", "/")).name.strip()
            if not name or name.startswith("."):
                continue
            daten = await upload.read(MAX_LAGER + 1)
            if len(daten) > MAX_LAGER:
                zu_gross.append(name)
                continue
            ziel, n = ablage / name, 1
            while ziel.exists():
                ziel = ablage / f"{Path(name).stem} ({n}){Path(name).suffix}"
                n += 1
            ziel.write_bytes(daten)
            gelegt.append(ziel.name)
        text = str(form.get("text") or "").strip()
        if text:
            name = textname(text)
            ziel, n = ablage / name, 1
            while ziel.exists():
                ziel = ablage / f"{Path(name).stem} ({n}).txt"
                n += 1
            ziel.write_text(text[:200_000].replace("\r\n", "\n") + "\n", encoding="utf-8")
            gelegt.append(ziel.name)
        if gelegt:
            n = len(gelegt)
            flash(
                f"{n} neue{'r' if n == 1 else ''} Zettel im Eingangskorb. Zieh {'ihn' if n == 1 else 'sie'} auf eine Provinz.",
                "beute",
            )
        for name in zu_gross:
            flash(
                f"{name} ist größer als 30 MB. Leg sie lieber direkt in den Lager-Ordner.", "error"
            )
        if not gelegt and not zu_gross:
            flash("Es wurde keine Datei mitgeschickt.", "error")
        return back(f"/steuer/{jahr}#sq-korb")

    @app.post("/steuer/{jahr}/lager/beiseite", dependencies=guarded)
    async def lager_beiseite(request: Request, jahr: int):
        """Ein Zettel, der nirgends hingehört: weg aus dem Korb, aber nicht gelöscht."""
        form = await request.form()
        name = Path(str(form.get("name") or "")).name
        ziel = quest.lege_beiseite(ws.root, name)
        if ziel is None:
            flash("Den Zettel finde ich nicht mehr im Eingangskorb.")
        else:
            flash(
                f"„{name}“ liegt jetzt im Ordner „{ziel.parent.name}“."
                " Nichts ist gelöscht, und die Beraterin bekommt es nicht."
            )
        return back(str(form.get("back") or f"/steuer/{jahr}"))

    @app.get("/steuer/{jahr}/lager/anzahl")
    def lager_anzahl(jahr: int):
        """Wie viele Zettel im Lager liegen. Die Karte fragt das, wenn du aus dem Finder zurückkommst."""
        return {"anzahl": len(quest.lager_dateien(ws.root))}

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
        extra = " und ".join(
            n
            for k, n in (("buchungsliste", "Buchungsliste"), ("verfahrensdoku", "Verfahrensdoku"))
            if neu[k]
        )
        if neu["euer"]:
            teile.append("die EÜR" + (f" mit {extra}" if extra else ""))
        elif extra:
            teile.append(f"die {extra}")
        if teile:
            text = f"FRITS-Ernte eingebracht: {', '.join(teile)} → {ziele[gebiet_id].titel}"
            quest.notiere(c, jahr, "fund", text)
            flash(text + "!", "beute", "done")
        else:
            flash("Aus FRITS ist schon alles da. Nichts doppelt.")
        if belohnen(jahr, daten):
            return back(f"/steuer/{jahr}/drache")
        return back(f"/steuer/{jahr}")

    # ----- Mail der Beraterin -----

    @app.get("/steuer/{jahr}/beraterin", response_class=HTMLResponse)
    def beraterin_seite(request: Request, jahr: int):
        return seite(request, "steuer_beraterin.html", ergebnis=None, text="", **lage(jahr))

    @app.post("/steuer/{jahr}/beraterin", dependencies=guarded)
    async def beraterin_lesen(request: Request, jahr: int):
        form = await request.form()
        text = str(form.get("text") or "")
        if any(k in form for k in ("beraterin_email", "beraterin_name", "beraterin_termin")):
            # Was oben im Kärtchen steht, geht nicht verloren, wenn man gleich das Siegel bricht
            p = profil_oder_404(jahr)
            beraterin_merken(p, form)
            p.save(quest.profil_datei(ws.root, jahr))
        daten = lage(jahr)
        wuensche = beraterin.zuordnen(text, daten["gebiete"], daten["stuecke"])
        if not wuensche:
            flash("Ich habe in der Mail keine Wünsche gefunden. Aufzählungen klappen am besten.")
            return seite(request, "steuer_beraterin.html", ergebnis=None, text=text, **daten)
        c = conn()
        titel = {g.id: g.titel for g in daten["gebiete"]}
        namen = {s["id"]: s["name"] for s in daten["stuecke"]}
        ergebnis = []
        for w in wuensche:
            for gid, sid in zip(w.gebiete, w.stuecke, strict=True):
                if sid:
                    quest.markiere_wunsch(c, sid)
                    ergebnis.append(
                        {"text": w.text, "wo": titel[gid], "karte": namen[sid], "neu": False}
                    )
                else:
                    name = w.text[:1].upper() + w.text[1:120]
                    quest.neues_stueck(c, jahr, gid, name, hinweis="Wunsch deiner Beraterin")
                    ergebnis.append({"text": w.text, "wo": titel[gid], "karte": name, "neu": True})
        vorher = daten
        quest.notiere(c, jahr, "brief", f"{len(wuensche)} Aufträge der Beraterin")
        markiert = sum(1 for e in ergebnis if not e["neu"])
        flash(
            f"Botschaft entschlüsselt: {len(wuensche)} Aufträge. {markiert} davon kennst du"
            " schon, die Karten sind jetzt markiert."
            if markiert
            else f"Botschaft entschlüsselt: {len(wuensche)} Aufträge auf deiner Karte.",
            "erfolg",
        )
        belohnen(jahr, vorher, auftraege=True)
        return seite(request, "steuer_beraterin.html", ergebnis=ergebnis, text="", **lage(jahr))

    # ----- Der Papierdrache -----

    @app.get("/steuer/{jahr}/drache", response_class=HTMLResponse)
    def drache_seite(request: Request, jahr: int):
        daten = lage(jahr)
        combo = dict(app.state.steuer_combo.get(jahr, {"combo": 0, "ruf": spiel.RUFE[0]}))
        frisch = app.state.steuer_combo.get(jahr, {}).pop("frisch", False)
        wurf = app.state.steuer_wurf.pop(jahr, None) if frisch else None
        beute = app.state.steuer_combo.get(jahr, {}).pop("beute", None) if frisch else None
        finale = jahr in app.state.steuer_finale and daten["drache"].besiegt
        app.state.steuer_finale.discard(jahr)
        return seite(
            request,
            "steuer_drache.html",
            combo=combo,
            frisch=frisch,
            finale=finale,
            wurf=wurf,
            beute=beute,
            lager=quest.lager_dateien(ws.root),
            **daten,
        )

    @app.post("/steuer/{jahr}/drache/schlag", dependencies=guarded)
    async def drache_schlag(request: Request, jahr: int):
        form = await request.form()
        s = stueck_im_jahr(jahr, int(str(form.get("stueck_id") or 0)))
        aktion = str(form.get("aktion") or "")
        if aktion == "gibts_nicht" and fehlt_nie(s["gebiet"], s["key"]):
            aktion = "fragen"
        beraterin_macht = aktion == "beraterin" and macht_beraterin(s["gebiet"], s["key"])
        if beraterin_macht:
            aktion = "fragen"
        vorher = lage(jahr)
        if not vorher["drache_wach"]:
            flash("Der Drache schläft noch. Bau erst deine Provinzen auf.")
            return back(f"/steuer/{jahr}/drache")
        kritisch = False
        if aktion == "datei":
            uploads = [f for f in form.getlist("datei") if hasattr(f, "filename") and f.filename]
            if not uploads:
                flash("Wähle eine Datei aus, dann trifft der Schlag.")
                return back(f"/steuer/{jahr}/drache")
            funde = await hochladen(jahr, s["id"], uploads)
            kritisch = any(f.neu for f in funde)
            if not kritisch and not any(f.stueck["id"] == s["id"] for f in funde):
                # Die Datei gehört schon zu einer anderen Karte: Das ist kein Treffer
                flash(doppelt_text(jahr, funde[0] if funde else None, s))
                return back(f"/steuer/{jahr}/drache#karte")
        elif aktion == "zettel":
            # Ein Zettel aus der Hand (dem Eingangskorb) direkt auf den Drachen geworfen
            name = Path(str(form.get("zettel") or "")).name
            quelle = quest.lager(ws.root) / name
            if not name or not quelle.is_file():
                flash("Den Zettel finde ich nicht mehr im Eingangskorb.")
                return back(f"/steuer/{jahr}/drache#karte")
            c = conn()
            fund = quest.sammeln(c, ws.root, s["id"], quelle, verschieben=True)
            if not fund.neu:
                flash(doppelt_text(jahr, fund, s))
                return back(f"/steuer/{jahr}/drache#karte")
            quest.notiere(c, jahr, "fund", f"{s['name']}: {name}")
            if fund.verweis:
                app.state.steuer_wurf[jahr] = {
                    "id": None,
                    "name": name,
                    "karte": s["name"],
                    "wie": "verweis",
                }
            else:
                datei = next(
                    d for d in quest.dateien(c, jahr, s["id"]) if d["pfad"] == str(fund.pfad)
                )
                app.state.steuer_wurf[jahr] = {
                    "id": datei["id"],
                    "name": name,
                    "karte": s["name"],
                    "wie": "zettel",
                }
            kritisch = True
        elif aktion in ("gibts_nicht", "fragen"):
            frage = str(form.get("frage") or "").strip() if aktion == "fragen" else ""
            if beraterin_macht:
                frage = MACHT_BERATERIN
            quest.setze_status(conn(), s["id"], aktion, frage)
        else:
            raise HTTPException(400)
        if aktion != "zettel":
            app.state.steuer_wurf[jahr] = {
                "id": None,
                "name": "",
                "karte": s["name"],
                "wie": aktion,
            }
        alt = app.state.steuer_combo.get(jahr, {"combo": 0})
        combo, ruf = spiel.treffer(alt["combo"], kritisch, zaehlt=aktion != "gibts_nicht")
        if aktion == "gibts_nicht" and "trommel" in vorher["helfer"]:
            combo, ruf = 0, "Die Kriegstrommel verstummt. Die Serie ist weg."
        app.state.steuer_combo[jahr] = {
            "combo": combo,
            "ruf": ruf,
            "frisch": "kritisch" if kritisch else "treffer",
        }
        quest.notiere(conn(), jahr, "treffer", f"{s['name']} ({ruf})")
        mult = spiel.multiplikator(combo, vorher["helfer"])
        if belohnen(jahr, vorher, ferne=False, mult=mult):
            return back(f"/steuer/{jahr}/drache")  # der Falt-Moment
        return back(f"/steuer/{jahr}/drache#karte")

    # ----- Hafenmarkt: Gold ausgeben -----

    @app.get("/steuer/{jahr}/markt", response_class=HTMLResponse)
    def markt(request: Request, jahr: int):
        daten = lage(jahr)
        return seite(
            request,
            "steuer_markt.html",
            markt=spiel.MARKT,
            plaetze=spiel.HELFER_PLAETZE,
            goldbuch=quest.goldbuch(conn(), jahr),
            goldsummen=quest.goldsummen(conn(), jahr),
            regeln=spiel,
            goldjahr=spiel.gold_jahr(jahr, today()),
            **daten,
        )

    @app.post("/steuer/{jahr}/markt/kaufen", dependencies=guarded)
    async def markt_kaufen(request: Request, jahr: int):
        form = await request.form()
        profil_oder_404(jahr)
        key = str(form.get("ware") or "")
        try:
            text = quest.kaufe(conn(), jahr, key)
        except quest.QuestError as exc:
            flash(str(exc))
            return back(f"/steuer/{jahr}/markt")
        flash(text, "beute", "done")
        w = spiel.ware(key)
        if w and w.art == "zierde":
            return back(f"/steuer/{jahr}?neu={key}#insel")  # der Moment: da steht sie jetzt
        return back(f"/steuer/{jahr}/markt#helfer")

    @app.post("/steuer/{jahr}/markt/helfer", dependencies=guarded)
    async def markt_helfer(request: Request, jahr: int):
        form = await request.form()
        profil_oder_404(jahr)
        try:
            text = quest.lege_helfer(
                conn(), jahr, str(form.get("ware") or ""), form.get("an") == "1"
            )
        except quest.QuestError as exc:
            flash(str(exc))
        else:
            flash(text)
        return back(f"/steuer/{jahr}/markt#helfer")

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
        zip_pfad = truhe_pfad(jahr, daten["profil"])
        mit_passwort = truhe.verschluesselt(zip_pfad) if zip_pfad.exists() else True
        betreff, text = truhe.mail(
            daten["profil"], daten["gebiete"], daten["stuecke"], mit_passwort=mit_passwort
        )
        return seite(
            request,
            "steuer_sieg.html",
            betreff=betreff,
            mailtext=text,
            zip_pfad=zip_pfad if zip_pfad.exists() else None,
            passwort=app.state.steuer_passwort.get(jahr),
            mit_passwort=mit_passwort,
            dateien=quest.dateien(conn(), jahr),
            **daten,
        )

    @app.post("/steuer/{jahr}/truhe", dependencies=guarded)
    async def truhe_packen(request: Request, jahr: int):
        daten = lage(jahr)
        if not daten["drache"].besiegt and jahr not in app.state.steuer_notausgang:
            return back(f"/steuer/{jahr}/drache")
        form = await request.form()
        kennwort = None if form.get("schloss") == "ohne" else truhe.passwort()
        ziel = truhe.packen(
            truhe_pfad(jahr, daten["profil"]),
            daten["profil"],
            daten["gebiete"],
            daten["stuecke"],
            quest.dateien(conn(), jahr),
            kennwort,
            quest.verweise(conn(), jahr),
        )
        if kennwort:
            app.state.steuer_passwort[jahr] = kennwort
            flash("Die Schatztruhe ist gepackt und mit Passwort verschlossen.", "erfolg", "done")
        else:
            app.state.steuer_passwort.pop(jahr, None)
            flash("Die Schatztruhe ist gepackt, ohne Passwort.", "erfolg", "done")
        quest.notiere(conn(), jahr, "truhe", ziel.name)
        return back(f"/steuer/{jahr}/sieg")

    @app.post("/steuer/{jahr}/beraterin/daten", dependencies=guarded)
    async def beraterin_daten(request: Request, jahr: int):
        form = await request.form()
        p = profil_oder_404(jahr)
        beraterin_merken(p, form)
        p.save(quest.profil_datei(ws.root, jahr))
        if request.headers.get("x-frits-leise"):
            return JSONResponse({"ok": True})  # automatisch gespeichert, ohne Seitenwechsel
        if p.beraterin_termin:
            flash(f"Gemerkt: Abgabe bis {schoenes_datum(p.beraterin_termin)}.")
        else:
            flash("Gemerkt.")
        return back(str(form.get("back") or f"/steuer/{jahr}/beraterin"))

    @app.post("/steuer/{jahr}/truhe/zeigen", dependencies=guarded)
    def truhe_zeigen(jahr: int):
        p = profil_oder_404(jahr)
        zip_pfad = truhe_pfad(jahr, p)
        if not zip_pfad.exists():
            flash("Pack zuerst die Schatztruhe.")
        elif not system.reveal_path(zip_pfad):
            flash(f"Deine Truhe liegt hier: {zip_pfad}")
        return back(f"/steuer/{jahr}/sieg")

    @app.post("/steuer/{jahr}/mail", dependencies=guarded)
    async def mail_oeffnen(request: Request, jahr: int):
        form = await request.form()
        p = profil_oder_404(jahr)
        zip_pfad = truhe_pfad(jahr, p)
        if not zip_pfad.exists():
            flash("Pack zuerst die Schatztruhe, dann hänge ich sie an.")
            return back(f"/steuer/{jahr}/sieg")
        an = str(form.get("an") or "").strip()
        if an and an != p.beraterin_email:
            p.beraterin_email = an
            p.save(quest.profil_datei(ws.root, jahr))
        betreff = str(form.get("betreff") or f"Steuerunterlagen {jahr}")
        text = str(form.get("text") or "")
        result = system.compose_email(p.beraterin_email, betreff, text, zip_pfad)
        if result.opened:
            flash("Die Mail ist in deinem Mail-Programm. Du drückst dort auf Senden.", cue="sent")
            return back(f"/steuer/{jahr}/sieg")
        app.state.mailto = result.mailto
        flash(f"Hänge die Truhe bitte selbst an: {zip_pfad}")
        return back("/mailto")

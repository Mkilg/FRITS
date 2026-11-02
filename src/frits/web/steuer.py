"""Die Seiten der Steuer-Quest in der FRITS-App.

Die Spielregeln stehen in frits.steuer; hier wird nur angezeigt, gespeichert und weitergeleitet.
Wie überall in FRITS: Jeder Knopf, der etwas ändert, ist ein POST mit dem geheimen Token.
"""

from __future__ import annotations

import hashlib
import math
import re
import shutil
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from frits import store, system
from frits.settings import Workspace
from frits.steuer import beraterin, ernte, pruefung, quest, spiel, truhe
from frits.steuer.katalog import (
    FAMILIENSTAND,
    HAKEN,
    MACHT_BERATERIN,
    Profil,
    angabe,
    fehlt_nie,
    fragen,
    gebiet,
    ist_kern,
    macht_beraterin,
    reich,
    warum,
)
from frits.steuer.katalog import quellen_text as katalog_quellen

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


def kopf_rest(text: str) -> tuple[str, str]:
    """Teilt eine Belohnung in Überschrift und Rest: "Neuer Orden: Drachenbändigerin." und den
    Satz dahinter. Im vollen Siegesdialog steht der Rest klein darunter (Marie: Textwand)."""
    teile = re.split(r"(?<=[.!])\s+", text.strip(), maxsplit=1)
    return (teile[0], teile[1] if len(teile) > 1 else "")


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
        return "done", "Der Drache ist besiegt! Die Fundkiste wartet auf dich."
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


# Was der Browser direkt zeigen darf. Alles andere (auch HTML oder SVG) wird nur heruntergeladen.
ZEIGBAR = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/plain; charset=utf-8",
}
VORSCHAUBILD = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def groesse_text(bytes_: int) -> str:
    if bytes_ < 1024 * 1024:
        return f"{max(1, round(bytes_ / 1024))} KB" if bytes_ else "0 KB"
    return f"{bytes_ / (1024 * 1024):.1f} MB".replace(".", ",")


def angabe_text(d: dict) -> str:
    """Was bei einer eingetippten Angabe drinsteht (ohne die Überschrift-Zeile)."""
    try:
        roh = Path(d["pfad"]).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return roh.split("\n\n", 1)[-1].strip()


def beleg_info(d: dict) -> dict:
    """Eine Datei fürs Belegbuch: was für eine Art, wie groß, bei Angaben der Text selbst."""
    pfad = Path(d["pfad"])
    endung = pfad.suffix.lower()
    original = d.get("original") or pfad.name
    groesse = pfad.stat().st_size if pfad.is_file() else 0
    art = "datei"
    text = ""
    if endung == ".txt" and quest.ist_angabe(original) and pfad.is_file():
        art = "angabe"
        text = angabe_text(d)
    elif endung in VORSCHAUBILD:
        art = "bild"
    elif endung == ".pdf":
        art = "pdf"
    elif endung in (".txt", ".csv"):
        art = "text"
    return {
        **d,
        "name": pfad.name,
        "original": original,
        "art": art,
        "text": text,
        "groesse": groesse,
        "groesse_text": groesse_text(groesse),
        "zeigbar": endung in ZEIGBAR,
        "da": pfad.is_file(),
    }


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

    def tagesstand(alle: list[dict]) -> dict:
        """Ein Tagesziel für ganz FRITS: Mit Fritte zählt, was heute echt erledigt wurde
        (Fritte-Karten, Liste und Quest-Funde zusammen, frits.spiel.tagesziel). Ohne Fritte
        bleibt das Quest-Tagwerk. "tagwerk" ist immer (erledigt, ziel)."""
        if settings().haustier:
            try:
                from frits.spiel.tagesziel import zaehlen

                z = zaehlen(conn(), date.today())
                return {
                    "tagwerk": (int(z["erledigt"]), int(z["ziel"])),
                    "tagesziel_info": {"text": z["text"], "erklaerung": z.get("erklaerung", "")},
                }
            except Exception:  # noqa: BLE001 — ohne Frittes Zählung gilt das alte Tagwerk
                z = None
        return {"tagwerk": spiel.tagwerk(alle, date.today()), "tagesziel_info": None}

    def sammlung_stand() -> tuple[int, int] | None:
        """(gesammelt, Plätze) in Frittes gemeinsamer Sammlung, oder None ohne Sammlung."""
        try:
            from frits.spiel import sammlung

            return sammlung.stand(sammlung.faecher(conn(), today(), alltag=settings().haustier))
        except Exception:  # noqa: BLE001 — ohne Sammlung zeigt die Insel ihre Orden selbst
            return None

    def gewuerze() -> int:
        """Wie viele Gewürze in Frittes Regal stehen (0, wenn Fritte ausgeschaltet ist)."""
        if not settings().haustier:
            return 0
        try:
            from frits.spiel.kassensturz import regal

            return len(regal(conn()))
        except Exception:  # noqa: BLE001 — ohne Fritte-Tabellen gibt es eben keine Gewürze
            return 0

    def kampf_anfang() -> dict:
        """Die Serie zu Kampfbeginn: Frittes Gewürze wärmen auf (siehe spiel.start_serie)."""
        n = gewuerze()
        start = spiel.start_serie(n)
        if not start:
            return {"combo": 0, "ruf": spiel.RUFE[0]}
        ruf = (
            f"Frittes {'Gewürz wärmt' if n == 1 else 'Gewürze wärmen'} dich auf:"
            " Deine Gold-Serie startet schon warm."
        )
        return {"combo": start, "ruf": ruf, "gewuerze": n}

    app.state.steuer_notausgang = set()
    app.state.steuer_passwort = {}
    # Jahre, deren Drache gerade gefallen ist: dort die Falt-Animation zeigen
    app.state.steuer_finale = set()
    # Der letzte Zettel aus der Hand, damit man ihn zurücklegen kann
    app.state.steuer_wurf = {}
    # Der letzte Schlag ohne Datei (Fragen, "Gibt's nicht") darf zurück: Karte und Serie wie vorher
    app.state.steuer_zurueck = {}
    # Fritte stutzt: Die Rückfrage bleibt am Zettel im Eingangskorb, auch nach dem Neuladen
    app.state.steuer_stutzen = {}

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
            angabe=angabe,
            steuernummer=settings().steuernummer,
            MACHT_BERATERIN=MACHT_BERATERIN,
            datum=schoenes_datum,
            vermute=spiel.vermute,
            ahnung=spiel.ahnung,
            ware=spiel.ware,
            hafen_angebot=spiel.hafen_angebot,
            helfer_satz=spiel.im_kampf,
            zierde_fuer=spiel.zierde_fuer,
            # Frittes gemeinsame Vitrine (Orden, Sticker, Pokale), sobald es sie gibt
            sammlung_da=any(getattr(r, "path", None) == "/sammlung" for r in app.routes),
            sammlung_stand=sammlung_stand,
            stutzen=app.state.steuer_stutzen,
            kopf_rest=kopf_rest,
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
        # Jeder Orden aus der Chronik stellt seine Zierde auf die Insel (auch Orden von früher).
        # Nur echte, notierte Orden: So zeigen Insel und Sammlung immer dasselbe.
        quest.zierden_abgleichen(c)
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
            "zier_herkunft": zier_herkunft(c),
            # Das Goldene Zeitalter kommt erst mit dem Sieg über den Drachen
            "zeitalter": spiel.zeitalter(
                gesamt.anteil if drache.besiegt else min(gesamt.anteil, 0.99),
                silber=bool(zweifel),
            ),
            "naechstes": spiel.naechstes_zeitalter(gesamt.anteil),
            "weg": spiel.weg_zum_zeitalter(gesamt),
            "drache": drache,
            "drache_wach": wach,
            "auftrag": spiel.auftrag(jahr, staende, drache, zettel, alle, wach),
            **tagesstand(alle),
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

    def doppelt_melden(
        jahr: int, fund: quest.Fund | None, karte: dict, zurueck: str | None = None
    ) -> None:
        """Ehrlich sagen, warum eine Datei nicht (noch einmal) zählt, und einen Weg weiter
        zeigen: zum Beleg, und wenn es passt, "Steht da mit drin, hier auch zählen"."""
        if fund is None or fund.stueck["id"] == karte["id"]:
            flash("Diese Datei hattest du schon gesammelt. Doppelt zählt nicht, aber gut gemerkt!")
            return
        bei = fund.stueck
        wo = f"„{bei['name']}“ ({titel_von(jahr, bei['gebiet'])})"
        d = quest.datei_mit_pfad(conn(), jahr, fund.pfad)
        link = f"/steuer/{jahr}/belege#karte-{bei['id']}"
        if d and quest.darf_mitzaehlen(karte, bei):
            flash(
                f"Diese Datei liegt schon bei {wo}. Steht das für „{karte['name']}“ mit drin?",
                "warn",
                aktion={
                    "url": f"/steuer/{jahr}/stueck/{karte['id']}/mitzaehlen",
                    "knopf": "Ja, hier auch zählen",
                    "felder": {"datei_id": d["id"], "back": zurueck or ""},
                    "link": link,
                },
            )
            return
        flash(
            f"Diese Datei liegt schon bei {wo}. Eine Datei zählt nur für eine Karte,"
            " außer in derselben Provinz oder bei derselben Unterlage für mehrere Wohnungen.",
            "warn",
            aktion={"link": link},
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
        gemeinsam = nachher["tagesziel_info"] is not None
        # Mit dem gemeinsamen Tagesziel kann es auch bei Fritte erreicht worden sein:
        # Dann zahlt die Insel ihr Gold beim nächsten Quest-Schritt dieses Tages.
        erreicht = ziel <= heute and (gemeinsam or vorher["tagwerk"][0] < ziel)
        # Ein Tag, ein Tagesziel: Das Gold gibt es einmal am Tag, nicht einmal pro Steuerjahr
        if erreicht and not sieg and not quest.hat_gold(c, None, tag):
            # Einmal am Tag: Wer zurücklegt und neu zuordnet, bekommt kein zweites Fest
            quest.notiere(c, jahr, "tagwerk", "Tagesziel geschafft")
            lohn = spiel.tagwerk_gold(helfer) if goldjahr else 0
            if lohn:
                lohn = quest.verdiene(c, jahr, tag, lohn, "Tagesziel")
            plus += lohn
            if gemeinsam:
                text = f"Tagesziel geschafft: {ziel} echte Erledigungen heute"
            else:
                text = f"Tagesziel geschafft: {ziel} Karten heute"
            flash(
                text
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
            z = spiel.zierde_fuer(e.key)
            if z:
                quest.zierde_freischalten(c, z.key)
            flash(
                f"Neuer Orden: {e.name}. {e.beschreibung}"
                + (f" Dafür steht jetzt {z.name} auf deiner Insel." if z else ""),
                "orden",
            )
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
                    "Der Drache faltet sich zur Fundkiste. Ein paar wichtige Unterlagen"
                    " soll deine Beraterin noch prüfen, darum Silber.",
                    "sieg",
                    "sent",
                )
            else:
                flash("Der Drache faltet sich zu einer Fundkiste zusammen.", "sieg", "sent")
            if plus and not ferne and basis * faktor:
                # Der größte Treffer des Kampfes soll man sehen, auch wenn der Sieg ihn verdrängt
                # (ein Treffer ohne Gold dämpft die Feier nur, der bleibt weg)
                flash(f"Letzter Treffer: {basis} × {faktor} = +{basis * faktor} Gold.", "sieg")
            if goldjahr and fritte_truhe(c, jahr, silber=bool(nachher["zweifel"])):
                flash(
                    "Fritte hat eine Truhe für dich bekommen. Sie wartet bei Fritte.",
                    "sieg",
                    link=("/fritte#truhen", "Zu Fritte"),
                )
            koerner = gold_zu_salz(c, jahr) if goldjahr else 0
            if koerner:
                flash(
                    f"Dein übriges Gold ist zu {koerner} Salzkörnern bei Fritte geworden.",
                    "sieg",
                    link=("/fritte", "Zu Fritte"),
                )
        return sieg

    def salz_tausch():
        """Frittes Kasse für das Restgold nach dem Sieg, oder None (Fritte aus oder noch nicht da)."""
        if not settings().haustier:
            return None
        try:
            from frits.haustier import salz_aus_quest
        except ImportError:
            return None
        return salz_aus_quest

    def gold_zu_salz(c, jahr: int) -> int:
        """Eine Währung: Drachengold gilt nur im Kampf. Was beim Sieg übrig ist, wird zu
        Salzkörnern bei Fritte (1 Gold = 1 Korn, einmal pro Jahr). Gibt die Körner zurück."""
        tausch = salz_tausch()
        rest = quest.gold(c, jahr)
        if tausch is None or rest <= 0:
            return 0
        try:
            koerner = int(tausch(c, jahr, rest, f"Restgold aus der Steuer-Quest {jahr}") or 0)
        except Exception:  # noqa: BLE001 — der Sieg darf an Frittes Kasse nie scheitern
            return 0
        if koerner > 0:
            # Das ganze Restgold geht hinüber; den Kurs bestimmt Frittes Kasse
            quest.gold_ausgeben(c, jahr, rest, f"Zu {koerner} Salzkörnern bei Fritte")
        return koerner

    def fritte_truhe(c, jahr: int, silber: bool) -> bool:
        """Der Sieg zahlt in den Alltag zurück: eine Truhe bei Fritte, einmal pro Steuerjahr.
        Gold-Sieg: mindestens selten. Ohne Fritte (ausgeschaltet oder noch nicht da) nichts."""
        if not settings().haustier:
            return False
        try:
            from frits.spiel.truhen import truhe_geben
        except ImportError:
            return False
        try:
            return bool(
                truhe_geben(
                    c,
                    f"drachensieg:{jahr}",
                    seltenheit="gewöhnlich" if silber else "selten",
                    text=f"Der Drache {jahr} ist besiegt!",
                )
            )
        except Exception:  # noqa: BLE001 — der Sieg darf an Frittes Truhe nie scheitern
            return False

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

    def zweifel_an(
        jahr: int, s: dict, pfad: Path, original: str, ausdruecklich: bool = False
    ) -> pruefung.Zweifel | None:
        """Fritte stutzt, wenn eine Datei offensichtlich nicht zur Karte passt (siehe pruefung).
        Kennt FRITS die Datei schon, kümmert sich doppelt_melden darum."""
        c = conn()
        if quest.datei_mit_hash(c, jahr, quest.datei_hash(pfad)):
            return None
        return pruefung.pruefe(
            pfad,
            original,
            s,
            quest.stuecke(c, jahr),
            quellen=katalog_quellen(s["gebiet"], s["key"]),
            ausdruecklich=ausdruecklich,
        )

    def nachfragen(
        jahr: int, s: dict, name: str, z: pruefung.Zweifel, zurueck: str, aus_korb: bool = False
    ) -> None:
        """Die Rückfrage als Zettel mit zwei Wegen. Bis dahin gibt es kein Gold.
        Sie bleibt auch am Zettel im Eingangskorb stehen, damit ein Neuladen sie nicht verliert."""
        app.state.steuer_stutzen[name] = {
            "jahr": jahr,
            "stueck_id": s["id"],
            "karte": s["name"],
            "text": z.text,
        }
        wo = (
            "Es bleibt erst mal im Eingangskorb."
            if aus_korb
            else "Ich habe es erst mal in den Eingangskorb gelegt."
        )
        flash(
            f"Fritte stutzt: {z.text} {wo}",
            "warn",
            aktion={
                "url": f"/steuer/{jahr}/lager/zuordnen",
                "knopf": "Trotzdem ablegen",
                "felder": {"name": name, "stueck_id": s["id"], "trotzdem": "1", "back": zurueck},
                "zweit": {
                    "url": f"/steuer/{jahr}/lager/beiseite",
                    "knopf": "Beiseite legen",
                    "felder": {"name": name, "back": zurueck},
                },
            },
        )

    def in_den_korb(tmp: Path, name: str) -> Path:
        ablage = quest.lager(ws.root)
        ziel, n = ablage / name, 1
        while ziel.exists():
            ziel = ablage / f"{Path(name).stem} ({n}){Path(name).suffix}"
            n += 1
        tmp.replace(ziel)
        return ziel

    async def hochladen(
        jahr: int, stueck_id: int, uploads: list[UploadFile], zurueck: str = ""
    ) -> tuple[list[quest.Fund], int]:
        """Speichert hochgeladene Dateien und sammelt sie ein. Gibt alle Funde zurück (neu oder
        schon da), damit die Seite ehrlich sagen kann, was passiert ist, und wie viele Dateien
        Fritte erst einmal in den Eingangskorb gelegt hat, weil sie nicht zur Karte passen."""
        funde, geparkt = [], 0
        c = conn()
        s = quest.stueck(c, stueck_id)
        ablage = quest.lager(ws.root)
        ablage.mkdir(parents=True, exist_ok=True)
        for upload in uploads:
            name = Path(upload.filename or "").name
            if not name:
                continue
            tmp = ablage / f".upload-{name}"
            with tmp.open("wb") as fh:
                shutil.copyfileobj(upload.file, fh)
            z = zweifel_an(jahr, s, tmp, name)
            if z is not None:
                ziel = in_den_korb(tmp, name)
                nachfragen(jahr, s, ziel.name, z, zurueck or f"/steuer/{jahr}/gebiet/{s['gebiet']}")
                geparkt += 1
                continue
            fund = quest.sammeln(c, ws.root, stueck_id, tmp, original=name, verschieben=True)
            funde.append(fund)
            if fund.neu:
                quest.notiere(c, jahr, "fund", f"{fund.stueck['name']}: {name}")
        return funde, geparkt

    def zier_herkunft(c) -> dict[str, str]:
        """Woher jede Zierde kommt. Die Insel ist für alle Jahre dieselbe, darum sagt die Zierde,
        in welchem Jahr ihr Orden verdient wurde (Lena: Brunnen in 2023 ohne Orden in 2023)."""
        out = {}
        for o in quest.orden_fuer_sammlung(c):
            if o["erreicht"] and o["zierde"] and o["zierde"] not in out:
                out[o["zierde"]] = f"Belohnung für den Orden „{o['name']}“ ({o['jahr']})"
        return out

    def orden_nachtragen(jahr: int, erfolge) -> int:
        """Orden, die erreicht, aber nie notiert sind (aus älteren Spielständen), kommen in die
        Chronik und stellen ihre Zierde auf. Einmal pro Orden und Jahr, mit einer Meldung."""
        c = conn()
        n = 0
        for e in erfolge:
            if not e.erreicht or quest.hat_notiz(c, jahr, "erfolg", e.name):
                continue
            quest.notiere(c, jahr, "erfolg", e.name)
            z = spiel.zierde_fuer(e.key)
            if z:
                quest.zierde_freischalten(c, z.key)
            flash(
                f"Orden nachgetragen: {e.name}. {e.beschreibung}"
                + (f" Dafür steht jetzt {z.name} auf deiner Insel." if z else ""),
                "orden",
            )
            n += 1
        return n

    # ----- Einstieg und Fragebogen -----

    @app.get("/steuer")
    def steuer_start():
        return back(f"/steuer/{standard_jahr()}")

    @app.get("/steuer/{jahr}", response_class=HTMLResponse)
    def karte(request: Request, jahr: int, neu: str = ""):
        if profil(jahr) is None:
            return back(f"/steuer/{jahr}/intro")
        daten = lage(jahr)
        if (
            daten["drache"].besiegt
            and daten["ressourcen"].gold > 0
            and spiel.gold_jahr(jahr, today())
        ):
            # Jahre, die vor der einen Währung gewonnen wurden: Ihr Restgold lag tot im Beutel
            koerner = gold_zu_salz(conn(), jahr)
            if koerner:
                flash(
                    f"Dein Restgold aus dem gewonnenen Jahr {jahr} ist zu {koerner} Salzkörnern"
                    " bei Fritte geworden.",
                    "beute",
                    link=("/fritte", "Zu Fritte"),
                )
                daten = lage(jahr)
        nachtrag = orden_nachtragen(jahr, daten["erfolge"])
        if nachtrag:
            daten = lage(jahr)
        geerntet = ernte_von_selbst(jahr, daten)
        if geerntet:
            return back(f"/steuer/{jahr}/drache")
        if geerntet is not None:
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
                # Wer FRITS eingerichtet hat oder in dem Jahr Rechnungen geschrieben hat,
                # arbeitet selbstständig: Die Provinz steht schon da (Fragebogen sagt woher).
                if settings().setup_done or ernte.hat_rechnungen(ws, jahr):
                    p.mengen["freiberuflich"] = [1, 0]
                    app.state.steuer_uebernommen.add(jahr)
                if settings().tax_mode == "regelbesteuerung":
                    p.umsatzsteuer = "quartal"
            p.jahr = jahr
            app.state.steuer_entwurf[jahr] = p
        return p

    app.state.steuer_entwurf = {}
    app.state.steuer_uebernommen = set()  # Jahre, deren Fragebogen FRITS vorbelegt hat

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
            uebernommen=jahr in app.state.steuer_uebernommen,
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
                f"Deine Insel hat {len(gebiete)} Provinz{'' if len(gebiete) == 1 else 'en'}. Fritte hat dir den Weg markiert.",
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
            s["datei_liste"] = [
                {**d, "angabe": angabe_text(d) if quest.ist_angabe(d["original"]) else None}
                for d in quest.dateien(c, jahr, s["id"])
            ]
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

    @app.post("/steuer/{jahr}/stueck/{stueck_id}/angabe", dependencies=guarded)
    async def angabe_eintippen(request: Request, jahr: int, stueck_id: int):
        """Eintippen statt hochladen: "14 km einfach, 120 Tage". Die Angabe wird eine kleine
        Textdatei bei der Karte, kommt so ins Zip und ins Inhaltsverzeichnis."""
        form = await request.form()
        s = stueck_im_jahr(jahr, stueck_id)
        zurueck = str(form.get("back") or f"/steuer/{jahr}/gebiet/{s['gebiet']}")
        text = str(form.get("text") or "").strip()[:2000]
        if not text:
            flash("Tipp kurz ein, was du weißt. Ein Stichwort reicht.")
            return back(zurueck)
        vorher = lage(jahr)
        ablage = quest.lager(ws.root)
        ablage.mkdir(parents=True, exist_ok=True)
        original = f"Angabe {s['name']}.txt"
        tmp = ablage / f".angabe-{s['id']}.txt"
        tmp.write_text(f"{s['name']}\n\n{text}\n", encoding="utf-8")
        c = conn()
        # Ändern statt stapeln: Eine Karte hat höchstens eine Angabe, die neue ersetzt die alte
        alte = [
            d
            for d in quest.dateien(c, jahr, s["id"])
            if quest.ist_angabe(d["original"]) and angabe_text(d) != text
        ]
        for d in alte:
            quest.entferne_datei(c, d["id"])
        fund = quest.sammeln(c, ws.root, s["id"], tmp, original=original, verschieben=True)
        if fund.neu and alte:
            flash(f"Geändert: {s['name']} ist neu notiert.", "beute", "done")
        elif fund.neu:
            quest.notiere(c, jahr, "fund", f"{s['name']}: eingetippt")
            flash(
                f"+1 Angabe! {s['name']} ist notiert und kommt so in die Fundkiste.",
                "beute",
                "done",
            )
        else:
            flash("Genau diese Angabe liegt schon bei der Karte.")
        if belohnen(jahr, vorher):
            return back(f"/steuer/{jahr}/drache")
        return back(zurueck)

    @app.post("/steuer/{jahr}/stueck/{stueck_id}/datei", dependencies=guarded)
    async def datei_hochladen(request: Request, jahr: int, stueck_id: int):
        form = await request.form()
        s = stueck_im_jahr(jahr, stueck_id)
        uploads = [f for f in form.getlist("datei") if hasattr(f, "filename") and f.filename]
        if not uploads:
            flash("Da war keine Datei dabei. Zieh eine Datei in das Feld oder wähle eine aus.")
            return back(f"/steuer/{jahr}/gebiet/{s['gebiet']}")
        vorher = lage(jahr)
        zurueck = str(form.get("back") or f"/steuer/{jahr}/gebiet/{s['gebiet']}")
        funde, geparkt = await hochladen(jahr, stueck_id, uploads, zurueck)
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
        elif funde or not geparkt:
            doppelt_melden(jahr, funde[0] if funde else None, s)
        if belohnen(jahr, vorher):
            return back(f"/steuer/{jahr}/drache")
        return back(zurueck)

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
        zurueck = str(form.get("back") or f"/steuer/{jahr}")
        if form.get("trotzdem") != "1":
            z = zweifel_an(jahr, s, quelle, name, ausdruecklich=form.get("gewaehlt") == "1")
            if z is not None:
                nachfragen(jahr, s, name, z, zurueck, aus_korb=True)
                return back(zurueck)
        vorher = lage(jahr)
        c = conn()
        fund = quest.sammeln(c, ws.root, stueck_id, quelle, verschieben=True)
        app.state.steuer_stutzen.pop(name, None)
        if fund.neu:
            quest.notiere(c, jahr, "fund", f"{s['name']}: {name}")
            wie = ""
            if fund.verweis:
                wie = f" (dieselbe Datei wie bei {titel_von(jahr, fund.verweis['gebiet'])})"
            flash(f"+1 Beleg! {name} → {s['name']}{wie}", "beute", "done")
        else:
            doppelt_melden(jahr, fund, s, str(form.get("back") or ""))
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
        gestempelt, doppelt = 0, 0
        for quelle in quest.lager_dateien(ws.root):
            karte = spiel.ahnung(quelle.name, alle).stueck
            if karte is None:
                continue
            fund = quest.sammeln(c, ws.root, karte["id"], quelle, verschieben=True)
            if fund.neu:
                quest.notiere(c, jahr, "fund", f"{karte['name']}: {quelle.name}")
                gestempelt += 1
            else:
                doppelt += 1  # dieselbe Datei lag schon bei einer Karte: der Zettel war eine Kopie
        if gestempelt or doppelt:
            extra = (
                f" {doppelt} {'war eine Kopie' if doppelt == 1 else 'waren Kopien'} von Belegen,"
                " die schon bei ihren Karten liegen."
                if doppelt
                else ""
            )
            flash(
                f"Die Spürnase hat {gestempelt} Zettel abgestempelt.{extra} "
                "Falsch gelandet? Im Belegbuch siehst du alles.",
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
        d = quest.datei(conn(), jahr, datei_id)
        quest.entferne_datei(conn(), datei_id)
        # Wer zurücklegt, verliert die Serie: sonst ließe sich die Combo hochtreiben
        if jahr in app.state.steuer_combo:
            app.state.steuer_combo[jahr] = {"combo": 0, "ruf": spiel.RUFE[0]}
        if d and quest.ist_angabe(d["original"]):
            flash("Die Angabe ist gelöscht. Du kannst sie jederzeit neu eintippen.")
        else:
            flash("Die Datei liegt wieder im Eingangskorb. Nichts ist verloren.")
        lp = lage(jahr)["drache"].lp
        if vorher["drache_wach"] and lp > vorher["drache"].lp:
            flash(f"Der Drache hat wieder {lp} Karte{'n' if lp != 1 else ''}.", "treffer")
        belohnen(jahr, vorher, ferne=False)
        return back(str(form.get("back") or f"/steuer/{jahr}"))

    # ----- Belegbuch: alles, was schon da ist, an einem Ort -----

    def datei_pfad(jahr: int, datei_id: int) -> tuple[dict, Path]:
        """Die Datei zu dieser Nummer, nur wenn sie wirklich im Quest-Ordner dieses Jahres liegt."""
        d = quest.datei(conn(), jahr, datei_id)
        if d is None:
            raise HTTPException(404)
        pfad = Path(d["pfad"]).resolve()
        if not pfad.is_file() or quest.ordner(ws.root, jahr).resolve() not in pfad.parents:
            raise HTTPException(404)
        return d, pfad

    @app.get("/steuer/{jahr}/belege", response_class=HTMLResponse)
    def belegbuch(request: Request, jahr: int):
        daten = lage(jahr)
        c = conn()
        nach_stueck: dict[int, list[dict]] = {}
        for d in quest.dateien(c, jahr):
            nach_stueck.setdefault(d["stueck_id"], []).append(beleg_info(d))
        mit: dict[int, list[dict]] = {}
        for v in quest.verweise(c, jahr):
            v["bei_titel"] = titel_von(jahr, v["bei_gebiet"])
            mit.setdefault(v["stueck_id"], []).append(v)
        provinzen = []
        for nr, g in enumerate(daten["gebiete"], start=1):
            karten = [
                {**s, "dateien": nach_stueck.get(s["id"], []), "mit": mit.get(s["id"], [])}
                for s in daten["stuecke"]
                if s["gebiet"] == g.id
            ]
            provinzen.append(
                {
                    "g": g,
                    "ordner": truhe.ordnername(nr, g),
                    "karten": karten,
                    "anzahl": sum(len(k["dateien"]) for k in karten),
                    "leer": [
                        k
                        for k in karten
                        if k["status"] == "offen" and k["art"] != "bonus" and not k["mit"]
                    ],
                }
            )
        alle = [d for p in provinzen for k in p["karten"] for d in k["dateien"]]
        zahlen = None
        try:
            from frits import euer

            summe = euer.summarize(ws.db, jahr, settings().tax_mode)
            if summe.invoice_count or summe.expense_count or summe.credit_count:
                zahlen = summe
        except Exception:  # noqa: BLE001 - die Zahlen sind ein Extra, das Belegbuch geht auch ohne
            zahlen = None
        return seite(
            request,
            "steuer_belege.html",
            belegprovinzen=provinzen,
            summen={
                "belege": sum(1 for d in alle if d["art"] != "angabe"),
                "angaben": sum(1 for d in alle if d["art"] == "angabe"),
                "groesse": groesse_text(sum(d["groesse"] for d in alle)),
                "leer": sum(len(p["leer"]) for p in provinzen),
                "fragen": sum(
                    1
                    for s in daten["stuecke"]
                    if s["frage"] != MACHT_BERATERIN and (s["status"] == "fragen" or s["frage"])
                ),
            },
            korb=quest.lager_dateien(ws.root),
            zahlen=zahlen,
            **daten,
        )

    @app.get("/steuer/{jahr}/datei/{datei_id}")
    def datei_zeigen(jahr: int, datei_id: int):
        """Zeigt einen Beleg im Browser. Nur Bilder, PDFs und Text werden direkt gezeigt,
        alles andere wird heruntergeladen (so kann keine Datei als Webseite laufen)."""
        d, pfad = datei_pfad(jahr, datei_id)
        typ = ZEIGBAR.get(pfad.suffix.lower())
        name = d["original"] or pfad.name
        return FileResponse(
            pfad,
            media_type=typ or "application/octet-stream",
            filename=name,
            content_disposition_type="inline" if typ else "attachment",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.post("/steuer/{jahr}/datei/{datei_id}/ki", dependencies=guarded)
    async def datei_mit_ki(jahr: int, datei_id: int):
        """Öffnet den freien Chat mit diesem Beleg: hier gelesen und geschwärzt.
        Raus geht erst etwas, wenn du dort eine Frage stellst."""
        d, pfad = datei_pfad(jahr, datei_id)
        mit_datei = getattr(app.state, "notfall_mit_datei", None)
        if mit_datei is None:
            flash("Der Chat mit der KI ist gerade nicht da.")
            return back(f"/steuer/{jahr}/belege")
        key, probleme = await run_in_threadpool(mit_datei, pfad, d["original"] or pfad.name)
        for p in probleme:
            # Keine Sackgasse: Den Beleg kann man immer noch selbst ansehen
            flash(
                p if key else f"{p} Es wurde nichts verschickt.",
                "warn",
                aktion={"link": f"/steuer/{jahr}/datei/{datei_id}", "link_text": "Ohne KI ansehen"},
            )
        if key:
            return back(f"/chat/{key}#unterlagen")
        return back(f"/steuer/{jahr}/belege#karte-{d['stueck_id']}")

    @app.post("/steuer/{jahr}/stueck/{stueck_id}/mitzaehlen", dependencies=guarded)
    async def mitzaehlen(request: Request, jahr: int, stueck_id: int):
        """Eine Datei, die schon bei einer anderen Karte liegt, zählt hier mit, z. B. die 1099
        für "Quellensteuer" und "Dividenden". Nur in derselben Provinz oder für Wünsche."""
        form = await request.form()
        s = stueck_im_jahr(jahr, stueck_id)
        zurueck = str(form.get("back") or f"/steuer/{jahr}/gebiet/{s['gebiet']}")
        try:
            datei_id = int(str(form.get("datei_id") or ""))
        except ValueError:
            raise HTTPException(400) from None
        vorher = lage(jahr)
        try:
            neu = quest.mitzaehlen(conn(), stueck_id, datei_id)
        except quest.QuestError as exc:
            flash(str(exc))
            return back(zurueck)
        if neu:
            flash(f"+1! {s['name']} zählt mit, die Datei liegt im Zip einmal.", "beute", "done")
        if belohnen(jahr, vorher):
            return back(f"/steuer/{jahr}/drache")
        return back(zurueck)

    @app.post("/steuer/{jahr}/stueck/{stueck_id}/wunsch-weg", dependencies=guarded)
    async def wunsch_entfernen(request: Request, jahr: int, stueck_id: int):
        """Eine Zeile der Mail falsch zugeordnet? Die Markierung (oder die neue Karte) geht weg."""
        form = await request.form()
        s = stueck_im_jahr(jahr, stueck_id)
        vorher = lage(jahr)
        zurueck = str(form.get("back") or f"/steuer/{jahr}/gebiet/{s['gebiet']}")
        was = quest.wunsch_weg(conn(), stueck_id)
        # Ein Fehlklick soll keinen echten Wunsch kosten: Der Zettel bietet den Weg zurück an
        felder = {"back": zurueck}
        if was == "karte":
            text = f"„{s['name']}“ ist wieder weg. Es war nur ein falsch gelesener Wunsch."
            felder.update(
                gebiet=s["gebiet"],
                name=s["name"],
                hinweis=s.get("hinweis") or "",
                status=s["status"],
                frage=s.get("frage") or "",
            )
        else:
            text = f"„{s['name']}“ ist nicht mehr als Wunsch der Beraterin markiert."
            felder["stueck_id"] = s["id"]
        flash(
            text,
            "warn",
            aktion={
                "url": f"/steuer/{jahr}/wunsch-zurueck",
                "knopf": "Doch gewünscht",
                "felder": felder,
            },
        )
        belohnen(jahr, vorher, ferne=False)
        return back(zurueck)

    @app.post("/steuer/{jahr}/wunsch-zurueck", dependencies=guarded)
    async def wunsch_zurueck(request: Request, jahr: int):
        """Rückgängig für "Hat sie gar nicht gewünscht?"."""
        form = await request.form()
        profil_oder_404(jahr)
        vorher = lage(jahr)
        c = conn()
        if form.get("stueck_id"):
            s = stueck_im_jahr(jahr, int(str(form.get("stueck_id"))))
            quest.markiere_wunsch(c, s["id"])
            name = s["name"]
        else:
            gebiet, name = str(form.get("gebiet") or ""), str(form.get("name") or "").strip()
            if not name or gebiet not in {g.id for g in vorher["gebiete"]}:
                raise HTTPException(400)
            neu = quest.neues_stueck(c, jahr, gebiet, name, hinweis=str(form.get("hinweis") or ""))
            status = str(form.get("status") or "offen")
            if status in ("fragen", "gibts_nicht"):
                quest.setze_status(c, neu, status, str(form.get("frage") or ""))
        flash(f"„{name}“ ist wieder ein Wunsch der Beraterin.", "beute")
        belohnen(jahr, vorher, ferne=False)
        return back(str(form.get("back") or f"/steuer/{jahr}"))

    @app.post("/steuer/{jahr}/stueck/{stueck_id}/frage", dependencies=guarded)
    async def frage_merken(request: Request, jahr: int, stueck_id: int):
        """Eine Frage zu einer Karte, ohne den Stempel zu ändern. Sie kommt in die Mail."""
        form = await request.form()
        s = stueck_im_jahr(jahr, stueck_id)
        text = str(form.get("frage") or "").strip()
        quest.setze_frage(conn(), stueck_id, text)
        if text:
            flash(f"Notiert. Deine Frage zu {s['name']} kommt in die Mail an deine Beraterin.")
        else:
            flash(f"Die Frage zu {s['name']} ist gelöscht.")
        return back(str(form.get("back") or f"/steuer/{jahr}/belege#karte-{stueck_id}"))

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
        c = conn()
        im_korb = {quest.datei_hash(p) for p in quest.lager_dateien(ws.root)}
        for upload in [*form.getlist("files"), *form.getlist("datei")]:
            name = Path(str(getattr(upload, "filename", "") or "").replace("\\", "/")).name.strip()
            if not name or name.startswith("."):
                continue
            daten = await upload.read(MAX_LAGER + 1)
            if len(daten) > MAX_LAGER:
                zu_gross.append(name)
                continue
            # Kopien gleich erkennen, statt sie als neue Zettel in den Korb zu legen
            sha = hashlib.sha256(daten).hexdigest()
            schon = quest.datei_mit_hash(c, jahr, sha)
            if schon:
                bei = quest.stueck(c, schon["stueck_id"])
                flash(
                    f"{name}: Kenne ich schon, liegt bei „{bei['name']}“. Nichts zu tun.",
                    "warn",
                    aktion={"link": f"/steuer/{jahr}/belege#karte-{bei['id']}"},
                )
                continue
            if sha in im_korb:
                flash(f"{name}: Genau diese Datei liegt schon im Eingangskorb.")
                continue
            im_korb.add(sha)
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
        app.state.steuer_stutzen.pop(name, None)
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

    def ernte_holen(jahr: int, gebiet_id: str, titel: str) -> str | None:
        """Kopiert alles Neue aus FRITS in die Provinz und meldet es. None: war schon alles da."""
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
        if not teile:
            return None
        text = f"FRITS-Ernte eingebracht: {', '.join(teile)} → {titel}"
        quest.notiere(c, jahr, "fund", text)
        flash(text + "!", "beute", "done")
        return text

    def ernte_von_selbst(jahr: int, daten: dict) -> bool | None:
        """Schreibst du mit FRITS, kommt die Ernte von selbst auf die Insel, ohne Knopf.
        Nur wenn klar ist, wohin: ein Betrieb, oder der, den du beim letzten Mal gewählt hast.
        Gibt None zurück, wenn nichts kam; sonst, ob der Drache dabei gefallen ist."""
        ziele = {g.id: g for g in ernte.ziele(daten["gebiete"])}
        if not ziele:
            return None
        gebiet_id = next(iter(ziele)) if len(ziele) == 1 else ernte.fruehere_wahl(conn(), jahr)
        if gebiet_id not in ziele:
            return None
        if not ernte.finden(conn(), ws, jahr, settings().tax_mode).neu_gesamt:
            return None
        if ernte_holen(jahr, gebiet_id, ziele[gebiet_id].titel) is None:
            return None
        return belohnen(jahr, daten)

    @app.post("/steuer/{jahr}/ernte", dependencies=guarded)
    async def ernte_einbringen(request: Request, jahr: int):
        form = await request.form()
        daten = lage(jahr)
        ziele = {g.id: g for g in ernte.ziele(daten["gebiete"])}
        gebiet_id = str(form.get("gebiet") or "")
        if gebiet_id not in ziele:
            flash("Wähle aus, zu welchem Betrieb die Rechnungen gehören.")
            return back(f"/steuer/{jahr}")
        if ernte_holen(jahr, gebiet_id, ziele[gebiet_id].titel) is None:
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
        quest.notiere(c, jahr, "brief", f"{len(ergebnis)} Aufträge der Beraterin")
        markiert = sum(1 for e in ergebnis if not e["neu"])
        # Eine Zahl für Meldung und Seite: "für beide Wohnungen" sind zwei Aufträge
        anzahl = len(ergebnis)
        flash(
            f"Botschaft entschlüsselt: {anzahl} Aufträge. Für {markiert} davon gibt es schon"
            " eine Karte auf deiner Insel, die ist jetzt markiert."
            if markiert
            else f"Botschaft entschlüsselt: {anzahl} Aufträge auf deiner Karte.",
            "erfolg",
        )
        belohnen(jahr, vorher, auftraege=True)
        return seite(request, "steuer_beraterin.html", ergebnis=ergebnis, text="", **lage(jahr))

    # ----- Der Papierdrache -----

    @app.get("/steuer/{jahr}/drache", response_class=HTMLResponse)
    def drache_seite(request: Request, jahr: int):
        daten = lage(jahr)
        if jahr not in app.state.steuer_combo and daten["drache_wach"]:
            app.state.steuer_combo[jahr] = kampf_anfang()
        combo = dict(app.state.steuer_combo.get(jahr, {"combo": 0, "ruf": spiel.RUFE[0]}))
        # Das Banner zeigt den echten Faktor (mit Kriegstrommel und Sanduhr), nicht die Zählung
        combo["mult"] = spiel.multiplikator(combo["combo"], daten["helfer"])
        # Vorschau statt Rückblick: So viel bringt der nächste Beleg (ein Volltreffer zählt +2)
        combo["basis"] = spiel.gold_fuer({"key": "", "gebiet": ""}, "gesammelt", daten["helfer"])
        combo["naechster"] = spiel.multiplikator(combo["combo"] + 2, daten["helfer"])
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
            goldjahr=spiel.gold_jahr(jahr, today()),
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
            funde, geparkt = await hochladen(jahr, s["id"], uploads, f"/steuer/{jahr}/drache")
            kritisch = any(f.neu for f in funde)
            if geparkt and not funde:
                return back(f"/steuer/{jahr}/drache#karte")  # Fritte fragt erst nach
            if not kritisch and not any(f.stueck["id"] == s["id"] for f in funde):
                # Die Datei gehört schon zu einer anderen Karte: Das ist kein Treffer
                doppelt_melden(jahr, funde[0] if funde else None, s, f"/steuer/{jahr}/drache")
                return back(f"/steuer/{jahr}/drache#karte")
        elif aktion == "zettel":
            # Ein Zettel aus der Hand (dem Eingangskorb) direkt auf den Drachen geworfen
            name = Path(str(form.get("zettel") or "")).name
            quelle = quest.lager(ws.root) / name
            if not name or not quelle.is_file():
                flash("Den Zettel finde ich nicht mehr im Eingangskorb.")
                return back(f"/steuer/{jahr}/drache#karte")
            z = zweifel_an(jahr, s, quelle, name)
            if z is not None:
                nachfragen(jahr, s, name, z, f"/steuer/{jahr}/drache", aus_korb=True)
                return back(f"/steuer/{jahr}/drache#karte")
            c = conn()
            fund = quest.sammeln(c, ws.root, s["id"], quelle, verschieben=True)
            if not fund.neu:
                doppelt_melden(jahr, fund, s, f"/steuer/{jahr}/drache")
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
        alt = app.state.steuer_combo.get(jahr) or kampf_anfang()
        if aktion in ("gibts_nicht", "fragen"):
            app.state.steuer_zurueck[jahr] = {"stueck_id": s["id"], "combo": dict(alt)}
            app.state.steuer_wurf[jahr]["zurueck"] = s["id"]
        else:
            app.state.steuer_zurueck.pop(jahr, None)
        # Die Gold-Serie wächst nur mit Belegen: Sie vervielfacht nur Beleg-Gold, also soll sie
        # auch nur mit Belegen wachsen. Fragen und "Gibt's nicht" treffen, die Serie wartet.
        combo, ruf = spiel.treffer(alt["combo"], kritisch, zaehlt=kritisch)
        if not kritisch and aktion != "gibts_nicht":
            ruf = spiel.SERIE_WARTET
        if aktion == "gibts_nicht" and "trommel" in vorher["helfer"]:
            combo, ruf = 0, "Die Kriegstrommel verstummt. Die Serie ist weg."
        app.state.steuer_combo[jahr] = {
            "combo": combo,
            "ruf": ruf,
            "frisch": "kritisch" if kritisch else "treffer",
            "wartet": not kritisch,
        }
        quest.notiere(conn(), jahr, "treffer", f"{s['name']} ({ruf})")
        mult = spiel.multiplikator(combo, vorher["helfer"])
        if belohnen(jahr, vorher, ferne=False, mult=mult):
            return back(f"/steuer/{jahr}/drache")  # der Falt-Moment
        return back(f"/steuer/{jahr}/drache#karte")

    @app.post("/steuer/{jahr}/drache/zuruecknehmen", dependencies=guarded)
    async def drache_zuruecknehmen(request: Request, jahr: int):
        """Ein Fehlklick im Kampf ("Lieber fragen", "Gibt's nicht", "Finde ich nicht") kostet
        keinen Zug: Die Karte geht zurück zum Drachen, die Serie steht wie vor dem Klick."""
        form = await request.form()
        stueck_id = int(str(form.get("stueck_id") or 0))
        merk = app.state.steuer_zurueck.get(jahr)
        s = stueck_im_jahr(jahr, stueck_id)
        if (
            not merk
            or merk["stueck_id"] != stueck_id
            or s["status"] not in ("gibts_nicht", "fragen")
        ):
            flash("Das lässt sich nicht mehr zurücknehmen. Auf der Provinz-Karte geht es noch.")
            return back(f"/steuer/{jahr}/drache#karte")
        vorher = lage(jahr)
        if vorher["drache"].besiegt:
            flash("Der Drache ist schon besiegt. Den Stempel nimmst du in der Provinz zurück.")
            return back(f"/steuer/{jahr}/drache")
        app.state.steuer_zurueck.pop(jahr, None)
        app.state.steuer_wurf.pop(jahr, None)
        quest.setze_status(conn(), stueck_id, "offen")
        combo = dict(merk["combo"])
        combo["ruf"] = "Zurückgenommen. Die Serie steht wie vorher."
        combo.pop("frisch", None)
        app.state.steuer_combo[jahr] = combo
        flash(f"Zurückgenommen: {s['name']} ist wieder beim Drachen.")
        belohnen(jahr, vorher)
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
            serie_start=spiel.start_serie(gewuerze()),
            zu_salz=salz_tausch() is not None,
            orden_zu_zierde={z: o for o, z in spiel.ORDEN_ZIERDE.items()},
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

    # ----- Sieg, Fundkiste und Mail -----

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
            flash("Die Fundkiste ist gepackt und mit Passwort verschlossen.", "erfolg", "done")
        else:
            app.state.steuer_passwort.pop(jahr, None)
            flash("Die Fundkiste ist gepackt, ohne Passwort.", "erfolg", "done")
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
            flash("Pack zuerst die Fundkiste.")
        elif not system.reveal_path(zip_pfad):
            flash(f"Deine Fundkiste liegt hier: {zip_pfad}")
        return back(f"/steuer/{jahr}/sieg")

    @app.post("/steuer/{jahr}/mail", dependencies=guarded)
    async def mail_oeffnen(request: Request, jahr: int):
        form = await request.form()
        p = profil_oder_404(jahr)
        zip_pfad = truhe_pfad(jahr, p)
        if not zip_pfad.exists():
            flash("Pack zuerst die Fundkiste, dann hänge ich sie an.")
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
        flash(f"Hänge die Fundkiste bitte selbst an: {zip_pfad}")
        return back("/mailto")

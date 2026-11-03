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
from markupsafe import Markup, escape
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


def umbruch(name: str) -> Markup:
    """Ein Dateiname, der nach _ und - umbrechen darf statt mitten im Wort
    („Steuerbescheid_202 / 4_Finanzamt.pdf“, Lena Runde 39). Die Endung bleibt am Wort
    (Runde 40: „Verlustvortrag_2022. / png“), lange Wörter trennt das CSS mit Bindestrich."""
    teile = re.split(r"(?<=[_\-])", name)
    return Markup("<wbr>").join(escape(t) for t in teile)


# Karten, die Briefe aus dem Vorjahr erwarten: Für 2025 ist der Steuerbescheid 2024 richtig
VORJAHRES_KARTEN = {"bescheid", "vorauszahlungen", "verlustvortrag"}


def fremdes_jahr(name: str, jahr: int, karte: str | None = None) -> int | None:
    """Ein Zettel, dessen Name ein anderes Steuerjahr nennt („krankenversicherung-2025.pdf“
    auf der Insel 2024): der Korb ist für alle Jahre gemeinsam (Lena, Runde 37).
    karte: der Schlüssel der vermuteten Karte; Vorjahres-Briefe sind dort kein Irrtum."""
    jahre = {int(j) for j in re.findall(r"(?<!\d)(20[1-3]\d)(?!\d)", name)}
    if not jahre or jahr in jahre:
        return None
    if karte in VORJAHRES_KARTEN and jahr - 1 in jahre:
        return None
    return max(jahre)


def fritte_tipp(daten: dict, lager: list) -> tuple[str, str]:
    """Was Fritte, die Kartografin, gerade sagt: (Stimmung, Satz)."""
    if daten["drache"].besiegt and daten["stuecke"]:
        # Lena (Runde 34): Fritte sagte nach dem Packen weiter „Die Fundkiste wartet“
        kiste = daten.get("kiste")
        if kiste == (0, 0):
            return "done", "Die Fundkiste ist gepackt. Jetzt nur noch ab damit zu deiner Beraterin."
        if kiste:
            return "thinking", "Seit dem Packen hat sich etwas getan. Pack die Fundkiste neu."
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
                "Im Eingangskorb liegt ein Zettel. Leg ihn auf die Insel, ich stempel.",
            )
        return "welcome", f"Im Eingangskorb liegen {n} Zettel. Leg sie auf die Insel, ich stempel."
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
                "Ich hab dir die erste Provinz markiert. Das Leuchtfeuer zeigt dir den Weg, ich erklär dir alles.",
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

    def helfer_regal(c, jahr: int | None = None) -> dict | None:
        """Das Regal „Für die Insel“: die Helfer eines Steuerjahres, zum Kaufen mit
        Salzkörnern aus dem einen Beutel. Ohne Jahr: das neueste Quest-Jahr (None ohne Quest).
        Frittes Laden zeigt es (app.state.helfer_regal), gekauft wird hier bei der Quest."""
        if jahr is None:
            jahre = quest_jahre()
            if not jahre:
                return None
            jahr = jahre[-1]
        besitz = quest.besitz(c, jahr)
        an = quest.helfer(c, jahr)
        waren = [
            {
                "ware": w,
                "hat": w.key in besitz,
                "an": w.key in an,
                "im_kampf": spiel.im_kampf(w.key),
            }
            for w in spiel.MARKT
            if w.art == "helfer"
        ]
        return {
            "jahr": jahr,
            "waren": waren,
            "plaetze": spiel.HELFER_PLAETZE,
            "am_tisch": len(an),
            "tausch": spiel.TAUSCH_PREIS,
            "guthaben": quest.gold(c),
            "sanduhr_bremst": spiel.multiplikator(spiel.start_serie(gewuerze())) > 3,
            "goldjahr": spiel.gold_jahr(jahr, today()),
            "besiegt": spiel.drache(quest.stuecke(c, jahr)).besiegt,
            "schatz": spiel.GOLD_SCHATZ,
        }

    app.state.helfer_regal = lambda c: helfer_regal(c)

    def markt_link(jahr: int) -> str:
        """Wohin der Inselstand führt: in Frittes Laden (ein Laden für alles), ohne Fritte
        zum Hafenmarkt der Quest."""
        return "/fritte/laden#insel" if settings().haustier else f"/steuer/{jahr}/markt"

    def kampf_anfang() -> dict:
        """Die Serie zu Kampfbeginn: Frittes Gewürze wärmen auf (siehe spiel.start_serie)."""
        n = gewuerze()
        start = spiel.start_serie(n)
        if not start:
            return {"combo": 0, "ruf": spiel.RUFE[0]}
        ruf = (
            f"Frittes {'Gewürz wärmt' if n == 1 else 'Gewürze wärmen'} dich auf:"
            " Deine Serie startet schon warm."
        )
        return {"combo": start, "ruf": ruf, "gewuerze": n}

    app.state.steuer_notausgang = set()
    app.state.steuer_passwort = {}
    # Jahre, deren Drache gerade gefallen ist: dort die Falt-Animation zeigen
    app.state.steuer_finale = set()
    app.state.steuer_gepackt = set()  # Lena (Runde 41): Packen ist das Finale des Jahres
    # Der letzte Zettel aus der Hand, damit man ihn zurücklegen kann
    app.state.steuer_wurf = {}
    # Der letzte Schlag ohne Datei (Fragen, "Gibt's nicht") darf zurück: Karte und Serie wie vorher
    app.state.steuer_zurueck = {}
    # Fritte stutzt: Die Rückfrage bleibt am Zettel im Eingangskorb, auch nach dem Neuladen
    app.state.steuer_stutzen = {}
    # Ließ sich die Mail nicht öffnen: der mailto-Link für den Ausweg am Brief, je Jahr
    app.state.steuer_mailto = {}  # je Jahr: {"basis": die Mail, die sich nicht öffnen ließ}
    # Der eigene Brieftext, still gemerkt beim Verlassen des Felds (Lena, Runde 51), je Jahr:
    # was FRITS schrieb ("basis") und was daraus wurde ("eigen"). Nur im Speicher, nie auf Platte.
    app.state.steuer_mailtext = {}

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

    def offene_stutzen() -> dict:
        """Rückfragen am Korb nur, solange die Karte noch offen ist (Lena, Runde 35): Sonst
        legte „Trotzdem zu …“ eine zweite Datei auf eine erledigte Karte."""
        if not app.state.steuer_stutzen:
            return {}
        c = conn()
        offen = {}
        for name, st in app.state.steuer_stutzen.items():
            row = c.execute(
                "SELECT status FROM steuer_stuecke WHERE id = ?", (st["stueck_id"],)
            ).fetchone()
            if row and row[0] == "offen":
                offen[name] = st
        return offen

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
            markt_link=markt_link,
            koerner=spiel.koerner,
            stutzen=offene_stutzen(),
            fremdes_jahr=fremdes_jahr,
            umbruch=umbruch,
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
        # Eine Währung: Gold von früher zieht einmal in Frittes Beutel um
        quest.umziehen(c)
        drache = spiel.drache(alle)
        zettel = len(quest.lager_dateien(ws.root))
        zweifel = spiel.zweifel(alle)
        wach = spiel.drache_wach(alle) or jahr in app.state.steuer_notausgang
        besitz = quest.besitz(c, jahr)
        kiste = quest.kiste_stand(c, jahr)
        return {
            "jahr": jahr,
            "profil": p,
            "gebiete": gebiete,
            "stuecke": alle,
            "provinzen": staende,
            "gesamt": gesamt,
            "ressourcen": replace(spiel.ressourcen(alle, staende), gold=quest.gold(c)),
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
            "auftrag": spiel.auftrag(jahr, staende, drache, zettel, alle, wach, kiste),
            "kiste": kiste,
            **tagesstand(alle),
            "zweifel": zweifel,
            "burg_fehlt": not any(g.provinz.key == "burg" for g in gebiete),
            "jahre": quest_jahre(),
            "vergleich": vergleich,
            "erfolge": erfolge,
            "chronik": quest.chronik(c, jahr),
        }

    def heute_auftrag(c) -> dict | None:
        """Der eine nächste Quest-Schritt für „Als Nächstes“ auf Heute (app.state.quest_auftrag).
        Das älteste Steuerjahr, dessen Fundkiste noch nicht gepackt ist, weil es zuerst fällig
        ist. Ohne Quest: der Einstieg fürs Vorjahr (Lena, Runde 41: der erste Abend blieb
        sonst stumm). None, wenn jede Fundkiste gepackt ist."""
        jahre = quest_jahre()
        if not jahre:
            return {"jahr": standard_jahr(), "art": "start", "titel": "Leg los",
                    "text": "Ein paar Fragen, dann weiß FRITS, welche Unterlagen du brauchst. "
                    "Was schon in FRITS steckt, ist von selbst dabei.",
                    "knopf": "Zur Steuer-Quest", "ziel": "/steuer"}  # fmt: skip
        for jahr in jahre:
            if quest.kiste_stand(c, jahr) == (0, 0):
                continue  # gepackt und aktuell; neue Belege danach melden sich (Lena, Runde 33)
            a = lage(jahr)["auftrag"]
            ziel = a.ziel if a.ziel.startswith("/") else f"/steuer/{jahr}{a.ziel}"
            return {"jahr": jahr, "art": a.art, "titel": a.titel, "text": a.text,
                    "knopf": a.knopf, "ziel": ziel}  # fmt: skip
        return None

    app.state.quest_auftrag = heute_auftrag

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

    def fertiger_name(name: str) -> bool:
        """„Frau Weber“ ja, „Frau“ oder „W“ allein nicht."""
        teile = name.split()
        anreden = {"frau", "herr", "dr.", "dr", "prof.", "frau dr.", "herr dr."}
        return (
            len(name) >= 3
            and name.lower() not in anreden
            and not (len(teile) == 1 and teile[0].lower() in anreden)
        )

    def fertige_mail(mail: str) -> bool:
        return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s.]{2,}", mail))

    def beraterin_teilen(jahr: int, p: Profil) -> None:
        """Lena (Runde 48): „Sie, Frau Weber“ galt nur für ein Jahr. Jahre, die noch nichts über
        die Beraterin wissen, übernehmen Name, Mail und Anrede. Lena (Runde 50): Nur fertige
        Werte, und halbe Reste von früher („Frau“, „weber@kanzlei“) werden vervollständigt.

        Lena (Runde 51): Name und Mail sind ein Paar. Ein anderes Jahr zieht nur mit, wenn es
        noch gar nichts weiß, oder wenn es seine Beraterin übernommen hat (geteilt oder als Kopie
        des Vorjahrs) und genau das Paar hat, das dieses Jahr zuletzt geteilt hat. Sonst bekam beim Wechsel Feld für Feld ein Jahr
        „Frau Lange“ mit Brauns Mail und behielt sie. Ein Jahr mit selbst getippter Beraterin
        bleibt, wie es ist: Wer 2025 zu Herrn Kurz wechselt, hat 2024 noch Frau Weber.
        """
        alt = tuple(p.beraterin_geteilt) if len(p.beraterin_geteilt) == 2 else None

        def wert(jetzt: str, fertig: bool, vorher: str) -> str:
            # Fertig oder bewusst geleert: so teilen. Halb getippt: das Alte bleibt stehen.
            return jetzt if fertig or not jetzt else vorher

        name = wert(p.beraterin_name, fertiger_name(p.beraterin_name), alt[0] if alt else "")
        mail = wert(p.beraterin_email, fertige_mail(p.beraterin_email), alt[1] if alt else "")
        paar = (name, mail)
        if alt is None and not (name or mail):
            return

        leert = alt is not None and sum(map(bool, paar)) < sum(map(bool, alt))

        def passt(alt_wert: str, neu: str) -> bool:
            return not alt_wert or not neu or neu.startswith(alt_wert)

        for j in quest_jahre():
            q = profil(j) if j != jahr else None
            if q is None:
                continue
            hat = (q.beraterin_name, q.beraterin_email)
            uebernommen = q.beraterin_von != 0  # nie selbst getippt, sondern übernommen
            von = jahr
            if hat == ("", ""):
                # Lena (Runde 53): Wer gerade leert, gibt leeren Jahren kein halbes Paar
                neu = paar if any(paar) and not leert else None
            elif uebernommen and hat in (paar, alt):
                neu = paar
            elif hat == paar:
                # Selbst getippt, dieselbe Beraterin: nur die Anrede zieht mit (Runde 47)
                neu, von = paar, q.beraterin_von
            elif any(paar) and passt(hat[0], name) and passt(hat[1], mail):
                # Halbe Reste derselben Beraterin von früher („Frau“) werden vervollständigt;
                # was hier fehlt, bleibt dort stehen
                neu = (name or hat[0], mail or hat[1])
                if neu == hat:
                    neu = None  # nichts zu ergänzen: Anrede und Herkunft bleiben (Lena, R52)
            else:
                neu = None
            if neu is None:
                continue
            vorher = (*hat, q.anrede, q.beraterin_von)
            q.beraterin_name, q.beraterin_email = neu
            q.anrede = p.anrede
            q.beraterin_von = von
            if (q.beraterin_name, q.beraterin_email, q.anrede, q.beraterin_von) != vorher:
                q.save(quest.profil_datei(ws.root, j))
        if (list(paar), p.beraterin_von) != (p.beraterin_geteilt, 0):
            p.beraterin_geteilt = list(paar)
            p.beraterin_von = 0  # hier getippt: dieses Jahr ist jetzt die Quelle
            p.save(quest.profil_datei(ws.root, jahr))

    def beraterin_vom_nachbarn(jahr: int, p: Profil) -> None:
        """Ein neues Jahr übernimmt die Beraterin aus dem nächstgelegenen Quest-Jahr."""
        nachbarn = sorted(
            (j for j in quest_jahre() if j != jahr), key=lambda j: (abs(j - jahr), -j)
        )
        for j in nachbarn:
            q = profil(j)
            if q is not None and (q.beraterin_name or q.beraterin_email):
                p.beraterin_name, p.beraterin_email, p.anrede = (
                    q.beraterin_name,
                    q.beraterin_email,
                    q.anrede,
                )
                p.beraterin_von = j
                return

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
            # Lena (Runde 46): Hier fehlte der Weg zum Beleg, den es bei anderen Karten gibt
            flash(
                "Diese Datei hattest du schon gesammelt. Doppelt zählt nicht, aber gut gemerkt!",
                link=(f"/steuer/{jahr}/belege#karte-{karte['id']}", "Zum Beleg") if fund else None,
            )
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
        # Lena (Runde 40): Erst „Gibt's nicht“, dann doch eine Datei: Das Korn für „Gibt's
        # nicht“ geht still zurück, die Karte zahlt wie ein direkter Fund
        alt_status = {s["id"]: s for s in vorher["stuecke"]}
        for s in nachher["stuecke"]:
            war = alt_status.get(s["id"])
            if war and war["status"] == "gibts_nicht" and s["status"] == "gesammelt":
                quest.storniere(c, jahr, spiel.karten_grund(war, "antwort"), war["name"])
        frueher = quest.stornierte_gruende(c, jahr)  # zurückgelegt und neu: ohne Serie
        for v in spiel.verdienste(vorher["stuecke"], nachher["stuecke"], mult, helfer, frueher):
            if not goldjahr:
                break
            if ":wunsch:" in v.grund and v.grund.startswith("fund:"):
                if wuensche >= spiel.WUNSCH_DECKEL:
                    continue  # Wünsche zahlen nur bis zum Deckel (man kann sie selbst erfinden)
                wuensche += 1
            if quest.verdiene(c, jahr, v.grund, v.betrag, v.text):
                basis, faktor, plus = basis + v.basis, v.mult, plus + v.betrag
        if plus and ferne and app.state.flashes and app.state.flashes[-1]["kind"] == "beute":
            # Lena (Runde 33): Das Banner zum Fund nannte die Körner nicht
            app.state.flashes[-1]["text"] += f" · +{spiel.koerner(plus)}"
        # Wieder offen: Das Gold dafür geht zurück (sonst lohnt ein Fehlwurf bei ×8)
        wieder = 0  # was ein erneutes Ablegen außerhalb des Kampfs ungefähr bringt
        for grund, text in spiel.rueckbuchungen(vorher["stuecke"], nachher["stuecke"]):
            minus += quest.storniere(c, jahr, grund, text)
        if goldjahr:
            alt = {s["id"]: s for s in vorher["stuecke"]}
            frueher = quest.stornierte_gruende(c, jahr)
            for s in nachher["stuecke"]:
                war = alt.get(s["id"])
                if s["status"] == "offen" and war and war["status"] != "offen":
                    art = "fund" if war["status"] == "gesammelt" else "antwort"
                    grund = spiel.karten_grund(war, art)
                    basis = spiel.gold_fuer(war, war["status"], helfer)
                    wieder += min(basis, frueher.get(grund, basis))
        neu_siegel = {p.gebiet.id for p in nachher["provinzen"] if p.stand.siegel}
        wartend = [
            p.gebiet.titel
            for p in vorher["provinzen"]
            if p.stand.siegel and p.gebiet.id not in neu_siegel
        ]
        if auftraege and wartend:
            flash(
                f"Neue Aufträge für {', '.join(wartend)}. Dein Siegel wartet, bis du sie erledigt"
                " hast. Die Salzkörner dafür behältst du.",
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
            text = f"−{spiel.koerner(minus + minus_siegel)} zurückgebucht"
            text += f" ({' und '.join(teile)})." if minus and minus_siegel else "."
            if minus_siegel and not minus:
                text = f"−{spiel.koerner(minus_siegel)} zurückgebucht: Das Siegel ist wieder offen."
            # Lena (Runde 37): ehrlich rechnen. Ein Treffer aus dem Kampf trug die Serie, das
            # erneute Ablegen bringt nur den Grundwert (plus das Siegel, wenn es wieder voll wird)
            zurueck = wieder + minus_siegel
            if zurueck >= minus + minus_siegel:
                text += " Legst du wieder etwas ab, bekommst du sie zurück."
            elif zurueck:
                text += (
                    f" Legst du sie wieder ab, bekommst du etwa {spiel.koerner(zurueck)} zurück."
                    " Die Serie aus dem Kampf bekommt diese Karte nicht noch einmal."
                )
            flash(text, "hinweis")
            for name in quest.decke_schulden(c, jahr):
                flash(
                    f"Die Salzkörner waren schon ausgegeben, darum ist {name} zurück im Hafen."
                    " Du kannst es wieder holen, sobald du sie neu verdient hast.",
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
                    # Lena (Runde 40): Ein neu verdientes Siegel zahlt höchstens wie damals,
                    # sonst brachte Zurücklegen einen Nachschlag für spätere Funde
                    wert = spiel.siegel_gold(funde)
                    frueher_siegel = quest.stornierte_gruende(c, jahr).get(f"siegel:{p.gebiet.id}")
                    if frueher_siegel is not None:
                        wert = min(wert, frueher_siegel)
                    plus += quest.verdiene(
                        c, jahr, f"siegel:{p.gebiet.id}", wert, f"Siegel: {titel}"
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
                + (f", +{spiel.koerner(lohn)}." if lohn else ".")
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
            # Lena (Runde 41): „Erster Fund“ kam auf der Insel 2024 noch einmal
            schon_frueher = quest.hat_notiz(c, None, "erfolg", e.name)
            quest.notiere(c, jahr, "erfolg", e.name)
            if schon_frueher:
                # Lena (Runde 44): „Erste Ernte“ kam 2026 als „Neuer Orden“ wieder
                if e.key != "erster-fund":
                    flash(f"Orden wieder verdient: {e.name}.", "beute")
                continue
            z = spiel.zierde_fuer(e.key)
            if z:
                quest.zierde_freischalten(c, z.key)
            flash(
                f"Neuer Orden: {e.name}. {e.beschreibung}"
                + (f" Neu auf deiner Insel: {z.name}." if z else ""),
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
        if sieg and quest.hat_notiz(c, jahr, "sieg"):
            # Lena (Runde 37): Nach Zurücklegen und Wiederablegen kam ein zweites Fest ohne Beute
            flash("Der Drache ist wieder besiegt. Deine Beute von vorhin bleibt.", "beute")
            app.state.steuer_combo.pop(jahr, None)
            return False  # kein Sprung zur Drachenseite
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
                text = f"Letzter Treffer: {basis} × {faktor} = +{spiel.koerner(basis * faktor)}"
                if plus > basis * faktor:
                    # Lena (Runde 44): Die Vorschau nannte das Siegel, der Sieg nur +16 von 20
                    text += f", dazu das Siegel +{plus - basis * faktor}"
                flash(text + ".", "sieg")
            if goldjahr and fritte_truhe(c, jahr, silber=bool(nachher["zweifel"])):
                flash(
                    "Fritte hat eine Truhe für dich bekommen. Sie wartet bei Fritte.",
                    "sieg",
                    link=("/fritte#truhen", "Zu Fritte"),
                )
        return sieg

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
                    text=f"Der Drache {jahr} ist besiegt",
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
            "besser_id": z.besser["id"] if z.besser else None,
            "besser": z.besser["name"] if z.besser else "",
        }
        wo = (
            "Es bleibt erst mal im Eingangskorb."
            if aus_korb
            else "Ich habe es erst mal in den Eingangskorb gelegt."
        )
        # Im Kampf ist „Trotzdem ablegen“ ein Schlag mit Datei, mit Serie (Lena, Runde 32)
        im_kampf = zurueck.startswith(f"/steuer/{jahr}/drache")
        url = f"/steuer/{jahr}/drache/schlag" if im_kampf else f"/steuer/{jahr}/lager/zuordnen"
        felder = {"name": name, "stueck_id": s["id"], "trotzdem": "1", "back": zurueck}
        if im_kampf:
            felder.update(aktion="zettel", zettel=name)
        trotzdem = {"url": url, "knopf": "Trotzdem ablegen", "felder": felder}
        beiseite = {
            "url": f"/steuer/{jahr}/lager/beiseite",
            "knopf": "Beiseite legen",
            "felder": {"name": name, "back": zurueck},
        }
        if z.besser:
            # Lena (Runde 45): Fritte nannte die richtige Karte, aber ohne Weg dorthin
            aktion = {
                "url": f"/steuer/{jahr}/lager/zuordnen",
                "knopf": f"Zu „{z.besser['name']}“",
                "felder": {
                    "name": name,
                    "stueck_id": z.besser["id"],
                    "trotzdem": "1",
                    "back": zurueck,
                },
                "zweit": trotzdem,
                "dritt": beiseite,
            }
        else:
            aktion = {**trotzdem, "zweit": beiseite}
        flash(f"Fritte stutzt: {z.text} {wo}", "warn", aktion=aktion)

    def in_den_korb(tmp: Path, name: str) -> Path:
        """Legt die Datei in den Eingangskorb. Liegt genau diese Datei dort schon, bleibt es
        bei der einen (Lena, Runde 32: fünf gleiche Kopien nach mehreren Versuchen)."""
        ablage = quest.lager(ws.root)
        sha = quest.datei_hash(tmp)
        for da in quest.lager_dateien(ws.root):
            if da.stat().st_size == tmp.stat().st_size and quest.datei_hash(da) == sha:
                tmp.unlink(missing_ok=True)
                return da
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
                + (f" Neu auf deiner Insel: {z.name}." if z else ""),
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
            p = Profil.load(entwurf_datei(jahr)) or profil(jahr)
            if p is None:
                p = profil(jahr - 1)
                if p is not None and (p.beraterin_name or p.beraterin_email):
                    # Lena (Runde 52): Die Kopie des Vorjahrs hat ihre Beraterin übernommen
                    # und zieht darum mit, wenn sie dort wechselt
                    p.beraterin_von = p.beraterin_von or jahr - 1
            if p is None:
                p = Profil(jahr=jahr, name=settings().name)
                # Wer FRITS eingerichtet hat oder in dem Jahr Rechnungen geschrieben hat,
                # arbeitet selbstständig: Die Provinz steht schon da (Fragebogen sagt woher).
                if settings().setup_done or ernte.hat_rechnungen(ws, jahr):
                    p.mengen["freiberuflich"] = [1, 0]
                    app.state.steuer_uebernommen.add(jahr)
                if settings().tax_mode == "regelbesteuerung":
                    p.umsatzsteuer = "quartal"
            if not (p.beraterin_name or p.beraterin_email):
                beraterin_vom_nachbarn(jahr, p)
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
        # Zettel für Zettel (Marie, 2.11.: zu viel auf einmal): Eine Karte liegt vorn, die
        # anderen warten im Stapel. Die Liste aller Karten bleibt als Umschalter, gemerkt im Cookie.
        gewaehlt = request.query_params.get("ansicht", "")
        ansicht = gewaehlt or request.cookies.get("sq_ansicht", "")
        ansicht = ansicht if ansicht in ("stapel", "liste") else "stapel"
        offen = [s for s in eigene if s["status"] == "offen"]
        offen = [s for s in offen if s["art"] != "bonus"] + [
            s for s in offen if s["art"] == "bonus"
        ]
        wahl = request.query_params.get("karte", "")
        vorn = next((s for s in eigene if str(s["id"]) == wahl), None) or (
            offen[0] if offen else None
        )
        spaeter = None
        if vorn is not None:
            rest = [s for s in offen if s["id"] != vorn["id"]]
            nach = [s for s in rest if eigene.index(s) > eigene.index(vorn)]
            spaeter = (nach or rest or [None])[0]
        antwort = seite(
            request,
            "steuer_gebiet.html",
            g=g,
            eigene=eigene,
            vorjahr=vorjahr,
            provinzstand=stand,
            lager=quest.lager_dateien(ws.root),
            stapel=ansicht == "stapel" and vorn is not None,
            ansicht=ansicht,
            vorn=vorn,
            spaeter=spaeter,
            aus_frits=ernte.aus_frits(g.provinz.key),
            von_frits_ids=quest.von_frits(c, jahr),
            schatz_salz=schatz_salz(jahr, daten),
            **daten,
        )
        if gewaehlt in ("stapel", "liste"):
            antwort.set_cookie("sq_ansicht", gewaehlt, max_age=3600 * 24 * 365, samesite="lax")
        return antwort

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
            if spiel.ist_bonus(s):
                flash(f"Abgehakt: {s['name']} gibt es dieses Jahr nicht.", "beute")
            else:
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
            flash("Da war keine Datei dabei. Wähl eine Datei aus oder leg sie ins Feld.")
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
        erst = len(app.state.flashes)
        wieder_wach = vorher["drache"].besiegt and not lage(jahr)["drache"].besiegt
        # Lena (Runde 39): Zurücklegen kostete die ganze Serie (×7 → ×2). Seit Runde 39 bringt
        # eine schon gewertete Karte beim Wiederablegen keine Serie mehr, also lässt sich damit
        # nichts hochtreiben: Die Serie bleibt stehen, nur die Karte verliert ihre Körner.
        if jahr in app.state.steuer_combo and not vorher["drache"].besiegt:
            combo = app.state.steuer_combo[jahr]
            for weg in ("frisch", "beute"):
                combo.pop(weg, None)
            combo["ruf"] = "Zurückgelegt. Deine Serie bleibt stehen."
            combo["wartet"] = True
        elif wieder_wach:
            # Lena (Runde 41): Nach dem Sieg stand „×4, bleibt stehen“, obwohl es ×8 war.
            # Der Kampf war vorbei; ein neuer Kampf beginnt mit einer neuen Serie.
            app.state.steuer_combo[jahr] = {
                **kampf_anfang(),
                "ruf": "Der Drache ist wieder wach. Ein neuer Kampf, eine neue Serie.",
            }
        if d and quest.ist_angabe(d["original"]):
            flash("Die Angabe ist gelöscht. Du kannst sie jederzeit neu eintippen.")
        else:
            flash("Die Datei liegt wieder im Eingangskorb.")  # die Körner nennt belohnen
        lp = lage(jahr)["drache"].lp
        if vorher["drache_wach"] and lp > vorher["drache"].lp:
            flash(f"Der Drache hat wieder {lp} Karte{'n' if lp != 1 else ''}.", "treffer")
        belohnen(jahr, vorher, ferne=False)
        zusammenfassen(erst)
        ziel = str(form.get("back") or f"/steuer/{jahr}")
        if d and "#provinz-" in ziel and not im_belegbuch(jahr, d["stueck_id"]):
            # Lena (Runde 38): Die letzte Datei einer Bonus-Provinz ist weg, die Provinz fehlt
            # im Belegbuch. Dann lieber zum Eingangskorb, wo die Datei jetzt liegt.
            ziel = f"/steuer/{jahr}#sq-korb"
        return back(ziel)

    def zusammenfassen(ab: int) -> None:
        """Lena (Runde 41): Nach dem Zurücklegen kamen vier Meldungen auf einmal („noch 3“).
        Schlichte Meldungen ab Position `ab` werden zu einer: ein Zettel, ein Gedanke."""
        neu = app.state.flashes[ab:]
        schlicht = [
            n
            for n in neu
            if n["kind"] in ("ok", "hinweis", "treffer")
            and not (n.get("link") or n.get("aktion") or n.get("undo") or n.get("knopf"))
        ]
        if len(schlicht) < 2:
            return
        schlicht[0]["text"] = " ".join(n["text"] for n in schlicht)
        schlicht[0]["kind"] = "hinweis"
        for n in schlicht[1:]:
            app.state.flashes.remove(n)

    def im_belegbuch(jahr: int, stueck_id: int) -> bool:
        """Steht die Provinz dieser Karte noch im Belegbuch? (gleiche Regel wie steuer_belege)"""
        c = conn()
        try:
            gebiet = quest.stueck(c, stueck_id)["gebiet"]
        except quest.QuestError:
            return False
        karten = [s for s in lage(jahr)["stuecke"] if s["gebiet"] == gebiet]
        ids = {s["id"] for s in karten}
        if any(d["stueck_id"] in ids for d in quest.dateien(c, jahr)):
            return True
        if any(v["stueck_id"] in ids for v in quest.verweise(c, jahr)):
            return True
        return any(s["status"] != "offen" or s["frage"] or s["art"] != "bonus" for s in karten)

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
        gelegt, zu_gross, bekannt = [], [], 0
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
                bekannt += 1
                continue
            if sha in im_korb:
                flash(
                    f"{name}: Genau diese Datei liegt schon im Eingangskorb.",
                    link=(f"/steuer/{jahr}#sq-korb", "Zum Korb"),
                )
                bekannt += 1
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
                f"{n} neue{'r' if n == 1 else ''} Zettel im Eingangskorb."
                f" Leg {'ihn' if n == 1 else 'sie'} auf eine Provinz.",
                "beute",
            )
        for name in zu_gross:
            flash(
                f"{name} ist größer als 30 MB. Leg sie lieber direkt in den Lager-Ordner.", "error"
            )
        if not gelegt and not zu_gross and not bekannt:
            # Lena (Runde 37): Nach „Kenne ich schon“ wirkte diese Meldung wie ein Fehler
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
            flash(
                "Den Zettel finde ich nicht mehr im Eingangskorb.",
                link=(f"/steuer/{jahr}#sq-korb", "Zum Korb"),
            )
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
        text = f"Aus FRITS dazugekommen: {', '.join(teile)} → {titel}"
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
            beraterin_teilen(jahr, p)
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
        # Lena (Runde 39): Für eine schon gewertete Karte versprach die Seite „2 × 2 = 4“
        karte = daten["drache"].karte
        if karte:
            c = conn()
            frueher = quest.stornierte_gruende(c, jahr)
            grund = spiel.karten_grund(karte, "fund")
            if grund in frueher:
                basis = spiel.gold_fuer(karte, "gesammelt", daten["helfer"])
                combo["gewertet"] = min(basis, frueher[grund])
            # Lena (Runde 40/41): Kommt mit dieser Karte ein Siegel, gehört es in die Vorschau
            probe = [
                {**s, "status": "gesammelt"} if s["id"] == karte["id"] else s
                for s in daten["stuecke"]
            ]
            p = next(
                (
                    p
                    for p in spiel.provinzen(daten["gebiete"], probe)
                    if p.gebiet.id == karte["gebiet"]
                ),
                None,
            )
            siegel = f"siegel:{karte['gebiet']}"
            if p and p.stand.siegel and not quest.hat_gold(c, jahr, siegel):
                funde = sum(1 for x in p.stuecke if x["status"] == "gesammelt")
                wert = spiel.siegel_gold(funde)
                combo["siegel_dazu"] = min(wert, frueher.get(siegel, wert))
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
                flash(
                    "Den Zettel finde ich nicht mehr im Eingangskorb.",
                    link=(f"/steuer/{jahr}#sq-korb", "Zum Korb"),
                )
                return back(f"/steuer/{jahr}/drache#karte")
            z = None if form.get("trotzdem") == "1" else zweifel_an(jahr, s, quelle, name)
            if z is not None:
                nachfragen(jahr, s, name, z, f"/steuer/{jahr}/drache", aus_korb=True)
                return back(f"/steuer/{jahr}/drache#karte")
            app.state.steuer_stutzen.pop(name, None)
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
        # Lena (Runde 38): Eine Karte, die schon einmal gewertet und zurückgelegt wurde, lässt
        # die Serie nicht wachsen und zahlt nur den Grundwert (sonst lohnt Umbuchen)
        gewertet = spiel.karten_grund(s, "fund") in quest.stornierte_gruende(conn(), jahr)
        wieder = kritisch and gewertet
        combo, ruf = spiel.treffer(alt["combo"], kritisch, zaehlt=kritisch and not wieder)
        if wieder:
            ruf = spiel.SCHON_GEWERTET
        elif not kritisch and aktion != "gibts_nicht":
            ruf = spiel.SERIE_WARTET
        if aktion == "gibts_nicht" and "trommel" in vorher["helfer"]:
            combo, ruf = 0, "Die Kriegstrommel verstummt. Die Serie ist weg."
        app.state.steuer_combo[jahr] = {
            "combo": combo,
            "ruf": ruf,
            "frisch": "kritisch" if kritisch and not wieder else "treffer",
            "wartet": not kritisch or wieder,
        }
        if wieder and jahr in app.state.steuer_wurf:
            app.state.steuer_wurf[jahr]["wieder"] = True
        if gewertet and not kritisch and jahr in app.state.steuer_wurf:
            # Lena (Runde 40): Fragen auf eine schon gewertete Karte bringt 0, ohne ein Wort
            app.state.steuer_wurf[jahr]["ohne_koerner"] = True
        if wieder and vorher["drache"].lp <= 1:
            # Lena (Runde 39): Bei der letzten Karte sah man den Satz der Serie nie
            flash(spiel.SCHON_GEWERTET, "hinweis")
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

    # ----- Hafenmarkt: Salzkörner ausgeben -----

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
            orden_zu_zierde={z: o for o, z in spiel.ORDEN_ZIERDE.items()},
            **daten,
        )

    def regal_ziel(form, jahr: int) -> str:
        """Zurück dorthin, wo gekauft wurde: Frittes Laden oder der Hafenmarkt."""
        if form.get("zurueck") == "laden":
            return "/fritte/laden#insel"
        return f"/steuer/{jahr}/markt#helfer"

    @app.post("/steuer/{jahr}/markt/kaufen", dependencies=guarded)
    async def markt_kaufen(request: Request, jahr: int):
        form = await request.form()
        profil_oder_404(jahr)
        key = str(form.get("ware") or "")
        ziel = regal_ziel(form, jahr)
        try:
            text = quest.kaufe(conn(), jahr, key)
        except quest.QuestError as exc:
            flash(str(exc))
            return back(ziel)
        flash(text, "beute", "done")
        return back(ziel)

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
        return back(regal_ziel(form, jahr))

    @app.post("/steuer/{jahr}/drache/notausgang", dependencies=guarded)
    def notausgang(jahr: int):
        app.state.steuer_notausgang.add(jahr)
        flash("Kein Problem. Was fehlt, steht in der Mail. Der Drache wartet geduldig.")
        return back(f"/steuer/{jahr}/sieg")

    # ----- Sieg, Fundkiste und Mail -----

    def truhe_pfad(jahr: int, p: Profil, vollstaendig: bool | None = None) -> Path:
        """Die Zip für die Beraterin. Ein Nachtrag nach dem Notausgang heißt anders, damit
        die vollständige Kiste nicht wie die erste aussieht (Lena, Runde 47)."""
        if vollstaendig is None:
            vollstaendig = quest.nachtrag_seit(conn(), jahr) is not None
        name = f"-{quest._slug(p.name)}" if p.name else ""
        zusatz = "-vollstaendig" if vollstaendig else ""
        return quest.ordner(ws.root, jahr) / f"Steuer-{jahr}{name}{zusatz}.zip"

    def nachtrag_aus(jahr: int, halb: tuple[str, int] | None) -> truhe.Nachtrag | None:
        """Was die vollständige Kiste nach dem Notausgang sagt: wann die letzte halbe kam,
        ob es mehrere waren und welche Karten seitdem einen Beleg bekamen."""
        if not halb:
            return None
        zeit, anzahl = halb
        neu_ids = [d for d in quest.dateien(conn(), jahr) if str(d["hinzugefuegt"]) > zeit]
        namen = {s["id"]: s["name"] for s in quest.stuecke(conn(), jahr)}
        return truhe.Nachtrag(
            datum=schoenes_datum(zeit[:10]),
            mehrere=anzahl > 1,
            neu=tuple(
                dict.fromkeys(namen[d["stueck_id"]] for d in neu_ids if d["stueck_id"] in namen)
            ),
            neue_dateien=frozenset(d["id"] for d in neu_ids),
        )

    def mail_entwurf(jahr: int, daten: dict, mit_passwort: bool) -> tuple[str, str]:
        return truhe.mail(
            daten["profil"],
            daten["gebiete"],
            daten["stuecke"],
            mit_passwort=mit_passwort,
            nachtrag=nachtrag_aus(jahr, quest.nachtrag_seit(conn(), jahr)),
        )

    def mail_basis(jahr: int, daten: dict) -> tuple[str, str]:
        """Die Mail, wie FRITS sie gerade schreiben würde (wie auf der Siegseite)."""
        zip_pfad = truhe_pfad(jahr, daten["profil"])
        return mail_entwurf(
            jahr, daten, truhe.verschluesselt(zip_pfad) if zip_pfad.exists() else True
        )

    @app.get("/steuer/{jahr}/sieg", response_class=HTMLResponse)
    def sieg(request: Request, jahr: int):
        daten = lage(jahr)
        if not daten["drache"].besiegt and jahr not in app.state.steuer_notausgang:
            flash("Erst der Drache, dann die Beute. Oder nimm den Notausgang, wenn es eilt.")
            return back(f"/steuer/{jahr}/drache")
        zip_pfad = truhe_pfad(jahr, daten["profil"])
        frisch_gepackt = jahr in app.state.steuer_gepackt
        app.state.steuer_gepackt.discard(jahr)
        fritte_warten = 0
        if frisch_gepackt and settings().haustier:
            try:
                from frits.spiel import stand as fritte_stand

                fritte_warten = fritte_stand.wartende(conn())
            except Exception:  # noqa: BLE001 - der Hinweis auf Fritte ist ein Extra
                fritte_warten = 0
        mit_passwort = truhe.verschluesselt(zip_pfad) if zip_pfad.exists() else True
        betreff, text = basis = mail_entwurf(jahr, daten, mit_passwort)
        eigen = app.state.steuer_mailtext.get(jahr)
        alte_texte: list[str] = list(eigen.get("frueher", [])) if eigen else []
        if eigen and eigen["basis"] == basis:
            # Lena (Runde 50/51): Der eigene Text bleibt, solange FRITS dieselbe Mail schreiben
            # würde, auch nach dem Neuladen.
            betreff, text = eigen["eigen"]
        elif eigen and eigen["eigen"] != eigen["basis"] and eigen["eigen"][1] not in alte_texte:
            # Nach dem Finale gilt die neue Mail; der alte eigene Text verschwindet nicht stumm
            alte_texte.insert(0, eigen["eigen"][1])
        # Der Ausweg gilt nur für die Mail, die sich nicht öffnen ließ, nicht nach dem Finale
        ausweg = app.state.steuer_mailto.get(jahr)
        ausweg_gilt = bool(ausweg and ausweg["basis"] == basis)
        return seite(
            request,
            "steuer_sieg.html",
            betreff=betreff,
            mailtext=text,
            zip_pfad=zip_pfad if zip_pfad.exists() else None,
            passwort=app.state.steuer_passwort.get(jahr),
            mit_passwort=mit_passwort,
            dateien=quest.dateien(conn(), jahr),
            lager=quest.lager_dateien(ws.root),
            noch_frei=noch_frei(daten),
            noch_sparen=[s for s in noch_frei(daten) if spiel.spart_steuern(s)],
            noch_gruppen=noch_gruppen(daten),
            schatz_salz=schatz_salz(jahr, daten),
            kiste_aenderung=spiel.kiste_aenderung(*daten["kiste"]) if daten["kiste"] else "",
            frisch_gepackt=frisch_gepackt,
            # Lena (Runde 44): Das Finale zeigte eine offene Kiste
            kiste_zu=(
                "/static/bilder/truhe-zu.webp"
                if (resources.files("frits.web") / "static" / "bilder" / "truhe-zu.webp").is_file()
                else ""
            ),
            fritte_warten=fritte_warten,
            # Lena (Runde 49): Der Link wird jedes Mal neu gebaut. Sonst öffnete er nach dem
            # Finale noch die alte, halbe Mail, während daneben schon die vollständige Zip lag.
            mail_ausweg=(
                system.mailto_link(daten["profil"].beraterin_email, betreff, text)
                if ausweg_gilt
                else ""
            ),
            alte_texte=alte_texte[:3],
            kennung=basis_kennung(basis),
            finale_offen=frisch_gepackt and daten["drache"].besiegt and zip_pfad.exists(),
            **daten,
        )

    def schatz_salz(jahr: int, daten: dict) -> int:
        """Was ein freiwilliger Schatz gerade bringt: der Köder (Lena, Runde 32)."""
        if not spiel.gold_jahr(jahr, today()):
            return 0
        return spiel.gold_fuer({"art": "bonus"}, "gesammelt", daten["helfer"])

    def noch_frei(daten: dict) -> list[dict]:
        """Offene freiwillige Zettel für „Lohnt sich noch“ nach dem Sieg, gezählt wie in den
        Provinzen (Lena, Runde 32: Werkstatt 7, Siegseite 5 passte nicht)."""
        frei = [s for s in daten["stuecke"] if s["status"] == "offen" and spiel.ist_bonus(s)]
        # Lena (Runde 35): Was Steuern spart, zuerst; Ordnungskram (Buchungsliste, GoBD) danach
        return sorted(frei, key=lambda s: not spiel.spart_steuern(s))

    def noch_gruppen(daten: dict) -> list[dict]:
        """„Lohnt sich noch“ nach Provinz, in Insel-Reihenfolge; Provinzen mit Schätzen, die
        Steuern sparen, zuerst. So zeigt „Zum ersten Schatz“ auf die erste Zeile (Lena, R36)."""
        frei = noch_frei(daten)
        gruppen = []
        for p in daten["provinzen"]:
            karten = [s for s in frei if s["gebiet"] == p.gebiet.id]
            if karten:
                gruppen.append({"gebiet": p.gebiet.id, "titel": p.gebiet.titel, "karten": karten})
        return sorted(gruppen, key=lambda g: not spiel.spart_steuern(g["karten"][0]))

    @app.post("/steuer/{jahr}/truhe", dependencies=guarded)
    async def truhe_packen(request: Request, jahr: int):
        daten = lage(jahr)
        if not daten["drache"].besiegt and jahr not in app.state.steuer_notausgang:
            return back(f"/steuer/{jahr}/drache")
        form = await request.form()
        kennwort = None if form.get("schloss") == "ohne" else truhe.passwort()
        schon = quest.kiste_stand(conn(), jahr) is not None
        # Lena (Runde 46): Wer erst über den Notausgang packte, bekam nach dem Sieg nie ein
        # Finale. Das erste Packen nach dem Sieg ist das Finale, auch wenn es ein Neu-Packen ist.
        finale = (
            daten["drache"].besiegt
            and not quest.hat_notiz(conn(), jahr, "kiste_finale")
            and (not schon or quest.vor_dem_sieg_gepackt(conn(), jahr))
        )
        # Das Finale nach einem Notausgang ist ein Nachtrag: eigener Name für die Zip
        nachtrag = None
        if (finale and schon) or quest.hat_notiz(conn(), jahr, "kiste_finale"):
            nachtrag = nachtrag_aus(jahr, quest.halbe_sendungen(conn(), jahr))
        if nachtrag:
            # Die halbe Kiste liegt sonst gleich daneben und wird aus Versehen angehängt
            alt = truhe_pfad(jahr, daten["profil"], vollstaendig=False)
            if alt.exists():
                alt.replace(alt.with_name(alt.stem + "-erste-Sendung.zip"))
        ziel = truhe.packen(
            truhe_pfad(jahr, daten["profil"], vollstaendig=nachtrag is not None),
            daten["profil"],
            daten["gebiete"],
            daten["stuecke"],
            quest.dateien(conn(), jahr),
            kennwort,
            quest.verweise(conn(), jahr),
            nachtrag,
        )
        if kennwort and schon:
            # Lena (Runde 35): Das neue Passwort stand zwei Bildschirme tiefer, ungenannt
            app.state.steuer_passwort[jahr] = kennwort
            flash(
                "Neu gepackt, mit neuem Passwort. Schick es deiner Beraterin noch einmal,"
                " das alte passt nicht mehr.",
                "erfolg",
                None if finale else "done",  # Lena (Runde 47): im Finale klingt nur das Siegel
            )
        elif kennwort:
            app.state.steuer_passwort[jahr] = kennwort
            if schon and not finale:  # sonst sagt es das Finale, mit Ton auf dem Siegel
                flash("Die Fundkiste ist gepackt und mit Passwort verschlossen.", "erfolg", "done")
        else:
            app.state.steuer_passwort.pop(jahr, None)
            if schon and not finale:
                flash("Die Fundkiste ist gepackt, ohne Passwort.", "erfolg", "done")
        quest.notiere(conn(), jahr, "truhe", ziel.name)
        quest.kiste_gepackt(conn(), jahr)
        if not schon or finale:
            app.state.steuer_gepackt.add(jahr)
        if finale:
            quest.notiere(conn(), jahr, "kiste_finale", ziel.name)
        return back(f"/steuer/{jahr}/sieg#kiste")

    @app.post("/steuer/{jahr}/beraterin/daten", dependencies=guarded)
    async def beraterin_daten(request: Request, jahr: int):
        form = await request.form()
        p = profil_oder_404(jahr)
        beraterin_merken(p, form)
        p.save(quest.profil_datei(ws.root, jahr))
        # Beim Tippen speichert die Karte still alle 700 ms; geteilt wird erst beim Verlassen
        # des Felds (x-frits-teilen), sonst landeten halbe Werte in anderen Jahren (Lena, R50)
        if not request.headers.get("x-frits-leise") or request.headers.get("x-frits-teilen"):
            beraterin_teilen(jahr, p)
        if request.headers.get("x-frits-leise"):
            return JSONResponse({"ok": True})  # automatisch gespeichert, ohne Seitenwechsel
        if p.beraterin_termin:
            flash(f"Gemerkt: Abgabe bis {schoenes_datum(p.beraterin_termin)}.")
        else:
            flash("Gemerkt.")
        return back(str(form.get("back") or f"/steuer/{jahr}/beraterin"))

    @app.post("/steuer/{jahr}/truhe/zeigen", dependencies=guarded)
    def truhe_zeigen(request: Request, jahr: int):
        p = profil_oder_404(jahr)
        zip_pfad = truhe_pfad(jahr, p)
        if request.headers.get("x-frits-leise"):
            # Lena (Runde 49): vom Brief aus ohne Neuladen, sonst ging der geänderte Text verloren
            gezeigt = zip_pfad.exists() and system.reveal_path(zip_pfad)
            # Lena (Runde 53): Am Handy war der Pfad 5 Zeilen hoch; „~“ statt des Benutzerordners
            anzeige = str(zip_pfad)
            try:
                anzeige = "~/" + str(zip_pfad.relative_to(Path.home()))
            except ValueError:
                pass
            return JSONResponse({"ok": bool(gezeigt), "pfad": str(zip_pfad), "anzeige": anzeige})
        if not zip_pfad.exists():
            flash("Pack zuerst die Fundkiste.")
        elif not system.reveal_path(zip_pfad):
            flash(f"Deine Fundkiste liegt hier: {zip_pfad}")
        return back(f"/steuer/{jahr}/sieg")

    @app.post("/steuer/{jahr}/mail/merken", dependencies=guarded)
    async def mail_merken(request: Request, jahr: int):
        """Lena (Runde 51): Wer den Brief ändert und neu lädt, verlor den Text, außer er drückte
        noch einmal „In Mail öffnen“. Jetzt merkt FRITS ihn still beim Verlassen des Felds."""
        form = await request.form()
        profil_oder_404(jahr)
        mail_text_merken(
            jahr,
            str(form.get("betreff") or ""),
            str(form.get("text") or ""),
            str(form.get("kennung") or ""),
        )
        return JSONResponse({"ok": True})

    def basis_kennung(basis: tuple[str, str]) -> str:
        """Kurze Kennung der Mail, die FRITS schrieb; der Brief schickt sie beim Merken mit."""
        return hashlib.sha256("\x00".join(basis).encode()).hexdigest()[:16]

    def mail_text_merken(jahr: int, betreff: str, text: str, kennung: str = "") -> None:
        """Merkt den eigenen Brieftext. Galt der alte für eine andere Mail (vor dem Finale),
        bleibt er als „früher“ greifbar, auch wenn man den neuen Brief ändert (Lena, R52).
        Lena (Runde 53): Über zwei Wechsel (Notausgang, dann Finale) blieb nur die letzte
        Fassung. Jetzt bleiben die letzten drei, neueste zuerst. Kommt der Text von einer
        Seite mit altem Brief (kennung passt nicht, etwa beim Wegklicken zu „Neu packen“),
        ist er eine frühere Fassung und überschreibt den neuen Brief nicht."""
        basis = mail_basis(jahr, lage(jahr))
        vorher = app.state.steuer_mailtext.get(jahr) or {}
        frueher = list(vorher.get("frueher", []))
        neuer_brief = bool(vorher) and vorher["basis"] != basis
        veraltet = bool(kennung) and kennung != basis_kennung(basis)

        def dazu(alt: str) -> None:
            if alt.strip() and alt not in frueher:
                frueher.insert(0, alt)
            del frueher[3:]

        if veraltet:
            # Der Text kam von einer Seite mit dem alten Brief, etwa beim Wegklicken zu
            # „Neu packen“. Er ersetzt die alte eigene Fassung, nie den neuen Brief.
            if neuer_brief and (betreff, text) != vorher["basis"]:
                dazu(text)
            elif neuer_brief and vorher["eigen"] != vorher["basis"]:
                dazu(vorher["eigen"][1])
            eigen = vorher["eigen"] if vorher and not neuer_brief else basis
        else:
            if neuer_brief and vorher["eigen"] != vorher["basis"]:
                dazu(vorher["eigen"][1])
            eigen = (betreff, text)
        if eigen == basis and not frueher:
            app.state.steuer_mailtext.pop(jahr, None)  # wieder wie von FRITS: nichts zu merken
        else:
            app.state.steuer_mailtext[jahr] = {"basis": basis, "eigen": eigen, "frueher": frueher}

    @app.post("/steuer/{jahr}/mail", dependencies=guarded)
    async def mail_oeffnen(request: Request, jahr: int):
        form = await request.form()
        p = profil_oder_404(jahr)
        if form.get("oeffnen") != "1":
            # Lena (Runde 52): Ein Skript, das den Brief absendete, öffnete ohne Klick die Mail.
            # Mail-Programm und Zip gehen nur mit dem Knopf „In Mail öffnen“ auf.
            mail_text_merken(
                jahr,
                str(form.get("betreff") or ""),
                str(form.get("text") or ""),
                str(form.get("kennung") or ""),
            )
            return back(f"/steuer/{jahr}/sieg#brief")
        zip_pfad = truhe_pfad(jahr, p)
        if not zip_pfad.exists():
            flash("Pack zuerst die Fundkiste, dann hänge ich sie an.")
            return back(f"/steuer/{jahr}/sieg")
        kiste = lage(jahr)["kiste"]
        if kiste and spiel.kiste_aenderung(*kiste) and form.get("alte_kiste") != "1":
            # Lena (Runde 53): Seit dem Packen kam etwas dazu. Mit der alten Kiste geht die
            # Mail nur auf, wenn man das ausdrücklich will (Knopf „Mit der alten Kiste“).
            mail_text_merken(
                jahr,
                str(form.get("betreff") or ""),
                str(form.get("text") or ""),
                str(form.get("kennung") or ""),
            )
            flash(
                "Seit dem Packen hat sich etwas geändert. Pack die Fundkiste erst neu, sonst "
                "bekommt deine Beraterin die alte.",
                "warn",
                link=(f"/steuer/{jahr}/sieg#kiste", "Zur Fundkiste"),
            )
            return back(f"/steuer/{jahr}/sieg#kiste")
        an = str(form.get("an") or "").strip()
        if an and an != p.beraterin_email:
            p.beraterin_email = an
            p.save(quest.profil_datei(ws.root, jahr))
        betreff = str(form.get("betreff") or f"Steuerunterlagen {jahr}")
        text = str(form.get("text") or "")
        basis = mail_entwurf(jahr, lage(jahr), truhe.verschluesselt(zip_pfad))
        mail_text_merken(jahr, betreff, text)
        result = system.compose_email(p.beraterin_email, betreff, text, zip_pfad)
        if result.opened:
            app.state.steuer_mailto.pop(jahr, None)
            flash("Die Mail ist in deinem Mail-Programm. Du drückst dort auf Senden.", cue="sent")
            return back(f"/steuer/{jahr}/sieg")
        # Lena (Runde 48): Der Ausweg führte auf die Rechnungsseite („Das PDF hängst du selbst
        # an“, „Rechnungsordner öffnen“). Er bleibt jetzt am Brief, mit Weg zur Zip.
        app.state.steuer_mailto[jahr] = {"basis": basis}
        return back(f"/steuer/{jahr}/sieg#brief")

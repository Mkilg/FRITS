"""Fritte als Haustier: she feels good when the paperwork has a steady rhythm.

Calm on purpose (Fritz likes slow productivity): Fritte is never sad, never ill and never
gone. After a quiet stretch she just dozes with her scarf and book, and wakes up happy.
What counts is the weekly rhythm, not a daily streak.

Salzkörner, cards and what she owns live in the game tables (frits.spiel.stand).
This module is about her: her mood, her room, her hats.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from frits import store
from frits.spiel import ausbau, stand
from frits.spiel.stand import SpielFehler as LadenFehler

MUNTER_BIS = 4  # days since the last paperwork: up to here she's lively
GEMUETLICH_BIS = 11  # then cosy; after that she dozes
WOCHEN = 4  # how many weeks the rhythm dots show


@dataclass(frozen=True)
class Ding:
    """Something from the little shop. x and y are percent from the left and from the floor."""

    id: str
    name: str
    preis: int
    zeichen: str  # stands in until a painted picture "ding-<id>" is there
    text: str
    art: str  # "zimmer" or "hut"
    x: float = 50
    y: float = 8
    groesse: float = 2.4  # size, relative to the room's width
    artikel: str = "die"  # der | die | das, for "Die Teetasse hat jetzt einen Platz"


# fmt: off
LADEN: tuple[Ding, ...] = (
    Ding("teetasse", "Teetasse", 5, "🍵", "Für eine ruhige Pause zwischendurch.", "zimmer", 63, 7, 1.8),
    Ding("ketchup", "Ketchup-Flasche", 8, "🥫", "Für den kleinen Hunger.", "zimmer", 71, 7, 2.0),
    Ding("pflanze", "Topfpflanze", 10, "🪴", "Wächst langsam, aber stetig. Wie gute Arbeit.", "zimmer", 91, 6, 3.2),
    Ding("buecher", "Bücherstapel", 12, "📚", "Etwas zum Schmökern am Abend.", "zimmer", 37, 6, 2.2, artikel="der"),
    Ding("muetze", "Schirmmütze", 15, "🧢", "Sitzt locker, für gemütliche Tage.", "hut"),
    Ding("kerze", "Kerze", 15, "🕯️", "Steht auf der Fensterbank und macht es warm.", "zimmer", 18, 44, 1.6),
    Ding("uhr", "Wanduhr", 18, "🕰️", "Tickt leise. Hier hetzt niemand.", "zimmer", 86, 62, 2.0),
    Ding("radio", "Radio", 20, "📻", "Ein altes Radio. Spielt leise Musik zum Konzentrieren.", "zimmer", 80, 7, 2.2, artikel="das"),
    Ding("sonnenhut", "Sonnenhut", 20, "👒", "Für den Sommer und gute Laune.", "hut", artikel="der"),
    Ding("bild", "Bild", 25, "🖼️", "Eine Landschaft im Holzrahmen, für die Wand.", "zimmer", 66, 60, 2.6, artikel="das"),
    Ding("sessel", "Lesesessel", 35, "🛋️", "Der beste Platz für den Feierabend.", "zimmer", 13, 4, 4.2, artikel="der"),
    Ding("zylinder", "Zylinder", 40, "🎩", "Für feierliche Momente, zum Beispiel die Steuererklärung.", "hut", artikel="der"),
    Ding("katze", "Katze", 45, "🐈", "Eine kleine Katze. Schläft meistens, ein gutes Vorbild.", "zimmer", 27, 4, 2.4),
    Ding("doktorhut", "Doktorhut", 50, "🎓", "Für alle, die ihre Buchhaltung im Griff haben.", "hut", artikel="der"),
    Ding("krone", "Papierkrone", 60, "👑", "Selbst gefaltet, aus einem alten Kassenbon.", "hut"),
)
# fmt: on
DINGE = {d.id: d for d in LADEN}

# Which painted Fritte goes with which feeling (the pictures from static/bilder).
BILD = {
    "neu": "welcome",
    "munter": "welcome",
    "gemuetlich": "thinking",
    "doest": "feierabend",
    "freut_sich": "done",
}
LAUNE_NAME = {
    "neu": "neugierig",
    "munter": "munter",
    "gemuetlich": "gemütlich",
    "doest": "döst",
    "freut_sich": "freut sich",
}
SAETZE = {
    "neu": "Hallo! Ich wohne jetzt hier. Wenn du Papierkram erledigst, sammle ich Salzkörner.",
    "munter": "Mir geht's prima. Danke, dass du so schön dranbleibst.",
    "gemuetlich": "Mir geht's gut, ich lese ein bisschen. Wann immer du Zeit hast, bin ich da.",
    "doest": "Ich hab ein kleines Nickerchen gemacht. Schön, dass du da bist. Keine Eile.",
}


@dataclass
class Fritte:
    """Everything the page needs to show her."""

    laune: str
    satz: str
    bild: str  # mascot mood: welcome | thinking | done | feierabend
    guthaben: int
    wartend: int  # cards waiting for the Kassensturz
    tage_ruhig: int | None  # days since the last paperwork, None if there was none yet
    wochen: list[bool]  # oldest first, the last one is this week
    zimmer: list[Ding]
    hut: Ding | None
    gekauft: set[str]
    stufe: int = 1  # how far her house is built out
    bau: str = ""  # name of the stage being built, if any

    @property
    def laune_name(self) -> str:
        return LAUNE_NAME[self.laune]


def ansehen(conn: sqlite3.Connection, heute: date, alte_datei: Path | None = None) -> Fritte:
    """How Fritte is today. Also turns new paperwork into waiting cards."""
    wartend = stand.abgleichen(conn, heute, alte_datei)
    tage = stand.pflegetage(conn, heute)
    tage_ruhig = (heute - max(tage)).days if tage else None
    if tage_ruhig is None:
        laune = "neu"
    elif tage_ruhig < MUNTER_BIS:
        laune = "munter"
    elif tage_ruhig < GEMUETLICH_BIS:
        laune = "gemuetlich"
    else:
        laune = "doest"

    montag = heute - timedelta(days=heute.weekday())
    wochen = []
    for n in range(WOCHEN - 1, -1, -1):
        start = montag - timedelta(weeks=n)
        wochen.append(any(start <= d < start + timedelta(days=7) for d in tage))

    gekauft = [d for d in stand.besitz(conn, "ding") if d in DINGE]
    hut = stand.einstellung(conn, "hut")
    return Fritte(
        laune=laune,
        satz=SAETZE[laune],
        bild=BILD[laune],
        guthaben=stand.guthaben(conn),
        wartend=wartend,
        tage_ruhig=tage_ruhig,
        wochen=wochen,
        zimmer=[DINGE[d] for d in gekauft if DINGE[d].art == "zimmer"],
        hut=DINGE[hut] if hut in gekauft and DINGE[hut].art == "hut" else None,
        gekauft=set(gekauft),
        stufe=ausbau.stufe(conn).nummer,
        bau=bau.name if (bau := ausbau.im_bau(conn)) else "",
    )


# ---------- changing things ----------


def kaufen(conn: sqlite3.Connection, heute: date, ding_id: str) -> Ding:
    ding = DINGE.get(ding_id)
    if ding is None:
        raise LadenFehler("Das gibt es im Laden nicht.")
    stand.vorbereiten(conn)
    with store.transaction(conn):
        if stand.besitzt(conn, ding.id):
            raise LadenFehler(f"{ding.name}: hat Fritte schon.")
        stand.bezahlen(conn, heute.isoformat(), ding.preis, f"{ding.name} gekauft")
        stand.nimm(conn, ding.id, "ding", heute.isoformat(), ding.preis)
        if ding.art == "hut":
            stand.setze(conn, "hut", ding.id)  # a new hat goes straight on
    return ding


def aufsetzen(conn: sqlite3.Connection, ding_id: str) -> None:
    """Put a hat on, or take it off again (ding_id "" or the hat she already wears)."""
    stand.vorbereiten(conn)
    ding = DINGE.get(ding_id)
    traegt = stand.einstellung(conn, "hut")
    if ding and ding.art == "hut" and stand.besitzt(conn, ding.id) and traegt != ding.id:
        stand.setze(conn, "hut", ding.id)
    else:
        stand.setze(conn, "hut", "")

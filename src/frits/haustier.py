"""Fritte als Haustier: she feels good when the paperwork has a steady rhythm.

Calm on purpose (Fritz likes slow productivity): Fritte is never sad, never ill and never
gone. After a quiet stretch she just dozes with her scarf and book, and wakes up happy.
What counts is the weekly rhythm, not a daily streak.

Salzkörner, cards and what she owns live in the game tables (frits.spiel.stand).
This module is about her: her mood, her room, her hats.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from frits import store
from frits.spiel import ausbau, kassensturz, saison, stand
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
    truhe: str = ""  # only found in chests, with this rarity (never in the shop)


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
# Only in Frittes Truhen. Their worth is the story, not the price.
SCHAETZE: tuple[Ding, ...] = (
    Ding("muschel", "Muschel", 0, "🐚", "Rauscht ein bisschen, wenn man genau hinhört.", "zimmer", 43, 4, 1.4, truhe="gewöhnlich"),
    Ding("postkarte", "Postkarte", 0, "💌", "Ein Gruß von irgendwo, mit Reißzwecke an der Wand.", "zimmer", 76, 67, 1.6, truhe="gewöhnlich"),
    Ding("schleife", "Rote Schleife", 0, "🎀", "Klein, aber fein. Für gute Tage.", "hut", truhe="gewöhnlich"),
    Ding("kleeblatt", "Glücksklee", 0, "🍀", "Vierblättrig, aus einer Truhe. Steht auf der Fensterbank.", "zimmer", 25, 44, 1.6, artikel="der", truhe="selten"),
    Ding("schatzkarte", "Schatzkarte", 0, "🗺️", "Eine alte Karte für die Wand. Das Kreuz markiert die Kasse.", "zimmer", 36, 60, 2.4, truhe="selten"),
    Ding("seemannsmuetze", "Seemannsmütze", 0, "⚓", "Für Fritte, die Schatzsucherin.", "hut", truhe="selten"),
    Ding("waage", "Goldene Waage", 0, "⚖️", "Soll und Haben, immer im Gleichgewicht.", "zimmer", 10, 44, 1.8, truhe="legendär"),
    Ding("sternenhut", "Sternenhut", 0, "🌟", "Glänzt ein bisschen. Nur aus einer legendären Truhe.", "hut", artikel="der", truhe="legendär"),
)
# fmt: on
DINGE = {d.id: d for d in (*LADEN, *SCHAETZE)}

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
    bereit: int  # about how many Salzkörner the waiting cards would bring right now
    tage_ruhig: int | None  # days since the last paperwork, None if there was none yet
    wochen: list[bool]  # oldest first, the last one is this week
    zimmer: list[Ding]
    hut: Ding | None
    gekauft: set[str]
    stufe: int = 1  # how far her house is built out
    bau: str = ""  # name of the stage being built, if any
    ort: int = 1  # which of her places (1 = am Markt)
    pokale: list[str] = field(default_factory=list)  # seasons whose goal was reached
    fest: str = ""  # where this season's festival was held, if it was

    @property
    def laune_name(self) -> str:
        return LAUNE_NAME[self.laune]


def ansehen(conn: sqlite3.Connection, heute: date, alte_datei: Path | None = None) -> Fritte:
    """How Fritte is today. Also turns new paperwork into waiting cards."""
    altlasten(conn, heute)
    wartend = stand.abgleichen(conn, heute, alte_datei)
    bereit = kassensturz.vorschau(conn, heute, aufgeraeumt=False)[1]["summe"] if wartend else 0
    if wartend:  # the preview drops cards whose paperwork was deleted meanwhile
        wartend = stand.wartende(conn)
    tage = stand.pflegetage(conn, heute)
    tage_ruhig = (heute - max(tage)).days if tage else None
    erster_besuch = not conn.execute("SELECT 1 FROM spiel_kassenstuerze LIMIT 1").fetchone()
    if tage_ruhig is None or erster_besuch:
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
        bereit=bereit,
        tage_ruhig=tage_ruhig,
        wochen=wochen,
        zimmer=[DINGE[d] for d in gekauft if DINGE[d].art == "zimmer"],
        hut=DINGE[hut] if hut in gekauft and DINGE[hut].art == "hut" else None,
        gekauft=set(gekauft),
        stufe=ausbau.stufe(conn).nummer,
        bau=bau.name if (bau := ausbau.im_bau(conn)) else "",
        ort=ausbau.ort(conn).nummer,
        pokale=saison.pokale(conn),
        fest=stand.einstellung(conn, f"fest:{heute.year}-Q{saison.quartal(heute)[0]}"),
    )


# ---------- changing things ----------


def kaufen(conn: sqlite3.Connection, heute: date, ding_id: str) -> Ding:
    ding = DINGE.get(ding_id)
    if ding is None or ding.truhe:
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


def salz_aus_quest(conn: sqlite3.Connection, jahr: int, gold: int, text: str,
                   heute: date | None = None) -> int:  # fmt: skip
    """One purse (Lena): Drachengold only counts inside its Steuer-Quest year. What is left
    when the dragon is beaten becomes Salzkörner for Fritte: 2 gold = 1 Korn, at most 60
    (a victory should help with the house, not buy it: the Stube costs 150).

    Returns the Körner booked. Once per quest year: a second call, or too little gold,
    books nothing and returns 0. When it returns more than 0, the whole rest of that
    year's gold is used up. Opens its own transaction (call it outside the quest's).
    Works even before Fritte was ever opened."""
    koerner = salz_fuer_gold(gold)
    if koerner <= 0:
        return 0
    tag = (heute or date.today()).isoformat()
    stand.vorbereiten(conn)
    with store.transaction(conn):
        if stand.einstellung(conn, f"drachensieg_salz:{jahr}"):
            return 0
        stand.setze(conn, f"drachensieg_salz:{jahr}", tag)
        stand.buche(
            conn, tag, koerner, "drachensieg", text or f"Drachengold aus der Steuer-Quest {jahr}"
        )
    return koerner


GOLD_PRO_KORN = 2
SALZ_PRO_SIEG = 30  # Lena: with 60, day one already bought the Stube (150)


def salz_fuer_gold(gold: int) -> int:
    """How many Salzkörner this much leftover dragon gold becomes (for the victory page)."""
    return max(0, min(int(gold) // GOLD_PRO_KORN, SALZ_PRO_SIEG))


# What the finds that were removed in round 10 are worth now (Lena: no find is lost).
ALT_WUERZE_KORN = 10  # one Würze from a jar


def altlasten(conn: sqlite3.Connection, heute: date) -> int:
    """Once: older games still hold finds that no longer exist (Jokerkarten, jars of
    Truhenwürze and Drachenwürze). They become Salzkörner, one line in the Kassenbuch
    each, and the old jars and cards are cleared. Returns the Körner booked."""
    stand.vorbereiten(conn)
    if stand.einstellung(conn, "umstellung:runde10"):
        return 0
    with store.transaction(conn):
        if stand.einstellung(conn, "umstellung:runde10"):
            return 0
        tag = heute.isoformat()
        summe = 0
        # A Jokerkarte still waiting would never be scored now: it is paid out instead.
        # Scored ones were already paid at their Kassensturz.
        for (schluessel,) in stand.rows(
            conn, "SELECT schluessel FROM spiel_karten WHERE art = 'joker' AND kassensturz IS NULL"
        ):
            nummer = schluessel.split(":", 1)[1]
            row = stand.rows(
                conn, "SELECT inhalt FROM spiel_truhen WHERE id = ?", (int(nummer),)
            ) if nummer.isdigit() else []  # fmt: skip
            if row and row[0][0]:
                summe += int(json.loads(row[0][0]).get("wert", 0))
        conn.execute("DELETE FROM spiel_karten WHERE art = 'joker' AND kassensturz IS NULL")
        wuerze = float(stand.einstellung(conn, "truhen_wuerze", "0") or 0)
        wuerze += float(stand.einstellung(conn, "drachen_wuerze", "0") or 0)
        summe += round(wuerze * ALT_WUERZE_KORN)
        stand.setze(conn, "truhen_wuerze", "0")
        stand.setze(conn, "drachen_wuerze", "0")
        if summe > 0:
            stand.buche(conn, tag, summe, "umstellung",
                        "Ausgleich für Jokerkarten und Würzgläser, die es nicht mehr gibt")  # fmt: skip
        stand.setze(conn, "umstellung:runde10", tag)
    return summe

"""Fritte als Haustier: she feels good when the paperwork has a steady rhythm.

Calm on purpose (Fritz likes slow productivity): Fritte is never sad, never ill and never
gone. After a quiet stretch she just dozes with her scarf and book, and wakes up happy.
What counts is the weekly rhythm, not a daily streak.

Salzkörner (salt grains) are earned from real paperwork. They are counted from the
records FRITS already keeps (invoices, payments, quotes, reminders, expenses), so Fritz
also gets grains for work he did before this feature existed. Only what he bought,
what she wears and the Feierabend days live in fritte.toml next to frits.db.
"""

from __future__ import annotations

import sqlite3
import tomllib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import tomli_w

# How many Salzkörner each kind of paperwork brings.
PUNKTE = {
    "rechnung": 3,  # an invoice got its number
    "bezahlt": 2,  # a payment was recorded
    "angebot": 2,
    "erinnerung": 1,  # a friendly payment reminder
    "ausgabe": 1,
    "feierabend": 1,  # the shutdown ritual, once a day
}
PUNKTE_TEXT = (
    ("Rechnung geschrieben", PUNKTE["rechnung"]),
    ("Zahlung eingetragen", PUNKTE["bezahlt"]),
    ("Angebot geschrieben", PUNKTE["angebot"]),
    ("Zahlungserinnerung", PUNKTE["erinnerung"]),
    ("Ausgabe eingetragen", PUNKTE["ausgabe"]),
    ("Feierabend gemacht (einmal am Tag)", PUNKTE["feierabend"]),
)

MUNTER_BIS = 4  # days since the last paperwork: up to here she's lively
GEMUETLICH_BIS = 11  # then cosy; after that she dozes
WOCHEN = 4  # how many weeks the rhythm dots show
TAGE_MERKEN = 70  # care days kept in fritte.toml


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
class Zustand:
    """What lives in fritte.toml."""

    gekauft: list[str] = field(default_factory=list)
    traegt: str = ""  # id of the hat she wears, or ""
    ausgegeben: int = 0
    feierabende: int = 0
    letzter_feierabend: str = ""
    pflegetage: list[str] = field(default_factory=list)  # ISO days with paperwork
    punkte_gesehen: int = 0  # grains earned when Fritz last looked

    @classmethod
    def laden(cls, path: Path) -> Zustand:
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            return cls()  # a broken file must never break the Heute page
        known = cls.__dataclass_fields__
        z = cls(**{k: v for k, v in data.items() if k in known})
        z.gekauft = [d for d in z.gekauft if d in DINGE]
        if z.traegt not in z.gekauft or DINGE[z.traegt].art != "hut":
            z.traegt = ""
        return z

    def speichern(self, path: Path) -> None:
        data = {k: getattr(self, k) for k in self.__dataclass_fields__}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(tomli_w.dumps(data), encoding="utf-8")


@dataclass
class Fritte:
    """Everything the page needs to show her."""

    laune: str
    satz: str
    bild: str  # mascot mood: welcome | thinking | done | feierabend
    verdient: int
    guthaben: int
    neu_verdient: int  # since the last visit
    erster_besuch: bool  # then neu_verdient is a welcome gift for the work done so far
    tage_ruhig: int | None  # days since the last paperwork, None if there was none yet
    wochen: list[bool]  # oldest first, the last one is this week
    zimmer: list[Ding]
    hut: Ding | None
    gekauft: set[str]

    @property
    def laune_name(self) -> str:
        return LAUNE_NAME[self.laune]


# ---------- counting the paperwork ----------


def _rows(conn: sqlite3.Connection, sql: str) -> list:
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.OperationalError:  # e.g. no expenses table yet
        return []


def _zahl(conn, sql: str) -> int:
    rows = _rows(conn, sql)
    return int(rows[0][0] or 0) if rows else 0


def verdiente_punkte(conn: sqlite3.Connection, zustand: Zustand) -> int:
    return (
        PUNKTE["rechnung"] * _zahl(conn, "SELECT COUNT(*) FROM invoices WHERE cancels IS NULL")
        + PUNKTE["bezahlt"]
        * _zahl(conn, "SELECT COUNT(*) FROM invoices WHERE paid_date IS NOT NULL")
        + PUNKTE["angebot"] * _zahl(conn, "SELECT COUNT(*) FROM quotes")
        + PUNKTE["erinnerung"] * _zahl(conn, "SELECT COUNT(*) FROM reminders")
        + PUNKTE["ausgabe"] * _zahl(conn, "SELECT COUNT(*) FROM expenses")
        + PUNKTE["feierabend"] * zustand.feierabende
    )


def _tage_aus_db(conn: sqlite3.Connection) -> set[date]:
    days = set()
    for sql in (
        "SELECT issue_date FROM invoices",
        "SELECT paid_date FROM invoices",
        "SELECT emailed FROM invoices",
        "SELECT created FROM quotes",
        "SELECT created FROM reminders",
        "SELECT received FROM jobs",
    ):
        for (value,) in _rows(conn, sql):
            try:
                days.add(date.fromisoformat(str(value)[:10]))
            except (TypeError, ValueError):
                continue
    return days


def _pflegetage(conn, zustand: Zustand, heute: date) -> set[date]:
    days = _tage_aus_db(conn)
    for value in zustand.pflegetage:
        try:
            days.add(date.fromisoformat(value))
        except ValueError:
            continue
    return {d for d in days if d <= heute}


def _merke_tag(zustand: Zustand, heute: date) -> None:
    tag = heute.isoformat()
    if tag not in zustand.pflegetage:
        zustand.pflegetage.append(tag)
    zustand.pflegetage = sorted(zustand.pflegetage)[-TAGE_MERKEN:]


# ---------- what the page shows ----------


def ansehen(conn: sqlite3.Connection, path: Path, heute: date, merken: bool = True) -> Fritte:
    """How Fritte is today. With merken=True this visit counts as "seen" for the
    "+5 seit deinem letzten Besuch" note, and new paperwork marks today as a care day."""
    erster_besuch = not Path(path).exists()
    zustand = Zustand.laden(path)
    verdient = verdiente_punkte(conn, zustand)
    neu = max(0, verdient - zustand.punkte_gesehen)
    if merken and (erster_besuch or verdient != zustand.punkte_gesehen):
        # Something new since last time (an expense, say, which has no date of its own).
        # On the very first visit the grains are for older work, so today doesn't count.
        if verdient > zustand.punkte_gesehen and not erster_besuch:
            _merke_tag(zustand, heute)
        zustand.punkte_gesehen = verdient
        zustand.speichern(path)

    tage = _pflegetage(conn, zustand, heute)
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

    return Fritte(
        laune=laune,
        satz=SAETZE[laune],
        bild=BILD[laune],
        verdient=verdient,
        guthaben=max(0, verdient - zustand.ausgegeben),
        neu_verdient=neu,
        erster_besuch=erster_besuch,
        tage_ruhig=tage_ruhig,
        wochen=wochen,
        zimmer=[DINGE[d] for d in zustand.gekauft if DINGE[d].art == "zimmer"],
        hut=DINGE.get(zustand.traegt),
        gekauft=set(zustand.gekauft),
    )


# ---------- changing things ----------


class LadenFehler(Exception):
    pass


def kaufen(conn: sqlite3.Connection, path: Path, ding_id: str) -> Ding:
    ding = DINGE.get(ding_id)
    if ding is None:
        raise LadenFehler("Das gibt es im Laden nicht.")
    zustand = Zustand.laden(path)
    if ding.id in zustand.gekauft:
        raise LadenFehler(f"{ding.name}: hat Fritte schon.")
    guthaben = verdiente_punkte(conn, zustand) - zustand.ausgegeben
    if guthaben < ding.preis:
        fehlen = ding.preis - max(0, guthaben)
        raise LadenFehler(
            f"Für {ding.name} fehlen noch {fehlen} Salzkörner. Die kommen ganz von allein."
        )
    zustand.gekauft.append(ding.id)
    zustand.ausgegeben += ding.preis
    if ding.art == "hut":
        zustand.traegt = ding.id  # a new hat goes straight on
    zustand.speichern(path)
    return ding


def aufsetzen(path: Path, ding_id: str) -> None:
    """Put a hat on, or take it off again (ding_id "" or the hat she already wears)."""
    zustand = Zustand.laden(path)
    if ding_id in zustand.gekauft and DINGE[ding_id].art == "hut" and zustand.traegt != ding_id:
        zustand.traegt = ding_id
    else:
        zustand.traegt = ""
    zustand.speichern(path)


def feierabend(path: Path, heute: date) -> bool:
    """The shutdown ritual brings one grain a day. Returns True if this one counted."""
    zustand = Zustand.laden(path)
    if zustand.letzter_feierabend == heute.isoformat():
        return False
    zustand.feierabende += 1
    zustand.letzter_feierabend = heute.isoformat()
    _merke_tag(zustand, heute)
    zustand.speichern(path)
    return True

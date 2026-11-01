"""Saisons: one goal per quarter, gentle like a boss you can't lose to.

Every quarter has a goal and a theme that doubles the base Körner of one kind of card.
The goal is the weekly rhythm itself: paperwork in seven of every ten weeks. It counts
weeks, not Salzkörner, so a holiday, a quiet month or saving cards up changes nothing, and
spices can't bend it. Reaching it brings a bonus and a cup for Fritte's shelf. Missing it
changes nothing: the next season simply starts anew.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from frits.spiel import ausbau, stand

NAMEN = {1: "Winter", 2: "Frühling", 3: "Sommer", 4: "Herbst"}
REIHE = {name: q for q, name in NAMEN.items()}
ZEICHEN = {1: "❄️", 2: "🌱", 3: "☀️", 4: "🍂"}
# The theme follows the year a little: receipts in winter (taxes are near), quotes when
# things are sown, long evenings in summer, invoices at harvest time.
THEMEN = {
    1: ("ausgabe", "Belegzeit: Ausgaben bringen doppelt so viele Körner."),
    2: ("angebot", "Aussaat: Angebote bringen doppelt so viele Körner."),
    3: ("feierabend", "Lange Abende: Feierabende bringen doppelt so viele Körner."),
    4: ("rechnung", "Erntezeit: Rechnungen und Gutschriften bringen doppelt so viele Körner."),
}
# The bonus for reaching the goal grows with the house (in Salzkörner, before the Ruf).
BONI = {1: 30, 2: 75, 3: 175, 4: 300, 5: 500}
ANTEIL = 0.7  # the goal: paperwork in 7 of every 10 weeks, so a holiday never hurts
FEST_PREIS = 400  # a season festival (from the first Frittenbude on), before the Ruf
NICHT_GEZAEHLT = ("start", "feierabend", "aufgabe", "pflege", "korrektur")


@dataclass
class Saison:
    id: str  # "2026-Q4"
    name: str  # "Herbst 2026"
    anfang: date
    ende: date
    ziel: int  # weeks with paperwork
    erreicht: int
    wochen: int  # weeks of the season that count (from the first look on)
    bonus: int
    geschafft: str  # the day it was reached, or ""
    thema: str  # the kind of card that counts double
    thema_text: str
    zeichen: str
    fest: str = ""  # where Fritte held this season's festival, if she did

    @property
    def prozent(self) -> int:
        return min(100, round(100 * self.erreicht / self.ziel)) if self.ziel else 0


def quartal(heute: date) -> tuple[int, date, date]:
    q = (heute.month - 1) // 3 + 1
    anfang = date(heute.year, 3 * q - 2, 1)
    ende = date(heute.year + 1, 1, 1) if q == 4 else date(heute.year, 3 * q + 1, 1)
    return q, anfang, date.fromordinal(ende.toordinal() - 1)


def _montag(tag: date) -> date:
    return tag - timedelta(days=tag.weekday())


def buero_wochen(conn, anfang: date, ende: date) -> int:
    """Weeks with paperwork inside the season, by the days FRITS itself noticed new work
    (never a typed date). The welcome round, Feierabend and corrections don't count."""
    tage = [
        date.fromisoformat(g)
        for (g,) in stand.rows(
            conn,
            "SELECT DISTINCT gesehen FROM spiel_karten WHERE gesehen BETWEEN ? AND ?"
            " AND (kassensturz IS NULL OR kassensturz != 0)"
            f" AND art NOT IN ({', '.join('?' * len(NICHT_GEZAEHLT))})",
            (anfang.isoformat(), ende.isoformat(), *NICHT_GEZAEHLT),
        )
        if g
    ]
    return len({_montag(t) for t in tage})


def jetzt(conn, heute: date) -> Saison:
    """The season of this day. Its goal and bonus are fixed the first time it is looked
    at: a season started late (Fritte just moved in) only asks for the weeks left."""
    stand.vorbereiten(conn)
    q, anfang, ende = quartal(heute)
    sid = f"{heute.year}-Q{q}"
    ziel = int(stand.einstellung(conn, f"saison:{sid}:wochen_ziel", "0") or 0)
    if not ziel:
        ab = max(anfang, _montag(heute))
        wochen = len({_montag(ab + timedelta(days=n)) for n in range((ende - ab).days + 1)})
        ziel = max(1, math.ceil(ANTEIL * wochen))
        stand.setze(conn, f"saison:{sid}:wochen_ziel", str(ziel))
        stand.setze(conn, f"saison:{sid}:wochen", str(wochen))
    wochen = int(stand.einstellung(conn, f"saison:{sid}:wochen", "0") or 0) or ziel
    bonus = int(stand.einstellung(conn, f"saison:{sid}:bonus", "0") or 0)
    if not bonus:
        alt = int(stand.einstellung(conn, f"saison:{sid}:ziel", "0") or 0)  # before weeks
        bonus = (
            alt // 4
            if alt
            else round(BONI[ausbau.stufe(conn).nummer] * (1 + ausbau.RUF_PREIS * ausbau.ruf(conn)))
        )
        stand.setze(conn, f"saison:{sid}:bonus", str(bonus))
    thema, text = THEMEN[q]
    return Saison(
        sid, f"{NAMEN[q]} {heute.year}", anfang, ende, ziel,
        buero_wochen(conn, anfang, ende), wochen, bonus,
        stand.einstellung(conn, f"saison:{sid}:geschafft"), thema, text, ZEICHEN[q],
        stand.einstellung(conn, f"fest:{sid}"),
    )  # fmt: skip


def pruefen(conn, heute: date) -> Saison | None:
    """Inside the Kassensturz transaction: a season's goal was reached. The season just
    over counts too, in case its last weeks were only scored now."""
    vorige = quartal(heute)[1] - timedelta(days=1)
    for tag in (vorige, heute):
        if not stand.einstellung(conn, f"saison:{tag.year}-Q{quartal(tag)[0]}:wochen_ziel"):
            continue  # a season Fritte never saw
        s = jetzt(conn, tag)
        if s.geschafft or s.erreicht < s.ziel:
            continue
        s.geschafft = heute.isoformat()
        stand.setze(conn, f"saison:{s.id}:geschafft", s.geschafft)
        stand.buche(conn, s.geschafft, s.bonus, "saison", f"Saisonziel {s.name} geschafft")
        pokale = [p for p in stand.einstellung(conn, "pokale").split(",") if p]
        stand.setze(conn, "pokale", ",".join([*pokale, s.name]))
        return s
    return None


def pokale(conn) -> list[str]:
    stand.vorbereiten(conn)
    return [p for p in stand.einstellung(conn, "pokale").split(",") if p]


# ---------- festivals: the calm, endless goal ----------


def fest_moeglich(conn) -> bool:
    """From the first Frittenbude on, Fritte can hold one festival a season."""
    return bool(ausbau.ruf(conn)) or ausbau.stufe(conn).nummer >= len(ausbau.STUFEN)


def fest_preis(conn) -> int:
    return round(FEST_PREIS * (1 + ausbau.RUF_PREIS * ausbau.ruf(conn)))


def feiern(conn, heute: date) -> Saison:
    """Hold this season's festival at Fritte's place. Nothing is lost if she doesn't:
    it is a page in her album, a garland in her room, and a use for saved Salzkörner."""
    s = jetzt(conn, heute)
    if not fest_moeglich(conn):
        raise stand.SpielFehler("Ein Fest gibt es ab der ersten Frittenbude.")
    if s.fest:
        raise stand.SpielFehler(f"In diesem {NAMEN[quartal(heute)[0]]} hat Fritte schon gefeiert.")
    ort = ausbau.ort(conn).name
    wo = ort[0].lower() + ort[1:]
    stand.bezahlen(conn, heute.isoformat(), fest_preis(conn), f"{FESTE[quartal(heute)[0]]} {wo}")
    stand.setze(conn, f"fest:{s.id}", ort)
    s.fest = ort
    return s


FESTE = {1: "Winterfest", 2: "Frühlingsfest", 3: "Sommerfest", 4: "Herbstfest"}


def feste(conn) -> list[tuple[str, str, str]]:
    """All festivals so far, oldest first: (season name, festival, place)."""
    stand.vorbereiten(conn)
    out = []
    for key, ort in stand.rows(
        conn, "SELECT schluessel, wert FROM spiel_einstellung WHERE schluessel LIKE 'fest:%'"
    ):
        sid = key.split(":", 1)[1]
        jahr, q = sid.split("-Q")
        out.append((sid, f"{NAMEN[int(q)]} {jahr}", FESTE[int(q)], ort))
    return [(name, fest, ort) for _, name, fest, ort in sorted(out)]

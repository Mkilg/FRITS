"""Saisons: one goal per quarter, gentle like a boss you can't lose to.

Every quarter has a goal in Salzkörner and a theme that doubles the base Körner of one
kind of card. Reaching the goal brings a bonus and a cup for Fritte's shelf. Missing it
changes nothing: the next season simply starts with a new goal. The goal is fixed the
first time the season is looked at, so building out the house mid-season makes it easier,
never harder.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from frits.spiel import ausbau, stand

NAMEN = {1: "Winter", 2: "Frühling", 3: "Sommer", 4: "Herbst"}
ZEICHEN = {1: "❄️", 2: "🌱", 3: "☀️", 4: "🍂"}
# The theme follows the year a little: receipts in winter (taxes are near), quotes when
# things are sown, long evenings in summer, invoices at harvest time.
THEMEN = {
    1: ("ausgabe", "Belegzeit: Ausgaben bringen doppelt so viele Körner."),
    2: ("angebot", "Aussaat: Angebote bringen doppelt so viele Körner."),
    3: ("feierabend", "Lange Abende: Feierabende bringen doppelt so viele Körner."),
    4: ("rechnung", "Erntezeit: Rechnungen bringen doppelt so viele Körner."),
}
ZIELE = {1: 120, 2: 300, 3: 700, 4: 1200, 5: 2000}  # by the house stage at the season's start
BONUS_ANTEIL = 4  # reaching the goal brings a quarter of it on top


@dataclass
class Saison:
    id: str  # "2026-Q4"
    name: str  # "Herbst 2026"
    anfang: date
    ende: date
    ziel: int
    erreicht: int
    geschafft: str  # the day it was reached, or ""
    thema: str  # the kind of card that counts double
    thema_text: str
    zeichen: str

    @property
    def prozent(self) -> int:
        return min(100, round(100 * self.erreicht / self.ziel)) if self.ziel else 0


def quartal(heute: date) -> tuple[int, date, date]:
    q = (heute.month - 1) // 3 + 1
    anfang = date(heute.year, 3 * q - 2, 1)
    ende = date(heute.year + 1, 1, 1) if q == 4 else date(heute.year, 3 * q + 1, 1)
    return q, anfang, date.fromordinal(ende.toordinal() - 1)


def jetzt(conn, heute: date) -> Saison:
    stand.vorbereiten(conn)
    q, anfang, ende = quartal(heute)
    sid = f"{heute.year}-Q{q}"
    ziel = int(stand.einstellung(conn, f"saison:{sid}:ziel", "0") or 0)
    if not ziel:
        faktor = 1 + ausbau.RUF_PREIS * ausbau.ruf(conn)
        ziel = round(ZIELE[ausbau.stufe(conn).nummer] * faktor)
        stand.setze(conn, f"saison:{sid}:ziel", str(ziel))
    erreicht = conn.execute(
        "SELECT COALESCE(SUM(summe), 0) FROM spiel_kassenstuerze WHERE tag BETWEEN ? AND ?",
        (anfang.isoformat(), ende.isoformat()),
    ).fetchone()[0]
    thema, text = THEMEN[q]
    return Saison(
        sid, f"{NAMEN[q]} {heute.year}", anfang, ende, ziel, int(erreicht),
        stand.einstellung(conn, f"saison:{sid}:geschafft"), thema, text, ZEICHEN[q],
    )  # fmt: skip


def pruefen(conn, heute: date) -> Saison | None:
    """Inside the Kassensturz transaction: the season's goal was just reached."""
    s = jetzt(conn, heute)
    if s.geschafft or s.erreicht < s.ziel:
        return None
    s.geschafft = heute.isoformat()
    stand.setze(conn, f"saison:{s.id}:geschafft", s.geschafft)
    stand.buche(
        conn, s.geschafft, s.ziel // BONUS_ANTEIL, "saison", f"Saisonziel {s.name} geschafft"
    )
    pokale = [p for p in stand.einstellung(conn, "pokale").split(",") if p]
    stand.setze(conn, "pokale", ",".join([*pokale, s.name]))
    return s


def pokale(conn) -> list[str]:
    stand.vorbereiten(conn)
    return [p for p in stand.einstellung(conn, "pokale").split(",") if p]

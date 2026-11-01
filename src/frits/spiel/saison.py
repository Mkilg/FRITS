"""Saisons: one goal per quarter, gentle like a boss you can't lose to.

Every quarter has a goal in Salzkörner and a theme that doubles the base Körner of one
kind of card. Reaching the goal brings a bonus and a cup for Fritte's shelf. Missing it
changes nothing: the next season simply starts with a new goal. The goal is fixed the
first time the season is looked at, so building out the house mid-season makes it easier,
never harder.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from frits.spiel import ausbau, stand

NAMEN = {1: "Winter", 2: "Frühling", 3: "Sommer", 4: "Herbst"}
ZEICHEN = {1: "❄️", 2: "🌱", 3: "☀️", 4: "🍂"}
# The theme follows the year a little: receipts in winter (taxes are near), quotes when
# things are sown, long evenings in summer, invoices at harvest time.
THEMEN = {
    1: ("ausgabe", "Belegzeit: Ausgaben bringen doppelt so viele Körner."),
    2: ("angebot", "Aussaat: Angebote bringen doppelt so viele Körner."),
    3: ("feierabend", "Lange Abende: Feierabende bringen doppelt so viele Körner."),
    4: ("rechnung", "Erntezeit: Rechnungen und Gutschriften bringen doppelt so viele Körner."),
}
ZIELE = {1: 120, 2: 300, 3: 700, 4: 1200, 5: 2000}  # when there is no history yet
BONUS_ANTEIL = 4  # reaching the goal brings a quarter of it on top
TEMPO = 0.9  # the goal: 90 % of what the same pace as the eight weeks before would bring
RUECKBLICK_WOCHEN = 8


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
        ziel = _ziel(conn, anfang, ende)
        stand.setze(conn, f"saison:{sid}:ziel", str(ziel))
    erreicht = conn.execute(
        "SELECT COALESCE(SUM(summe), 0) FROM spiel_kassenstuerze WHERE tag BETWEEN ? AND ?"
        " AND id != ?",
        (anfang.isoformat(), ende.isoformat(), _willkommen(conn)),
    ).fetchone()[0]
    thema, text = THEMEN[q]
    return Saison(
        sid, f"{NAMEN[q]} {heute.year}", anfang, ende, ziel, int(erreicht),
        stand.einstellung(conn, f"saison:{sid}:geschafft"), thema, text, ZEICHEN[q],
    )  # fmt: skip


def _willkommen(conn) -> int:
    """The welcome round is a present and doesn't count towards a season."""
    return int(stand.einstellung(conn, "willkommen_ks", "0") or 0)


def _ziel(conn, anfang: date, ende: date) -> int:
    """Fritz's own pace decides: the eight weeks before the season, at 90 %. Only the
    weeks before the season count, so the goal is the same whenever it is first looked
    at, and moving house just before a season changes nothing. Without a history yet,
    the goal of the house stage."""
    von = anfang - timedelta(weeks=RUECKBLICK_WOCHEN)
    erster = conn.execute(
        "SELECT MIN(tag) FROM spiel_kassenstuerze WHERE id != ?", (_willkommen(conn),)
    ).fetchone()[0]
    summe = conn.execute(
        "SELECT COALESCE(SUM(summe), 0) FROM spiel_kassenstuerze WHERE tag >= ? AND tag < ?"
        " AND id != ?",
        (von.isoformat(), anfang.isoformat(), _willkommen(conn)),
    ).fetchone()[0]
    wochen = ((ende - anfang).days + 1) / 7
    if erster and erster <= (anfang - timedelta(weeks=4)).isoformat() and summe:
        tempo = summe / RUECKBLICK_WOCHEN
        return max(ZIELE[1], round(tempo * wochen * TEMPO / 10) * 10)
    return round(ZIELE[ausbau.stufe(conn).nummer] * (1 + ausbau.RUF_PREIS * ausbau.ruf(conn)))


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

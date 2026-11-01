"""Die FRITS-Ernte: holt Rechnungen, Ausgaben-Belege und die EÜR aus dem normalen FRITS
in die Steuer-Quest, damit man nichts doppelt suchen muss.

- Rechnungen: alle, die im Steuerjahr geschrieben oder bezahlt wurden (die PDFs aus invoices/).
- Belege: die Dateien der Ausgaben, die im Steuerjahr bezahlt wurden (aus done/).
- EÜR: rechnet FRITS frisch aus und legt sie als PDF und CSV dazu.
- Für die Beraterin: die Buchungsliste (alle Zahlungen, passend zur EÜR) und die
  Verfahrensdokumentation nach den GoBD (siehe frits.export).

Die Dateien werden kopiert, nie verschoben: Das normale FRITS bleibt, wie es ist.
Dieselbe Datei zweimal wird erkannt, man kann die Ernte also beliebig oft einbringen.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from frits import euer, export
from frits.expenses import list_expenses
from frits.settings import Workspace
from frits.steuer import quest
from frits.steuer.katalog import Gebiet
from frits.store import transaction

# Provinzen, in die FRITS-Daten passen (ein eigener Betrieb oder eine Gesellschaft)
ZIELE = ("werkstatt", "handelskontor", "gilde")
# Welche Sammelstücke in diesen Provinzen welche Rolle haben
ROLLEN = {
    "rechnungen": ("ausgangsrechnungen", "rechnungen"),
    "belege": ("belege",),
    "euer": ("euer",),
    "buchungsliste": ("buchungsliste",),
    "verfahrensdoku": ("verfahrensdoku",),
}
NAMEN = {
    "rechnungen": "Ausgangsrechnungen",
    "belege": "Belege und Quittungen",
    "euer": "EÜR-Übersicht",
    "buchungsliste": "Buchungsliste",
    "verfahrensdoku": "Verfahrensdokumentation (GoBD)",
}
EUER_PRAEFIX = "EUER_"
BUCHUNGSLISTE_PRAEFIX = "Buchungsliste_"
VERFAHRENSDOKU_PRAEFIX = "Verfahrensdokumentation_"


@dataclass
class Ernte:
    jahr: int
    rechnungen: list[Path] = field(default_factory=list)
    belege: list[Path] = field(default_factory=list)
    euer: euer.EuerSummary | None = None
    neu: dict[str, int] = field(default_factory=dict)

    @property
    def leer(self) -> bool:
        return not (self.rechnungen or self.belege or self.euer)

    @property
    def fuer_beraterin(self) -> bool:
        """Buchungsliste und Verfahrensdoku gibt es, sobald FRITS Zahlen für das Jahr hat."""
        return self.euer is not None

    @property
    def neu_gesamt(self) -> int:
        return sum(self.neu.values())

    @property
    def gewinn(self) -> str:
        if self.euer is None:
            return ""
        return euer._euro(self.euer.profit) + " €"


def ziele(gebiete: list[Gebiet]) -> list[Gebiet]:
    """Die Provinzen, in die die Ernte passt. Die erste ist der Vorschlag."""
    return [g for g in gebiete if g.provinz.key in ZIELE]


def _hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rechnungen(db: Path, jahr: int) -> list[Path]:
    conn = sqlite3.connect(db)
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'invoices'"
        ).fetchone():
            return []
        von, bis = f"{jahr:04d}-01-01", f"{jahr + 1:04d}-01-01"
        rows = conn.execute(
            "SELECT pdf_path FROM invoices WHERE pdf_path IS NOT NULL"
            " AND ((issue_date >= ? AND issue_date < ?) OR (paid_date >= ? AND paid_date < ?))"
            " ORDER BY number",
            (von, bis, von, bis),
        ).fetchall()
    finally:
        conn.close()
    return [Path(r[0]) for r in rows if Path(r[0]).is_file()]


def _belege(ws: Workspace, jahr: int) -> list[Path]:
    gesehen, result = set(), []
    for e in list_expenses(ws.db, year=jahr):
        if not e.source_file:
            continue
        pfad = ws.done / e.source_file
        if pfad.is_file() and pfad not in gesehen:
            gesehen.add(pfad)
            result.append(pfad)
    return result


def _euer_dateien(ws: Workspace, summary: euer.EuerSummary) -> tuple[Path, Path]:
    ordner = quest.ordner(ws.root, summary.year) / ".frits"
    ordner.mkdir(parents=True, exist_ok=True)
    csv = euer.write_csv(summary, ordner / f"{EUER_PRAEFIX}{summary.year}.csv")
    pdf = euer.write_pdf(summary, ordner / f"{EUER_PRAEFIX}{summary.year}.pdf")
    return csv, pdf


def _buchungsliste(ws: Workspace, jahr: int) -> Path:
    return export.buchungsliste(ws, jahr, quest.ordner(ws.root, jahr) / ".frits")


def _hat_datei(conn: sqlite3.Connection, jahr: int, praefix: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM steuer_dateien WHERE jahr = ? AND original LIKE ?",
            (jahr, praefix + "%"),
        ).fetchone()
    )


def finden(conn: sqlite3.Connection, ws: Workspace, jahr: int, tax_mode: str) -> Ernte:
    """Was das normale FRITS für dieses Steuerjahr hat, und was davon neu ist."""
    quest.ensure_schema(conn)
    ernte = Ernte(jahr, rechnungen=_rechnungen(ws.db, jahr), belege=_belege(ws, jahr))
    summary = euer.summarize(ws.db, jahr, tax_mode)
    if summary.invoice_count or summary.credit_count or summary.expense_count:
        ernte.euer = summary
    bekannt = {
        r[0] for r in conn.execute("SELECT sha256 FROM steuer_dateien WHERE jahr = ?", (jahr,))
    }
    ernte.neu = {
        "rechnungen": sum(1 for p in ernte.rechnungen if _hash(p) not in bekannt),
        "belege": sum(1 for p in ernte.belege if _hash(p) not in bekannt),
        "euer": 0,
        "buchungsliste": 0,
        "verfahrensdoku": 0,
    }
    if ernte.euer is not None:
        csv, _pdf = _euer_dateien(ws, ernte.euer)
        ernte.neu["euer"] = 0 if _hash(csv) in bekannt else 1
        # Die Liste ändert sich mit jeder neuen Zahlung; die Doku braucht es einmal im Jahr
        ernte.neu["buchungsliste"] = 0 if _hash(_buchungsliste(ws, jahr)) in bekannt else 1
        ernte.neu["verfahrensdoku"] = 0 if _hat_datei(conn, jahr, VERFAHRENSDOKU_PRAEFIX) else 1
    return ernte


def _fach(conn: sqlite3.Connection, jahr: int, gebiet_id: str, rolle: str) -> int:
    """Das Sammelstück für diese Rolle in der Provinz; fehlt es, wird es angelegt."""
    keys = ROLLEN[rolle]
    marks = ", ".join("?" * len(keys))
    row = conn.execute(
        f"SELECT id FROM steuer_stuecke WHERE jahr = ? AND gebiet = ? AND key IN ({marks})"
        " ORDER BY id",
        (jahr, gebiet_id, *keys),
    ).fetchone()
    if row:
        return row[0]
    return quest.neues_stueck(
        conn, jahr, gebiet_id, NAMEN[rolle], herkunft="frits", hinweis="Aus deinem FRITS"
    )


def _alte_euer_weg(conn: sqlite3.Connection, stueck_id: int, praefix: str = EUER_PRAEFIX) -> None:
    """Eine neue EÜR ersetzt die alte, damit nur die aktuelle Zahl in der Truhe liegt.
    Genauso bei der Buchungsliste."""
    alte = conn.execute(
        "SELECT id, pfad FROM steuer_dateien WHERE stueck_id = ? AND original LIKE ?",
        (stueck_id, praefix + "%"),
    ).fetchall()
    with transaction(conn):
        for row in alte:
            Path(row["pfad"]).unlink(missing_ok=True)
            conn.execute("DELETE FROM steuer_dateien WHERE id = ?", (row["id"],))


def einbringen(
    conn: sqlite3.Connection, ws: Workspace, jahr: int, gebiet_id: str, tax_mode: str
) -> dict[str, int]:
    """Kopiert alles Neue aus FRITS in die Provinz. Gibt zurück, wie viel pro Rolle neu war."""
    ernte = finden(conn, ws, jahr, tax_mode)
    neu = {"rechnungen": 0, "belege": 0, "euer": 0, "buchungsliste": 0, "verfahrensdoku": 0}
    for rolle, dateien in (("rechnungen", ernte.rechnungen), ("belege", ernte.belege)):
        if not dateien:
            continue
        fach = _fach(conn, jahr, gebiet_id, rolle)
        for pfad in dateien:
            if quest.sammeln(conn, ws.root, fach, pfad, original=pfad.name).neu:
                neu[rolle] += 1
    if ernte.euer is not None and ernte.neu["euer"]:
        fach = _fach(conn, jahr, gebiet_id, "euer")
        _alte_euer_weg(conn, fach)
        for pfad in _euer_dateien(ws, ernte.euer):
            quest.sammeln(conn, ws.root, fach, pfad, original=pfad.name)
        neu["euer"] = 1
    ablage = quest.ordner(ws.root, jahr) / ".frits"
    if ernte.neu["buchungsliste"]:
        fach = _fach(conn, jahr, gebiet_id, "buchungsliste")
        _alte_euer_weg(conn, fach, BUCHUNGSLISTE_PRAEFIX)
        pfad = export.buchungsliste(ws, jahr, ablage)
        quest.sammeln(conn, ws.root, fach, pfad, original=pfad.name)
        neu["buchungsliste"] = 1
    if ernte.neu["verfahrensdoku"]:
        fach = _fach(conn, jahr, gebiet_id, "verfahrensdoku")
        pfad = export.verfahrensdoku(ws, jahr, ablage)
        quest.sammeln(conn, ws.root, fach, pfad, original=pfad.name)
        neu["verfahrensdoku"] = 1
    return neu


def hat_rechnungen(ws: Workspace, jahr: int) -> bool:
    """Für den Fragebogen: Hat FRITS in diesem Jahr Rechnungen geschrieben?"""
    return bool(_rechnungen(ws.db, jahr))

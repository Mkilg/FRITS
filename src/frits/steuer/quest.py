"""Der Spielstand der Steuer-Quest: welche Sammelstücke es gibt, welche Dateien dazugehören.

Alles liegt in derselben SQLite-Datei wie die Rechnungen (frits.db), die Dateien selbst in
FRITS/steuer/<jahr>/<gebiet>/. Nichts davon verlässt den Computer.

Ein Sammelstück hat genau einen Status:
  offen       noch im Nebel
  gesammelt   mindestens eine Datei ist da
  gibts_nicht gibt es dieses Jahr nicht (zählt als erledigt)
  fragen      landet als Frage in der Mail an die Beraterin (zählt als erledigt)
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from frits.steuer.katalog import Gebiet, Profil, reich
from frits.store import transaction

STATUS = ("offen", "gesammelt", "gibts_nicht", "fragen")

SCHEMA = """
CREATE TABLE IF NOT EXISTS steuer_stuecke (
    id INTEGER PRIMARY KEY,
    jahr INTEGER NOT NULL,
    gebiet TEXT NOT NULL,          -- z. B. "gehoeft-2"
    key TEXT NOT NULL,             -- aus dem Katalog, oder "wunsch-<n>"
    name TEXT NOT NULL,
    hinweis TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'offen',
    herkunft TEXT NOT NULL DEFAULT 'katalog',   -- katalog | beraterin | vorjahr
    wunsch INTEGER NOT NULL DEFAULT 0,          -- 1: die Beraterin hat danach gefragt
    frage TEXT NOT NULL DEFAULT '',             -- eigene Frage an die Beraterin
    geaendert TEXT NOT NULL,
    UNIQUE (jahr, gebiet, key)
);
CREATE TABLE IF NOT EXISTS steuer_dateien (
    id INTEGER PRIMARY KEY,
    stueck_id INTEGER NOT NULL REFERENCES steuer_stuecke(id),
    jahr INTEGER NOT NULL,
    pfad TEXT NOT NULL,
    original TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    hinzugefuegt TEXT NOT NULL,
    UNIQUE (jahr, sha256)
);
CREATE TABLE IF NOT EXISTS steuer_chronik (
    id INTEGER PRIMARY KEY,
    jahr INTEGER NOT NULL,
    zeit TEXT NOT NULL,
    art TEXT NOT NULL,             -- fund | siegel | erfolg | brief | treffer | sieg | truhe
    text TEXT NOT NULL
);
"""


class QuestError(Exception):
    """Etwas ging nicht. Die Nachricht wird so angezeigt, also bitte freundlich formulieren."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.executescript(SCHEMA)
    return conn


def ordner(root: Path, jahr: int) -> Path:
    return Path(root) / "steuer" / str(jahr)


def lager(root: Path) -> Path:
    """Der Ablage-Ordner für neue Steuer-Dokumente (das "Lagerhaus")."""
    return Path(root) / "steuer" / "lager"


def profil_datei(root: Path, jahr: int) -> Path:
    return ordner(root, jahr) / "fragebogen.toml"


# ---------- Sammelstücke ----------


def start(conn: sqlite3.Connection, profil: Profil) -> list[Gebiet]:
    """Legt für alle Gebiete die Sammelstücke aus dem Katalog an.

    Läuft auch erneut, wenn sich der Fragebogen ändert: Neues kommt dazu, und Gebiete,
    die es nicht mehr gibt, verschwinden, solange dort noch nichts gesammelt wurde.
    """
    ensure_schema(conn)
    gebiete = reich(profil)
    ids = {g.id for g in gebiete}
    with transaction(conn):
        for g in gebiete:
            for s in g.provinz.stuecke:
                conn.execute(
                    "INSERT OR IGNORE INTO steuer_stuecke (jahr, gebiet, key, name, hinweis, geaendert)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (profil.jahr, g.id, s.key, s.name, s.hinweis, _now()),
                )
        _vom_vorjahr(conn, profil.jahr, ids)
        rows = conn.execute(
            "SELECT id, gebiet FROM steuer_stuecke WHERE jahr = ?", (profil.jahr,)
        ).fetchall()
        for row in rows:
            if row["gebiet"] in ids:
                continue
            has_files = conn.execute(
                "SELECT 1 FROM steuer_dateien WHERE stueck_id = ?", (row["id"],)
            ).fetchone()
            if not has_files:
                conn.execute("DELETE FROM steuer_stuecke WHERE id = ?", (row["id"],))
    return gebiete


def _vom_vorjahr(conn: sqlite3.Connection, jahr: int, ids: set[str]) -> None:
    """Eigene Sammelstücke vom letzten Jahr (z. B. Wünsche der Beraterin) wieder anlegen."""
    rows = conn.execute(
        "SELECT gebiet, key, name, hinweis FROM steuer_stuecke"
        " WHERE jahr = ? AND herkunft != 'katalog' AND status = 'gesammelt'",
        (jahr - 1,),
    ).fetchall()
    for row in rows:
        if row["gebiet"] not in ids:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO steuer_stuecke"
            " (jahr, gebiet, key, name, hinweis, herkunft, geaendert)"
            " VALUES (?, ?, ?, ?, ?, 'vorjahr', ?)",
            (jahr, row["gebiet"], row["key"], row["name"], "Hattest du letztes Jahr.", _now()),
        )


def stuecke(conn: sqlite3.Connection, jahr: int, gebiet: str | None = None) -> list[dict]:
    ensure_schema(conn)
    sql = (
        "SELECT s.*, (SELECT COUNT(*) FROM steuer_dateien d WHERE d.stueck_id = s.id) AS dateien"
        " FROM steuer_stuecke s WHERE s.jahr = ?"
    )
    args: list = [jahr]
    if gebiet:
        sql += " AND s.gebiet = ?"
        args.append(gebiet)
    return [dict(r) for r in conn.execute(sql + " ORDER BY s.id", args)]


def stueck(conn: sqlite3.Connection, stueck_id: int) -> dict:
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM steuer_stuecke WHERE id = ?", (stueck_id,)).fetchone()
    if not row:
        raise QuestError("Dieses Sammelstück gibt es nicht mehr.")
    return dict(row)


def neues_stueck(
    conn: sqlite3.Connection,
    jahr: int,
    gebiet: str,
    name: str,
    herkunft: str = "beraterin",
    hinweis: str = "",
) -> int:
    ensure_schema(conn)
    with transaction(conn):
        n = conn.execute(
            "SELECT COUNT(*) FROM steuer_stuecke WHERE jahr = ? AND key LIKE 'wunsch-%'", (jahr,)
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO steuer_stuecke"
            " (jahr, gebiet, key, name, hinweis, herkunft, wunsch, geaendert)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                jahr,
                gebiet,
                f"wunsch-{n + 1}",
                name,
                hinweis,
                herkunft,
                1 if herkunft == "beraterin" else 0,
                _now(),
            ),
        )
    return cur.lastrowid


def setze_status(
    conn: sqlite3.Connection, stueck_id: int, status: str, frage: str | None = None
) -> dict:
    if status not in STATUS:
        raise QuestError("Diesen Status kenne ich nicht.")
    alt = stueck(conn, stueck_id)
    if status == "offen" and _anzahl_dateien(conn, stueck_id):
        status = "gesammelt"  # mit Dateien ist es nicht mehr im Nebel
    with transaction(conn):
        conn.execute(
            "UPDATE steuer_stuecke SET status = ?, frage = ?, geaendert = ? WHERE id = ?",
            (status, frage if frage is not None else alt["frage"], _now(), stueck_id),
        )
    return stueck(conn, stueck_id)


def markiere_wunsch(conn: sqlite3.Connection, stueck_id: int) -> None:
    with transaction(conn):
        conn.execute("UPDATE steuer_stuecke SET wunsch = 1 WHERE id = ?", (stueck_id,))


def _anzahl_dateien(conn: sqlite3.Connection, stueck_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM steuer_dateien WHERE stueck_id = ?", (stueck_id,)
    ).fetchone()[0]


# ---------- Dateien sammeln ----------


@dataclass(frozen=True)
class Fund:
    neu: bool  # False: genau diese Datei war schon gesammelt
    pfad: Path
    stueck: dict


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(text: str) -> str:
    table = str.maketrans(
        {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
    )
    text = re.sub(r"[^A-Za-z0-9]+", "-", text.translate(table)).strip("-")
    return text[:60] or "Dokument"


def sammeln(
    conn: sqlite3.Connection,
    root: Path,
    stueck_id: int,
    quelle: Path,
    original: str | None = None,
    verschieben: bool = False,
) -> Fund:
    """Legt eine Datei zu einem Sammelstück und benennt sie ordentlich um.

    Beispiel: 2025_Gehoeft-2_Grundsteuerbescheid.pdf. Dieselbe Datei zweimal wird erkannt.
    """
    quelle = Path(quelle)
    if not quelle.is_file():
        raise QuestError(f"Die Datei {quelle.name} finde ich nicht.")
    s = stueck(conn, stueck_id)
    jahr = s["jahr"]
    sha = _hash(quelle)
    vorhanden = conn.execute(
        "SELECT d.pfad, d.stueck_id FROM steuer_dateien d WHERE d.jahr = ? AND d.sha256 = ?",
        (jahr, sha),
    ).fetchone()
    if vorhanden:
        if verschieben:
            quelle.unlink()
        return Fund(False, Path(vorhanden["pfad"]), stueck(conn, vorhanden["stueck_id"]))

    ziel_ordner = ordner(root, jahr) / s["gebiet"]
    ziel_ordner.mkdir(parents=True, exist_ok=True)
    gebiet_name = s["gebiet"].capitalize()
    basis = f"{jahr}_{_slug(gebiet_name)}_{_slug(s['name'])}"
    endung = quelle.suffix.lower()
    ziel = ziel_ordner / f"{basis}{endung}"
    n = 2
    while ziel.exists():
        ziel = ziel_ordner / f"{basis}_{n}{endung}"
        n += 1
    if verschieben:
        shutil.move(str(quelle), ziel)
    else:
        shutil.copy2(quelle, ziel)
    with transaction(conn):
        conn.execute(
            "INSERT INTO steuer_dateien (stueck_id, jahr, pfad, original, sha256, hinzugefuegt)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (stueck_id, jahr, str(ziel), original or quelle.name, sha, _now()),
        )
        conn.execute(
            "UPDATE steuer_stuecke SET status = 'gesammelt', geaendert = ? WHERE id = ?",
            (_now(), stueck_id),
        )
    return Fund(True, ziel, stueck(conn, stueck_id))


def dateien(conn: sqlite3.Connection, jahr: int, stueck_id: int | None = None) -> list[dict]:
    ensure_schema(conn)
    if stueck_id is None:
        rows = conn.execute("SELECT * FROM steuer_dateien WHERE jahr = ? ORDER BY id", (jahr,))
    else:
        rows = conn.execute(
            "SELECT * FROM steuer_dateien WHERE stueck_id = ? ORDER BY id", (stueck_id,)
        )
    return [dict(r) for r in rows]


def entferne_datei(conn: sqlite3.Connection, datei_id: int) -> None:
    """Nimmt eine falsch zugeordnete Datei wieder heraus und legt sie zurück ins Lager."""
    row = conn.execute("SELECT * FROM steuer_dateien WHERE id = ?", (datei_id,)).fetchone()
    if not row:
        return
    pfad = Path(row["pfad"])
    root = pfad.parents[3]  # FRITS/steuer/<jahr>/<gebiet>/datei
    if pfad.exists():
        ziel = lager(root) / row["original"]
        ziel.parent.mkdir(parents=True, exist_ok=True)
        if ziel.exists():
            ziel = ziel.with_name(f"{ziel.stem}_{row['id']}{ziel.suffix}")
        shutil.move(str(pfad), ziel)
    with transaction(conn):
        conn.execute("DELETE FROM steuer_dateien WHERE id = ?", (datei_id,))
        if not _anzahl_dateien(conn, row["stueck_id"]):
            conn.execute(
                "UPDATE steuer_stuecke SET status = 'offen' WHERE id = ? AND status = 'gesammelt'",
                (row["stueck_id"],),
            )


def lager_dateien(root: Path) -> list[Path]:
    folder = lager(root)
    folder.mkdir(parents=True, exist_ok=True)
    return sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))


# ---------- Chronik ----------


def notiere(conn: sqlite3.Connection, jahr: int, art: str, text: str) -> None:
    ensure_schema(conn)
    with transaction(conn):
        conn.execute(
            "INSERT INTO steuer_chronik (jahr, zeit, art, text) VALUES (?, ?, ?, ?)",
            (jahr, _now(), art, text),
        )


def chronik(conn: sqlite3.Connection, jahr: int, anzahl: int = 8) -> list[dict]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM steuer_chronik WHERE jahr = ? ORDER BY id DESC LIMIT ?", (jahr, anzahl)
    )
    return [dict(r) for r in rows]


def hat_notiz(conn: sqlite3.Connection, jahr: int, art: str, text: str | None = None) -> bool:
    ensure_schema(conn)
    sql, args = "SELECT 1 FROM steuer_chronik WHERE jahr = ? AND art = ?", [jahr, art]
    if text is not None:
        sql += " AND text = ?"
        args.append(text)
    return conn.execute(sql, args).fetchone() is not None


# ---------- Vergleich mit dem Vorjahr ----------


def vergleich(conn: sqlite3.Connection, jahr: int) -> list[dict]:
    """Was letztes Jahr gesammelt war, und wie es dieses Jahr damit steht.

    Leer, wenn es letztes Jahr noch keine Steuer-Quest gab.
    """
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT v.gebiet, v.key, v.name, j.id AS stueck_id, j.status AS jetzt"
        " FROM steuer_stuecke v"
        " LEFT JOIN steuer_stuecke j ON j.jahr = ? AND j.gebiet = v.gebiet AND j.key = v.key"
        " WHERE v.jahr = ? AND v.status = 'gesammelt' ORDER BY v.id",
        (jahr, jahr - 1),
    )
    return [{**dict(r), "jetzt": r["jetzt"] or "nicht im Fragebogen"} for r in rows]

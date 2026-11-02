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

from frits.spiel import stand
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
    art TEXT NOT NULL DEFAULT 'pflicht',        -- pflicht | bonus (Bonus blockiert nie den Sieg)
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
CREATE TABLE IF NOT EXISTS steuer_gold (
    id INTEGER PRIMARY KEY,
    jahr INTEGER NOT NULL,
    zeit TEXT NOT NULL,
    betrag INTEGER NOT NULL,       -- plus: verdient, minus: auf dem Markt ausgegeben
    grund TEXT,                    -- z. B. "fund:12", zahlt nur einmal aus; Käufe: NULL
    text TEXT NOT NULL,
    UNIQUE (jahr, grund)
);
CREATE TABLE IF NOT EXISTS steuer_verweise (
    id INTEGER PRIMARY KEY,        -- eine Datei gilt auch für diese Karte (z. B. beide Wohnungen)
    jahr INTEGER NOT NULL,
    stueck_id INTEGER NOT NULL REFERENCES steuer_stuecke(id),
    datei_id INTEGER NOT NULL REFERENCES steuer_dateien(id),
    UNIQUE (stueck_id, datei_id)
);
CREATE TABLE IF NOT EXISTS steuer_aus_frits (
    jahr INTEGER NOT NULL,         -- Dateien, die die FRITS-Ernte gebracht hat (nicht selbst
    sha256 TEXT NOT NULL,          -- hochgeladen): für „Hat FRITS schon dabei“
    PRIMARY KEY (jahr, sha256)
);
CREATE TABLE IF NOT EXISTS steuer_schuld (
    jahr INTEGER PRIMARY KEY,      -- Salzkörner, die beim Zurückbuchen im Beutel fehlten
    betrag INTEGER NOT NULL        -- (schon ausgegeben): decke_schulden holt sie zurück
);
CREATE TABLE IF NOT EXISTS steuer_besitz (
    jahr INTEGER NOT NULL,
    ware TEXT NOT NULL,            -- siehe spiel.MARKT
    angelegt INTEGER NOT NULL DEFAULT 0,   -- Helfer: 1, solange er mitspielt
    gekauft TEXT NOT NULL,
    PRIMARY KEY (jahr, ware)
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


def beiseite(root: Path) -> Path:
    """Wohin Zettel kommen, die nirgends hingehören. Nichts wird gelöscht."""
    return Path(root) / "steuer" / "beiseite gelegt"


def lege_beiseite(root: Path, name: str) -> Path | None:
    """Verschiebt einen Zettel aus dem Lager nach "beiseite gelegt". Gibt das Ziel zurück."""
    quelle = lager(root) / Path(name).name
    if not Path(name).name or not quelle.is_file():
        return None
    ordner_ = beiseite(root)
    ordner_.mkdir(parents=True, exist_ok=True)
    ziel, n = ordner_ / quelle.name, 1
    while ziel.exists():
        ziel = ordner_ / f"{quelle.stem} ({n}){quelle.suffix}"
        n += 1
    shutil.move(str(quelle), ziel)
    return ziel


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
        erwartet = set()
        for g in gebiete:
            for s in g.stuecke:
                erwartet.add((g.id, s.key))
                conn.execute(
                    "INSERT OR IGNORE INTO steuer_stuecke"
                    " (jahr, gebiet, key, name, hinweis, art, geaendert)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (profil.jahr, g.id, s.key, s.name, s.hinweis, s.art, _now()),
                )
                # Texte aus dem Katalog aktuell halten (z. B. neue Steuer-Regeln)
                conn.execute(
                    "UPDATE steuer_stuecke SET name = ?, hinweis = ?, art = ?"
                    " WHERE jahr = ? AND gebiet = ? AND key = ? AND herkunft = 'katalog'"
                    " AND (name != ? OR hinweis != ? OR art != ?)",
                    (s.name, s.hinweis, s.art, profil.jahr, g.id, s.key, s.name, s.hinweis, s.art),
                )
        _vom_vorjahr(conn, profil.jahr, ids)
        # Was laut Fragebogen nicht mehr dazugehört, verschwindet, solange es unberührt ist.
        rows = conn.execute(
            "SELECT id, gebiet, key, herkunft, status FROM steuer_stuecke WHERE jahr = ?",
            (profil.jahr,),
        ).fetchall()
        for row in rows:
            if row["gebiet"] in ids and (
                row["herkunft"] != "katalog" or (row["gebiet"], row["key"]) in erwartet
            ):
                continue
            if row["gebiet"] in ids and row["status"] != "offen":
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


# Eine eingetippte Angabe ist eine Antwort, aber kein Dokument (siehe angabe_eintippen)
ANGABE_PRAEFIX = "Angabe "
_ECHT = "NOT (d.original LIKE 'Angabe %' AND d.original LIKE '%.txt')"


def ist_angabe(original: str) -> bool:
    return original.startswith(ANGABE_PRAEFIX) and original.endswith(".txt")


def stuecke(conn: sqlite3.Connection, jahr: int, gebiet: str | None = None) -> list[dict]:
    ensure_schema(conn)
    sql = (
        "SELECT s.*, (SELECT COUNT(*) FROM steuer_dateien d WHERE d.stueck_id = s.id) AS dateien,"
        # echte Belege: Dateien und mitbenutzte Dateien, aber keine eingetippten Angaben
        f" (SELECT COUNT(*) FROM steuer_dateien d WHERE d.stueck_id = s.id AND {_ECHT})"
        " + (SELECT COUNT(*) FROM steuer_verweise v JOIN steuer_dateien d ON d.id = v.datei_id"
        f" WHERE v.stueck_id = s.id AND {_ECHT}) AS belege"
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


def setze_frage(conn: sqlite3.Connection, stueck_id: int, frage: str) -> dict:
    """Nur die Frage ändern, der Stempel bleibt: eine Frage zu einem Beleg, der schon da ist."""
    stueck(conn, stueck_id)
    with transaction(conn):
        conn.execute(
            "UPDATE steuer_stuecke SET frage = ?, geaendert = ? WHERE id = ?",
            (frage.strip()[:500], _now(), stueck_id),
        )
    return stueck(conn, stueck_id)


def _root(conn: sqlite3.Connection) -> Path | None:
    """Der FRITS-Ordner, in dem diese Datenbank liegt (frits.db liegt direkt darin)."""
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main" and row[2]:
            return Path(row[2]).parent
    return None


def heile_pfad(conn: sqlite3.Connection, pfad: str) -> str:
    """Gespeicherte Pfade sind absolut. Wurde der FRITS-Ordner verschoben, kopiert oder aus
    einer Sicherung zurückgeholt, gilt immer der Teil ab "steuer/<jahr>" im JETZIGEN Ordner
    (dem neben frits.db), auch wenn der alte Ordner noch da ist. Ein Pfad außerhalb des
    jetzigen Ordners wird nie benutzt: Sonst würde eine Kopie die Dateien des Originals
    zeigen, verschieben oder in die Truhe packen."""
    alt = Path(pfad)
    root = _root(conn)
    if root is None:
        return pfad
    teile = alt.parts
    for i in range(len(teile) - 2, -1, -1):
        if teile[i] == "steuer" and re.fullmatch(r"\d{4}", teile[i + 1]):
            return str(root.joinpath(*teile[i:]))
    return pfad if _liegt_in(alt, root) else str(root / "steuer" / "fehlt" / alt.name)


def _liegt_in(pfad: Path, root: Path) -> bool:
    try:
        return pfad.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _geheilt(conn: sqlite3.Connection, row) -> dict:
    d = dict(row)
    if d.get("pfad"):
        d["pfad"] = heile_pfad(conn, d["pfad"])
    return d


def datei(conn: sqlite3.Connection, jahr: int, datei_id: int) -> dict | None:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM steuer_dateien WHERE id = ? AND jahr = ?", (datei_id, jahr)
    ).fetchone()
    return _geheilt(conn, row) if row else None


def wunsch_weg(conn: sqlite3.Connection, stueck_id: int) -> str:
    """Falsch gelesen: Das hat die Beraterin gar nicht gewünscht. Eine Katalog-Karte verliert
    nur die Markierung; eine Karte, die erst aus ihrer Mail entstand, verschwindet, solange
    nichts darauf liegt. Gibt "markierung" oder "karte" zurück."""
    s = stueck(conn, stueck_id)
    leer = (
        not _anzahl_dateien(conn, stueck_id)
        and not conn.execute(
            "SELECT 1 FROM steuer_verweise WHERE stueck_id = ?", (stueck_id,)
        ).fetchone()
    )
    with transaction(conn):
        if s["herkunft"] == "beraterin" and leer:
            conn.execute("DELETE FROM steuer_stuecke WHERE id = ?", (stueck_id,))
            return "karte"
        conn.execute(
            "UPDATE steuer_stuecke SET wunsch = 0, geaendert = ? WHERE id = ?", (_now(), stueck_id)
        )
    return "markierung"


def markiere_wunsch(conn: sqlite3.Connection, stueck_id: int) -> None:
    with transaction(conn):
        conn.execute("UPDATE steuer_stuecke SET wunsch = 1 WHERE id = ?", (stueck_id,))


def _anzahl_dateien(conn: sqlite3.Connection, stueck_id: int) -> int:
    """Eigene Dateien und mitbenutzte (Verweise) zusammen."""
    eigene = conn.execute(
        "SELECT COUNT(*) FROM steuer_dateien WHERE stueck_id = ?", (stueck_id,)
    ).fetchone()[0]
    verweise = conn.execute(
        "SELECT COUNT(*) FROM steuer_verweise WHERE stueck_id = ?", (stueck_id,)
    ).fetchone()[0]
    return eigene + verweise


# ---------- Dateien sammeln ----------


@dataclass(frozen=True)
class Fund:
    neu: bool  # False: genau diese Datei war schon gesammelt
    pfad: Path
    stueck: dict
    verweis: dict | None = None  # die Karte, bei der die Datei liegt (wird mitbenutzt)


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


def _ablagename(gebiet_id: str) -> str:
    """Klartext statt Spielname für Dateinamen, die die Beraterin sieht:
    "gehoeft-2" wird "Vermietete-Wohnung-2", "kaserne-p2-1" wird "Angestellt-Partner-1"."""
    from frits.steuer.katalog import katalog

    teile = gebiet_id.split("-")
    provinz = katalog().get(teile[0])
    name = _slug(provinz.bereich if provinz else teile[0])
    if len(name) > 32:
        name = name[:33].rsplit("-", 1)[0]  # an einer Wortgrenze kürzen
    for t in teile[1:]:
        name += "-Partner" if t == "p2" else f"-{t}"
    return name


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
        bei = stueck(conn, vorhanden["stueck_id"])
        if bei["id"] != s["id"] and darf_mitbenutzen(s, bei):
            # Eine Liste für beide Wohnungen: die Datei bleibt, wo sie ist, und zählt hier mit
            datei_id = conn.execute(
                "SELECT id FROM steuer_dateien WHERE jahr = ? AND sha256 = ?", (jahr, sha)
            ).fetchone()["id"]
            with transaction(conn):
                cur = conn.execute(
                    "INSERT OR IGNORE INTO steuer_verweise (jahr, stueck_id, datei_id)"
                    " VALUES (?, ?, ?)",
                    (jahr, s["id"], datei_id),
                )
                conn.execute(
                    "UPDATE steuer_stuecke SET status = 'gesammelt', geaendert = ? WHERE id = ?",
                    (_now(), s["id"]),
                )
            return Fund(
                bool(cur.rowcount),
                Path(heile_pfad(conn, vorhanden["pfad"])),
                stueck(conn, s["id"]),
                bei,
            )
        return Fund(False, Path(heile_pfad(conn, vorhanden["pfad"])), bei)

    ziel_ordner = ordner(root, jahr) / s["gebiet"]
    ziel_ordner.mkdir(parents=True, exist_ok=True)
    basis = f"{jahr}_{_ablagename(s['gebiet'])}_{_slug(s['name'])}"
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


def darf_mitzaehlen(karte: dict, bei: dict) -> bool:
    """Darf man eine Datei auf Nachfrage für eine zweite Karte zählen lassen? In derselben
    Provinz (die 1099 hat Dividenden, Quellensteuer und Verkäufe) und für jeden Wunsch der
    Beraterin. Automatisch passiert das nur in darf_mitbenutzen()."""
    if karte["id"] == bei["id"] or karte["jahr"] != bei["jahr"]:
        return False
    if karte.get("wunsch") or karte.get("herkunft") == "beraterin":
        return True
    # Bonus-Schätze bringen extra Gold: eine Datei soll nicht mehrere davon auf einmal heben
    return karte.get("art") != "bonus" and karte["gebiet"] == bei["gebiet"]


def mitzaehlen(conn: sqlite3.Connection, stueck_id: int, datei_id: int) -> bool:
    """Die Datei datei_id zählt auch für stueck_id. True, wenn das neu ist."""
    karte = stueck(conn, stueck_id)
    row = conn.execute(
        "SELECT * FROM steuer_dateien WHERE id = ? AND jahr = ?", (datei_id, karte["jahr"])
    ).fetchone()
    if row is None:
        raise QuestError("Diese Datei finde ich nicht mehr.")
    if not darf_mitzaehlen(karte, stueck(conn, row["stueck_id"])):
        raise QuestError("Diese Datei kann hier nicht mitzählen.")
    with transaction(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO steuer_verweise (jahr, stueck_id, datei_id) VALUES (?, ?, ?)",
            (karte["jahr"], stueck_id, datei_id),
        )
        conn.execute(
            "UPDATE steuer_stuecke SET status = 'gesammelt', geaendert = ? WHERE id = ?",
            (_now(), stueck_id),
        )
    return bool(cur.rowcount)


def datei_hash(pfad: Path) -> str:
    return _hash(pfad)


def merke_aus_frits(conn: sqlite3.Connection, jahr: int, pfad: Path) -> None:
    """Diese Datei kam aus der FRITS-Ernte, nicht von dir."""
    ensure_schema(conn)
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO steuer_aus_frits (jahr, sha256) VALUES (?, ?)",
            (jahr, datei_hash(pfad)),
        )


def von_frits(conn: sqlite3.Connection, jahr: int) -> set[int] | None:
    """Karten, deren Dateien alle aus der FRITS-Ernte kommen. None, solange die Ernte in
    diesem Jahr noch nichts gemerkt hat (ältere Spielstände: dann zählt die Rolle)."""
    ensure_schema(conn)
    if not conn.execute("SELECT 1 FROM steuer_aus_frits WHERE jahr = ?", (jahr,)).fetchone():
        return None
    rows = conn.execute(
        "SELECT d.stueck_id, MIN(a.sha256 IS NOT NULL) AS alle FROM steuer_dateien d"
        " LEFT JOIN steuer_aus_frits a ON a.jahr = d.jahr AND a.sha256 = d.sha256"
        " WHERE d.jahr = ? GROUP BY d.stueck_id",
        (jahr,),
    )
    return {r["stueck_id"] for r in rows if r["alle"]}


def datei_mit_hash(conn: sqlite3.Connection, jahr: int, sha: str) -> dict | None:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM steuer_dateien WHERE jahr = ? AND sha256 = ?", (jahr, sha)
    ).fetchone()
    return dict(row) if row else None


def datei_mit_pfad(conn: sqlite3.Connection, jahr: int, pfad: Path) -> dict | None:
    for d in dateien(conn, jahr):
        if d["pfad"] == str(pfad):
            return d
    return None


def darf_mitbenutzen(karte: dict, bei: dict) -> bool:
    """Darf eine Datei für zwei Karten zählen? Nur für dieselbe Unterlage in Geschwister-
    Provinzen, z. B. eine Mietliste für Wohnung 1 und 2. Sonst ließe sich eine Datei auf
    jede Karte legen."""
    return karte["key"] == bei["key"] and (
        karte["gebiet"].split("-")[0] == bei["gebiet"].split("-")[0]
    )


def verweise(conn: sqlite3.Connection, jahr: int, stueck_id: int | None = None) -> list[dict]:
    """Mitbenutzte Dateien: für welche Karte, welche Datei, und bei welcher Karte sie liegt."""
    ensure_schema(conn)
    sql = (
        "SELECT v.id, v.stueck_id, v.datei_id, d.pfad, d.original, d.stueck_id AS bei_id,"
        " s.name AS bei_name, s.gebiet AS bei_gebiet"
        " FROM steuer_verweise v JOIN steuer_dateien d ON d.id = v.datei_id"
        " JOIN steuer_stuecke s ON s.id = d.stueck_id WHERE v.jahr = ?"
    )
    werte: tuple = (jahr,)
    if stueck_id is not None:
        sql += " AND v.stueck_id = ?"
        werte = (jahr, stueck_id)
    return [_geheilt(conn, r) for r in conn.execute(sql + " ORDER BY v.id", werte)]


def _offen_wenn_leer(conn: sqlite3.Connection, stueck_id: int) -> None:
    if not _anzahl_dateien(conn, stueck_id):
        conn.execute(
            "UPDATE steuer_stuecke SET status = 'offen' WHERE id = ? AND status = 'gesammelt'",
            (stueck_id,),
        )


def entferne_verweis(conn: sqlite3.Connection, verweis_id: int) -> None:
    """Die Karte benutzt die Datei nicht mehr mit. Die Datei selbst bleibt, wo sie ist."""
    row = conn.execute("SELECT * FROM steuer_verweise WHERE id = ?", (verweis_id,)).fetchone()
    if not row:
        return
    with transaction(conn):
        conn.execute("DELETE FROM steuer_verweise WHERE id = ?", (verweis_id,))
        _offen_wenn_leer(conn, row["stueck_id"])


def dateien(conn: sqlite3.Connection, jahr: int, stueck_id: int | None = None) -> list[dict]:
    ensure_schema(conn)
    if stueck_id is None:
        rows = conn.execute("SELECT * FROM steuer_dateien WHERE jahr = ? ORDER BY id", (jahr,))
    else:
        rows = conn.execute(
            "SELECT * FROM steuer_dateien WHERE stueck_id = ? ORDER BY id", (stueck_id,)
        )
    return [_geheilt(conn, r) for r in rows]


def entferne_datei(conn: sqlite3.Connection, datei_id: int) -> None:
    """Nimmt eine falsch zugeordnete Datei wieder heraus und legt sie zurück ins Lager."""
    row = conn.execute("SELECT * FROM steuer_dateien WHERE id = ?", (datei_id,)).fetchone()
    if not row:
        return
    pfad = Path(heile_pfad(conn, row["pfad"]))
    root = pfad.parents[3]  # FRITS/steuer/<jahr>/<gebiet>/datei
    if ist_angabe(row["original"]):
        pfad.unlink(missing_ok=True)  # Eingetipptes wird kein Zettel im Korb, es ist einfach weg
    elif pfad.exists():
        ziel = lager(root) / row["original"]
        ziel.parent.mkdir(parents=True, exist_ok=True)
        if ziel.exists():
            ziel = ziel.with_name(f"{ziel.stem}_{row['id']}{ziel.suffix}")
        shutil.move(str(pfad), ziel)
    with transaction(conn):
        # Wer die Datei mitbenutzt hat, verliert sie auch (die andere Wohnung ist dann offen)
        mit = conn.execute(
            "SELECT stueck_id FROM steuer_verweise WHERE datei_id = ?", (datei_id,)
        ).fetchall()
        conn.execute("DELETE FROM steuer_verweise WHERE datei_id = ?", (datei_id,))
        conn.execute("DELETE FROM steuer_dateien WHERE id = ?", (datei_id,))
        for r in [row, *mit]:
            _offen_wenn_leer(conn, r["stueck_id"])


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


# ---------- Salzkörner und Markt ----------
# Eine Währung (Marie, 2.11.): Die Quest zahlt in Frittes Beutel (frits.spiel.stand). Das
# Goldbuch (steuer_gold, der Name ist von früher) bleibt das Journal der Quest: jede Buchung
# mit Grund, ein Grund zahlt nur einmal aus, darum bringt Zurücknehmen und neu Stempeln
# nichts. Der Kontostand kommt nur aus dem Beutel. Jede Zeile bucht im Beutel unter
# "quest:<jahr>:gold:<zeile>", Rücknahmen unter "quest:<jahr>:zurueck:<zeile>".
# Zierde gilt für alle Jahre (jahr 0 in steuer_besitz), Helfer nur für das Jahr.

ALLE_JAHRE = 0


def _beutel_text(jahr: int, text: str) -> str:
    return f"Steuer-Quest {jahr}: {text}"


def jahres_salz(conn: sqlite3.Connection, jahr: int) -> int:
    """Was dieses Steuerjahr schon in Frittes Beutel gezahlt hat (nach Rücknahmen). Zählt
    gegen den Deckel; Käufe und was aus dem Hafen zurückkommt, zählen nicht mit."""
    stand.vorbereiten(conn)
    row = conn.execute(
        "SELECT COALESCE(SUM(betrag), 0) FROM spiel_beutel WHERE grund LIKE ? AND grund NOT LIKE ?",
        (f"quest:{jahr}:%", f"quest:{jahr}:hafen:%"),
    ).fetchone()
    return int(row[0])


def verdiene(conn: sqlite3.Connection, jahr: int, grund: str, betrag: int, text: str) -> int:
    """Bucht Salzkörner in Frittes Beutel, wenn es für diesen Grund noch keine gab.

    Höchstens bis zum Deckel des Jahres (spiel.SALZ_DECKEL): Darüber steht die Zeile mit 0
    im Goldbuch, damit derselbe Grund nicht immer wieder anklopft. Gibt zurück, was wirklich
    in den Beutel ging (0, wenn der Grund schon bezahlt war)."""
    from frits.steuer import spiel

    ensure_schema(conn)
    stand.vorbereiten(conn)
    if betrag <= 0:
        return 0
    with transaction(conn):
        _umzug(conn, jahr)
        if conn.execute(
            "SELECT 1 FROM steuer_gold WHERE jahr = ? AND grund = ?", (jahr, grund)
        ).fetchone():
            return 0
        wert = max(0, min(int(betrag), spiel.SALZ_DECKEL - jahres_salz(conn, jahr)))
        zeile = text if wert == betrag else f"{text} (Jahresdeckel erreicht)"
        cur = conn.execute(
            "INSERT INTO steuer_gold (jahr, zeit, betrag, grund, text) VALUES (?, ?, ?, ?, ?)",
            (jahr, _now(), wert, grund, zeile),
        )
        if wert:
            wert = stand.beutel_gutschreiben(
                conn, f"quest:{jahr}:gold:{cur.lastrowid}", wert, _beutel_text(jahr, text)
            )
    return wert


def storniere(conn: sqlite3.Connection, jahr: int, grund: str, text: str) -> int:
    """Bucht die Salzkörner für einen Grund zurück und gibt den Grund wieder frei.

    Beide Zeilen bleiben im Goldbuch stehen (plus und minus), nichts verschwindet heimlich.
    Fehlt im Beutel etwas (schon ausgegeben), merkt sich die Quest das für decke_schulden.
    Gibt den zurückgebuchten Betrag zurück (0, wenn es für den Grund nichts gab)."""
    ensure_schema(conn)
    stand.vorbereiten(conn)
    with transaction(conn):
        _umzug(conn, jahr)
        row = conn.execute(
            "SELECT id, betrag FROM steuer_gold WHERE jahr = ? AND grund = ?", (jahr, grund)
        ).fetchone()
        if row is None:
            return 0
        conn.execute("UPDATE steuer_gold SET grund = NULL WHERE id = ?", (row["id"],))
        cur = conn.execute(
            "INSERT INTO steuer_gold (jahr, zeit, betrag, grund, text) VALUES (?, ?, ?, NULL, ?)",
            (jahr, _now(), -row["betrag"], f"Zurückgebucht: {text}"),
        )
        # Nur was diese Zeile wirklich in den Beutel gebracht hat (Zeilen von vor der einen
        # Währung brachten dort nichts, ihr Rest kam beim Umzug hinüber)
        im_beutel = stand.beutel_gebucht(conn, f"quest:{jahr}:gold:{row['id']}") or 0
        if im_beutel > 0:
            genommen = -stand.beutel_gutschreiben(
                conn,
                f"quest:{jahr}:zurueck:{cur.lastrowid}",
                -im_beutel,
                _beutel_text(jahr, f"zurückgebucht: {text}"),
            )
            if genommen < im_beutel:
                conn.execute(
                    "INSERT INTO steuer_schuld (jahr, betrag) VALUES (?, ?)"
                    " ON CONFLICT(jahr) DO UPDATE SET betrag = betrag + excluded.betrag",
                    (jahr, im_beutel - genommen),
                )
    return int(row["betrag"])


def umziehen(conn: sqlite3.Connection, jahr: int | None = None) -> None:
    """Einmal pro Steuerjahr: das Gold von vor der einen Währung in Frittes Beutel.

    Läuft vor der ersten neuen Buchung eines Jahres, also sind dann alle Zeilen alt. Das
    Goldbuch wird auf Salzkörner umgerechnet (ein Drittel, wie die neuen Preise), der Rest
    kommt in den Beutel, höchstens bis zum Deckel. Hat ein gewonnenes Jahr sein Restgold
    schon zu Salzkörnern gemacht (alter Weg, Fritte-Thread), kommt nichts mehr dazu.
    jahr=None: alle Jahre im Goldbuch. Nie in einer offenen Transaktion aufrufen."""
    ensure_schema(conn)
    stand.vorbereiten(conn)
    if jahr is None:
        jahre = [r[0] for r in conn.execute("SELECT DISTINCT jahr FROM steuer_gold").fetchall()]
    else:
        jahre = [jahr]
    for j in jahre:
        if stand.beutel_gebucht(conn, f"quest:{j}:umzug") is None:
            with transaction(conn):
                _umzug(conn, j)


def _umzug(conn: sqlite3.Connection, jahr: int) -> None:
    """umziehen für ein Jahr, in der Transaktion des Aufrufers (Schema muss bereit sein)."""
    from frits.steuer import spiel

    grund = f"quest:{jahr}:umzug"
    if stand.beutel_gebucht(conn, grund) is not None:
        return
    conn.execute("UPDATE steuer_gold SET betrag = betrag / 3 WHERE jahr = ?", (jahr,))
    rest = conn.execute(
        "SELECT COALESCE(SUM(betrag), 0) FROM steuer_gold WHERE jahr = ?", (jahr,)
    ).fetchone()[0]
    if stand.einstellung(conn, f"drachensieg_salz:{jahr}") or not spiel.gold_jahr(
        jahr, datetime.now().date()
    ):
        rest = 0
    stand.beutel_gutschreiben(
        conn,
        grund,
        max(0, min(int(rest), spiel.SALZ_DECKEL)),
        _beutel_text(jahr, "Gold von früher, jetzt Salzkörner"),
    )


def wunsch_funde(conn: sqlite3.Connection, jahr: int) -> int:
    """Wie viele Wünsche der Beraterin in diesem Jahr schon Gold gebracht haben."""
    ensure_schema(conn)
    sql = "SELECT COUNT(*) FROM steuer_gold WHERE jahr = ? AND grund LIKE 'fund:%:wunsch:%'"
    return int(conn.execute(sql, (jahr,)).fetchone()[0])


def hat_gold(conn: sqlite3.Connection, jahr: int | None, grund: str) -> bool:
    """Gab es Gold mit diesem Grund schon? jahr=None: in irgendeinem Steuerjahr."""
    ensure_schema(conn)
    if jahr is None:
        sql, args = "SELECT 1 FROM steuer_gold WHERE grund = ?", (grund,)
    else:
        sql, args = "SELECT 1 FROM steuer_gold WHERE jahr = ? AND grund = ?", (jahr, grund)
    return conn.execute(sql, args).fetchone() is not None


def gold(conn: sqlite3.Connection, jahr: int | None = None) -> int:
    """Die Salzkörner in Frittes Beutel: ein Kontostand für ganz FRITS, in jedem Jahr gleich."""
    return stand.guthaben(conn)


def goldsummen(conn: sqlite3.Connection, jahr: int) -> tuple[int, int]:
    """(verdient, ausgegeben) in diesem Jahr. Zurückgebuchtes zählt beim Verdienten ab,
    was zurück in den Hafen ging, beim Ausgegebenen."""
    ensure_schema(conn)
    rows = conn.execute("SELECT betrag, text FROM steuer_gold WHERE jahr = ?", (jahr,)).fetchall()

    def ausgabe(r) -> bool:
        if r["text"].startswith("Zurück in den Hafen"):
            return True
        return r["betrag"] < 0 and not r["text"].startswith("Zurück")

    ausgegeben = -sum(r["betrag"] for r in rows if ausgabe(r))
    verdient = sum(r["betrag"] for r in rows) + ausgegeben
    return verdient, ausgegeben


def goldbuch(conn: sqlite3.Connection, jahr: int, anzahl: int = 30) -> list[dict]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM steuer_gold WHERE jahr = ? ORDER BY id DESC LIMIT ?", (jahr, anzahl)
    )
    return [dict(r) for r in rows]


def besitz(conn: sqlite3.Connection, jahr: int) -> dict[str, bool]:
    """Was auf dem Markt gekauft wurde: Ware -> angelegt (nur für Helfer wichtig).
    Zierde aus anderen Jahren ist dabei: Die Insel behält, was einmal gebaut ist."""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT ware, angelegt FROM steuer_besitz WHERE jahr IN (?, ?)", (jahr, ALLE_JAHRE)
    )
    return {r["ware"]: bool(r["angelegt"]) for r in rows}


def zierde_freischalten(conn: sqlite3.Connection, key: str) -> bool:
    """Stellt eine Zierde für alle Jahre auf die Insel (Belohnung für einen Orden).
    True, wenn sie neu ist."""
    ensure_schema(conn)
    with transaction(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO steuer_besitz (jahr, ware, angelegt, gekauft) VALUES (?, ?, 0, ?)",
            (ALLE_JAHRE, key, _now()),
        )
    return cur.rowcount > 0


def zierden_abgleichen(conn: sqlite3.Connection) -> None:
    """Stellt die Zierden aller notierten Orden auf und nimmt Orden-Zierden weg, deren Orden
    nie notiert wurde (ein früherer Fehler stellte sie schon beim bloßen Ansehen auf).

    Gekaufte Zierden von früher bleiben (ihr Kauf steht im Goldbuch)."""
    from frits.steuer import spiel

    ensure_schema(conn)
    notiert = {
        e.key
        for e in spiel.erfolge([], [])
        if conn.execute(
            "SELECT 1 FROM steuer_chronik WHERE art = 'erfolg' AND text = ?", (e.name,)
        ).fetchone()
    }
    da = {
        r["ware"]
        for r in conn.execute("SELECT ware FROM steuer_besitz WHERE jahr = ?", (ALLE_JAHRE,))
    }
    for orden, zier in spiel.ORDEN_ZIERDE.items():
        if orden in notiert:
            if zier not in da:
                zierde_freischalten(conn, zier)
        elif zier in da and not hat_gold(conn, None, f"kauf:{zier}"):
            with transaction(conn):
                conn.execute(
                    "DELETE FROM steuer_besitz WHERE jahr = ? AND ware = ?", (ALLE_JAHRE, zier)
                )


def orden_fuer_sammlung(conn: sqlite3.Connection) -> list[dict]:
    """Die Orden der Steuer-Quest für Frittes gemeinsame Sammlung (/sammlung).

    Erreichte Orden stehen pro Jahr da (aus der Chronik), nicht erreichte einmal mit jahr=None,
    damit die Vitrine leere Plätze zeigen kann. zierde: was der Orden auf die Insel stellt."""
    from frits.steuer import spiel

    ensure_schema(conn)
    alle = spiel.erfolge([], [])
    erreicht = conn.execute(
        "SELECT jahr, text FROM steuer_chronik WHERE art = 'erfolg' ORDER BY jahr, id"
    ).fetchall()
    nach_name = {e.name: e for e in alle}
    out, gesehen, schon = [], set(), set()
    for r in erreicht:
        e = nach_name.get(r["text"])
        if e is None or (r["jahr"], e.key) in schon:
            continue
        schon.add((r["jahr"], e.key))
        gesehen.add(e.key)
        out.append(_orden_eintrag(e, r["jahr"], True))
    out += [_orden_eintrag(e, None, False) for e in alle if e.key not in gesehen]
    return out


def _orden_eintrag(e, jahr: int | None, erreicht: bool) -> dict:
    from frits.steuer import spiel

    return {
        "jahr": jahr,
        "key": e.key,
        "name": e.name,
        "zeichen": e.zeichen,
        "beschreibung": e.beschreibung,
        "erreicht": erreicht,
        "zierde": spiel.ORDEN_ZIERDE.get(e.key),
    }


def helfer(conn: sqlite3.Connection, jahr: int) -> frozenset[str]:
    """Die angelegten Helfer."""
    return frozenset(k for k, an in besitz(conn, jahr).items() if an)


def _abbuchen(
    conn: sqlite3.Connection, jahr: int, betrag: int, text: str, was: str, grund: str | None = None
) -> None:
    """Bezahlt aus Frittes Beutel, in der Transaktion des Aufrufers: Zwei schnelle Klicks
    können nie zweimal bezahlen. Im Goldbuch steht der Kauf mit dem Grund "kauf:<ware>",
    damit er zurück in den Hafen gehen kann (siehe decke_schulden)."""
    _umzug(conn, jahr)
    try:
        stand.beutel_bezahlen(conn, betrag, _beutel_text(jahr, text))
    except stand.SpielFehler:
        fehlt = betrag - max(0, stand.guthaben(conn))
        raise QuestError(
            f"Für {was} fehlen dir noch {fehlt} Salzkorn."
            if fehlt == 1
            else f"Für {was} fehlen dir noch {fehlt} Salzkörner."
        ) from None
    conn.execute(
        "INSERT INTO steuer_gold (jahr, zeit, betrag, grund, text) VALUES (?, ?, ?, ?, ?)",
        (jahr, _now(), -betrag, grund, text),
    )


def decke_schulden(conn: sqlite3.Connection, jahr: int) -> list[str]:
    """Der Beutel geht nie unter null. Fehlten beim Zurückbuchen Salzkörner, weil sie schon
    ausgegeben sind, geht der letzte Kauf aus diesem Jahr zurück in den Hafen, bis es
    reicht; was vom Preis übrig ist, kommt zurück in den Beutel. Sonst könnte man bei ×8
    verdienen, ausgeben, die Karte zurücklegen und neu verdienen. Gibt die Namen der
    zurückgegebenen Waren zurück.

    Ist gar kein Kauf mehr da (die Körner stecken in Helfer-Wechseln oder bei Fritte),
    bleibt es dabei: Das sind höchstens ein paar Körner."""
    from frits.steuer import spiel

    ensure_schema(conn)
    stand.vorbereiten(conn)
    zurueck: list[str] = []
    with transaction(conn):
        row = conn.execute("SELECT betrag FROM steuer_schuld WHERE jahr = ?", (jahr,)).fetchone()
        schuld = int(row[0]) if row else 0
        while schuld > 0:
            kauf = conn.execute(
                "SELECT id, betrag, grund FROM steuer_gold WHERE jahr = ? AND grund LIKE 'kauf:%'"
                " ORDER BY id DESC LIMIT 1",
                (jahr,),
            ).fetchone()
            if kauf is None:
                break
            key = kauf["grund"].removeprefix("kauf:")
            w = spiel.ware(key)
            name = w.name if w else key
            wo = ALLE_JAHRE if w and w.art == "zierde" else jahr
            preis = -int(kauf["betrag"])
            conn.execute("DELETE FROM steuer_besitz WHERE jahr = ? AND ware = ?", (wo, key))
            conn.execute("UPDATE steuer_gold SET grund = NULL WHERE id = ?", (kauf["id"],))
            conn.execute(
                "INSERT INTO steuer_gold (jahr, zeit, betrag, grund, text) VALUES (?, ?, ?, NULL, ?)",
                (jahr, _now(), preis, f"Zurück in den Hafen: {name}"),
            )
            rest = preis - min(preis, schuld)
            schuld -= preis - rest
            if rest:
                stand.beutel_gutschreiben(
                    conn,
                    f"quest:{jahr}:hafen:{kauf['id']}",
                    rest,
                    _beutel_text(jahr, f"{name} zurück im Hafen"),
                )
            zurueck.append(name)
        conn.execute("DELETE FROM steuer_schuld WHERE jahr = ?", (jahr,))
    return zurueck


def kaufe(conn: sqlite3.Connection, jahr: int, key: str) -> str:
    """Kauft eine Ware vom Markt. Ein neuer Helfer wird gleich angelegt, wenn ein Platz frei ist.

    Gibt eine freundliche Bestätigung zurück, oder wirft QuestError (zu wenig Gold, schon da)."""
    from frits.steuer import spiel

    w = spiel.ware(key)
    if w is None:
        raise QuestError("Diese Ware gibt es auf dem Markt nicht.")
    if w.art == "zierde":
        raise QuestError("Zierden kann man nicht kaufen: Jeder Orden stellt eine auf die Insel.")
    ensure_schema(conn)
    stand.vorbereiten(conn)
    anlegen = w.art == "helfer" and len(helfer(conn, jahr)) < spiel.HELFER_PLAETZE
    with transaction(conn):
        if conn.execute(
            "SELECT 1 FROM steuer_besitz WHERE ware = ? AND jahr IN (?, ?)",
            (key, jahr, ALLE_JAHRE),
        ).fetchone():
            raise QuestError(f"{w.name} hast du schon.")
        _abbuchen(conn, jahr, w.preis, f"Gekauft: {w.name}", w.name, f"kauf:{key}")
        conn.execute(
            "INSERT INTO steuer_besitz (jahr, ware, angelegt, gekauft) VALUES (?, ?, ?, ?)",
            (ALLE_JAHRE if w.art == "zierde" else jahr, key, int(anlegen), _now()),
        )
    if w.art == "zierde":
        return f"{w.name} steht jetzt auf deiner Insel, auch in den nächsten Jahren."
    if anlegen:
        return f"{w.name} spielt jetzt mit."
    return f"{w.name} ist gekauft. Mach mit einem anderen Helfer Pause, dann ist Platz."


def lege_helfer(conn: sqlite3.Connection, jahr: int, key: str, an: bool) -> str:
    """Helfer an- oder ablegen. Es gibt nur wenige Plätze, darum muss man wählen (Balatro).
    Pause machen ist gratis, an den Tisch holen kostet spiel.TAUSCH_PREIS: Sonst wäre die
    beste Strategie, vor jeder Karte umzustecken."""
    from frits.steuer import spiel

    w = spiel.ware(key)
    if w is None or w.art != "helfer" or key not in besitz(conn, jahr):
        raise QuestError("Diesen Helfer hast du noch nicht.")
    aktiv = helfer(conn, jahr)
    if an and key in aktiv:
        return f"{w.name} spielt schon mit."
    if an and len(aktiv) >= spiel.HELFER_PLAETZE:
        raise QuestError(
            f"Nur {spiel.HELFER_PLAETZE} Helfer passen an den Tisch. Mach erst mit einem Pause."
        )
    stand.vorbereiten(conn)
    with transaction(conn):
        if an:
            _abbuchen(conn, jahr, spiel.TAUSCH_PREIS, f"An den Tisch: {w.name}", w.name)
        conn.execute(
            "UPDATE steuer_besitz SET angelegt = ? WHERE jahr = ? AND ware = ?",
            (int(an), jahr, key),
        )
    return f"{w.name} spielt jetzt mit." if an else f"{w.name} macht Pause."

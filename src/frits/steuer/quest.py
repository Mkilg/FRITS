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

from frits.dbspalte import spalte_dazu
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
CREATE TABLE IF NOT EXISTS steuer_kiste (
    jahr INTEGER PRIMARY KEY,      -- Stand beim letzten Packen der Fundkiste: höchste Datei-id
    bis_datei INTEGER NOT NULL,    -- und Anzahl, damit „neu packen“ auffällt (Lena, Runde 33)
    anzahl INTEGER NOT NULL,
    inhalt TEXT,                   -- Karte:Prüfsumme je Datei, sortiert (Lena, Runde 44)
    stati TEXT                     -- Karte:Status je Karte, sortiert (Lena, Runde 56)
);
CREATE TABLE IF NOT EXISTS steuer_halb (
    jahr INTEGER PRIMARY KEY,      -- Inhalt der letzten Kiste vor dem Sieg (Notausgang), damit
    inhalt TEXT NOT NULL           -- die vollständige Mail ehrlich sagt, was neu ist (Lena, R54)
);
CREATE TABLE IF NOT EXISTS steuer_korb_herkunft (
    sha256 TEXT PRIMARY KEY,       -- Belege, die „Neu beginnen“ in den Korb gelegt hat: aus
    jahr INTEGER NOT NULL          -- welchem Jahr sie kamen (Lena, Runde 61)
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
    spalte_dazu(conn, "steuer_kiste", "inhalt", "TEXT")  # Kisten von vor Runde 44
    spalte_dazu(conn, "steuer_kiste", "stati", "TEXT")  # Kisten von vor Runde 56
    conn.commit()
    return conn


def _kiste_inhalt(conn: sqlite3.Connection, jahr: int) -> set[str]:
    """Was in der Fundkiste läge: je Datei ihre Karte und Prüfsumme. Wer eine Datei
    zurücklegt und genau dieselbe wieder ablegt, ändert daran nichts."""
    rows = conn.execute(
        "SELECT d.stueck_id, d.sha256 FROM steuer_dateien d WHERE d.jahr = ?", (jahr,)
    ).fetchall()
    return {f"{r[0]}:{r[1]}" for r in rows}


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


def von_frits(conn: sqlite3.Connection, jahr: int) -> set[int]:
    """Karten, deren Dateien alle aus der FRITS-Ernte kommen. Ohne gemerkte Ernte: keine
    (Lena, Runde 32: Raten nach der Rolle zeigte eigene Uploads als „von FRITS“)."""
    ensure_schema(conn)
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


def vor_dem_sieg_gepackt(conn: sqlite3.Connection, jahr: int) -> bool:
    """Wurde die Fundkiste zuerst gepackt, als der Drache noch lebte (Notausgang)? Dann
    hatte das Jahr noch kein Finale: Es kommt beim ersten Packen nach dem Sieg."""
    ensure_schema(conn)
    erste = {
        art: conn.execute(
            "SELECT MIN(id) FROM steuer_chronik WHERE jahr = ? AND art = ?", (jahr, art)
        ).fetchone()[0]
        for art in ("truhe", "sieg")
    }
    return erste["truhe"] is not None and (erste["sieg"] is None or erste["truhe"] < erste["sieg"])


def halbe_sendungen(conn: sqlite3.Connection, jahr: int) -> tuple[str, int] | None:
    """Kisten, die vor dem Sieg gepackt wurden (Notausgang): Zeit der letzten und ihre Zahl.
    None, solange der Drache lebt (keine Sieg-Notiz) oder es keine halbe Kiste gab."""
    ensure_schema(conn)
    sieg = conn.execute(
        "SELECT MIN(id) FROM steuer_chronik WHERE jahr = ? AND art = 'sieg'", (jahr,)
    ).fetchone()[0]
    if sieg is None:
        return None
    zeit, anzahl = conn.execute(
        "SELECT MAX(zeit), COUNT(*) FROM steuer_chronik WHERE jahr = ? AND art = 'truhe'"
        " AND id < ?",
        (jahr, sieg),
    ).fetchone()
    return (str(zeit), int(anzahl)) if anzahl else None


def nachtrag_seit(conn: sqlite3.Connection, jahr: int) -> tuple[str, int] | None:
    """Ist die Fundkiste ein Nachtrag? Dann (Zeit der letzten halben Kiste, Zahl der halben
    Kisten). Erst ab dem Finale nach dem Sieg (Lena, Runde 47: Die zweite Mail las sich wie
    die erste, die Beraterin wusste nicht, welche Kiste die vollständige ist)."""
    if not hat_notiz(conn, jahr, "kiste_finale"):
        return None
    return halbe_sendungen(conn, jahr)


def hat_notiz(
    conn: sqlite3.Connection, jahr: int | None, art: str, text: str | None = None
) -> bool:
    """Steht so eine Notiz in der Chronik? jahr=None: in irgendeinem Steuerjahr."""
    ensure_schema(conn)
    sql, args = "SELECT 1 FROM steuer_chronik WHERE art = ?", [art]
    if jahr is not None:
        sql += " AND jahr = ?"
        args.append(jahr)
    if text is not None:
        sql += " AND text = ?"
        args.append(text)
    return conn.execute(sql, args).fetchone() is not None


def _kiste_stati(conn: sqlite3.Connection, jahr: int) -> set[str]:
    """Wie jede Karte beim Packen gestempelt war: Das Inhaltsverzeichnis der Zip und der Brief
    sagen „fehlt noch“, „gibt es nicht“ oder „frag ich dich“ (Lena, Runde 56)."""
    rows = conn.execute("SELECT id, status FROM steuer_stuecke WHERE jahr = ?", (jahr,))
    return {f"{r[0]}:{r[1]}" for r in rows}


def kiste_karten(conn: sqlite3.Connection, jahr: int) -> int:
    """Wie viele Karten seit dem Packen anders gestempelt sind (ohne Datei-Änderungen)."""
    ensure_schema(conn)
    row = conn.execute("SELECT stati FROM steuer_kiste WHERE jahr = ?", (jahr,)).fetchone()
    if row is None or row[0] is None:
        return 0
    damals = dict(x.split(":", 1) for x in filter(None, row[0].split("\n")))
    jetzt = dict(x.split(":", 1) for x in _kiste_stati(conn, jahr))
    # Ein neuer Beleg ändert den Status von selbst (offen → gesammelt), ein herausgenommener
    # auch (gesammelt → offen); das zählt schon als Beleg. Wer danach „Gibt's nicht“ stempelt,
    # hat aber auch die Karte geändert (Lena, Runde 57: „1 Beleg herausgenommen“ war nur halb).
    return sum(
        1
        for k, v in jetzt.items()
        if k in damals
        and damals[k] != v
        and v != "gesammelt"
        and (damals[k], v) != ("gesammelt", "offen")
    )


def kiste_gepackt(conn: sqlite3.Connection, jahr: int) -> None:
    """Merkt sich, welche Dateien in der eben gepackten Fundkiste liegen."""
    ensure_schema(conn)
    bis, anzahl = conn.execute(
        "SELECT COALESCE(MAX(id), 0), COUNT(*) FROM steuer_dateien WHERE jahr = ?", (jahr,)
    ).fetchone()
    inhalt = "\n".join(sorted(_kiste_inhalt(conn, jahr)))
    stati = "\n".join(sorted(_kiste_stati(conn, jahr)))
    vor_dem_sieg = not hat_notiz(conn, jahr, "sieg")
    with transaction(conn):
        conn.execute(
            "INSERT OR REPLACE INTO steuer_kiste (jahr, bis_datei, anzahl, inhalt, stati)"
            " VALUES (?, ?, ?, ?, ?)",
            (jahr, bis, anzahl, inhalt, stati),
        )
        if vor_dem_sieg:
            conn.execute(
                "INSERT OR REPLACE INTO steuer_halb (jahr, inhalt) VALUES (?, ?)", (jahr, inhalt)
            )


def halb_inhalt(conn: sqlite3.Connection, jahr: int) -> set[str] | None:
    """Was in der letzten Kiste vor dem Sieg lag (Karte:Prüfsumme), oder None."""
    ensure_schema(conn)
    row = conn.execute("SELECT inhalt FROM steuer_halb WHERE jahr = ?", (jahr,)).fetchone()
    return set(filter(None, row[0].split("\n"))) if row else None


def kiste_stand(conn: sqlite3.Connection, jahr: int) -> tuple[int, int] | None:
    """None, solange die Fundkiste nie gepackt wurde. Sonst (neu, weg): Dateien, die seit dem
    Packen dazukamen oder herausgenommen wurden. (0, 0) heißt: Die Kiste ist aktuell."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT bis_datei, anzahl, inhalt FROM steuer_kiste WHERE jahr = ?", (jahr,)
    ).fetchone()
    if row is None:
        # Vor Runde 34 gepackt: nur die Chronik kennt den Zeitpunkt
        zeit = conn.execute(
            "SELECT MAX(zeit) FROM steuer_chronik WHERE jahr = ? AND art = 'truhe'", (jahr,)
        ).fetchone()[0]
        if zeit is None:
            return None
        neu = conn.execute(
            "SELECT COUNT(*) FROM steuer_dateien WHERE jahr = ? AND hinzugefuegt > ?",
            (jahr, zeit),
        ).fetchone()[0]
        return neu, 0
    bis, anzahl, inhalt = row
    if inhalt is not None:
        # Nach Inhalt vergleichen, nicht nach Zeilen-Nummern (Lena, Runde 44: zurückgelegt
        # und dieselbe Datei wieder abgelegt hieß „2 neue und 2 herausgenommen“)
        damals = set(filter(None, inhalt.split("\n")))
        jetzt = _kiste_inhalt(conn, jahr)
        return len(jetzt - damals), len(damals - jetzt)
    neu, alt = conn.execute(
        "SELECT COALESCE(SUM(id > ?), 0), COALESCE(SUM(id <= ?), 0) FROM steuer_dateien"
        " WHERE jahr = ?",
        (bis, bis, jahr),
    ).fetchone()
    return neu, max(0, anzahl - alt)


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


def schon_bezahlt(conn: sqlite3.Connection, jahr: int, grund: str) -> bool:
    ensure_schema(conn)
    return bool(
        conn.execute(
            "SELECT 1 FROM steuer_gold WHERE jahr = ? AND grund = ?", (jahr, grund)
        ).fetchone()
    )


def karte_hat_salz(conn: sqlite3.Connection, jahr: int, grund: str) -> bool:
    """Zahlt eine Antwort („Gibt's nicht“, „Lieber fragen“) nichts, weil dieselbe Karte schon
    einen Fund bezahlt hat? Im normalen Spiel bucht der Weg zurück auf „offen“ den Fund
    zurück; nach „Neu beginnen“ sind die Karten aber offen, ohne dass etwas zurückgebucht
    wurde (Lena, Runde 63). Ein Fund nach einer Frage zahlt dagegen extra, wie immer."""
    art, _, karte = grund.partition(":")
    if art != "antwort" or not karte:
        return False
    ensure_schema(conn)
    return bool(
        conn.execute(
            "SELECT 1 FROM steuer_gold WHERE jahr = ? AND grund = ? AND betrag > 0",
            (jahr, f"fund:{karte}"),
        ).fetchone()
    )


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


STORNO = "storno:"


def stornierte_gruende(conn: sqlite3.Connection, jahr: int) -> dict[str, int]:
    """Gründe, deren Salzkörner schon einmal zurückgebucht wurden, mit dem kleinsten Betrag,
    den sie damals gebracht haben. Eine solche Karte war schon gewertet: Legt man sie wieder ab,
    zahlt sie höchstens den Grundwert und nie mehr als damals, ohne Serie aus dem Kampf (sonst
    ließe sich durch Zurücklegen und Neuwerfen Salz farmen, auch mit einem neuen Helfer)."""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT grund, betrag FROM steuer_gold WHERE jahr = ? AND substr(grund, 1, ?) = ?",
        (jahr, len(STORNO), STORNO),
    ).fetchall()
    frueher: dict[str, int] = {}
    for grund, betrag in rows:
        g = grund[len(STORNO) :].rsplit(":", 1)[0]
        frueher[g] = min(frueher.get(g, betrag), betrag)
    return frueher


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
        # Der Grund wird frei, bleibt aber lesbar: Wer eine Karte zurücklegt und neu wirft,
        # bekommt nur den Grundwert (siehe stornierte_gruende, Lena Runde 38)
        conn.execute(
            "UPDATE steuer_gold SET grund = ? WHERE id = ?",
            (f"{STORNO}{grund}:{row['id']}", row["id"]),
        )
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
    # Lena (Runde 68): je Zeile abgerundet wurden aus 3 + 4 + 5 Gold nur 3 Salz statt 4. Die
    # Summe zählt: die fehlenden Körner gehen an die Zeilen mit dem größten Rest
    zeilen = conn.execute("SELECT rowid, betrag FROM steuer_gold WHERE jahr = ?", (jahr,))
    zeilen = [(r[0], int(r[1])) for r in zeilen.fetchall()]
    neu = {rid: int(b / 3) for rid, b in zeilen}
    fehlt = int(sum(b for _, b in zeilen) / 3) - sum(neu.values())
    reste = sorted(zeilen, key=lambda z: z[1] - 3 * neu[z[0]], reverse=fehlt > 0)
    for rid, _ in reste[: abs(fehlt)]:
        neu[rid] += 1 if fehlt > 0 else -1
    conn.executemany(
        "UPDATE steuer_gold SET betrag = ? WHERE rowid = ?", [(b, rid) for rid, b in neu.items()]
    )
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


def sicherungen(root: Path) -> Path:
    """Wohin „Neu beginnen“ ein Steuerjahr vorher sichert."""
    return Path(root) / "steuer" / "sicherungen"


def _sichern(conn: sqlite3.Connection, root: Path, jahr: int, anlass: str = "") -> Path:
    """Kopiert den Jahres-Ordner und die ganze Datenbank nach steuer/sicherungen/<jahr>-<Zeit>.
    anlass („vor dem Neu-Beginnen“) steht in anlass.txt, damit die Liste sagt, was das war."""
    quelle = ordner(root, jahr)
    ziel = sicherungen(root) / f"{jahr}-{datetime.now():%Y-%m-%d-%H%M%S}"
    n = 2
    while ziel.exists():
        ziel = ziel.with_name(f"{ziel.name.split('_')[0]}_{n}")
        n += 1
    ziel.mkdir(parents=True)
    if quelle.is_dir():
        shutil.copytree(quelle, ziel / str(jahr))
    with sqlite3.connect(ziel / "frits.db") as kopie:
        conn.backup(kopie)
    if anlass:
        (ziel / "anlass.txt").write_text(anlass, encoding="utf-8")
    return ziel


def sicherung_jahr(name: str) -> int | None:
    """Das Jahr aus dem Namen eines Sicherungs-Ordners („2025-2026-11-03-091500“)."""
    treffer = re.fullmatch(r"(\d{4})-\d{4}-\d{2}-\d{2}-\d{6}(_\d+)?", name)
    return int(treffer[1]) if treffer else None


def jahres_stand(conn: sqlite3.Connection, jahr: int) -> dict:
    """Woran man einen Stand erkennt (Lena, Runde 63: sechs gleiche Einträge): Belege,
    erledigte Pflicht-Karten, Prozent und ob der Drache schon gefallen ist."""
    belege = sum(
        1
        for (original,) in conn.execute(
            "SELECT original FROM steuer_dateien WHERE jahr = ?", (jahr,)
        )
        if not ist_angabe(original)
    )
    gesamt, erledigt = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(status != 'offen'), 0) FROM steuer_stuecke"
        " WHERE jahr = ? AND art = 'pflicht'",
        (jahr,),
    ).fetchone()
    sieg = conn.execute(
        "SELECT 1 FROM steuer_chronik WHERE jahr = ? AND art = 'sieg'", (jahr,)
    ).fetchone()
    return {
        "belege": belege,
        "prozent": round(100 * erledigt / gesamt) if gesamt else 0,
        "sieg": sieg is not None,
        "leer": not gesamt and not belege,
    }


def sicherungs_liste(root: Path) -> list[dict]:
    """Die Sicherungen, neueste zuerst: Name, Jahr, wann, Anlass und ihr Stand."""
    basis = sicherungen(root)
    if not basis.is_dir():
        return []
    liste = []
    for d in basis.iterdir():
        jahr = sicherung_jahr(d.name)
        if not (d.is_dir() and jahr and (d / "frits.db").is_file()):
            continue
        wann = datetime.strptime(d.name[5:22], "%Y-%m-%d-%H%M%S")
        eintrag = {"name": d.name, "jahr": jahr, "wann": wann, "anlass": ""}
        if (d / "anlass.txt").is_file():
            eintrag["anlass"] = (d / "anlass.txt").read_text(encoding="utf-8").strip()[:60]
        try:
            alt = sqlite3.connect(f"file:{d / 'frits.db'}?mode=ro", uri=True)
            try:
                eintrag |= jahres_stand(alt, jahr)
            finally:
                alt.close()
        except sqlite3.Error:
            eintrag |= {"belege": 0, "prozent": 0, "sieg": False, "leer": False}
        liste.append(eintrag)
    return sorted(liste, key=lambda x: (x["wann"], x["name"]), reverse=True)


_JAHRES_TABELLEN = (
    "steuer_verweise",
    "steuer_dateien",
    "steuer_chronik",
    "steuer_kiste",
    "steuer_halb",
    "steuer_stuecke",
)

# Goldbuch-Gründe, die am Stand der Karten hängen (Tagesziele und Käufe nicht)
_KARTEN_SALZ = ("fund:", "antwort:", "siegel:")


@dataclass(frozen=True)
class Zurueck:
    jahr: int
    davor: Path
    in_den_korb: int  # Belege, die neuer als die Sicherung sind: jetzt im Eingangskorb
    salz: int  # wie sich das Salz dadurch geändert hat (meist minus)
    doppelt: int = 0  # Zettel im Korb, die sha-gleich wieder an ihrer Karte liegen


def _umschreiben(text: str | None, karten: dict[int, int]) -> str | None:
    """Zeilen „<Karten-Nummer>:…“ (Fundkiste) auf neue Karten-Nummern umschreiben."""
    if not text or not karten:
        return text
    zeilen = []
    for z in text.split("\n"):
        nr, _, rest = z.partition(":")
        if nr.isdigit() and int(nr) in karten:
            z = f"{karten[int(nr)]}:{rest}"
        zeilen.append(z)
    return "\n".join(zeilen)


def _zeilen_zurueck(conn: sqlite3.Connection, alt: sqlite3.Connection, jahr: int) -> None:
    """Die Zeilen des Jahres aus der Sicherung. Nummern, die inzwischen ein anderes Jahr
    benutzt, bekommen eine neue (Lena, Runde 63: sonst scheiterte jedes Zurückholen, sobald
    in 2024 ein Beleg dazukam); was auf sie zeigt, wird umgeschrieben. Orden bleiben verdient,
    auch die aus dem Spiel nach der Sicherung."""
    karten: dict[int, int] = {}
    dateien: dict[int, int] = {}
    orden = {
        r[0]
        for r in conn.execute(
            "SELECT text FROM steuer_chronik WHERE jahr = ? AND art = 'erfolg'", (jahr,)
        )
    }
    for tabelle in _JAHRES_TABELLEN:
        nur = " AND art != 'erfolg'" if tabelle == "steuer_chronik" else ""
        conn.execute(f"DELETE FROM {tabelle} WHERE jahr = ?{nur}", (jahr,))
    for tabelle in reversed(_JAHRES_TABELLEN):  # Karten zuerst, dann was auf sie zeigt
        spalten = [r[1] for r in conn.execute(f"PRAGMA table_info({tabelle})")]
        da = {r[1] for r in alt.execute(f"PRAGMA table_info({tabelle})")}
        gemeinsam = [sp for sp in spalten if sp in da]
        if not gemeinsam:
            continue
        for z in alt.execute(
            f"SELECT {', '.join(gemeinsam)} FROM {tabelle} WHERE jahr = ?", (jahr,)
        ).fetchall():
            zeile = dict(zip(gemeinsam, tuple(z), strict=True))
            if zeile.get("art") == "erfolg" and zeile.get("text") in orden:
                continue  # Chronik: dieser Orden ist schon da
            if "stueck_id" in zeile:
                zeile["stueck_id"] = karten.get(zeile["stueck_id"], zeile["stueck_id"])
            if "datei_id" in zeile:
                zeile["datei_id"] = dateien.get(zeile["datei_id"], zeile["datei_id"])
            if tabelle == "steuer_kiste" and zeile.get("bis_datei") is not None:
                bis = zeile["bis_datei"]
                zeile["bis_datei"] = max(
                    [neu for alt_nr, neu in dateien.items() if alt_nr <= bis], default=bis
                )
            for sp in ("inhalt", "stati"):
                if sp in zeile and tabelle in ("steuer_kiste", "steuer_halb"):
                    zeile[sp] = _umschreiben(zeile[sp], karten)
            alte_nr = zeile.get("id")
            if (
                alte_nr is not None
                and conn.execute(f"SELECT 1 FROM {tabelle} WHERE id = ?", (alte_nr,)).fetchone()
            ):
                del zeile["id"]  # vergeben: SQLite gibt eine neue
            liste = ", ".join(zeile)
            cur = conn.execute(
                f"INSERT INTO {tabelle} ({liste}) VALUES ({', '.join('?' for _ in zeile)})",
                tuple(zeile.values()),
            )
            if alte_nr is not None and tabelle == "steuer_stuecke":
                karten[alte_nr] = cur.lastrowid
            elif alte_nr is not None and tabelle == "steuer_dateien":
                dateien[alte_nr] = cur.lastrowid


def _salz_wie_damals(conn: sqlite3.Connection, alt: sqlite3.Connection, jahr: int) -> int:
    """Das Salz der Karten und Siegel wieder so, wie es zur Sicherung gebucht war (Lena,
    Runde 63: Neu beginnen, anders spielen und zurückholen brachte +10 bei gleichem Stand).
    Was seitdem dazukam, wird sichtbar zurückgebucht; was seitdem zurückgebucht wurde, kommt
    wieder. Gibt die Änderung zurück."""

    def lebend(db: sqlite3.Connection) -> dict[str, tuple[int, str]]:
        try:
            rows = db.execute(
                "SELECT grund, betrag, text FROM steuer_gold WHERE jahr = ? AND grund IS NOT NULL",
                (jahr,),
            ).fetchall()
        except sqlite3.Error:
            return {}
        return {g: (int(b), str(t)) for g, b, t in rows if str(g).startswith(_KARTEN_SALZ)}

    damals, jetzt = lebend(alt), lebend(conn)
    if not damals and not jetzt:
        return 0
    aenderung = 0
    for grund in sorted(set(jetzt) - set(damals)):
        aenderung -= storniere(conn, jahr, grund, jetzt[grund][1])
    for grund in sorted(set(damals) - set(jetzt)):
        betrag, text = damals[grund]
        aenderung += verdiene(
            conn, jahr, grund, betrag, text.replace(" (Jahresdeckel erreicht)", "")
        )
    return aenderung


def zurueckholen(conn: sqlite3.Connection, root: Path, name: str) -> Zurueck:
    """Holt ein Steuerjahr aus einer Sicherung zurück (Lena, Runde 61). Der Stand davor wird
    selbst erst gesichert, so lässt sich auch das Zurückholen zurücknehmen.

    Lena (Runde 63):
    - Belege des Jahres, die nicht in der Sicherung sind, kommen in den Eingangskorb.
      Nichts verschwindet in einem Sicherungs-Ordner.
    - Das Salz der Karten ist danach wie zur Sicherung (kein Salz aus Schleifen).
    - Nummern, die ein anderes Jahr inzwischen benutzt, werden neu vergeben.
    Belege im Korb, die wieder an ihrer Karte liegen, verschwinden dort (gleiche Prüfsumme)."""
    ensure_schema(conn)
    quelle = sicherungen(root) / Path(name).name
    jahr = sicherung_jahr(quelle.name)
    if jahr is None or not (quelle / "frits.db").is_file():
        raise ValueError(f"Keine Sicherung: {name}")
    alt = sqlite3.connect(f"file:{quelle / 'frits.db'}?mode=ro", uri=True)
    alt.row_factory = sqlite3.Row
    try:
        damals = {
            r[0] for r in alt.execute("SELECT sha256 FROM steuer_dateien WHERE jahr = ?", (jahr,))
        }
        try:
            herkunft_damals = {
                r[0]
                for r in alt.execute(
                    "SELECT sha256 FROM steuer_korb_herkunft WHERE jahr = ?", (jahr,)
                )
            }
        except sqlite3.Error:
            herkunft_damals = set()  # Sicherung von vor Runde 61
        # Belege, die seit der Sicherung ins Jahr kamen: gleich in den Korb, nicht weg
        retten = [
            (Path(heile_pfad(conn, r["pfad"])), r["original"], r["sha256"], r["id"])
            for r in conn.execute(
                "SELECT id, pfad, original, sha256 FROM steuer_dateien WHERE jahr = ?", (jahr,)
            ).fetchall()
            if r["sha256"] not in damals and not ist_angabe(r["original"])
        ]
        davor = _sichern(conn, root, jahr, "vor dem Zurückholen")
        try:
            with transaction(conn):
                _zeilen_zurueck(conn, alt, jahr)
        except BaseException:
            shutil.rmtree(davor, ignore_errors=True)  # es hat sich wirklich nichts geändert
            raise
        salz = _salz_wie_damals(conn, alt, jahr)
    finally:
        alt.close()
    korb = lager(root)
    korb.mkdir(parents=True, exist_ok=True)
    gerettet: list[tuple[str, str]] = []
    for pfad, original, sha, nr in retten:
        if not pfad.is_file():
            continue
        neu = korb / Path(original).name
        if neu.exists():
            neu = neu.with_name(f"{neu.stem}_{nr}{neu.suffix}")
        shutil.move(str(pfad), neu)
        gerettet.append((sha, neu.name))
    jetzt = ordner(root, jahr)
    if jetzt.is_dir():
        shutil.rmtree(jetzt)
    if (quelle / str(jahr)).is_dir():
        shutil.copytree(quelle / str(jahr), jetzt)
    zurueck = {
        r["sha256"]
        for r in conn.execute(
            "SELECT pfad, sha256 FROM steuer_dateien WHERE jahr = ?", (jahr,)
        ).fetchall()
        if Path(heile_pfad(conn, r["pfad"])).is_file()
    }
    # Lena (Runde 65): Ein Zettel, dessen Name ein anderes Jahr nennt („Riester_2024.pdf“), lag
    # nur aus Versehen im Jahr. Er bekommt kein „Aus 2025“, sonst sagt kein Jahr „gehört hierher“.
    herkunft = {sha for sha, name in gerettet if not _nennt_anderes_jahr(name, jahr)}
    herkunft |= herkunft_damals
    im_korb = []
    doppelt = 0
    for datei in lager_dateien(root):
        sha = _sha(datei)
        if sha in zurueck:
            datei.unlink()  # liegt wieder an seiner Karte
            doppelt += 1
        elif sha in herkunft:
            im_korb.append((sha, jahr))
    with transaction(conn):
        conn.execute("DELETE FROM steuer_korb_herkunft WHERE jahr = ?", (jahr,))
        conn.executemany(
            "INSERT OR REPLACE INTO steuer_korb_herkunft (sha256, jahr) VALUES (?, ?)", im_korb
        )
    return Zurueck(jahr, davor, len(gerettet), salz, doppelt)


def _nennt_anderes_jahr(name: str, jahr: int) -> bool:
    """Nennt der Dateiname nur andere Steuerjahre (und nicht dieses)?"""
    jahre = {int(j) for j in re.findall(r"(?<!\d)(20[1-3]\d)(?!\d)", name)}
    return bool(jahre) and jahr not in jahre


def _sha(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for stueck in iter(lambda: f.read(1 << 20), b""):
            h.update(stueck)
    return h.hexdigest()


_SHA_GEMERKT: dict[tuple[str, int, int], str] = {}


def _sha_gemerkt(pfad: Path) -> str:
    """sha256 einer Datei, gemerkt solange Größe und Zeit gleich bleiben (der Korb wird bei
    jedem Seitenaufruf geprüft)."""
    st = pfad.stat()
    schluessel = (str(pfad), st.st_size, st.st_mtime_ns)
    if schluessel not in _SHA_GEMERKT:
        if len(_SHA_GEMERKT) > 2000:
            _SHA_GEMERKT.clear()
        _SHA_GEMERKT[schluessel] = _sha(pfad)
    return _SHA_GEMERKT[schluessel]


def schon_abgelegt(conn: sqlite3.Connection, jahr: int, datei: Path) -> dict | None:
    """Die Karte, auf der derselbe Zettel (gleicher Inhalt) in diesem Jahr schon liegt.

    Lena (Runde 69): Solche Doppel im Korb sahen aus wie Arbeit und zählten bei „Lohnt sich
    noch“ mit."""
    ensure_schema(conn)
    if not conn.execute("SELECT 1 FROM steuer_dateien WHERE jahr = ? LIMIT 1", (jahr,)).fetchone():
        return None
    try:
        sha = _sha_gemerkt(datei)
    except OSError:
        return None
    row = conn.execute(
        "SELECT s.id, s.name, s.gebiet FROM steuer_dateien d"
        " JOIN steuer_stuecke s ON s.id = d.stueck_id WHERE d.jahr = ? AND d.sha256 = ?",
        (jahr, sha),
    ).fetchone()
    return {"id": row[0], "name": row[1], "gebiet": row[2]} if row else None


def korb_jahr(conn: sqlite3.Connection, datei: Path) -> int | None:
    """Aus welchem Jahr ein Zettel im Korb stammt, wenn „Neu beginnen“ ihn dort hingelegt hat."""
    ensure_schema(conn)
    if not conn.execute("SELECT 1 FROM steuer_korb_herkunft LIMIT 1").fetchone():
        return None
    try:
        sha = _sha(datei)
    except OSError:
        return None
    row = conn.execute("SELECT jahr FROM steuer_korb_herkunft WHERE sha256 = ?", (sha,)).fetchone()
    return row[0] if row else None


def neu_beginnen(conn: sqlite3.Connection, root: Path, jahr: int) -> Path:
    """Beginnt die Quest eines Jahres von vorn, ohne etwas zu verlieren (Marie, 2026-11-03).

    1. Sicherung: der ganze Jahres-Ordner (Belege, Fragebogen, Fundkiste) und eine Kopie der
       Datenbank nach steuer/sicherungen/<jahr>-<Zeit>/.
    2. Jeder Beleg wandert zurück in den Eingangskorb. Eingetippte Angaben bleiben nur in der
       Sicherung; die fragt die Quest neu ab.
    3. Fragebogen, Stempel, Chronik (bis auf die Orden) und Fundkiste des Jahres sind weg,
       die Karten wieder offen.
       Die Karten behalten ihre Nummer: Salzkörner gibt es für dieselbe Karte nicht zweimal.
    Gibt den Sicherungs-Ordner zurück."""
    ensure_schema(conn)
    quelle = ordner(root, jahr)
    ziel = _sichern(conn, root, jahr, "vor dem Neu-Beginnen")
    korb = lager(root)
    korb.mkdir(parents=True, exist_ok=True)
    herkunft = []
    im_korb: set[str] = set()
    for d in korb.iterdir():
        if d.is_file():
            try:
                im_korb.add(_sha(d))
            except OSError:
                pass
    for row in conn.execute("SELECT * FROM steuer_dateien WHERE jahr = ?", (jahr,)).fetchall():
        pfad = Path(heile_pfad(conn, row["pfad"]))
        if ist_angabe(row["original"]) or not pfad.is_file():
            continue
        if row["sha256"] and row["sha256"] in im_korb:
            # Lena (Runde 68): Derselbe Zettel lag schon im Korb. Zweimal ablegen muss niemand;
            # die Kopie aus dem Jahr bleibt in der Sicherung
            if not _nennt_anderes_jahr(row["original"], jahr):
                herkunft.append((row["sha256"], jahr))
            continue
        if row["sha256"]:
            im_korb.add(row["sha256"])
        neu = korb / row["original"]
        if neu.exists():
            neu = neu.with_name(f"{neu.stem}_{row['id']}{neu.suffix}")
        shutil.move(str(pfad), neu)
        if not _nennt_anderes_jahr(neu.name, jahr):  # Lena (Runde 65), siehe zurueckholen
            herkunft.append((row["sha256"], jahr))
    with transaction(conn):
        conn.executemany(
            "INSERT OR REPLACE INTO steuer_korb_herkunft (sha256, jahr) VALUES (?, ?)", herkunft
        )
        for tabelle in _JAHRES_TABELLEN[:-1]:
            # Lena (Runde 61): Orden bleiben verdient (sonst kamen sie als „Neuer Orden“ wieder,
            # und ihre Zierden hätten beim Abgleich verschwinden können)
            nur = " AND art != 'erfolg'" if tabelle == "steuer_chronik" else ""
            conn.execute(f"DELETE FROM {tabelle} WHERE jahr = ?{nur}", (jahr,))
        conn.execute("DELETE FROM steuer_stuecke WHERE jahr = ? AND herkunft != 'katalog'", (jahr,))
        conn.execute(
            "UPDATE steuer_stuecke SET status = 'offen', frage = '', wunsch = 0, geaendert = ?"
            " WHERE jahr = ?",
            (_now(), jahr),
        )
    if quelle.is_dir():
        shutil.rmtree(quelle)
    return ziel

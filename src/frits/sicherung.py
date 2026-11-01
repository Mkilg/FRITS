"""Datensicherung: the whole FRITS folder as one locked zip on a second drive.

The GoBD want records kept safe for 8 to 10 years (§ 147 AO), and a Mac can die. So FRITS
packs everything in Dokumente/FRITS (database, invoices, receipts, customers, tax quest)
into ``FRITS-Sicherung_<datum>_<uhrzeit>.zip`` in a folder Fritz picks: a USB stick, an
external disk or iCloud Drive.

Customer data never leaves the Mac unlocked: the zip is AES-encrypted. The password is
made once, kept in the macOS keychain, and shown on the Sicherung page so Fritz can write
it down; without it the backup is useless to anyone, including him. Old backups are never
deleted by FRITS.

Restoring unpacks into a new folder next to the FRITS folder and never overwrites
anything; Fritz then swaps the folders himself (the page says how).
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath

import pyzipper

from frits.settings import Workspace

KEYCHAIN_SERVICE = "FRITS"
KEYCHAIN_ACCOUNT = "sicherung"
NAME = "FRITS-Sicherung_{stamp}.zip"
_STAMP = re.compile(r"^FRITS-Sicherung_(\d{4}-\d{2}-\d{2}_\d{4})(\d{2})?\.zip$")
DB_NAMES = {"frits.db", "frits.db-wal", "frits.db-shm", "frits.db-journal"}
# Pseudonym tables of the redaction are short-lived and the most sensitive thing there is;
# the run file only says which FRITS is running right now.
SKIP = {".mappings", ".frits-laeuft.json"}
REMIND_AFTER_DAYS = 7


class SicherungError(ValueError):
    """Something Fritz can fix; the message says what."""


def kennwort(create: bool = True) -> str | None:
    """The backup password from the keychain; made on first use."""
    import keyring
    from keyring.errors import KeyringError

    try:
        found = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except KeyringError:
        found = None
    if found or not create:
        return found
    from frits.steuer.truhe import passwort

    new = passwort()
    try:
        keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, new)
    except KeyringError as exc:
        raise SicherungError(
            "Der Schlüsselbund ist nicht erreichbar, deshalb kann ich kein Passwort für die "
            "Sicherung ablegen."
        ) from exc
    return new


def _files(root: Path, skip_dir: Path | None) -> list[Path]:
    result = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts[0] in SKIP or (len(rel.parts) == 1 and rel.name in DB_NAMES):
            continue
        if skip_dir and (path == skip_dir or skip_dir in path.parents):
            continue  # a backup folder inside the FRITS folder must not back up itself
        if path.is_symlink() or not path.is_file() or path.name.endswith(".tmp"):
            continue
        result.append(path)
    return result


def sichern(ws: Workspace, ziel: Path, passwort: str, jetzt: datetime | None = None) -> Path:
    """Write one encrypted backup of the whole FRITS folder into ``ziel``."""
    jetzt = jetzt or datetime.now()
    ziel = Path(ziel).expanduser()
    if not ziel.is_absolute():
        raise SicherungError("Bitte einen vollständigen Ordner angeben, z. B. /Volumes/USB.")
    try:
        ziel.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SicherungError(
            f"Den Ordner {ziel} kann ich nicht anlegen. Ist der USB-Stick angesteckt?"
        ) from exc
    if ziel.resolve() == ws.root.resolve():
        raise SicherungError("Die Sicherung gehört auf einen anderen Datenträger, nicht in FRITS.")
    path = ziel / NAME.format(stamp=jetzt.strftime("%Y-%m-%d_%H%M"))
    if path.exists():  # twice in one minute: keep both, never replace a backup
        path = ziel / NAME.format(stamp=jetzt.strftime("%Y-%m-%d_%H%M%S"))
    tmp = path.with_suffix(".zip.tmp")
    root = ws.root.resolve()
    inside = ziel.resolve() if root in ziel.resolve().parents else None
    with tempfile.TemporaryDirectory() as work:
        db_copy = Path(work) / "frits.db"
        if ws.db.exists():
            # SQLite's own backup: a consistent copy even while FRITS is writing.
            source = sqlite3.connect(ws.db)
            target = sqlite3.connect(db_copy)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
        try:
            with pyzipper.AESZipFile(
                tmp, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
            ) as zf:
                zf.setpassword(passwort.encode("utf-8"))
                if db_copy.exists():
                    zf.write(db_copy, "FRITS/frits.db")
                for file in _files(root, inside):
                    zf.write(file, str(PurePosixPath("FRITS", *file.relative_to(root).parts)))
            tmp.replace(path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise SicherungError(
                f"Die Sicherung nach {ziel} hat nicht geklappt ({exc.strerror or exc}). "
                "Ist genug Platz frei?"
            ) from exc
    return path


def letzte(ziel: Path | str) -> datetime | None:
    """When the newest backup in ``ziel`` was made (from its name), or None."""
    if not ziel:
        return None
    try:
        names = [p.name for p in Path(ziel).expanduser().iterdir()]
    except OSError:
        return None
    stamps = [m.group(1) + (m.group(2) or "00") for n in names if (m := _STAMP.match(n))]
    return datetime.strptime(max(stamps), "%Y-%m-%d_%H%M%S") if stamps else None


def faellig(ziel: str, jetzt: datetime | None = None) -> bool:
    """Should FRITS remind Fritz to back up? (Set up, and nothing in the last week.)"""
    if not ziel:
        return False
    newest = letzte(ziel)
    return newest is None or ((jetzt or datetime.now()) - newest).days >= REMIND_AFTER_DAYS


def vorschlaege(
    home: Path | None = None, volumes: Path = Path("/Volumes")
) -> list[tuple[str, str]]:
    """(label, folder) for places a backup can go: iCloud Drive and plugged-in drives."""
    home = home or Path.home()
    found = []
    icloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    if icloud.is_dir():
        found.append(("iCloud Drive", str(icloud / "FRITS-Sicherung")))
    try:
        drives = sorted(volumes.iterdir())
    except OSError:
        drives = []
    for drive in drives:
        if drive.is_symlink() or not drive.is_dir() or drive.name.startswith("."):
            continue  # "Macintosh HD" is a link to / : a backup there is no backup
        found.append((drive.name, str(drive / "FRITS-Sicherung")))
    return found


def wiederherstellen(sicherung: Path, passwort: str, ziel: Path) -> Path:
    """Unpack a backup into the new folder ``ziel`` (must not exist) and check it."""
    ziel = Path(ziel)
    if ziel.exists():
        raise SicherungError(f"{ziel} gibt es schon. Ich überschreibe nichts.")
    try:
        with pyzipper.AESZipFile(sicherung) as zf:
            zf.setpassword(passwort.encode("utf-8"))
            names = zf.namelist()
            if "FRITS/frits.db" not in names and "FRITS/settings.toml" not in names:
                raise SicherungError("Das ist keine FRITS-Sicherung.")
            zf.read(next(n for n in names if not n.endswith("/")))  # password check first
            ziel.mkdir(parents=True)
            base = ziel.resolve()
            for name in names:
                parts = PurePosixPath(name).parts
                if not parts or parts[0] != "FRITS" or ".." in parts or name.endswith("/"):
                    continue
                out = base.joinpath(*parts[1:])
                if base not in out.resolve().parents:
                    continue  # never write outside the new folder
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(zf.read(name))
    except RuntimeError as exc:  # pyzipper: wrong password
        raise SicherungError("Das Passwort passt nicht zu dieser Sicherung.") from exc
    except (pyzipper.BadZipFile, OSError) as exc:
        raise SicherungError("Die Datei ist keine lesbare FRITS-Sicherung.") from exc
    db = ziel / "frits.db"
    if db.exists():
        with sqlite3.connect(db) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SicherungError("Die Datenbank in der Sicherung ist beschädigt.")
    return ziel

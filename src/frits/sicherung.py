"""Datensicherung: the whole FRITS folder as one locked zip on a second drive.

The GoBD want records kept safe for 8 to 10 years (§ 147 AO), and a Mac can die. So FRITS
packs everything in Dokumente/FRITS (database, invoices, receipts, customers, tax quest)
into ``FRITS-Sicherung_<datum>_<uhrzeit>.zip`` in a folder Fritz picks: a USB stick, an
external disk or iCloud Drive.

Customer data never leaves the Mac unlocked: the zip is AES-encrypted. The password is
made once, kept in the macOS keychain, and shown on the Sicherung page so Fritz can write
it down; without it the backup is useless to anyone, including him. Old backups are never
deleted by FRITS.

Inside the locked zip sits one plain zip, ``FRITS.zip``, with the folder. So even the file
names (customer names, "Lohnsteuerbescheinigung") stay hidden without the password. Keka or
The Unarchiver open it without FRITS too.

Restoring unpacks into a new folder next to the FRITS folder and never overwrites
anything; Fritz then swaps the folders himself (the page says how).
"""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

import pyzipper

from frits.settings import Workspace

KEYCHAIN_SERVICE = "FRITS"
KEYCHAIN_ACCOUNT = "sicherung"
NAME = "FRITS-Sicherung_{stamp}.zip"
INNER = "FRITS.zip"  # the plain zip inside the locked one
_STAMP = re.compile(r"^FRITS-Sicherung_(\d{4}-\d{2}-\d{2}_\d{4})(\d{2})?\.zip$")
DB_NAMES = {"frits.db", "frits.db-wal", "frits.db-shm", "frits.db-journal"}
# Pseudonym tables of the redaction are short-lived and the most sensitive thing there is;
# the run file only says which FRITS is running right now; ".nachtragen" holds an old invoice
# only while Fritz checks it (it is filed with the invoices once he enters it).
SKIP = {".mappings", ".frits-laeuft.json", ".nachtragen"}
REMIND_AFTER_DAYS = 7


class SicherungError(ValueError):
    """Something Fritz can fix; the message says what."""


class KennwortFehlt(SicherungError):
    """This Mac has no backup password, but backups exist: Fritz types the one on his paper."""


def kennwort(create: bool = True, ziel: Path | str | None = None) -> str | None:
    """The backup password from the keychain; made on first use.

    Never made anew while backups already sit in ``ziel``: on a new Mac, or when the keychain
    can't be read, a fresh password would not open them, and the one on Fritz's paper would
    stop fitting the next backups. Then FRITS asks for the password from the paper.
    """
    import keyring
    from keyring.errors import KeyringError

    try:
        found = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except KeyringError as exc:
        if ziel and letzte(ziel):
            raise KennwortFehlt(
                "Der Schlüsselbund ist gerade nicht erreichbar. Gib das Passwort für die "
                "Sicherung von deinem Zettel ein, dann sichere ich damit."
            ) from exc
        raise KennwortFehlt(
            "Der Schlüsselbund ist gerade nicht erreichbar. Denk dir ein Passwort für die "
            "Sicherung aus, schreib es auf einen Zettel und gib es hier ein."
        ) from exc
    if found or not create:
        return found
    if ziel and letzte(ziel):
        raise KennwortFehlt(
            "Auf diesem Mac liegt noch kein Passwort für die Sicherung, aber am Sicherungsort "
            "gibt es schon Sicherungen. Gib das Passwort von deinem Zettel ein, damit alle "
            "Sicherungen dasselbe haben."
        )
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


MIN_NEU = 10  # a new password Fritz makes up himself


def kennwort_merken(passwort: str, ziel: Path | str | None = None) -> None:
    """The password from Fritz's paper: check it on the newest backup, then keep it."""
    import keyring
    from keyring.errors import KeyringError

    passwort = passwort.strip()
    if not passwort:
        raise KennwortFehlt("Bitte das Passwort von deinem Zettel eingeben.")
    newest = _neueste(ziel) if ziel else None
    if not newest and len(passwort) < MIN_NEU:
        raise KennwortFehlt(
            f"Nimm mindestens {MIN_NEU} Zeichen, zum Beispiel drei Wörter mit Bindestrich: "
            "Fritte-Wolke-Tisch."
        )
    if newest and not passt(newest, passwort):
        raise KennwortFehlt(
            "Dieses Passwort öffnet die letzte Sicherung nicht. Schau noch einmal auf deinen "
            "Zettel: Groß- und Kleinschreibung und Bindestriche zählen."
        )
    try:
        keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, passwort)
    except KeyringError:
        pass  # this backup still works with it; the page asks again next time


def passt(sicherung: Path, passwort: str) -> bool:
    """Does the password open this backup?"""
    try:
        with pyzipper.AESZipFile(sicherung) as zf:
            zf.setpassword(passwort.encode("utf-8"))
            first = next((n for n in zf.namelist() if not n.endswith("/")), None)
            if first is None:
                return False
            with zf.open(first) as f:
                f.read(1)
    except (RuntimeError, pyzipper.BadZipFile, OSError):
        return False
    return True


def _neueste(ziel: Path | str) -> Path | None:
    try:
        found = sorted(p for p in Path(ziel).expanduser().iterdir() if _STAMP.match(p.name))
    except OSError:
        return None
    return found[-1] if found else None


def selbe_platte(ws: Workspace, ziel: Path | str) -> bool:
    """Is the backup on the same disk as FRITS? Then a broken Mac takes both. iCloud Drive
    lives on the Mac too, but it is copied to Apple's servers, so it counts as elsewhere."""
    ziel = Path(ziel).expanduser()
    if "Mobile Documents" in ziel.parts:
        return False
    try:
        return os.stat(ziel).st_dev == os.stat(ws.root).st_dev
    except OSError:
        return False


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
        if _STAMP.match(path.name):
            continue  # an old backup that ended up in here must not grow into every next one
        result.append(path)
    return result


def sichern(ws: Workspace, ziel: Path, passwort: str, jetzt: datetime | None = None) -> Path:
    """Write one encrypted backup of the whole FRITS folder into ``ziel``."""
    jetzt = jetzt or datetime.now()
    ziel = Path(ziel).expanduser()
    if not ziel.is_absolute():
        raise SicherungError("Bitte einen vollständigen Ordner angeben, z. B. /Volumes/USB.")
    root = ws.root.resolve()
    target_dir = ziel.resolve()
    if target_dir == root or root in target_dir.parents:
        raise SicherungError(
            "Die Sicherung gehört auf einen anderen Datenträger, nicht in den Ordner FRITS: "
            "auf einen USB-Stick, eine externe Festplatte oder in iCloud Drive."
        )
    if not ziel.exists():
        if not ziel.parent.is_dir():
            # Never make a folder chain: "/Volumes/USB/..." without the stick would end up
            # on the Mac itself and only look like a backup.
            raise SicherungError(
                f"Den Ort {ziel.parent} gibt es gerade nicht. Ist der USB-Stick angesteckt?"
            )
        try:
            ziel.mkdir()
        except OSError as exc:
            raise SicherungError(
                f"Den Ordner {ziel} kann ich nicht anlegen. Ist der USB-Stick angesteckt?"
            ) from exc
    for stale in ziel.glob("FRITS-Sicherung_*.zip.tmp"):
        stale.unlink(missing_ok=True)  # left over from an interrupted backup
    path = ziel / NAME.format(stamp=jetzt.strftime("%Y-%m-%d_%H%M"))
    if path.exists():  # twice in one minute: keep both, never replace a backup
        path = ziel / NAME.format(stamp=jetzt.strftime("%Y-%m-%d_%H%M%S"))
    tmp = path.with_suffix(".zip.tmp")
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
        inner = Path(work) / INNER
        try:
            with zipfile.ZipFile(inner, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                if db_copy.exists():
                    zf.write(db_copy, "FRITS/frits.db")
                for file in _files(root, None):
                    zf.write(file, str(PurePosixPath("FRITS", *file.relative_to(root).parts)))
            with pyzipper.AESZipFile(
                tmp, "w", compression=pyzipper.ZIP_STORED, encryption=pyzipper.WZ_AES
            ) as zf:
                zf.setpassword(passwort.encode("utf-8"))
                zf.write(inner, INNER)
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
            if names == [INNER]:  # names hidden inside: unlock, then read the plain zip
                with tempfile.TemporaryDirectory() as work:
                    inner = Path(work) / INNER
                    with zf.open(INNER) as src, inner.open("wb") as dst:
                        while chunk := src.read(1 << 20):
                            dst.write(chunk)
                    with zipfile.ZipFile(inner) as plain:
                        _auspacken(plain, ziel)
            else:  # the first backups (FRITS 1.4) kept the files right in the locked zip
                _auspacken(zf, ziel)
    except RuntimeError as exc:  # pyzipper: wrong password
        if ziel.exists() and not any(ziel.iterdir()):
            ziel.rmdir()
        raise SicherungError("Das Passwort passt nicht zu dieser Sicherung.") from exc
    except (pyzipper.BadZipFile, zipfile.BadZipFile, OSError) as exc:
        raise SicherungError("Die Datei ist keine lesbare FRITS-Sicherung.") from exc
    db = ziel / "frits.db"
    if db.exists():
        with sqlite3.connect(db) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SicherungError("Die Datenbank in der Sicherung ist beschädigt.")
    return ziel


def _auspacken(zf, ziel: Path) -> None:
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

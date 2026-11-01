"""Datensicherung: one locked zip of the whole FRITS folder, and getting it back."""

from datetime import date, datetime
from decimal import Decimal

import pytest
import pyzipper

from frits import sicherung, store
from frits.expenses import Expense, add_expense, list_expenses
from frits.settings import Settings, Workspace


def _ws(tmp_path):
    ws = Workspace(tmp_path / "Dokumente" / "FRITS").ensure()
    Settings(name="Fritz").save(ws.settings_file)
    store.connect(ws.db)
    add_expense(
        ws.db, Expense(date(2026, 3, 1), "Bahn", "Ticket", Decimal("9.90"), None, "reisekosten")
    )
    (ws.invoices / "2026-0001_Kunde.pdf").write_bytes(b"%PDF-1.4 Rechnung")
    (ws.customers / "meier.md").write_text("Erika Meier, Gartenweg 1")
    (ws.mappings).mkdir(exist_ok=True)
    (ws.mappings / "job.json").write_text('{"[PERSON_1]": "Erika Meier"}')
    return ws


def _inner_names(path, password):
    import io
    import zipfile

    with pyzipper.AESZipFile(path) as zf:
        zf.setpassword(password.encode())
        return zipfile.ZipFile(io.BytesIO(zf.read(sicherung.INNER))).namelist()


def test_backup_is_locked_complete_and_comes_back(tmp_path):
    ws = _ws(tmp_path)
    usb = tmp_path / "Volumes" / "USB"
    usb.mkdir(parents=True)
    path = sicherung.sichern(ws, usb, "Wiese-Anker-47", datetime(2026, 11, 1, 18, 30))
    assert path.name == "FRITS-Sicherung_2026-11-01_1830.zip"
    with pyzipper.AESZipFile(path) as zf:
        # Without the password not even the file names show (customer names, tax papers).
        assert zf.namelist() == [sicherung.INNER]
        assert all(i.flag_bits & 0x1 for i in zf.infolist())  # locked
        with pytest.raises(RuntimeError):
            zf.read(sicherung.INNER)  # not without the password
    assert b"meier" not in path.read_bytes()
    names = _inner_names(path, "Wiese-Anker-47")
    assert "FRITS/frits.db" in names and "FRITS/invoices/2026-0001_Kunde.pdf" in names
    assert not any(".mappings" in n for n in names)  # pseudonym tables stay home
    assert sicherung.letzte(usb) == datetime(2026, 11, 1, 18, 30)
    assert not sicherung.faellig(str(usb), datetime(2026, 11, 7, 9))
    assert sicherung.faellig(str(usb), datetime(2026, 11, 8, 19))
    assert sicherung.faellig(str(tmp_path / "leer")) and not sicherung.faellig("")

    with pytest.raises(sicherung.SicherungError, match="Passwort passt nicht"):
        sicherung.wiederherstellen(path, "falsch", tmp_path / "zurueck")
    assert not (tmp_path / "zurueck").exists()  # nothing half-unpacked
    back = sicherung.wiederherstellen(path, "Wiese-Anker-47", tmp_path / "zurueck")
    assert (back / "customers" / "meier.md").read_text() == "Erika Meier, Gartenweg 1"
    restored = Workspace(back)
    assert [e.vendor for e in list_expenses(restored.db)] == ["Bahn"]
    with pytest.raises(sicherung.SicherungError, match="überschreibe nichts"):
        sicherung.wiederherstellen(path, "Wiese-Anker-47", back)


def test_a_backup_never_goes_into_frits_or_onto_a_missing_stick(tmp_path):
    ws = _ws(tmp_path)
    with pytest.raises(sicherung.SicherungError, match="anderen Datenträger"):
        sicherung.sichern(ws, ws.root / "sicherung", "pw")
    with pytest.raises(sicherung.SicherungError, match="anderen Datenträger"):
        sicherung.sichern(ws, ws.root, "pw")
    with pytest.raises(sicherung.SicherungError, match="vollständigen Ordner"):
        sicherung.sichern(ws, "USB", "pw")
    gone = tmp_path / "Volumes" / "USB-Stick" / "FRITS-Sicherung"
    with pytest.raises(sicherung.SicherungError, match="USB-Stick angesteckt"):
        sicherung.sichern(ws, gone, "pw")
    assert not (tmp_path / "Volumes").exists()  # no look-alike folder on the Mac itself


def test_old_backups_inside_frits_and_broken_leftovers_are_left_out(tmp_path):
    ws = _ws(tmp_path)
    (ws.root / "alt").mkdir()
    (ws.root / "alt" / "FRITS-Sicherung_2026-10-01_1000.zip").write_bytes(b"x" * 1000)
    usb = tmp_path / "usb"
    usb.mkdir()
    (usb / "FRITS-Sicherung_2026-11-01_0900.zip.tmp").write_bytes(b"halb")
    path = sicherung.sichern(ws, usb, "pw", datetime(2026, 11, 1, 10, 0))
    assert not any("FRITS-Sicherung" in n for n in _inner_names(path, "pw"))
    assert not list(usb.glob("*.tmp"))


def test_backups_from_frits_1_4_still_come_back(tmp_path):
    old = tmp_path / "FRITS-Sicherung_2026-11-01_1200.zip"
    with pyzipper.AESZipFile(old, "w", encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(b"pw")
        zf.writestr("FRITS/settings.toml", 'name = "Fritz"\n')
        zf.writestr("FRITS/customers/meier.md", "Erika Meier")
    back = sicherung.wiederherstellen(old, "pw", tmp_path / "zurueck")
    assert (back / "customers" / "meier.md").read_text() == "Erika Meier"


def test_no_silent_new_password_when_backups_exist(tmp_path, monkeypatch):
    import keyring
    from keyring.errors import KeyringError

    ws = _ws(tmp_path)
    usb = tmp_path / "usb"
    usb.mkdir()
    sicherung.sichern(ws, usb, "Zettel-Passwort-1", datetime(2026, 11, 1, 10, 0))
    vault = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, a: vault.get((s, a)))
    monkeypatch.setattr(keyring, "set_password", lambda s, a, p: vault.__setitem__((s, a), p))
    # A new Mac: no password in the keychain, but backups on the stick.
    with pytest.raises(sicherung.KennwortFehlt, match="Zettel"):
        sicherung.kennwort(ziel=usb)
    assert not vault  # nothing made up
    with pytest.raises(sicherung.KennwortFehlt, match="öffnet die letzte Sicherung nicht"):
        sicherung.kennwort_merken("falsch", usb)
    sicherung.kennwort_merken("Zettel-Passwort-1", usb)
    assert sicherung.kennwort(ziel=usb) == "Zettel-Passwort-1"
    # No backups anywhere yet: a new password is made, as before.
    vault.clear()
    assert sicherung.kennwort(ziel=tmp_path / "leer")

    def broken(*args):
        raise KeyringError("gesperrt")

    monkeypatch.setattr(keyring, "get_password", broken)
    with pytest.raises(sicherung.KennwortFehlt, match="nicht erreichbar"):
        sicherung.kennwort(ziel=tmp_path / "leer")


def test_same_disk_is_noticed_but_icloud_counts_as_elsewhere(tmp_path):
    ws = _ws(tmp_path)
    beside = tmp_path / "Sicherung"
    beside.mkdir()
    assert sicherung.selbe_platte(ws, beside)
    icloud = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "FRITS"
    icloud.mkdir(parents=True)
    assert not sicherung.selbe_platte(ws, icloud)


def test_a_foreign_zip_is_refused(tmp_path):
    other = tmp_path / "fremd.zip"
    with pyzipper.AESZipFile(other, "w", encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(b"pw")
        zf.writestr("../../boese.txt", "x")
    with pytest.raises(sicherung.SicherungError, match="keine FRITS-Sicherung"):
        sicherung.wiederherstellen(other, "pw", tmp_path / "z")
    assert not (tmp_path.parent / "boese.txt").exists()


def test_suggests_icloud_and_plugged_in_drives_but_not_the_mac_itself(tmp_path):
    home = tmp_path / "home"
    (home / "Library" / "Mobile Documents" / "com~apple~CloudDocs").mkdir(parents=True)
    volumes = tmp_path / "Volumes"
    (volumes / "USB-Stick").mkdir(parents=True)
    (volumes / "Macintosh HD").symlink_to("/")
    found = sicherung.vorschlaege(home, volumes)
    assert [label for label, _ in found] == ["iCloud Drive", "USB-Stick"]
    assert found[1][1] == str(volumes / "USB-Stick" / "FRITS-Sicherung")


def test_page_backs_up_shows_password_and_restores(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_web import TODAY

    from frits.web.app import create_app

    ws = _ws(tmp_path)
    usb = tmp_path / "Volumes" / "USB"
    usb.mkdir(parents=True)
    monkeypatch.setattr(sicherung, "kennwort", lambda create=True, ziel=None: "Moos-Eule-12")
    monkeypatch.setattr(sicherung, "vorschlaege", lambda: [("USB", str(usb))])
    app = create_app(ws, nlp_loader=lambda: None, today=lambda: TODAY)
    with TestClient(app) as client:
        token = app.state.token
        assert "Noch keine Datensicherung eingerichtet" in client.get("/feierabend").text
        page = client.get("/sicherung").text
        assert "Moos-Eule-12" not in page and f'value="{usb}"' in page
        page = client.post(
            "/sicherung", data={"token": token, "ziel": str(usb)}, follow_redirects=True
        ).text
        assert "Gesichert: FRITS-Sicherung_" in page and "Letzte Sicherung" in page
        assert "stempel-gesichert" in page
        assert "derselben Festplatte" in page  # tmp_path: same disk as FRITS
        assert Settings.load(ws.settings_file).backup_dir == str(usb)
        assert "Datensicherung ist über eine Woche her" not in client.get("/feierabend").text
        shown = client.post("/sicherung/kennwort", data={"token": token}).text
        assert '<code id="kennwort">Moos-Eule-12</code>' in shown

        (zipped,) = usb.iterdir()
        page = client.post(
            "/sicherung/zurueck",
            data={"token": token, "passwort": "Moos-Eule-12"},
            files={"datei": (zipped.name, zipped.read_bytes(), "application/zip")},
        ).text
        assert "Sicherung ausgepackt" in page
        restored = ws.root.parent / "FRITS aus Sicherung 02.11.2026"
        assert (restored / "customers" / "meier.md").exists()
        page = client.post(
            "/sicherung/zurueck",
            data={"token": token, "passwort": "falsch"},
            files={"datei": (zipped.name, zipped.read_bytes(), "application/zip")},
            follow_redirects=True,
        ).text
        assert "Passwort passt nicht" in page
        assert not (ws.root.parent / "FRITS aus Sicherung 02.11.2026 (2)").exists()


def test_two_backups_in_one_minute_are_both_kept(tmp_path):
    ws = _ws(tmp_path)
    usb = tmp_path / "usb"
    usb.mkdir()
    first = sicherung.sichern(ws, usb, "pw", datetime(2026, 11, 1, 18, 30, 5))
    second = sicherung.sichern(ws, usb, "pw", datetime(2026, 11, 1, 18, 30, 40))
    assert first != second and first.exists() and second.exists()
    assert sicherung.letzte(usb) == datetime(2026, 11, 1, 18, 30, 40)


def test_restored_copy_finds_its_invoice_pdfs(tmp_path):
    import shutil

    from fastapi.testclient import TestClient
    from test_ustva import _settings

    from frits.billing import Draft, Line, approve, new_draft
    from frits.web.app import create_app

    ws = Workspace(tmp_path / "alt" / "FRITS").ensure()
    _settings().save(ws.settings_file)
    conn = store.connect(ws.db)
    draft = Draft(
        "kunde",
        "Sender AG",
        "Funkweg 2, 50667 Köln",
        [Line("Moderation", "2026-03-02", Decimal(1), "pauschal", Decimal(100))],
        [],
        [],
        "notiz.txt",
    )
    number, _ = approve(conn, new_draft(conn, draft), _settings(), ws.invoices, date(2026, 3, 2))
    stored = conn.execute("SELECT pdf_path FROM invoices").fetchone()[0]
    conn.close()
    usb = tmp_path / "usb"
    usb.mkdir()
    path = sicherung.sichern(ws, usb, "pw", datetime(2026, 11, 1, 10, 0))
    # A new Mac: another user name, and the old folder is gone.
    new = Workspace(sicherung.wiederherstellen(path, "pw", tmp_path / "neu" / "FRITS"))
    shutil.rmtree(tmp_path / "alt")
    found = new.find(stored)
    assert found is not None and found.parent == new.invoices.resolve()
    assert new.find("/etc/passwd") is None and new.find(None) is None
    app = create_app(new, nlp_loader=lambda: None)
    with TestClient(app) as client:
        response = client.get(f"/invoices/{number}/pdf")
        assert response.status_code == 200 and response.content.startswith(b"%PDF")


def test_locked_keychain_first_backup_asks_for_a_new_password(tmp_path, monkeypatch):
    """Tester: the place was gone after the keychain message, and the first backup has no
    paper yet, so "Passwort von deinem Zettel" made no sense."""
    import keyring
    from fastapi.testclient import TestClient
    from keyring.errors import KeyringError

    from frits.web.app import create_app

    def broken(*args):
        raise KeyringError("gesperrt")

    monkeypatch.setattr(keyring, "get_password", broken)
    monkeypatch.setattr(keyring, "set_password", broken)
    ws = _ws(tmp_path)
    usb = tmp_path / "usb"
    usb.mkdir()
    with TestClient(create_app(ws, nlp_loader=lambda: None)) as client:
        token = client.app.state.token
        page = client.post("/sicherung", data={"token": token, "anderer": str(usb)})
        assert "Denk dir ein Passwort" in page.text
        assert "Neues Passwort für die Sicherung" in page.text
        assert f'value="{usb}" checked' in page.text or f'value="{usb}"' in page.text
        short = client.post(
            "/sicherung", data={"token": token, "ziel": str(usb), "passwort": "kurz"}
        )
        assert "mindestens 10 Zeichen" in short.text and str(usb) in short.text
        done = client.post(
            "/sicherung",
            data={"token": token, "ziel": str(usb), "passwort": "Fritte-Wolke-Tisch"},
        )
        assert "Gesichert" in done.text
    newest = sicherung._neueste(usb)
    assert newest and sicherung.passt(newest, "Fritte-Wolke-Tisch")

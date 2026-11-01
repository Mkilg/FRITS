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


def test_backup_is_locked_complete_and_comes_back(tmp_path):
    ws = _ws(tmp_path)
    usb = tmp_path / "Volumes" / "USB"
    path = sicherung.sichern(ws, usb, "Wiese-Anker-47", datetime(2026, 11, 1, 18, 30))
    assert path.name == "FRITS-Sicherung_2026-11-01_1830.zip"
    with pyzipper.AESZipFile(path) as zf:
        names = zf.namelist()
        assert all(i.flag_bits & 0x1 for i in zf.infolist())  # every file locked
        with pytest.raises(RuntimeError):
            zf.read("FRITS/customers/meier.md")  # not without the password
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


def test_a_backup_folder_inside_frits_does_not_copy_itself(tmp_path):
    ws = _ws(tmp_path)
    inner = ws.root / "sicherung"
    sicherung.sichern(ws, inner, "pw", datetime(2026, 11, 1, 10, 0))
    second = sicherung.sichern(ws, inner, "pw", datetime(2026, 11, 2, 10, 0))
    with pyzipper.AESZipFile(second) as zf:
        assert not any("FRITS-Sicherung" in n for n in zf.namelist())
    with pytest.raises(sicherung.SicherungError, match="anderen Datenträger"):
        sicherung.sichern(ws, ws.root, "pw")
    with pytest.raises(sicherung.SicherungError, match="vollständigen Ordner"):
        sicherung.sichern(ws, "USB", "pw")


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
    monkeypatch.setattr(sicherung, "kennwort", lambda create=True: "Moos-Eule-12")
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
    first = sicherung.sichern(ws, usb, "pw", datetime(2026, 11, 1, 18, 30, 5))
    second = sicherung.sichern(ws, usb, "pw", datetime(2026, 11, 1, 18, 30, 40))
    assert first != second and first.exists() and second.exists()
    assert sicherung.letzte(usb) == datetime(2026, 11, 1, 18, 30, 40)

import os
import re
import shutil
import time
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient

from frits import store, system
from frits.providers.recorded import RecordedProvider
from frits.settings import Settings, Workspace
from frits.web.app import create_app

TODAY = date(2026, 11, 2)


@pytest.fixture
def ws(tmp_path):
    workspace = Workspace(tmp_path / "FRITS").ensure()
    for note in (FIXTURES / "customers").glob("*.md"):
        shutil.copy(note, workspace.customers)
    Settings(
        name="Fritz Beispiel",
        address="Werkstattweg 1, 12345 Musterstadt",
        email="fritz@example.com",
        iban="DE89370400440532013000",
        bank="Beispielbank",
        steuernummer="123/456/78901",
        hourly_rate=Decimal(40),
    ).save(workspace.settings_file)
    return workspace


class Recorded:
    """Answers with the recorded reply for whichever file is in the inbox."""

    def __init__(self, ws):
        self.ws = ws
        self.calls = []

    def __call__(self, settings):
        return self

    def extract(self, text):
        self.calls.append(text)
        name = next(self.ws.inbox.iterdir()).stem
        return RecordedProvider.from_file(FIXTURES / "recorded" / f"{name}.json").extract(text)


@pytest.fixture
def provider(ws):
    return Recorded(ws)


@pytest.fixture
def mails(monkeypatch):
    sent = []

    def fake_compose(to, subject, body, attachment):
        sent.append({"to": to, "subject": subject, "body": body, "attachment": attachment})
        return system.Composed(True, system.mailto_link(to, subject, body))

    monkeypatch.setattr(system, "compose_email", fake_compose)
    monkeypatch.setattr(system, "open_path", lambda path: True)
    return sent


@pytest.fixture
def client(ws, provider, nlp, mails):
    app = create_app(ws, provider_factory=provider, nlp_loader=lambda: nlp, today=lambda: TODAY)
    with TestClient(app) as c:
        c.token = app.state.token
        yield c


def post(client, url, **data):
    return client.post(url, data={"token": client.token, **data}, follow_redirects=True)


def drop_and_read(client, ws, name):
    shutil.copy(FIXTURES / "docs" / name, ws.inbox / name)
    return post(client, "/inbox/process")


def latest_draft(ws):
    return store.connect(ws.db).execute("SELECT MAX(id) FROM drafts").fetchone()[0]


def test_every_page_renders(client):
    for url in (
        "/",
        "/drafts",
        "/invoices",
        "/quotes",
        "/customers",
        "/customers/new",
        "/settings",
        "/check-me",
        "/ueber",
        "/feierabend",
        "/customers/erika-meier",
    ):
        response = client.get(url)
        assert response.status_code == 200, url
        assert "FRITS" in response.text


def test_music_mood_is_chosen_in_settings(client, ws):
    post(client, "/sound")  # switch sound on
    page = client.get("/")
    assert 'data-frits-music="buero-block"' in page.text  # "Entspannt" unless chosen otherwise
    settings_page = client.get("/settings")
    assert "Musik-Stimmung" in settings_page.text
    assert 'data-frits-music="schwung"' in settings_page.text  # Probehören
    post(client, "/settings", music="motivierend")
    assert Settings.load(ws.settings_file).music == "motivierend"
    assert 'data-frits-music="schwung"' in client.get("/").text
    post(client, "/settings", music="gibt-es-nicht")
    assert Settings.load(ws.settings_file).music == "motivierend"
    # The Steuer-Quest is an adventure, whatever the mood
    assert 'data-frits-music="abenteuer"' in client.get("/steuer").text


def test_buttons_need_the_token(client, ws):
    shutil.copy(FIXTURES / "docs" / "01_notiz_meier.txt", ws.inbox)
    response = client.post("/inbox/process", data={"token": "wrong"})
    assert response.status_code == 403
    assert (ws.inbox / "01_notiz_meier.txt").exists()


def test_other_hostnames_are_refused(client):
    response = client.get("/", headers={"host": "evil.example:8765"})
    assert response.status_code == 403


def test_drop_review_approve_email_paid(client, ws, provider, mails):
    page = drop_and_read(client, ws, "01_notiz_meier.txt")
    assert "Fertig gelesen: 1 neuer Entwurf" in page.text
    # The AI saw placeholders only, and the review screen shows exactly that text.
    assert "Meier" not in provider.calls[0]
    draft_id = latest_draft(ws)
    review = client.get(f"/drafts/{draft_id}").text
    assert "Das hat die KI gesehen" in review
    assert "[PERSON_1]" in review or "[FIRMA_1]" in review
    assert "Erika Meier" in review  # the original, local only

    page = post(
        client,
        f"/drafts/{draft_id}",
        action="approve",
        customer="erika-meier",
        description=["Lampe im Flur montiert"],
        date=["12.10.2026"],
        quantity=["2"],
        unit=["Std"],
        price=["40"],
    )
    assert "Rechnung 2026-0001 ist fertig" in page.text
    assert 'class="stempel stempel-fertig"' in page.text
    assert client.get("/invoices/2026-0001/pdf").headers["content-type"] == "application/pdf"

    post(client, "/invoices/2026-0001/email")
    assert mails[0]["subject"] == "Rechnung 2026-0001"
    assert mails[0]["attachment"].name.startswith("2026-0001")
    assert "80,00 €" in mails[0]["body"]
    assert "an Mail übergeben" in client.get("/invoices").text

    page = post(client, "/invoices/2026-0001/paid")
    assert ">Bezahlt</span>" in page.text  # the ink stamp on the note
    assert "bezahlt am 02.11.2026" in client.get("/invoices").text


def test_editing_a_draft_saves_lines_and_german_numbers(client, ws):
    drop_and_read(client, ws, "01_notiz_meier.txt")
    draft_id = latest_draft(ws)
    post(
        client,
        f"/drafts/{draft_id}",
        action="save",
        customer="erika-meier",
        description=["Arbeit", "Material", ""],
        date=["", "", ""],
        quantity=["1,5", "1", ""],
        unit=["Std", "pauschal", "Std"],
        price=["40", "1.234,50", ""],
    )
    review = client.get(f"/drafts/{draft_id}").text
    assert "1.294,50 €" in review  # 1.5 * 40 + 1234.50


def test_bad_number_shows_a_message_not_an_error(client, ws):
    drop_and_read(client, ws, "01_notiz_meier.txt")
    draft_id = latest_draft(ws)
    page = post(
        client,
        f"/drafts/{draft_id}",
        action="save",
        description=["Arbeit"],
        date=[""],
        quantity=["zwei"],
        unit=["Std"],
        price=["40"],
    )
    assert page.status_code == 200
    assert "Bei der Menge von „Arbeit“ bitte nur eine Zahl eingeben" in page.text
    # Tester (Lena): the typed row vanished and no field was marked. The page now points at
    # the first row's quantity, and the form puts back what was typed.
    assert 'data-feld="quantity:0"' in page.text
    assert f'data-merken="entwurf-{draft_id}"' in page.text
    page = post(
        client,
        f"/drafts/{draft_id}",
        action="save",
        description=["Arbeit", "Kurs"],
        date=["", ""],
        quantity=["1", "1"],
        unit=["Std", "Std"],
        price=["40", "vierzig"],
    )
    assert "Beim Preis von „Kurs“" in page.text and 'data-feld="price:1"' in page.text


def test_injection_warning_is_shown(client, ws):
    drop_and_read(client, ws, "17_trick_notiz.txt")
    review = client.get(f"/drafts/{latest_draft(ws)}").text
    assert "Trick-Versuch" in review
    # No one-click button for a price that came from a document trying to steer the AI.
    assert "übernehmen</button>" not in review


def test_broken_file_goes_to_check_me_and_back(client, ws):
    page = drop_and_read(client, ws, "20_kaputt.pdf")
    assert "20_kaputt.pdf" in client.get("/check-me").text
    assert "Bitte einmal ansehen" in client.get("/").text
    post(client, "/check-me/retry", name="20_kaputt.pdf")
    assert (ws.inbox / "20_kaputt.pdf").exists()
    assert page.status_code == 200


def test_overdue_invoice_gets_a_friendly_reminder(client, ws, mails):
    drop_and_read(client, ws, "01_notiz_meier.txt")
    post(
        client,
        f"/drafts/{latest_draft(ws)}",
        action="approve",
        customer="erika-meier",
        description=["Arbeit"],
        date=[""],
        quantity=["2"],
        unit=["Std"],
        price=["40"],
    )
    conn = store.connect(ws.db)
    conn.execute("UPDATE invoices SET due_date = '2026-10-01'")
    assert "über dem Zahlungsziel" in client.get("/").text
    post(client, "/invoices/2026-0001/remind")
    assert mails[-1]["subject"] == "Zahlungserinnerung zu Rechnung 2026-0001"
    assert mails[-1]["attachment"].name == "2026-0001_Erinnerung_1.pdf"
    assert "1× erinnert" in client.get("/invoices").text


def test_cancel_creates_storno(client, ws):
    drop_and_read(client, ws, "01_notiz_meier.txt")
    post(
        client,
        f"/drafts/{latest_draft(ws)}",
        action="approve",
        customer="erika-meier",
        description=["Arbeit"],
        date=[""],
        quantity=["2"],
        unit=["Std"],
        price=["40"],
    )
    page = post(client, "/invoices/2026-0001/cancel")
    assert "Stornorechnung hat die Nummer 2026-0002" in page.text
    assert "Storno zu 2026-0001" in page.text


def test_quote_then_accept_becomes_invoice_draft(client, ws, mails):
    post(client, "/drafts/new", kind="quote", customer="baeckerei-sonnenschein")
    draft_id = latest_draft(ws)
    page = post(
        client,
        f"/drafts/{draft_id}",
        action="quote",
        customer="baeckerei-sonnenschein",
        description=["Backofen-Abzug reinigen"],
        date=[""],
        quantity=["3"],
        unit=["Std"],
        price=["45"],
    )
    assert "Angebot AN-2026-0001 ist fertig" in page.text
    assert client.get("/quotes/AN-2026-0001/pdf").status_code == 200
    post(client, "/quotes/AN-2026-0001/email")
    assert mails[-1]["subject"] == "Angebot AN-2026-0001"
    page = post(client, "/quotes/AN-2026-0001/accept")
    assert "Rechnungsentwurf" in page.text
    assert "135,00 €" in page.text


def test_new_customer_note_is_written_as_markdown(client, ws):
    post(
        client,
        "/customers",
        name="Paula Neu",
        company="Neu & Söhne",
        address="Gasse 2\n54321 Ort",
        email="paula@example.com",
        hourly_rate="50",
        aliases="Frau N.",
    )
    note = (ws.customers / "neu-soehne.md").read_text(encoding="utf-8")
    assert "type: kunde" in note
    assert "Gasse 2, 54321 Ort" in note
    assert "Neu &amp; Söhne" in client.get("/customers").text


def test_editing_customer_keeps_fritz_notes(client, ws):
    path = ws.customers / "erika-meier.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nMag Kaffee schwarz.\n", encoding="utf-8")
    post(
        client,
        "/customers",
        id="erika-meier",
        name="Erika Meier",
        address="Neue Str. 5\n10115 Berlin",
    )
    text = path.read_text(encoding="utf-8")
    assert "Mag Kaffee schwarz." in text
    assert "Neue Str. 5, 10115 Berlin" in text


def test_settings_save_and_key_goes_to_keychain(client, ws, monkeypatch):
    stored = {}
    monkeypatch.setattr("frits.web.app.store_api_key", lambda p, k: stored.update({p: k}))
    page = post(
        client,
        "/settings",
        name="Fritz Neu",
        address="Weg 1\n12345 Ort",
        iban="de89 3704 0044 0532 0130 00",
        hourly_rate="45,50",
        km_rate="0,30",
        vat_rate="19",
        payment_days="10",
        tax_mode="regelbesteuerung",
        provider="openrouter",
        api_key="sk-or-test",
        sound="on",
        verfahren_notiz="Papierbelege\n  im Regal.",
        checkboxes=["strict_local", "sound"],
    )
    assert "Schlüsselbund" in page.text
    assert Settings.load(ws.settings_file).verfahren_notiz == "Papierbelege im Regal."
    assert stored == {"openrouter": "sk-or-test"}
    saved = Settings.load(ws.settings_file)
    assert saved.hourly_rate == Decimal("45.50")
    assert saved.iban == "DE89370400440532013000"
    assert saved.tax_mode == "regelbesteuerung"
    assert saved.sound is True
    assert saved.effects == "ruhig"  # calm unless chosen otherwise
    assert "sk-or-test" not in ws.settings_file.read_text(encoding="utf-8")
    assert "sk-or-test" not in page.text


def test_spiel_effekte_ruhig_or_verspielt(client, ws):
    assert 'data-effekte="ruhig"' in client.get("/").text
    post(client, "/settings", effects="verspielt")
    assert Settings.load(ws.settings_file).effects == "verspielt"
    assert 'data-effekte="verspielt"' in client.get("/").text
    post(client, "/settings", effects="laut")  # unknown values change nothing
    assert Settings.load(ws.settings_file).effects == "verspielt"


def test_painted_pictures_take_over_when_they_are_there(tmp_path):
    from frits.web.app import mascot_or_picture, painted_pictures, picture_css

    assert painted_pictures(tmp_path) == {}
    assert picture_css({}) == ""
    assert "<svg" in mascot_or_picture({})("done")

    folder = tmp_path / "bilder"
    folder.mkdir()
    for name in ("fritte-fertig.webp", "schreibtisch-tag.png", "symbol-rechnungen.png"):
        (folder / name).write_bytes(b"picture")
    (folder / "stempel-rund.png").write_bytes(b"picture")
    (folder / "Böse Datei.png").write_bytes(b"picture")
    (folder / "notiz.txt").write_text("kein Bild")
    pictures = painted_pictures(tmp_path)
    assert set(pictures) == {
        "fritte-fertig",
        "schreibtisch-tag",
        "symbol-rechnungen",
        "stempel-rund",
    }
    assert 'src="/static/bilder/fritte-fertig.webp"' in mascot_or_picture(pictures)("done")
    assert "<svg" in mascot_or_picture(pictures)("thinking")  # not painted yet
    css = picture_css(pictures)
    assert "--tisch-bild: url(/static/bilder/schreibtisch-tag.png)" in css
    assert 'nav a[href="/invoices"]::before' in css
    assert "body .stempel {" in css


def test_pdf_route_never_serves_other_files(client, ws):
    store.connect(ws.db).execute(
        "INSERT INTO invoices (number, customer, issue_date, net, vat, gross, pdf_path)"
        " VALUES ('X', 'x', '2026-01-01', '0', '0', '0', ?)",
        (str(ws.settings_file),),
    )
    assert client.get("/invoices/X/pdf").status_code == 404


def test_feierabend_counts_today(client, ws):
    page = post(client, "/feierabend")
    assert "Wartet bis zum nächsten Mal" in page.text
    assert re.search(r"0 Rechnungen erstellt", page.text)


def test_receipt_becomes_expense_and_shows_in_euer(client, ws):
    shutil.copy(FIXTURES / "docs" / "10_bahnticket.png", ws.done / "10_bahnticket.png")
    conn = store.connect(ws.db)
    job = store.add_job(conn, "abc", "10_bahnticket.png", "receipt")
    assert "1 Beleg wartet" in client.get("/").text
    page = client.get(f"/expenses?receipt={job}")
    assert f'src="/receipts/{job}"' in page.text
    assert client.get(f"/receipts/{job}").status_code == 200

    page = post(
        client,
        "/expenses",
        date="2026-10-14",
        vendor="Deutsche Bahn",
        description="Fahrt zum Kunden",
        gross="23,80",
        vat_rate="0.19",
        category="reisekosten",
        source_file="10_bahnticket.png",
    )
    assert "23,80 €" in page.text
    assert "Belege zum Eintragen" not in page.text  # the receipt is done

    # A paid invoice plus the expense: EÜR shows both and the profit.
    drop_and_read(client, ws, "01_notiz_meier.txt")
    post(
        client,
        f"/drafts/{latest_draft(ws)}",
        action="approve",
        customer="erika-meier",
        description=["Arbeit"],
        date=[""],
        quantity=["2"],
        unit=["Std"],
        price=["40"],
    )
    post(client, "/invoices/2026-0001/paid")
    euer = client.get("/euer?year=2026").text
    assert "80,00 €" in euer and "23,80 €" in euer
    assert "Gewinn" in euer and "56,20 €" in euer  # Kleinunternehmer: gross counts
    assert client.get("/euer/2026.csv").status_code == 200
    assert client.get("/euer/2026.pdf").headers["content-type"] == "application/pdf"


def test_receipt_can_be_ignored(client, ws):
    conn = store.connect(ws.db)
    job = store.add_job(conn, "def", "privat.jpg", "receipt")
    post(client, f"/receipts/{job}/ignore")
    assert "Belege zum Eintragen" not in client.get("/expenses").text


def test_bad_expense_amount_is_a_message(client):
    page = post(
        client,
        "/expenses",
        date="2026-10-14",
        vendor="X",
        gross="viel",
        vat_rate="0.19",
        category="sonstiges",
    )
    assert "keine Zahl" in page.text


def test_expense_list_marks_cells_for_the_phone_layout(client):
    """Am Handy wird die Ausgaben-Tabelle zur Liste (frits.css, .table.ausgaben):
    Betrag und Summe brauchen ihre Klassen, sonst liegen sie rechts außer Sicht."""
    page = post(
        client,
        "/expenses",
        date="2026-10-14",
        vendor="Buchhandlung",
        description="Fachbuch",
        gross="24,00",
        vat_rate="0.07",
        category="sonstiges",
    )
    page = client.get("/expenses?year=2026")
    assert 'class="table ausgaben"' in page.text
    for cls in ("a-datum", "a-bei", "a-wofuer", "a-kat", "a-summe"):
        assert f'class="{cls}' in page.text or f"amount {cls}" in page.text
    assert "24,00" in page.text


def test_quit_stops_the_server(ws, provider, nlp):
    stopped = []
    app = create_app(
        ws, provider_factory=provider, nlp_loader=lambda: nlp, stop=lambda: stopped.append(1)
    )
    with TestClient(app) as c:
        assert c.get("/health").text == "FRITS"
        assert c.post("/quit", data={"token": "wrong"}).status_code == 403
        assert not stopped
        page = c.post("/quit", data={"token": app.state.token})
        assert "FRITS ist aus" in page.text
        assert stopped == [1]


def test_settings_offer_to_run_the_setup_again(client):
    page = client.get("/settings")
    assert 'href="/setup/welcome"' in page.text
    assert "Einführung wiederholen" in page.text


def test_goodbye_page_offers_a_restart(ws, provider, nlp, monkeypatch):
    app = create_app(ws, provider_factory=provider, nlp_loader=lambda: nlp, stop=lambda: None)
    with TestClient(app) as c:
        page = c.post("/quit", data={"token": app.state.token})
        assert "frits://start" not in page.text  # started from a terminal or Docker
        assert "docker compose up ui" in page.text
        assert "/health?reopen=1" in page.text  # the tab comes back by itself
        monkeypatch.setenv("FRITS_APP", "1")
        page = c.post("/quit", data={"token": app.state.token})
        assert 'href="frits://start"' in page.text


def test_waiting_goodbye_tab_means_no_second_tab(ws, provider, nlp, monkeypatch):
    from frits import web

    opened = []
    monkeypatch.setattr(web.webbrowser, "open", opened.append)
    monkeypatch.setattr(web, "_port_free", lambda port: False)  # FRITS answers at once
    monkeypatch.setenv("FRITS_VORLADEN", "0")
    app = create_app(ws, provider_factory=provider, nlp_loader=lambda: nlp)
    web._when_ready(app, 8765, "http://127.0.0.1:8765/", True)
    assert opened == ["http://127.0.0.1:8765/"]  # a normal start opens the browser
    with TestClient(app) as c:
        assert c.get("/health?reopen=1").text == "FRITS"
    web._when_ready(app, 8765, "http://127.0.0.1:8765/", True)
    assert len(opened) == 1  # the old tab came back, so no new one


def test_old_tab_and_loading_page_never_both_open_frits(ws, provider, nlp):
    # The Mac app's loading page finds FRITS first: the old "FRITS ist aus" tab steps back.
    app = create_app(ws, provider_factory=provider, nlp_loader=lambda: nlp)
    with TestClient(app) as c:
        found = c.get("/health?ladeseite=1")
        assert found.text == "FRITS" and found.headers["access-control-allow-origin"] == "*"
        assert c.get("/health?reopen=1").text == "FRITS-neu"
        assert c.get("/health").text == "FRITS"
    # The old tab was faster: the loading page says FRITS is open elsewhere.
    app = create_app(ws, provider_factory=provider, nlp_loader=lambda: nlp, stop=lambda: None)
    with TestClient(app) as c:
        assert c.get("/health?reopen=1").text == "FRITS"
        assert c.get("/health?ladeseite=1").text == "FRITS-offen"
        page = c.post("/quit", data={"token": app.state.token})
        assert "FRITS-neu" in page.text  # the goodbye tab knows the answer


def test_loading_page_means_no_browser_tab_and_the_model_is_warmed(ws, provider, monkeypatch):
    from frits import web

    opened, loads = [], []
    monkeypatch.setattr(web.webbrowser, "open", opened.append)
    monkeypatch.setattr(web, "_port_free", lambda port: False)
    monkeypatch.setattr(web.time, "sleep", lambda s: None)
    monkeypatch.delenv("FRITS_VORLADEN", raising=False)
    app = create_app(ws, provider_factory=provider, nlp_loader=lambda: loads.append(1) or "nlp")
    web._when_ready(app, 8765, "http://127.0.0.1:8765/", False)
    assert opened == [] and loads == [1]  # the language model is ready before the first read
    assert app.state.nlp_laden() == "nlp" and loads == [1]  # and loaded only once


def test_second_start_reopens_the_running_frits(monkeypatch, tmp_path):
    import json

    from frits import web

    monkeypatch.setenv("FRITS_HOME", str(tmp_path))
    (tmp_path / web.RUN_FILE).write_text(json.dumps(web._me() | {"pid": 1}))
    opened = []
    monkeypatch.setattr(web, "running_frits", lambda port=web.DEFAULT_PORT: True)
    monkeypatch.setattr(web.webbrowser, "open", opened.append)
    monkeypatch.setattr(web, "stop_other_frits", lambda *a, **k: pytest.fail("same FRITS"))
    web.serve()
    assert opened == ["http://127.0.0.1:8765/"]


def test_starting_a_new_version_ends_the_old_one(monkeypatch, tmp_path):
    import json

    import uvicorn

    from frits import web

    monkeypatch.setenv("FRITS_HOME", str(tmp_path))
    (tmp_path / web.RUN_FILE).write_text(json.dumps({"pid": 1, "version": "1.3.0", "code": "x"}))
    monkeypatch.setattr(web, "running_frits", lambda port=web.DEFAULT_PORT: True)
    stopped, started, told = [], [], []
    monkeypatch.setattr(web, "stop_other_frits", lambda port: stopped.append(port) or True)
    monkeypatch.setattr(web, "free_port", lambda: web.DEFAULT_PORT)
    monkeypatch.setattr(web, "_tell", told.append)
    monkeypatch.setattr("frits.web.app.create_app", lambda: object())
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.append(kw["port"]))
    monkeypatch.setattr(web, "_when_ready", lambda *a: None)  # no real server here
    web.serve(open_browser=False)
    assert stopped == [web.DEFAULT_PORT] and started == [web.DEFAULT_PORT] and not told
    assert not (tmp_path / web.RUN_FILE).exists()  # cleaned up when this FRITS ends

    # If the old one won't go, say so clearly and don't start a second FRITS.
    monkeypatch.setattr(web, "stop_other_frits", lambda port: False)
    web.serve(open_browser=False)
    assert started == [web.DEFAULT_PORT] and "neu starten" in told[0]


def test_an_old_frits_process_is_really_ended(monkeypatch, tmp_path):
    """A real process standing in for an old FRITS that holds a port and answers /health."""
    import json
    import subprocess
    import sys

    from frits import web

    script = tmp_path / "alte_frits.py"
    script.write_text(
        "import http.server, sys\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(b'FRITS')\n"
        "    def log_message(self, *a): pass\n"
        "http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()\n"
    )
    port = web.free_port(18765)
    old = subprocess.Popen([sys.executable, str(script), str(port)])
    try:
        for _ in range(50):
            if web.running_frits(port):
                break
            time.sleep(0.1)
        assert web.running_frits(port)
        monkeypatch.setenv("FRITS_HOME", str(tmp_path))
        (tmp_path / web.RUN_FILE).write_text(json.dumps({"pid": old.pid}))
        assert web.stop_other_frits(port, wait=5)
        assert old.wait(timeout=5) is not None and not web.running_frits(port)
    finally:
        old.kill()


def test_files_dropped_in_the_browser_land_in_the_inbox(client, ws):
    page = client.post(
        "/inbox/upload",
        data={"token": client.token, "back": "/"},
        files=[
            ("files", ("notiz.txt", b"2 Std Rasen", "text/plain")),
            ("files", ("../../boese.txt", b"x", "text/plain")),
            ("files", ("programm.exe", b"x", "application/octet-stream")),
        ],
        follow_redirects=True,
    )
    names = sorted(p.name for p in ws.inbox.iterdir())
    assert names == ["boese.txt", "notiz.txt"]  # never outside the inbox, never .exe
    assert "Im Eingang: notiz.txt, boese.txt" in page.text
    assert "programm.exe" in page.text  # told why it was refused


def test_upload_needs_the_token(client, ws):
    response = client.post(
        "/inbox/upload", files=[("files", ("a.txt", b"x", "text/plain"))], data={}
    )
    assert response.status_code == 403
    assert not list(ws.inbox.iterdir())


def test_new_customer_from_a_draft_is_prefilled_and_attached(client, ws):
    drop_and_read(client, ws, "14_herr_wolf.txt")
    draft_id = latest_draft(ws)
    page = client.get(f"/customers/new?back=/drafts/{draft_id}")
    assert "Aus dem Entwurf vorausgefüllt" in page.text
    assert "Wolf" in page.text
    assert "Am Hang 5" in page.text
    post(
        client,
        "/customers",
        name="Herr Wolf",
        address="Am Hang 5\n12345 Musterstadt",
        back=f"/drafts/{draft_id}",
    )
    row = store.connect(ws.db).execute("SELECT data FROM drafts WHERE id = ?", (draft_id,))
    assert '"customer_id": "herr-wolf"' in row.fetchone()[0]


def test_price_list_prices_lines_that_are_not_hours(client, ws):
    post(
        client,
        "/settings",
        price_name=["Fenster putzen", ""],
        price_unit=["Stk", "Stk"],
        price_value=["25", ""],
    )
    assert Settings.load(ws.settings_file).price_list[0].price == Decimal(25)
    drop_and_read(client, ws, "01_notiz_meier.txt")
    draft_id = latest_draft(ws)
    post(
        client,
        f"/drafts/{draft_id}",
        action="save",
        customer="erika-meier",
        description=["3 Fenster geputzt"],
        date=[""],
        quantity=["3"],
        unit=["Stk"],
        price=[""],
    )
    page = client.get(f"/drafts/{draft_id}")
    assert 'value="25,00"' in page.text


def test_invoice_numbers_follow_the_pattern_and_continue(client, ws):
    post(client, "/settings", number_pattern="RE-JJ-NNN", last_number_before="RE-26-041")
    page = client.get("/settings")
    assert "RE-26-042" in page.text
    page = post(client, "/settings", number_pattern="RE/JJ/NNN")
    assert "Nummern-Schema" in page.text


def test_about_page_and_bug_report_without_personal_data(client, mails):
    page = client.get("/ueber")
    assert page.status_code == 200
    assert "Über FRITS" in page.text and "Fehler melden" in page.text
    assert 'aria-current="page"' in page.text
    assert "nicht-kommerzielles Projekt" in page.text and "KI-Podcast der ARD" in page.text
    assert "kein Projekt der ARD" in page.text and "als Privatperson" in page.text
    for name in ("Espenlaub", "Schmalzried", "Gregor", "Freund Fritz"):
        assert name not in page.text
    assert "Platzhalter" not in page.text
    assert 'href="https://www.ardsounds.de/sendung/der-ki-podcast/' in page.text
    assert "keine Steuer-, Rechts- oder Finanzberatung" in page.text
    assert "Vorsatz und grober Fahrlässigkeit" in page.text

    page = post(client, "/ueber/fehler")
    assert "Fehler-Mail ist in Mail geöffnet" in page.text
    mail = mails[-1]
    assert mail["to"] == "hallo@kilg.de"
    assert mail["subject"].startswith("[FRITS Fehler] Version ")
    assert mail["attachment"] is None
    assert "FRITS-Version:" in mail["body"] and "Computer:" in mail["body"]
    # Nothing from Fritz's settings or customers goes into the mail.
    for private in ("Fritz Beispiel", "Werkstattweg", "DE89370400440532013000", "Meier"):
        assert private not in mail["body"]


def test_bug_report_falls_back_to_a_mail_link(client, monkeypatch):
    monkeypatch.setattr(
        system,
        "compose_email",
        lambda to, subject, body, attachment: system.Composed(
            False, system.mailto_link(to, subject, body)
        ),
    )
    page = post(client, "/ueber/fehler")
    assert "mailto:hallo%40kilg.de?subject=%5BFRITS%20Fehler%5D" in page.text
    assert "PDF" not in page.text
    assert "Rechnungsordner" not in page.text


def test_rare_pages_sit_behind_mehr_and_it_shows_where_you_are(client):
    page = client.get("/").text
    nav = page[page.index("<nav>") : page.index("</nav>")]
    assert '<details class="mehr">' in nav
    for label in ("So funktionieren KI-Agenten", "Einstellungen", "Über FRITS"):
        assert label in nav[nav.index("mehr-liste") :]
    settings_page = client.get("/settings").text
    assert '<summary aria-current="page">Mehr</summary>' in settings_page
    assert '<summary aria-current="page">' not in page


def test_painted_fritte_has_a_fixed_size_and_settings_preview_the_effects(client, ws):
    from frits.web.app import mascot_or_picture

    html = mascot_or_picture({"fritte-fertig": "/static/bilder/fritte-fertig.webp"})("done")
    assert 'width="84" height="112"' in html
    page = client.get("/settings").text
    assert "So sieht's aus" in page and "data-vorschau" in page


def test_hints_from_tester_round_3(client, ws, nlp):
    from dataclasses import replace

    from frits.billing import Draft, Line, new_draft

    settings = Settings.load(ws.settings_file)
    replace(settings, tax_mode="regelbesteuerung", vat_rate=Decimal(19)).save(ws.settings_file)
    conn = store.connect(ws.db)
    eu = Draft(
        "wien",
        "ORF",
        "Würzburggasse 30, 1136 Wien, Österreich",
        [Line("Beitrag", "2026-10-20", Decimal(1), "pauschal", Decimal(800))],
        [],
        [],
        "notiz.txt",
        customer_tax="eu",
        customer_ust_id="",
    )
    draft_id = new_draft(conn, eu)
    # One draft that needs something: singular, not "Da liegen Entwürfe".
    assert "Da liegt ein Entwurf, der noch etwas von dir braucht" in client.get("/").text
    # Hints only solvable on another page are marked, so the page points there instead of
    # asking "jetzt erstellen?" first.
    page = client.get(f"/drafts/{draft_id}").text
    assert 'data-elsewhere="customer">Für Reverse Charge fehlt' in page

    # The chat says up front that no AI is connected.
    replace(Settings.load(ws.settings_file), strict_local=True).save(ws.settings_file)
    app = create_app(ws, nlp_loader=lambda: nlp, today=lambda: TODAY)
    with TestClient(app) as plain:
        assert "Ohne verbundene KI" in plain.get("/chat").text


def test_painted_pictures_keep_their_space_while_loading(tmp_path):
    from PIL import Image

    from frits.web.app import picture_tag

    (tmp_path / "bilder").mkdir()
    Image.new("RGB", (420, 299)).save(tmp_path / "bilder" / "leer-eingang.webp")
    tag = picture_tag(tmp_path, {"leer-eingang": "/static/bilder/leer-eingang.webp"})
    html = tag("leer-eingang", "leer-bild")
    assert 'class="leer-bild"' in html and 'width="420" height="299"' in html
    assert tag("gibts-nicht", "x") == ""


def test_painted_icons_replace_the_emojis(client):
    # Marie's painted icons: bell in the header, paper tray on the drop field, salt,
    # course stages. Without the pictures FRITS falls back to the drawn/emoji versions.
    from frits.web.app import SALZ, salz_oder_bild

    home = client.get("/").text
    assert "/static/bilder/symbol-klang.webp" in home
    assert "/static/bilder/symbol-ablage.webp" in home and "📥" not in home
    assert "/static/bilder/symbol-salz.webp" in client.get("/fritte/laden").text
    course = client.get("/lernen").text
    assert (
        "/static/bilder/etappe-start.webp" in course
        and "/static/bilder/etappe-agent.webp" in course
    )
    assert salz_oder_bild({}) == SALZ


def test_fritte_lives_in_a_painted_room_with_painted_things(client):
    page = client.get("/fritte").text
    assert "/static/bilder/zimmer-1.webp" in page and "/static/bilder/gewuerz-pfeffer.webp" in page
    assert "/static/bilder/ding-teetasse.webp" in client.get("/fritte/laden").text


def test_check_me_file_can_be_seen_and_becomes_a_receipt(client, ws):
    drop_and_read(client, ws, "20_kaputt.pdf")
    page = client.get("/check-me").text
    assert "Ein Beleg: als Ausgabe eintragen" in page and "Ansehen" in page
    seen = client.get("/check-me/datei", params={"name": "20_kaputt.pdf"})
    assert (
        seen.status_code == 200
        and seen.content == (FIXTURES / "docs" / "20_kaputt.pdf").read_bytes()
    )
    assert client.get("/check-me/datei", params={"name": "../settings.toml"}).status_code == 404
    assert (
        client.get("/check-me/datei", params={"name": "20_kaputt.pdf.grund.txt"}).status_code == 404
    )
    response = post(client, "/check-me/beleg", name="20_kaputt.pdf")
    assert "Als Beleg übernommen" in response.text
    assert "receipt=" in str(response.url)
    assert not (ws.check_me / "20_kaputt.pdf").exists() and (ws.done / "20_kaputt.pdf").exists()
    status = store.connect(ws.db).execute("SELECT status FROM jobs").fetchone()[0]
    assert status == "receipt"


def test_check_me_file_can_be_put_away(client, ws):
    drop_and_read(client, ws, "20_kaputt.pdf")
    response = post(client, "/check-me/weglegen", name="20_kaputt.pdf")
    assert "zählt nirgends mit" in response.text and "Alles erledigt" in response.text
    assert (ws.done / "20_kaputt.pdf").exists()


def test_receipt_dropped_on_expenses_after_check_me_is_taken(client, ws):
    drop_and_read(client, ws, "20_kaputt.pdf")
    data = (FIXTURES / "docs" / "20_kaputt.pdf").read_bytes()
    response = client.post(
        "/expenses/beleg",
        data={"token": client.token},
        files={"files": ("20_kaputt.pdf", data, "application/pdf")},
        follow_redirects=True,
    )
    assert "kenne ich schon" not in response.text and "receipt=" in str(response.url)
    assert not any(ws.check_me.iterdir())
    again = client.post(
        "/expenses/beleg",
        data={"token": client.token},
        files={"files": ("20_kaputt.pdf", data, "application/pdf")},
        follow_redirects=True,
    )
    assert "wartet hier aufs Eintragen" in again.text


def test_ticket_that_redaction_is_unsure_about_becomes_a_receipt(client, ws, monkeypatch):
    from frits import pipeline
    from frits.redact import LeakError

    def unsure(*args, **kwargs):
        raise LeakError("etwas übrig")

    monkeypatch.setattr(pipeline, "redact", unsure)
    (ws.inbox / "DB_Ticket.txt").write_text(
        "DB Fernverkehr Online-Ticket\nFahrkarte Berlin - Hamburg\nGesamtpreis 49,90 €\n"
    )
    response = post(client, "/inbox/process")
    assert "1 Beleg" in response.text
    assert (ws.done / "DB_Ticket.txt").exists()
    row = store.connect(ws.db).execute("SELECT status, filename FROM jobs").fetchone()
    assert tuple(row) == ("receipt", "DB_Ticket.txt")


def test_questions_are_answered_in_free_text_and_redacted(client, ws, provider):
    from frits.billing import load_draft, save_draft

    drop_and_read(client, ws, "01_notiz_meier.txt")
    draft_id = latest_draft(ws)
    conn = store.connect(ws.db)
    draft, _ = load_draft(conn, draft_id)
    draft.questions = ["An welchem Tag war das?"]
    save_draft(conn, draft_id, draft)
    page = client.get(f"/drafts/{draft_id}").text
    assert 'name="answers"' in page and "Mit meinen Antworten neu vorschlagen" in page

    empty = post(client, f"/drafts/{draft_id}", action="answer", answers=" ")
    assert "Schreib deine Antworten" in empty.text

    shutil.copy(FIXTURES / "docs" / "01_notiz_meier.txt", ws.inbox)  # the recording to answer with
    page = post(
        client,
        f"/drafts/{draft_id}",
        action="answer",
        answers="Am 12.10., Frau Meier hat zugeschaut.",
    )
    assert "Neu vorgeschlagen" in page.text
    sent = provider.calls[-1]
    assert "Antworten des Auftragnehmers" in sent and "Am 12.10." in sent
    assert "Meier" not in sent  # the answers are redacted like the note
    audit = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    assert audit == 2


def test_hand_made_draft_has_nothing_to_read_again(client, ws):
    post(client, "/drafts/new")
    draft_id = latest_draft(ws)
    page = post(client, f"/drafts/{draft_id}", action="answer", answers="Hallo")
    assert "keine KI gelesen" in page.text


def test_letter_under_check_me_opens_in_the_notfall_chat(client, ws):
    (ws.check_me / "brief.txt").write_text(
        "Sehr geehrter Herr Beispiel, bitte zahlen Sie bis 30.11."
    )
    page = post(client, "/check-me/notfall", name="brief.txt")
    assert "<h1>Chat</h1>" in page.text and "Das sieht" in page.text
    assert "/chat/" in str(page.url)
    assert (ws.check_me / "brief.txt").exists()  # stays until Fritz puts it away


def test_letter_that_cant_be_redacted_goes_to_the_chat_anyway(client, ws, monkeypatch):
    """No more "drag it there again": the file is in the chat, waiting, nothing sent."""
    from frits import notfall
    from frits.redact import LeakError

    def nie(redacted, people=()):
        raise LeakError("test")

    monkeypatch.setattr(notfall, "assert_no_leaks", nie)
    (ws.check_me / "brief.txt").write_text("Sehr geehrter Herr Beispiel, bitte zahlen.")
    page = post(client, "/check-me/notfall", name="brief.txt")
    assert "/chat/" in str(page.url)
    assert "Noch nicht sicher geschwärzt" in page.text and "brief.txt" in page.text
    assert "Zieh die Datei" not in page.text
    g = next(iter(client.app.state.notfall.values()))
    assert len(g.wartend) == 1 and not g.docs


def test_earlier_invoice_is_entered_from_the_invoices_page(client, ws):
    page = client.get("/invoices").text
    assert "Rechnung von früher nachtragen" in page and "Zurückdatieren" in page
    response = client.post(
        "/invoices/frueher",
        data={
            "token": client.token,
            "number": "2026-0003",
            "customer": "Radio Süd",
            "issue_date": "2026-03-01",
            "description": "Moderation",
            "net": "1.200,00",
            "tax": "klein",
            "paid": "2026-03-20",
        },
        files={"original": ("alt.pdf", b"%PDF-1.4 alt", "application/pdf")},
        follow_redirects=True,
    )
    assert "Rechnung 2026-0003 ist nachgetragen" in response.text
    assert "nachgetragen</small>" in response.text
    assert client.get("/invoices/2026-0003/pdf").content == b"%PDF-1.4 alt"
    again = post(
        client,
        "/invoices/frueher",
        number="2026-0003",
        customer="X",
        issue_date="2026-03-01",
        net="5",
        tax="klein",
    )
    assert "gibt es in FRITS schon" in again.text
    future = post(
        client,
        "/invoices/frueher",
        number="2026-0099",
        customer="X",
        issue_date="2027-01-01",
        net="5",
        tax="klein",
    )
    assert "Zukunft" in future.text


def _old_invoice_pdf(lines):
    from fpdf import FPDF

    from frits.render import FONTS

    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("dejavu", fname=str(FONTS / "DejaVuSans.ttf"))
    pdf.set_font("dejavu", size=11)
    for line in lines:
        if line:
            pdf.cell(text=line, new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.ln()
    return bytes(pdf.output())


OLD_INVOICE = [
    "Fritz Beispiel · Werkstattweg 1 · 12345 Musterstadt",
    "Radio Süd GmbH",
    "Hauptstraße 5",
    "80331 München",
    "",
    "Musterstadt, 12. März 2026",
    "Rechnung Nr. 2026-0002",
    "Leistungsdatum: 10.03.2026",
    "Pos. Beschreibung Menge Gesamt",
    "1 Moderation Workshop 1 pauschal 1.200,00 €",
    "Gesamtbetrag 1.200,00 €",
    "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet.",
]


def _staged(ws):
    return sorted(p.name for p in ws.staging.iterdir()) if ws.staging.is_dir() else []


def test_old_invoice_dropped_is_read_and_entered_with_its_original(client, ws):
    page = client.get("/invoices").text
    assert 'action="/invoices/frueher/lesen"' in page and "Alte Rechnung hierher ziehen" in page
    original = _old_invoice_pdf(OLD_INVOICE)
    response = client.post(
        "/invoices/frueher/lesen",
        data={"token": client.token},
        files={"files": ("alte-rechnung.pdf", original, "application/pdf")},
        follow_redirects=True,
    )
    assert "frueher=" in str(response.url)
    [token] = _staged(ws)
    page = response.text
    assert 'id="frueher" open' in page
    assert "Vorausgefüllt aus dem Dokument, hier auf deinem Computer gelesen" in page
    assert "Nicht gefunden" not in page
    assert 'value="2026-0002" class="imported"' in page
    assert 'value="Radio Süd GmbH" class="imported"' in page
    assert 'value="2026-03-12" class="imported"' in page
    assert 'value="2026-03-10" class="imported"' in page
    assert 'value="1.200,00" class="imported"' in page
    assert '<option value="klein" selected>' in page
    assert f'name="staged" value="{token}"' in page
    assert "Verwerfen" in page and 'name="original"' not in page  # the PDF is the original
    assert client.get(f"/invoices/frueher/{token}/original").content == original
    # Fritz checks, adds the payment day and presses "Nachtragen".
    response = post(
        client,
        "/invoices/frueher",
        staged=token,
        number="2026-0002",
        customer="Radio Süd GmbH",
        issue_date="2026-03-12",
        service_date="2026-03-10",
        description="Moderation Workshop",
        net="1.200,00",
        tax="klein",
        paid="2026-03-20",
    )
    assert "Rechnung 2026-0002 ist nachgetragen" in response.text
    assert client.get("/invoices/2026-0002/pdf").content == original
    assert _staged(ws) == []  # nothing lingers once it is entered


def test_missing_fields_get_the_focus_and_a_mistake_keeps_the_review(client, ws):
    response = client.post(
        "/invoices/frueher/lesen",
        data={"token": client.token, "text": "Rechnung Nr. 7\nGesamtbetrag 300,00 €\n"},
        follow_redirects=True,
    )
    [token] = _staged(ws)
    page = response.text
    assert (
        "Nicht gefunden habe ich den Kunden, das Rechnungsdatum, den Betrag und die Umsatzsteuer"
        in page
    )
    assert 'name="customer" required maxlength="120" value="" autofocus' in page
    assert 'value="7" class="imported"' in page
    assert 'name="original"' in page  # pasted text: a PDF can still be added by hand
    future = post(
        client,
        "/invoices/frueher",
        staged=token,
        number="7",
        customer="X",
        issue_date="2027-01-01",
        net="300",
        tax="klein",
    )
    assert "Zukunft" in future.text and f"frueher={token}" in str(future.url)
    assert _staged(ws) == [token]


def test_old_invoice_photo_is_read_with_ocr_but_only_a_pdf_is_filed(client, ws, needs_ocr):
    from io import BytesIO

    from PIL import Image, ImageDraw, ImageFont

    from frits.render import FONTS

    img = Image.new("RGB", (1400, 700), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONTS / "DejaVuSans.ttf"), 40)
    for i, line in enumerate(
        (
            "Rechnungsnummer: 2026-0004",
            "Datum: 05.06.2026",
            "Gesamtbetrag 450,00 EUR",
            "Kleinunternehmer, keine Umsatzsteuer",
        )
    ):
        draw.text((60, 60 + i * 120), line, fill="black", font=font)
    photo = BytesIO()
    img.save(photo, "PNG")
    response = client.post(
        "/invoices/frueher/lesen",
        data={"token": client.token},
        files={"files": ("foto.png", photo.getvalue(), "image/png")},
        follow_redirects=True,
    )
    page = response.text
    assert 'value="2026-0004" class="imported"' in page
    assert 'value="2026-06-05" class="imported"' in page
    assert 'value="450,00" class="imported"' in page
    assert "Als Original lege ich nur PDFs ab" in page and 'name="original"' in page
    assert '<img class="receipt"' in page


def test_discarding_an_old_invoice_removes_the_file(client, ws):
    client.post(
        "/invoices/frueher/lesen",
        data={"token": client.token},
        files={"files": ("alt.pdf", _old_invoice_pdf(OLD_INVOICE), "application/pdf")},
    )
    [token] = _staged(ws)
    response = post(client, "/invoices/frueher/verwerfen", staged=token)
    assert "Verworfen. Es wurde nichts eingetragen." in response.text
    assert _staged(ws) == []
    assert store.connect(ws.db).execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0
    gone = client.get(f"/invoices?frueher={token}")
    assert "nicht mehr da" in gone.text and 'id="frueher" open' not in gone.text


def test_bad_staged_tokens_are_refused(client, ws):
    (ws.root / "geheim.pdf").write_bytes(b"%PDF-1.4 geheim")
    for bad in ("../geheim.pdf", "..", "a/b", "x" * 21, "%2e%2e"):
        response = post(
            client,
            "/invoices/frueher",
            staged=bad,
            number="2026-0005",
            customer="X",
            issue_date="2026-03-01",
            net="5",
            tax="klein",
        )
        assert response.status_code == 400, bad
        assert post(client, "/invoices/frueher/verwerfen", staged=bad).status_code == 400
    assert client.get("/invoices/frueher/..%2Fgeheim.pdf/original").status_code in (400, 404)
    assert client.get(f"/invoices/frueher/{'y' * 22}/original").status_code == 404
    assert store.connect(ws.db).execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0
    assert (ws.root / "geheim.pdf").exists()


def test_staged_old_invoices_older_than_a_day_are_cleaned_up(client, ws):
    old = ws.staging / ("o" * 22)
    old.mkdir(parents=True)
    (old / "gelesen.json").write_text("{}")
    long_ago = time.time() - 2 * 24 * 3600
    os.utime(old, (long_ago, long_ago))
    post(client, "/invoices/frueher/lesen", text="Rechnung Nr. 1")
    assert old.name not in _staged(ws) and len(_staged(ws)) == 1


def test_unreadable_drop_is_a_calm_error(client, ws):
    response = client.post(
        "/invoices/frueher/lesen",
        data={"token": client.token},
        files={"files": ("tabelle.xlsx", b"PK", "application/octet-stream")},
        follow_redirects=True,
    )
    assert "selbst ein" in response.text
    assert _staged(ws) == []


def test_phones_get_a_tab_bar_but_not_during_the_first_setup(client, ws):
    page = client.get("/").text
    tabs = page[
        page.index('<nav class="tabs"') : page.index("</nav>", page.index('<nav class="tabs"'))
    ]
    for label in ("Heute", "Ausgaben", "Quest", "Mehr"):
        assert label in tabs
    assert 'href="/invoices"' in tabs  # the rest sits behind "Mehr"
    settings = Settings.load(ws.settings_file)
    replace(settings, setup_done=False).save(ws.settings_file)
    assert 'class="tabs"' not in client.get("/setup/welcome").text


def test_phone_lists_and_the_category_that_follows_the_vendor(client):
    """Lena Runde 69: the invoice list is a card per invoice on the phone (classes for the
    grid), and the expense form knows the receipt reader's words, so "Deutsche Bahn"
    becomes a business trip while typing."""
    import json
    from html import unescape

    from frits.beleg import category_patterns, guess_category

    seite = client.get("/expenses").text
    roh = seite.split('name="category" data-raten="')[1].split('"')[0]
    muster = json.loads(unescape(roh))
    assert muster == category_patterns()
    assert guess_category("Deutsche Bahn") == "reisekosten"
    assert "reisekosten" in [k for k, _ in muster]
    vorlage = Path(__file__).parents[1] / "src/frits/web/templates/invoices.html"
    assert vorlage.read_text(encoding="utf-8").count('class="table rechnungen"') == 2


def test_mail_fallback_offers_the_pdf_to_download(client, ws, monkeypatch):
    """Lena (Runde 72): on the phone there is no folder to open, so the page that appears
    when Mail does not open offers the PDF itself, for the invoice and for a reminder."""
    drop_and_read(client, ws, "01_notiz_meier.txt")
    post(
        client,
        f"/drafts/{latest_draft(ws)}",
        action="approve",
        customer="erika-meier",
        description=["Arbeit"],
        date=[""],
        quantity=["2"],
        unit=["Std"],
        price=["40"],
    )
    monkeypatch.setattr(
        system,
        "compose_email",
        lambda to, subject, body, attachment: system.Composed(
            False, system.mailto_link(to, subject, body)
        ),
    )
    page = post(client, "/invoices/2026-0001/email").text
    assert 'href="/invoices/2026-0001/pdf" download' in page
    assert 'class="nur-maus"' in page  # the folder button stays for the Mac
    store.connect(ws.db).execute("UPDATE invoices SET due_date = '2026-10-01'")
    page = post(client, "/invoices/2026-0001/remind").text
    link = re.search(r'href="(/reminders/\d+/pdf)" download', page).group(1)
    pdf = client.get(link)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "Erinnerung_1.pdf" in pdf.headers["content-disposition"]
    assert client.get("/reminders/999/pdf").status_code == 404


def test_train_tickets_suggest_seven_percent_but_coaches_do_not():
    """Lena (Runde 72): "Deutsche Bahn" also sets 7 %. A Fernbus keeps 19 % (§ 12 Abs. 2
    Nr. 10 UStG reduces only rail and local transport)."""
    from frits.beleg import guess_vat, reduced_pattern

    [[rate, pattern]] = reduced_pattern()
    assert rate == "0.07"
    for text in (
        "Deutsche Bahn",
        "DB Fernverkehr",
        "BahnCard 25",
        "Fachbuch",
        "ICE 597",
        "FlixTrain",
    ):
        assert re.search(pattern, text, re.IGNORECASE), text
        assert guess_vat(text) == "0.07"
    for text in ("Flixbus", "Aral Tankstelle", "Adobe", "Ice Cream Factory", "DB Schenker"):
        assert not re.search(pattern, text, re.IGNORECASE), text


def test_phone_cards_for_customer_credits_and_settings_at_the_limit():
    templates = Path(__file__).parent.parent / "src" / "frits" / "web" / "templates"
    assert 'class="table rechnungen"' in (templates / "customer.html").read_text()
    assert 'href="/settings#steuern"' in (templates / "draft.html").read_text()
    assert (
        "data-raten" in (templates / "expenses.html").read_text().split('name="vat_rate"')[1][:80]
    )


def test_quotes_as_cards_and_their_own_folder(client, ws, monkeypatch):
    """Lena (Runde 74): the quote list is a card on the phone too, a mail fallback for a
    quote opens quotes/, and a number too big for SQLite is a missing page, not a 500."""
    post(client, "/drafts/new", kind="quote", customer="baeckerei-sonnenschein")
    post(
        client,
        f"/drafts/{latest_draft(ws)}",
        action="quote",
        customer="baeckerei-sonnenschein",
        description=["Backofen-Abzug reinigen"],
        date=[""],
        quantity=["3"],
        unit=["Std"],
        price=["45"],
    )
    assert 'class="table rechnungen"' in client.get("/quotes").text
    monkeypatch.setattr(
        system,
        "compose_email",
        lambda to, subject, body, attachment: system.Composed(
            False, system.mailto_link(to, subject, body)
        ),
    )
    page = post(client, "/quotes/AN-2026-0001/email").text
    assert 'action="/open/quotes"' in page and "Angebotsordner öffnen" in page
    assert "<h2>Angebot AN-2026-0001</h2>" in page and 'name="back" value="/mailto"' in page
    assert 'href="/quotes/AN-2026-0001/pdf" download' in page
    for url in (
        "/reminders/99999999999999999999/pdf",
        "/reminders/-99999999999999999999/pdf",
        "/reminders/+99999999999999999999/pdf",
        "/drafts/123456789012345678901",
        "/drafts/-123456789012345678901",
        "/euer/0.csv",
        "/euer/-1.pdf",
        "/euer/1_000.csv",
        "/euer/99999999999999999999.csv",
        "/euer/-1/verfahrensdoku.pdf",
        "/euer/0/buchungsliste.csv",
        "/euer?year=-1",
    ):
        assert client.get(url).status_code == 404, url
    assert not (ws.root / "euer").exists() or not any(
        "-1" in p.name or "1000" in p.name or "_0." in p.name for p in (ws.root / "euer").iterdir()
    )

import re
import shutil
from datetime import date
from decimal import Decimal

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
    assert "Fertig gelesen: 1 neue Entwürfe" in page.text
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
    assert "keine Zahl" in page.text


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
        checkboxes=["strict_local", "sound"],
    )
    assert "Schlüsselbund" in page.text
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


def test_second_start_reopens_the_running_frits(monkeypatch):
    from frits import web

    opened = []
    monkeypatch.setattr(web, "running_frits", lambda port=web.DEFAULT_PORT: True)
    monkeypatch.setattr(web.webbrowser, "open", opened.append)
    web.serve()
    assert opened == ["http://127.0.0.1:8765/"]


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
    assert 'value="25"' in page.text


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
    assert "nicht-kommerzielles Projekt" in page.text and "ARD KI-Podcast" in page.text
    assert "Platzhalter" not in page.text

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
    for label in ("KI-Werkstatt", "Einstellungen", "Über FRITS"):
        assert label in nav[nav.index("mehr-liste") :]
    settings_page = client.get("/settings").text
    assert '<summary aria-current="page">Mehr</summary>' in settings_page
    assert '<summary aria-current="page">' not in page

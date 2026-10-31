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
    assert client.get("/invoices/2026-0001/pdf").headers["content-type"] == "application/pdf"

    post(client, "/invoices/2026-0001/email")
    assert mails[0]["subject"] == "Rechnung 2026-0001"
    assert mails[0]["attachment"].name.startswith("2026-0001")
    assert "80,00 €" in mails[0]["body"]
    assert "an Mail übergeben" in client.get("/invoices").text

    post(client, "/invoices/2026-0001/paid")
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
    assert "sk-or-test" not in ws.settings_file.read_text(encoding="utf-8")
    assert "sk-or-test" not in page.text


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

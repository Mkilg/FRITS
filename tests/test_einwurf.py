"""The Einwurf: hand FRITS notes, mails, photos, receipts and contacts by drag & drop or paste,
right in the browser, without a folder."""

import re
import shutil
from datetime import date

import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient
from test_web import TODAY, Recorded, latest_draft, ws  # noqa: F401  (ws is a fixture)

from frits import beleg, kontakt, store
from frits.settings import Settings
from frits.web import app as web_app
from frits.web.app import create_app


@pytest.fixture
def client(ws, nlp, monkeypatch):  # noqa: F811
    monkeypatch.setattr("frits.system.open_path", lambda path: True)
    app = create_app(ws, provider_factory=Recorded(ws), nlp_loader=lambda: nlp, today=lambda: TODAY)
    with TestClient(app) as c:
        c.token = app.state.token
        yield c


def send(client, url, files=(), **data):
    return client.post(
        url, data={"token": client.token, **data}, files=list(files), follow_redirects=True
    )


# ----- Heute: the inbox -----


def test_pasted_text_becomes_a_note_in_the_inbox(client, ws):  # noqa: F811
    page = send(client, "/inbox/upload", back="/", text="Frau Meier, 2 Std Rasen gemäht")
    notes = list(ws.inbox.iterdir())
    assert len(notes) == 1 and re.fullmatch(r"notiz-\d{4}-\d\d-\d\d-\d{4}\.txt", notes[0].name)
    assert "2 Std Rasen" in notes[0].read_text(encoding="utf-8")
    assert "Im Eingang: notiz-" in page.text


def test_paste_twice_in_the_same_minute_keeps_both(client, ws):  # noqa: F811
    send(client, "/inbox/upload", text="eins")
    send(client, "/inbox/upload", text="zwei")
    assert sorted(p.read_text(encoding="utf-8").strip() for p in ws.inbox.iterdir()) == [
        "eins",
        "zwei",
    ]


def test_gleich_lesen_reads_right_away_and_opens_the_new_draft(client, ws):  # noqa: F811
    note = (FIXTURES / "docs" / "01_notiz_meier.txt").read_bytes()
    page = send(
        client,
        "/inbox/upload",
        files=[("files", ("01_notiz_meier.txt", note, "text/plain"))],
        back="/",
        lesen="on",
        lesen_merken="1",
    )
    assert "Fertig gelesen: 1 neuer Entwurf" in page.text
    assert str(page.url).endswith(f"/drafts/{latest_draft(ws)}")
    assert not list(ws.inbox.iterdir())


def test_unticking_gleich_lesen_is_remembered(client, ws):  # noqa: F811
    send(client, "/inbox/upload", text="Notiz", back="/", lesen_merken="1")
    assert Settings.load(ws.settings_file).auto_read is False
    assert len(list(ws.inbox.iterdir())) == 1  # only put down, not read
    assert "checked" not in re.search(
        r'<input type="checkbox" name="lesen"[^>]*>', client.get("/").text
    ).group(0)


def test_home_says_plainly_when_no_ai_is_connected(ws, nlp, monkeypatch):  # noqa: F811
    monkeypatch.setattr(web_app, "get_api_key", lambda provider: None)
    app = create_app(ws, nlp_loader=lambda: nlp, today=lambda: TODAY)
    with TestClient(app) as c:
        shutil.copy(FIXTURES / "docs" / "01_notiz_meier.txt", ws.inbox)
        page = c.get("/").text
    assert "Die KI ist noch nicht verbunden" in page
    assert "KI verbinden" in page and "Rechnung von Hand schreiben" in page
    assert "Jetzt lesen lassen" not in page  # no button that leads nowhere
    assert 'name="lesen"' not in page


def test_reading_after_a_drop_without_ai_keeps_fritz_on_the_page(ws, nlp, monkeypatch):  # noqa: F811
    monkeypatch.setattr("frits.providers.get_api_key", lambda provider: None)
    app = create_app(ws, nlp_loader=lambda: nlp, today=lambda: TODAY)
    with TestClient(app) as c:
        page = c.post(
            "/inbox/upload",
            data={
                "token": app.state.token,
                "text": "2 Std",
                "back": "/",
                "lesen": "on",
                "lesen_merken": "1",
            },
            follow_redirects=True,
        )
    assert str(page.url).endswith("/")
    assert "liegt sicher im Eingang" in page.text
    assert len(list(ws.inbox.iterdir())) == 1


def test_every_einwurf_explains_paste_and_has_a_text_box(client):
    for url in (
        "/",
        "/setup/business_details",
        "/setup/first_invoice",
        "/customers/new",
        "/expenses",
    ):
        page = client.get(url).text
        assert "data-einwurf" in page, url
        assert "<kbd data-taste-cmd>⌘</kbd><kbd>V</kbd>" in page, url
        assert '<textarea name="text"' in page, url


# ----- Einrichtung: details from an old invoice -----


def test_pasted_old_invoice_fills_the_setup_form(client, ws):  # noqa: F811
    text = "Fritz Muster\nHauptstraße 12\n50667 Köln\nIBAN DE89 3704 0044 0532 0130 00\n"
    page = send(client, "/setup/business_details/import", text=text)
    assert "Aus deiner Rechnung übernommen" in page.text
    assert 'value="DE89370400440532013000"' in page.text
    assert not list(ws.inbox.iterdir())


# ----- Kunden: from a signature or a contact card -----

SIGNATURE = """Viele Grüße
Martina Schulze
Schulze Gartenbau GmbH
Lindenallee 7
53111 Bonn
Tel. 0228 123456
martina@schulze-garten.example"""

VCARD = """BEGIN:VCARD
VERSION:3.0
N:Schulze;Martina;;;
FN:Martina Schulze
ORG:Schulze Gartenbau GmbH;
EMAIL;type=INTERNET;type=WORK:martina@schulze-garten.example
TEL;type=CELL:+49 170 1234567
item1.ADR;type=WORK:;;Lindenallee 7;Bonn;;53111;Deutschland
END:VCARD
"""


def test_contact_from_a_signature(nlp):
    found = kontakt.details(SIGNATURE, nlp)
    assert found == {
        "name": "Martina Schulze",
        "company": "Schulze Gartenbau GmbH",
        "address": "Lindenallee 7, 53111 Bonn",
        "email": "martina@schulze-garten.example",
        "phone": "0228 123456",
    }


def test_contact_from_a_vcard():
    found = kontakt.details(VCARD)
    assert found["name"] == "Martina Schulze"
    assert found["address"] == "Lindenallee 7, 53111 Bonn"
    assert found["phone"] == "+49 170 1234567"


def test_pasted_signature_fills_the_new_customer(client, ws):  # noqa: F811
    page = send(client, "/customers/new/einlesen", text=SIGNATURE)
    assert "Aus deinem Text vorausgefüllt" in page.text
    assert 'value="Schulze Gartenbau GmbH"' in page.text
    assert 'value="martina@schulze-garten.example"' in page.text
    assert not list(ws.customers.glob("*schulze*"))  # nothing saved before Fritz checks


def test_dragged_contact_card_fills_the_new_customer(client):
    page = send(
        client,
        "/customers/new/einlesen",
        files=[("files", ("Martina Schulze.vcf", VCARD.encode(), "text/vcard"))],
    )
    assert 'value="+49 170 1234567"' in page.text


# ----- Ausgaben: receipts straight onto the page -----


def test_guess_reads_total_date_shop_and_rate():
    text = """Gasthaus zur Linde
Hauptstr. 3, 50667 Köln
Tisch 4   31.10.2026 19:42
2 x Tagesgericht 12,50   25,00
1 x Apfelschorle  3,20
Summe 28,20
Netto 7% 26,36
MwSt 7% 1,84
Gegeben BAR 30,00
Rückgeld 1,80
"""
    assert beleg.guess(text, date(2026, 11, 2)) == {
        "gross": "28,20",
        "date": "2026-10-31",
        "vendor": "Gasthaus zur Linde",
        "vat_rate": "0.07",
    }


def test_guess_never_takes_a_date_for_an_amount():
    text = "DEUTSCHE BAHN\nFahrkarte\nKöln Hbf -> Bonn Hbf\n28.10.2026\nPreis: 9,40 EUR\n"
    found = beleg.guess(text, date(2026, 11, 2))
    assert found["gross"] == "9,40"
    assert found["date"] == "2026-10-28"


def test_guess_reads_an_order_confirmation():
    text = "Bestellt am 27. Oktober 2026\nGesamtsumme: 39,98 €\nEnthaltene MwSt. 19 %: 6,38 €"
    found = beleg.guess(text, date(2026, 11, 2))
    assert found == {"gross": "39,98", "date": "2026-10-27", "vat_rate": "0.19"}


def test_dropped_receipt_photo_opens_ready_to_check(client, ws, needs_ocr):  # noqa: F811
    photo = (FIXTURES / "docs" / "11_baumarkt.jpg").read_bytes()
    page = send(client, "/expenses/beleg", files=[("files", ("bon.jpg", photo, "image/jpeg"))])
    assert "receipt=" in str(page.url)
    assert "Diesen Beleg eintragen" in page.text
    assert 'value="24,94"' in page.text
    assert 'value="BAUMARKT MUSTERMANN"' in page.text
    assert 'value="2026-10-29"' in page.text
    assert (ws.done / "bon.jpg").exists()
    # The same receipt again is recognised, not counted twice.
    again = send(client, "/expenses/beleg", files=[("files", ("bon2.jpg", photo, "image/jpeg"))])
    assert "Den kenne ich schon. Er wartet hier aufs Eintragen" in again.text
    assert not (ws.done / "bon2.jpg").exists()


def test_pasted_receipt_text_is_kept_as_a_receipt(client, ws):  # noqa: F811
    page = send(client, "/expenses/beleg", text="Baumarkt\nSUMME EUR 12,50\n30.10.2026")
    assert 'value="12,50"' in page.text
    conn = store.connect(ws.db)
    row = conn.execute("SELECT filename, status FROM jobs").fetchone()
    assert row["status"] == "receipt" and row["filename"].startswith("beleg-")


def test_hotel_abroad_has_no_german_vorsteuer():
    text = "Hotel Am Ring\n1010 Wien, Österreich\nUID ATU12345678\nNächtigung 113,30\nMwSt 10 %\n"
    assert beleg.guess(text, date(2026, 11, 2))["vat_rate"] == ""
    berlin = "Café Wiener Straße\nBerlin\nSumme 12,00\nMwSt 19 %\n"
    assert beleg.guess(berlin, date(2026, 11, 2))["vat_rate"] == "0.19"


def test_entered_receipt_again_links_to_it_in_its_year(client, ws):  # noqa: F811
    """Tester: „Kenne ich schon“ had no link, and the page showed 2026 for a 2025 receipt."""
    text = "Baumarkt\nSUMME EUR 12,50\n30.10.2025"
    send(client, "/expenses/beleg", text=text)
    name = store.connect(ws.db).execute("SELECT filename FROM jobs").fetchone()[0]
    send(
        client,
        "/expenses",
        date="2025-10-30",
        vendor="Baumarkt",
        description="Schrauben",
        gross="12,50",
        vat_rate="0.19",
        category="waren",
        source_file=name,
    )
    again = send(client, "/expenses/beleg", text=text)
    assert "schon eingetragen (Ausgaben 2025)" in again.text
    expense = store.connect(ws.db).execute("SELECT id FROM expenses").fetchone()[0]
    assert f'href="/expenses?year=2025#ausgabe-{expense}">Zum Beleg</a>' in again.text
    target = client.get("/expenses?year=2025")
    assert f'id="ausgabe-{expense}"' in target.text
    assert len(list(ws.done.glob("beleg-*"))) == 1  # no second copy kept


def test_same_file_on_heute_twice_is_not_kept_twice(client, ws):  # noqa: F811
    name = "01_notiz_meier.txt"
    shutil.copy(FIXTURES / "docs" / name, ws.inbox / name)
    send(client, "/inbox/process")
    shutil.copy(FIXTURES / "docs" / name, ws.inbox / name)
    page = send(client, "/inbox/process")
    assert f"{name}: Daraus gibt es schon einen Entwurf." in page.text
    assert "Zum Entwurf</a>" in page.text
    assert [p.name for p in ws.done.iterdir() if p.name.startswith("01_")] == [name]


def test_shop_from_a_mail_is_the_sender_not_the_header():
    mail = "From: Thomann <rechnung@thomann.de>\nSubject: Ihre Rechnung\n\nSumme 129,00 €"
    assert beleg.guess_vendor(mail) == "Thomann"
    assert beleg.guess_vendor("From: no-reply@adobe.com\nBetreff: Abo") == "Adobe"


def test_price_and_tax_on_one_line():
    """Tester: the scanned DB ticket lost its 89,90 € because the line also named the MwSt."""
    text = "DB Fernverkehr AG Handy-Ticket\nPreis 89,90 EUR MwSt 7% 5,88 EUR\nBezahlt am 02.03.2025"
    assert beleg.guess(text, date(2025, 3, 5))["gross"] == "89,90"
    assert beleg.guess_total("Netto 10,00\nMwSt 19% 1,90\nSumme 11,90") == "11,90"


def test_pasted_booking_reads_paid_day_and_shop():
    """Tester (Lena): the travel day instead of the paid day, and „Buchungsbestätigung“ as shop."""
    text = (
        "Deutsche Bahn Buchungsbestätigung\nIhre Reise am 24.10.2026 Berlin Hbf - Köln Hbf\n"
        "Gesamtpreis 89,90 EUR inkl. 7% MwSt\nBezahlt per Kreditkarte am 20.10.2026"
    )
    found = beleg.guess(text, date(2026, 11, 1))
    assert found["gross"] == "89,90" and found["date"] == "2026-10-20"
    assert found["vendor"] == "Deutsche Bahn"
    assert beleg.guess_vendor("From: Rechnung <noreply@adobe.com>") == "Adobe"

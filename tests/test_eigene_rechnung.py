"""Tester round 8 (Marie): old invoices of Fritz's own.

An old invoice is not overdue just because it was entered today, his own invoice dropped on
Heute is income and not an expense, and a plain Word invoice still gives "Wofür?" and the day
of the work.
"""

from datetime import date

from test_web import _old_invoice_pdf, _staged, client, mails, post, provider, ws  # noqa: F401

from frits import store
from frits.billing import list_invoices
from frits.frueher import eigene_rechnung, lesen
from frits.settings import Settings

TODAY = date(2026, 11, 2)

FRITZ = Settings(
    name="Fritz Beispiel",
    company="Beispiel Medien",
    address="Werkstattweg 1, 12345 Musterstadt",
    iban="DE89370400440532013000",
    steuernummer="123/456/78901",
)

# A Word invoice without a table, the way Marie writes them.
WORD = [
    "Beispiel Medien",
    "Fritz Beispiel, Werkstattweg 1, 12345 Musterstadt",
    "Steuernummer: 123/456/78901",
    "",
    "An: Deutschlandradio, Raderberggürtel 40, 50968 Köln",
    "",
    "Rechnung Nr. 2025-007",
    "Datum: 15.02.2025",
    "",
    "Moderation Podiumsdiskussion „KI im Radio“, 10.02.2025: 1.200,00 €",
    "Umsatzsteuer 19 %: 228,00 €",
    "Gesamtbetrag: 1.428,00 €",
    "",
    "Bitte überweisen Sie den Betrag bis zum 01.03.2025 auf",
    "IBAN DE89 3704 0044 0532 0130 00",
]

# What Fritz receives: his name and address are on it too, but not his bank or tax number.
ADOBE = """Adobe Systems Software Ireland Ltd
4-6 Riverwalk, Dublin
Rechnung
Rechnungsnummer: IE123456
Fritz Beispiel
Werkstattweg 1
12345 Musterstadt
Creative Cloud 29,99 €
MwSt 19 % 5,70 €
Gesamt 35,69 €
"""


def test_word_invoice_gives_what_it_was_for_the_day_of_work_and_the_due_day():
    found = lesen("\n".join(WORD), (FRITZ.name, FRITZ.company, FRITZ.address), TODAY)
    assert found["number"] == "2025-007"
    assert found["customer"] == "Deutschlandradio"
    assert found["issue_date"] == "2025-02-15"
    assert found["description"] == "Moderation Podiumsdiskussion „KI im Radio“"
    assert found["service_date"] == "2025-02-10"
    assert found["due_date"] == "2025-03-01"
    assert found["net"] == "1.200,00" and found["tax"] == "19"


def test_plain_line_below_the_date_is_what_it_was_for():
    text = "Rechnung\nAn: Café Krümel\nRechnungs-Nr.: 88\nDatum 2. Januar 2026\nFotos Hochzeit\n"
    assert (
        lesen(text + "Summe brutto: 1.190,00 €\n", None, TODAY)["description"] == "Fotos Hochzeit"
    )
    # A greeting or a payment line is never taken for it.
    letter = "Rechnung Nr. 3\nSehr geehrte Frau Meier,\nBitte zahlen Sie bald.\nGesamt 5,00 €\n"
    assert "description" not in lesen(letter, None, TODAY)


def test_own_invoice_is_told_apart_from_one_fritz_received():
    assert eigene_rechnung("\n".join(WORD), FRITZ)
    assert not eigene_rechnung(ADOBE, FRITZ)
    # His IBAN on a direct debit or a bank statement doesn't make it his invoice.
    debit = (
        ADOBE + "Der Betrag wird per Lastschrift von IBAN DE89 3704 0044 0532 0130 00 abgebucht.\n"
    )
    assert not eigene_rechnung(debit, FRITZ)
    # Without his own details in the settings FRITS can't tell, so it doesn't guess.
    assert not eigene_rechnung("\n".join(WORD), Settings())


def test_own_old_invoice_on_heute_goes_to_rechnung_von_frueher(client, ws, provider):
    (ws.inbox / "Rechnung_2025-007_Word.pdf").write_bytes(_old_invoice_pdf(WORD))
    response = post(client, "/inbox/process")
    assert provider.calls == []  # read here, never shown to any AI
    assert "frueher=" in str(response.url)
    page = response.text
    assert "Das ist deine eigene Rechnung von früher, keine Ausgabe" in page
    assert 'value="2025-007" class="imported"' in page
    assert 'value="Deutschlandradio" class="imported"' in page
    assert 'value="Moderation Podiumsdiskussion „KI im Radio“" class="imported"' in page
    # Long past its due day: "Schon bezahlt? Ja" with the due day as a first guess.
    assert 'name="bezahlt" value="ja" required checked' in page
    assert 'value="2025-03-01" aria-label="Bezahlt am" class="imported"' in page
    conn = store.connect(ws.db)
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "frueher"
    [token] = _staged(ws)
    # It waits under Rechnungen, also after more than a day, until Fritz decides.
    assert (
        "Wartet noch: <strong>Rechnung_2025-007_Word.pdf</strong>" in client.get("/invoices").text
    )
    entered = post(
        client,
        "/invoices/frueher",
        staged=token,
        number="2025-007",
        customer="Deutschlandradio",
        issue_date="2025-02-15",
        service_date="2025-02-10",
        description="Moderation Podiumsdiskussion",
        net="1.200,00",
        tax="19",
        bezahlt="ja",
        paid="2025-03-01",
    )
    assert "Rechnung 2025-007 ist nachgetragen" in entered.text
    row = conn.execute("SELECT paid_date FROM invoices WHERE number = '2025-007'").fetchone()
    assert row["paid_date"] == "2025-03-01"
    # Paid, so Heute doesn't suggest a reminder to Deutschlandradio.
    [listed] = list_invoices(conn, TODAY)
    assert listed["status"] == "bezahlt"
    assert "Belege zum Eintragen" not in client.get("/expenses").text


def test_schon_bezahlt_ja_needs_the_day_and_nein_leaves_it_open(client, ws):
    base = {
        "number": "2025-003",
        "customer": "Radio Süd",
        "issue_date": "2025-05-02",
        "net": "100",
        "tax": "klein",
    }
    missing = post(client, "/invoices/frueher", bezahlt="ja", paid="", **base)
    assert "Wann kam das Geld?" in missing.text
    open_ = post(client, "/invoices/frueher", bezahlt="nein", paid="2025-05-10", **base)
    assert "ist nachgetragen" in open_.text
    row = store.connect(ws.db).execute("SELECT paid_date FROM invoices").fetchone()
    assert row["paid_date"] is None  # "Nein" wins over a day typed by mistake


def test_payment_day_is_not_guessed_across_the_turn_of_the_year(client, ws):
    text = (
        "Rechnung Nr. 2025-099\nAn: Radio Süd\nDatum: 20.12.2025\nGesamtbetrag 300,00 €\n§ 19 UStG"
    )
    page = post(client, "/invoices/frueher/lesen", text=text).text
    assert 'name="bezahlt" value="ja" required checked' in page
    assert 'value="" aria-label="Bezahlt am"' in page  # the day decides the EÜR year
    assert "Trag den Tag vom Kontoauszug ein" in page


def test_recent_old_invoice_is_still_open(client, ws):
    text = (
        "Rechnung Nr. 2026-010\nAn: Radio Süd\nDatum: 28.10.2026\nGesamtbetrag 300,00 €\n§ 19 UStG"
    )
    page = post(client, "/invoices/frueher/lesen", text=text).text
    assert 'name="bezahlt" value="nein" checked' in page


def test_own_invoice_entered_as_receipt_offers_the_way_to_rechnungen(client, ws):
    response = client.post(
        "/expenses/beleg",
        data={"token": client.token},
        files={"files": ("alt.pdf", _old_invoice_pdf(WORD), "application/pdf")},
        follow_redirects=True,
    )
    page = response.text
    assert "Das ist deine eigene Rechnung, keine Ausgabe." in page
    assert "Fritz Beispiel" not in page.split('name="vendor"')[1][:200]  # not "bei Fritz"
    job = store.connect(ws.db).execute("SELECT id FROM jobs").fetchone()[0]
    moved = post(client, f"/expenses/als-rechnung/{job}")
    assert "frueher=" in str(moved.url)
    assert 'value="2025-007" class="imported"' in moved.text
    assert "Belege zum Eintragen" not in client.get("/expenses").text


def test_local_ai_costs_nothing(client, ws):
    from types import SimpleNamespace

    conn = store.connect(ws.db)
    for name, cost in (("ollama", None), ("ollama", None)):
        usage = SimpleNamespace(
            provider=name, model="m", input_tokens=10, output_tokens=5, cost_usd=cost
        )
        store.log_ai_call(conn, None, usage, "x")
    page = client.get("/settings").text
    assert "0 €. Deine 2 Anfragen liefen über die lokale KI auf deinem Mac" in page
    assert "unter 1 Cent" not in page
    usage = SimpleNamespace(
        provider="claude", model="m", input_tokens=10, output_tokens=5, cost_usd=0.002
    )
    store.log_ai_call(conn, None, usage, "y")
    page = client.get("/settings").text
    assert "unter 1 Cent für 1 Anfrage, dazu 2 Anfragen an die lokale KI" in page

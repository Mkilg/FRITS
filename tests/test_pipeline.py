import shutil
from datetime import date
from decimal import Decimal

import pytest
from conftest import DOCS, FIXTURES, answer

from frits import store
from frits.billing import BillingError, Draft, approve, blockers, cancel, mark_paid, totals
from frits.pipeline import process_file
from frits.providers.recorded import RecordedProvider
from frits.settings import Settings, Workspace

READABLE = [d for d in DOCS if not answer(d)["broken"]]
IMAGES = {".png", ".jpg", ".jpeg", ".heic"}


@pytest.fixture
def ws(tmp_path):
    workspace = Workspace(tmp_path / "FRITS").ensure()
    for note in (FIXTURES / "customers").glob("*.md"):
        shutil.copy(note, workspace.customers)
    return workspace


@pytest.fixture
def settings():
    return Settings(
        name="Fritz Beispiel",
        address="Werkstattweg 1, 12345 Musterstadt",
        email="fritz@example.com",
        iban="DE89 3704 0044 0532 0130 00",
        bank="Beispielbank",
        steuernummer="123/456/78901",
        hourly_rate=Decimal(40),
        km_rate=Decimal("0.30"),
    )


def drop(ws, doc):
    target = ws.inbox / doc.name
    shutil.copy(doc, target)
    return target


def run(ws, settings, doc, nlp):
    provider = RecordedProvider.from_file(FIXTURES / "recorded" / f"{doc.stem}.json")
    return process_file(drop(ws, doc), ws, settings, provider, nlp), provider


def load_draft(ws, draft_id):
    row = (
        store.connect(ws.db).execute("SELECT data FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    )
    return Draft.from_json(row["data"])


@pytest.mark.parametrize("doc", READABLE, ids=lambda p: p.name)
def test_documents_become_drafts_without_leaking(doc, ws, settings, nlp, request):
    if doc.suffix in IMAGES:
        request.getfixturevalue("needs_ocr")
    expected = answer(doc)
    outcome, provider = run(ws, settings, doc, nlp)

    sent = provider.calls[0].lower()
    for value in [*expected["pii"], "Fritz Beispiel", "fritz@example.com"]:
        assert value.lower() not in sent, value
    assert not (ws.inbox / doc.name).exists()

    if expected["doc_type"] == "expense_receipt":
        assert outcome.status == "receipt"
        return
    assert outcome.status == "drafted"
    draft = load_draft(ws, outcome.draft_id)
    real = [line for line in draft.lines if "needs_price" not in line.flags]
    assert [float(line.quantity) for line in real] == [i["quantity"] for i in expected["items"]]
    if expected["injection"]:
        assert draft.warnings
        # The hijacked line is there but unpriced, so it can't reach the total or be approved.
        hijacked = [line for line in draft.lines if "needs_price" in line.flags]
        assert hijacked and hijacked[0].unit_price is None
        assert blockers(draft, settings)


def test_trick_note_total_ignores_injected_prices(ws, settings, nlp):
    outcome, _ = run(ws, settings, FIXTURES / "docs" / "17_trick_notiz.txt", nlp)
    draft = load_draft(ws, outcome.draft_id)
    assert totals(draft.lines, settings).gross == Decimal("80.00")  # 2 Std x 40, not 1000 or 500


def test_same_file_twice_is_skipped(ws, settings, nlp):
    doc = FIXTURES / "docs" / "01_notiz_meier.txt"
    assert run(ws, settings, doc, nlp)[0].status == "drafted"
    assert run(ws, settings, doc, nlp)[0].status == "skipped"


@pytest.mark.parametrize("name", ["19_leer.txt", "20_kaputt.pdf"])
def test_broken_files_go_to_check_me_with_a_reason(name, ws, settings, nlp):
    provider = RecordedProvider.from_file(FIXTURES / "recorded" / "01_notiz_meier.json")
    outcome = process_file(drop(ws, FIXTURES / "docs" / name), ws, settings, provider, nlp)
    assert outcome.status == "check-me"
    assert (ws.check_me / name).exists()
    assert (ws.check_me / f"{name}.grund.txt").read_text(encoding="utf-8").strip()
    assert provider.calls == []


def test_unknown_customer_blocks_approval(ws, settings, nlp):
    outcome, _ = run(ws, settings, FIXTURES / "docs" / "13_frau_berger.txt", nlp)
    draft = load_draft(ws, outcome.draft_id)
    assert draft.customer_name == "Berger"
    assert any("Kundenliste" in q for q in draft.questions)
    assert "Kunde mit Adresse fehlt." in blockers(draft, settings)


def test_approve_numbers_cancel_and_pay(ws, settings, nlp):
    conn = store.connect(ws.db)
    first, _ = run(ws, settings, FIXTURES / "docs" / "01_notiz_meier.txt", nlp)
    second, _ = run(ws, settings, FIXTURES / "docs" / "05_zwei_auftraege.txt", nlp)
    day = date(2026, 11, 2)

    number, pdf = approve(conn, first.draft_id, settings, ws.invoices, today=day)
    assert number == "2026-0001"
    assert pdf.read_bytes().startswith(b"%PDF")
    with pytest.raises(BillingError):
        approve(conn, first.draft_id, settings, ws.invoices, today=day)
    assert approve(conn, second.draft_id, settings, ws.invoices, today=day)[0] == "2026-0002"

    storno, storno_pdf = cancel(conn, "2026-0001", settings, ws.invoices, today=day)
    assert storno == "2026-0003" and storno_pdf.exists()
    row = conn.execute("SELECT gross, cancels FROM invoices WHERE number = ?", (storno,)).fetchone()
    assert Decimal(row["gross"]) == Decimal("-120.00") and row["cancels"] == "2026-0001"
    with pytest.raises(BillingError):
        cancel(conn, "2026-0001", settings, ws.invoices, today=day)

    mark_paid(conn, "2026-0002", day)
    paid = conn.execute("SELECT paid_date FROM invoices WHERE number = '2026-0002'").fetchone()
    assert paid["paid_date"] == "2026-11-02"


def test_customer_rate_overrides_default(ws, settings, nlp):
    # Erika Meier's note sets 40/h; Jonas Albrecht falls back to settings.
    settings.hourly_rate = Decimal(35)
    meier, _ = run(ws, settings, FIXTURES / "docs" / "01_notiz_meier.txt", nlp)
    jonas, _ = run(ws, settings, FIXTURES / "docs" / "16_nur_vorname.txt", nlp)
    assert load_draft(ws, meier.draft_id).lines[0].unit_price == Decimal(40)
    assert load_draft(ws, jonas.draft_id).lines[0].unit_price == Decimal(35)


def test_vat_mode_totals(settings):
    from frits.billing import Line

    settings.tax_mode = "regelbesteuerung"
    lines = [Line("Arbeit", None, Decimal(2), "Std", Decimal(40))]
    sums = totals(lines, settings)
    assert (sums.net, sums.vat, sums.gross) == (
        Decimal("80.00"),
        Decimal("15.20"),
        Decimal("95.20"),
    )


def test_incomplete_settings_block_approval(ws, nlp):
    outcome, _ = run(
        ws, Settings(hourly_rate=Decimal(40)), FIXTURES / "docs" / "01_notiz_meier.txt", nlp
    )
    with pytest.raises(BillingError, match="eigenen Angaben"):
        approve(store.connect(ws.db), outcome.draft_id, Settings(), ws.invoices)

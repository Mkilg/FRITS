"""Less friction: a short setup with a way out, plain error pages, undo instead of "Sicher?"."""

import shutil

import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient
from test_web import TODAY, Recorded, latest_draft, ws  # noqa: F401  (ws is a fixture)

from frits import expenses
from frits.settings import Settings
from frits.web.app import create_app


@pytest.fixture
def client(ws, nlp, monkeypatch):  # noqa: F811
    monkeypatch.setattr("frits.system.open_path", lambda path: True)
    app = create_app(ws, provider_factory=Recorded(ws), nlp_loader=lambda: nlp, today=lambda: TODAY)
    with TestClient(app) as c:
        c.token = app.state.token
        yield c


def post(client, url, **data):
    return client.post(url, data={"token": client.token, **data}, follow_redirects=True)


def test_first_setup_shows_no_menu_but_a_way_out(client, ws):  # noqa: F811
    page = client.get("/setup/welcome").text
    assert '<a href="/invoices"' not in page and 'class="feierabend"' not in page
    assert "Später einrichten" in page
    home = post(client, "/setup-spaeter")
    assert str(home.url).endswith("/")
    assert Settings.load(ws.settings_file).setup_done
    assert '<a href="/invoices"' in client.get("/setup/welcome").text  # later: the full menu again


def test_learning_steps_can_be_skipped(client):
    page = client.get("/setup/redaction_test").text
    assert "Lern-Teil überspringen" in page
    assert "Lern-Teil überspringen" not in client.get("/setup/business_details").text
    after = post(client, "/setup/redaction_test", action="skip_learning")
    assert str(after.url).endswith("/setup/first_invoice")


def test_optional_details_are_tucked_away(client):
    page = client.get("/setup/business_details").text
    summary = page.index("Firmenname, Telefon, Bank und BIC (freiwillig)")
    assert page.index('name="iban"') < summary < page.index('name="bic"')


def test_error_pages_speak_german(client):
    page = client.get("/steuer/2026/gebiet/gibts-nicht", headers={"accept": "text/html"})
    missing = client.get("/drafts/999")
    assert missing.status_code == 404
    assert "Diese Seite gibt es nicht" in missing.text
    assert '{"detail"' not in missing.text and '{"detail"' not in page.text
    refused = client.post("/inbox/process", data={"token": "falsch"})
    assert refused.status_code == 403
    assert "Bitte lade die Seite neu" in refused.text


def test_deleting_an_expense_can_be_undone(client, ws):  # noqa: F811
    post(
        client,
        "/expenses",
        date="2026-10-29",
        vendor="Baumarkt",
        gross="24,94",
        vat_rate="0.19",
        category="waren",
    )
    (expense,) = expenses.list_expenses(ws.db)
    page = post(client, f"/expenses/{expense.id}/delete")
    assert "Ausgabe gelöscht: 24,94 € bei Baumarkt" in page.text
    assert 'action="/rueckgaengig"' in page.text
    assert 'data-confirm="Diese Ausgabe' not in page.text  # no "Sicher?" any more
    assert not expenses.list_expenses(ws.db)
    page = post(client, "/rueckgaengig")
    assert "Wieder da" in page.text
    (back,) = expenses.list_expenses(ws.db)
    assert (back.vendor, back.gross, back.category) == ("Baumarkt", expense.gross, "waren")
    # Only once: a second click doesn't add it twice.
    post(client, "/rueckgaengig")
    assert len(expenses.list_expenses(ws.db)) == 1


def test_discarding_a_draft_can_be_undone(client, ws):  # noqa: F811
    shutil.copy(FIXTURES / "docs" / "01_notiz_meier.txt", ws.inbox)
    post(client, "/inbox/process")
    draft_id = latest_draft(ws)
    page = post(client, f"/drafts/{draft_id}/discard")
    assert "Entwurf verworfen" in page.text and "Rückgängig" in page.text
    page = post(client, "/rueckgaengig")
    assert str(page.url).endswith(f"/drafts/{draft_id}")
    assert "Der Entwurf ist wieder da" in page.text
    assert "Entwurf verwerfen" in page.text  # editable again

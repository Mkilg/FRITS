from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from frits.providers.recorded import RecordedProvider
from frits.schema import ExtractionResult, Item
from frits.settings import Settings, Workspace
from frits.setup import STEPS, sample_note, trick_demo
from frits.web.app import create_app


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path / "FRITS").ensure()


@pytest.fixture
def answer():
    return RecordedProvider(
        ExtractionResult(
            doc_type="work_note",
            customer_ref="[PERSON_1]",
            items=[
                Item(
                    description="Rasen gemäht",
                    date=None,
                    quantity=2,
                    unit="Std",
                    stated_price=None,
                    source_quote="2 Std Rasen gemäht",
                )
            ],
            questions=[],
        )
    )


@pytest.fixture
def client(ws, nlp, answer, monkeypatch):
    keys = {}
    monkeypatch.setattr("frits.web.app.store_api_key", lambda p, k: keys.update({p: k}))
    monkeypatch.setattr("frits.web.app.get_api_key", lambda p: keys.get(p))
    app = create_app(ws, provider_factory=lambda s: answer, nlp_loader=lambda: nlp)
    with TestClient(app) as c:
        c.token = app.state.token
        c.keys = keys
        yield c


def post(client, step, **data):
    return client.post(
        f"/setup/{step}", data={"token": client.token, **data}, follow_redirects=True
    )


def test_first_start_opens_the_assistant(client):
    page = client.get("/", follow_redirects=True)
    assert "Schritt 1 von 10" in page.text


def test_every_step_renders(client):
    for step in STEPS:
        assert client.get(f"/setup/{step}").status_code == 200, step


def test_walk_through_all_steps(client, ws, answer):
    post(client, "welcome")
    post(client, "choose_ai", provider="openrouter", checkboxes="strict_local")
    page = post(client, "store_key", api_key="sk-or-secret")
    assert client.keys == {"openrouter": "sk-or-secret"}
    assert "sk-or-secret" not in page.text

    page = post(client, "store_key", action="test")
    assert "Es klappt" in page.text
    assert "Petersen" not in answer.calls[0]  # the test call is redacted too
    assert "Rasen gemäht" in page.text

    post(
        client,
        "business_details",
        name="Fritz Beispiel",
        address="Werkstattweg 1\n12345 Ort",
        email="f@example.com",
        iban="DE89 3704 0044 0532 0130 00",
        bank="Bank",
        hourly_rate="42",
    )
    post(client, "tax_status", tax_mode="kleinunternehmer", steuernummer="123/456/78901")
    post(client, "first_invoice", action="sample")
    assert (ws.inbox / "beispiel-notiz.txt").exists()
    page = post(client, "done")
    assert "Alles eingerichtet" in page.text

    saved = Settings.load(ws.settings_file)
    assert saved.setup_done and saved.is_complete
    assert saved.provider == "openrouter"
    assert saved.hourly_rate == Decimal(42)
    assert "sk-or-secret" not in ws.settings_file.read_text(encoding="utf-8")


def test_redaction_lesson_hides_personal_data(client):
    page = post(client, "redaction_test", action="redact", text=sample_note(Settings()))
    seen = page.text.split("Das sieht die KI")[1].split("</pre>")[0]
    secrets = ("Lindner", "Lindenstraße", "0221 4455667", "hanna.lindner", "DE89", "Fritz Beispiel")
    for secret in secrets:
        assert secret not in seen
    assert "Hecke geschnitten" in seen
    assert "3 Std" in seen


def test_trick_demo_shows_prices_come_from_settings(nlp):
    demo = trick_demo(Settings(hourly_rate=Decimal(40)), nlp)
    descriptions = [line.description for line in demo.draft.lines]
    assert "Express-Zuschlag" not in descriptions  # not quoted from the document
    assert demo.draft.lines[0].unit_price == Decimal(40)  # not the 500 € the note asked for
    beratung = next(line for line in demo.draft.lines if line.description == "Beratung")
    assert beratung.unit_price is None  # Fritz has to decide
    assert demo.sums.gross == Decimal("80.00")
    assert demo.draft.warnings
    assert "Erika" not in demo.redacted


def test_trick_lesson_page(client):
    page = post(client, "trick_document", action="trick")
    assert "Die getäuschte KI schlägt vor" in page.text
    assert "Express-Zuschlag" in page.text
    assert "erfunden" in page.text
    assert "Gestrichen: Steht nicht im Dokument." in page.text
    assert "Warnung im Entwurf:" in page.text
    assert "Preise und Summen rechnet immer der Code" in page.text


def test_trick_lesson_shows_the_mock_document_right_away(client):
    page = client.get("/setup/trick_document")
    assert "Der Entwurf, den du bekommst" in page.text
    assert "Gestrichen" in page.text


def test_failed_connection_test_shows_what_to_check(client, answer, monkeypatch):
    from frits.providers import ProviderError, explain_status

    def broken(text):
        raise ProviderError(explain_status("Claude", 400, "Your credit balance is too low", "m"))

    monkeypatch.setattr(answer, "extract", broken)
    page = post(client, "store_key", action="test")
    assert "kein Guthaben" in page.text
    assert "Guthaben aufgeladen?" in page.text
    assert "console.anthropic.com/settings/billing" in page.text


def test_explain_status_covers_the_usual_failures():
    from frits.providers import explain_status

    assert "Modell" in explain_status("Claude", 404, "model: x", "x")
    assert "Schlüssel" in explain_status("OpenAI", 401, "", "x")
    assert "Guthaben" in explain_status("OpenRouter", 402, "Insufficient credits", "x")
    assert "Guthaben" in explain_status("OpenAI", 429, "You exceeded your current quota", "x")


def test_redaction_step_explains_what_if_a_name_slips_through(client):
    page = client.get("/setup/redaction_test")
    assert "Und wenn doch mal ein Name durchrutscht?" in page.text
    assert "AI Act" in page.text


def test_own_details_from_an_old_invoice(nlp):
    from frits.setup import own_details

    text = (
        "Fritz Muster · Hauptstraße 12 · 50667 Köln\n\nFrau Hanna Lindner\nLindenweg 4\n"
        "12345 Musterstadt\n\nRechnung Nr. 2026-0007\n2 Std Hecke geschnitten  80,00 €\n\n"
        "Fritz Muster | Hauptstraße 12, 50667 Köln | Tel. 0221 123456 | fritz@example.de\n"
        "Steuernummer 214/5678/1234 | Sparkasse KölnBonn | IBAN DE89 3704 0044 0532 0130 00\n"
    )
    found = own_details(text, nlp)
    assert found["name"] == "Fritz Muster"
    assert found["address"] == "Hauptstraße 12, 50667 Köln"  # the sender, not the customer
    assert found["iban"] == "DE89370400440532013000"
    assert found["email"] == "fritz@example.de"
    assert found["steuernummer"] == "214/5678/1234"
    assert found["bank"] == "Sparkasse KölnBonn"


def test_upload_an_old_invoice_fills_the_form(client, ws):
    text = "Fritz Muster\nHauptstraße 12\n50667 Köln\nIBAN DE89 3704 0044 0532 0130 00\n"
    page = client.post(
        "/setup/business_details/import",
        data={"token": client.token},
        files={"file": ("alte-rechnung.txt", text.encode(), "text/plain")},
        follow_redirects=True,
    )
    assert "Aus deiner Rechnung übernommen" in page.text
    assert 'value="DE89370400440532013000"' in page.text
    assert not Settings.load(ws.settings_file).iban  # nothing saved until Fritz says Weiter
    assert not list(ws.inbox.iterdir())  # and the file wasn't kept


def test_first_invoice_step_offers_drop_zone_and_folder_button(client):
    page = client.get("/setup/first_invoice")
    assert 'action="/inbox/upload"' in page.text
    assert 'formaction="/open/inbox"' in page.text


def test_setup_saves_number_pattern_and_last_number(client, ws):
    post(
        client,
        "tax_status",
        tax_mode="kleinunternehmer",
        number_pattern="RE-JJ-NNN",
        last_number_before="RE-26-041",
    )
    saved = Settings.load(ws.settings_file)
    assert (saved.number_pattern, saved.last_number_before) == ("RE-JJ-NNN", "RE-26-041")


def test_unknown_step_is_404(client):
    assert client.get("/setup/nope").status_code == 404


def test_redaction_lesson_uses_fritz_own_name(client):
    post(client, "business_details", name="Fritz Sonderbar", address="Weg 1")
    page = post(
        client,
        "redaction_test",
        action="redact",
        text=sample_note(Settings(name="Fritz Sonderbar")),
    )
    seen = page.text.split("Das sieht die KI")[1].split("</pre>")[0]
    assert "Sonderbar" not in seen


def test_wrong_key_says_why_and_offers_the_way_on(client):
    """Tester (Lena): only „ließ sich nicht speichern“, no reason, no way out."""
    post(client, "choose_ai", provider="claude")
    page = post(client, "store_key", api_key="sk-proj-abc123")
    assert "beginnt mit „sk-ant-“" in page.text
    assert "Ohne KI weitermachen</a>" in page.text
    assert not client.keys


def test_locked_keychain_says_so(client, monkeypatch):
    def locked(provider, key):
        raise RuntimeError("keychain")

    monkeypatch.setattr("frits.web.app.store_api_key", locked)
    post(client, "choose_ai", provider="claude")
    page = post(client, "store_key", api_key="sk-ant-api03-abc")
    assert "Schlüsselbund dieses Macs hat ihn nicht angenommen" in page.text
    assert "Ohne KI weitermachen" in page.text


def test_word_in_a_number_field_names_the_field(client, ws):
    """Tester (Lena): „neunzig“ as hourly rate emptied name, address and IBAN."""
    page = post(client, "business_details", name="Lena", hourly_rate="neunzig", km_rate="0,30")
    assert "Beim Stundensatz bitte nur eine Zahl eingeben, zum Beispiel 90." in page.text
    assert 'data-merken="setup-business_details"' in page.text  # the page puts typed values back

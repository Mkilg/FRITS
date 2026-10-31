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
    assert "kein Preis, du entscheidest" in page.text


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

"""The Notfall-Chat: any document in, only placeholders out, real names back in here."""

import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient
from test_web import TODAY, mails, ws  # noqa: F401  (pytest fixtures)

from frits import notfall, store
from frits.providers import Usage
from frits.web.app import create_app

BRIEF = (
    "Sehr geehrte Frau Albrecht,\n"
    "für Ihre Wohnung im Gartenweg 12, 50667 Köln ergibt sich eine Nachzahlung von 214,60 €.\n"
    "Bitte überweisen Sie bis 30.11.2026 auf DE89 3704 0044 0532 0130 00.\n"
    "Rückfragen an hausverwaltung.koenig@example.de.\n"
    "Mit freundlichen Grüßen\nPetra König, Hausverwaltung König\nAktenzeichen NK-2025-0815"
)
SECRETS = ("Albrecht", "Gartenweg", "DE89", "koenig@", "König", "Petra")


class FakeChat:
    """Answers like an AI would, and remembers exactly what it was sent."""

    def __init__(self):
        self.sent = []
        self.reply = "Sie müssen bis 30.11.2026 zahlen. Schreiben Sie [PERSON_1] eine kurze Mail."
        self.fail = None

    def __call__(self, settings):
        return self

    def extract(self, text):  # pragma: no cover - not used here
        raise AssertionError("the Notfall-Chat never extracts")

    def chat(self, system, messages):
        if self.fail:
            from frits.providers import ProviderError

            raise ProviderError(self.fail)
        self.sent.append((system, [dict(m) for m in messages]))
        return self.reply, Usage("claude", "claude-opus-5-5", 1000, 200)


@pytest.fixture
def ai():
    return FakeChat()


@pytest.fixture
def client(ws, ai, nlp, mails):  # noqa: F811
    app = create_app(ws, provider_factory=ai, nlp_loader=lambda: nlp, today=lambda: TODAY)
    with TestClient(app) as c:
        c.token = app.state.token
        c.app_state = app.state
        c.ws = ws
        yield c


def post(client, url, files=None, **data):
    return client.post(
        url, data={"token": client.token, **data}, files=files, follow_redirects=True
    )


def open_chat(client, text=BRIEF):
    response = post(client, "/notfall/neu", text=text)
    assert response.status_code == 200
    key = next(iter(client.app_state.notfall))
    return key, response


def everything_sent(ai) -> str:
    return "\n".join(system + "".join(m["content"] for m in msgs) for system, msgs in ai.sent)


def test_start_page_and_menu(client):
    response = client.get("/notfall")
    assert response.status_code == 200
    assert "So bleibt es geschützt" in response.text
    assert "ersetzt keine Rechts-, Steuer- oder Finanzberatung" in response.text
    assert 'href="/notfall"' in client.get("/").text  # in "Mehr" and on Heute


def test_pasted_letter_is_redacted_before_anything_goes_out(client, ai):
    key, page = open_chat(client)
    # The page shows the original and, next to it, what the AI will see.
    assert "Das sieht Claude" in page.text
    assert "[PERSON_1]" in page.text
    assert not ai.sent  # nothing sent just by dropping the document in
    # "König" alone (Hausverwaltung König) was hidden too, without asking.
    g = client.app_state.notfall[key]
    assert "König" in g.auto_hidden or all("König" not in d.redacted for d in g.docs)

    response = post(
        client, f"/notfall/{key}/frage", vorschlag=notfall.VORSCHLAEGE[0], modus="senden"
    )
    assert response.status_code == 200
    out = everything_sent(ai)
    for secret in SECRETS:
        assert secret not in out, secret
    assert "Eingefügter Text" not in out  # no file names either
    assert "<dokument" in out
    # The answer comes back with the real name put in, here.
    assert "Schreiben Sie Jonas Albrecht eine" in response.text  # a known customer
    # One line in the audit log, as for every AI call.
    rows = store.connect(client.ws.db).execute("SELECT COUNT(*) FROM audit").fetchone()
    assert rows[0] == 1


def test_question_is_redacted_and_names_stay_consistent(client, ai):
    key, _ = open_chat(client)
    post(client, f"/notfall/{key}/frage", frage="Was will Frau Albrecht?", action="senden")
    post(client, f"/notfall/{key}/frage", frage="Und ruft Herr Bergmann an?", action="senden")
    _system, messages = ai.sent[-1]
    assert len(messages) == 3  # question, answer, question
    assert "Albrecht" not in messages[0]["content"] and "Bergmann" not in messages[2]["content"]
    g = client.app_state.notfall[key]
    person = next(ph for ph, e in g.mapping.entries.items() if "Albrecht" in e["value"])
    assert person in messages[0]["content"]
    assert "<dokument" not in messages[2]["content"]  # the document went out once


def test_extra_words_and_taking_them_out_again(client, ai):
    key, _ = open_chat(client)
    post(client, f"/notfall/{key}/schwaerzen", extra="NK-2025-0815")
    g = client.app_state.notfall[key]
    assert "NK-2025-0815" not in g.docs[0].redacted
    assert "[GEHEIM_" in g.docs[0].redacted
    post(client, f"/notfall/{key}/schwaerzen", extra="")
    assert "NK-2025-0815" in g.docs[0].redacted


def test_copy_by_hand_without_a_key(client, ai):
    key, _ = open_chat(client)
    page = post(client, f"/notfall/{key}/frage", frage="Bis wann?", action="kopieren")
    assert "Jetzt kopieren" in page.text
    g = client.app_state.notfall[key]
    copy = g.copy_text()
    assert "Platzhalter" in copy and "<dokument" in copy  # house rules + document
    for secret in SECRETS:
        assert secret not in copy, secret
    assert not ai.sent
    page = post(
        client, f"/notfall/{key}/antwort", antwort="Zahlen Sie an [PERSON_1].", action="einfuegen"
    )
    assert "Zahlen Sie an" in page.text and "Albrecht" in page.text
    assert g.messages[-1].via == "kopiert"


def test_failed_call_keeps_the_question_and_sends_nothing_twice(client, ai):
    key, _ = open_chat(client)
    ai.fail = "Keine Verbindung zu Claude. Ist das Internet an?"
    page = post(client, f"/notfall/{key}/frage", frage="Muss ich zahlen?", action="senden")
    assert "Keine Verbindung" in page.text
    assert "Muss ich zahlen?" in page.text  # still in the field
    g = client.app_state.notfall[key]
    assert not g.messages and not g.docs[0].sent


def test_photo_is_read_here_and_never_sent_as_image(client, ai, needs_ocr):
    path = FIXTURES / "docs" / "11_baumarkt.jpg"
    with path.open("rb") as fh:
        page = post(client, "/notfall/neu", files={"files": (path.name, fh, "image/jpeg")})
    assert "mit Texterkennung gelesen" in page.text
    key = next(iter(client.app_state.notfall))
    post(client, f"/notfall/{key}/frage", frage="Was habe ich gekauft?", action="senden")
    out = everything_sent(ai)
    assert "11_baumarkt" not in out


def test_forgetting_and_restart(client):
    key, _ = open_chat(client)
    page = post(client, f"/notfall/{key}/vergessen")
    assert "Vergessen" in page.text
    page = client.get(f"/notfall/{key}", follow_redirects=True)
    assert "nie gespeichert" in page.text


def test_other_websites_cannot_send(client):
    key, _ = open_chat(client)
    response = client.post(f"/notfall/{key}/frage", data={"token": "falsch", "frage": "x"})
    assert response.status_code == 403


def test_claude_chat_retries_without_fallback_and_handles_refusal():
    from types import SimpleNamespace

    import anthropic
    import httpx2

    from frits.providers import ProviderError
    from frits.providers.anthropic_provider import ClaudeProvider

    def reply(stop="end_turn", text="Hallo [PERSON_1]"):
        return SimpleNamespace(
            stop_reason=stop,
            content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )

    calls = []

    def beta_create(**kw):
        calls.append(("beta", kw))
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.BadRequestError(
            "nope", response=httpx2.Response(400, request=request), body=None
        )

    def plain_create(**kw):
        calls.append(("plain", kw))
        return reply()

    p = ClaudeProvider("sk-test", "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")
    p.client = SimpleNamespace(
        beta=SimpleNamespace(messages=SimpleNamespace(create=beta_create)),
        messages=SimpleNamespace(create=plain_create),
    )
    text, usage = p.chat("Regeln", [{"role": "user", "content": "Hi"}])
    assert text == "Hallo [PERSON_1]" and usage.model == "claude-opus-5-5"
    assert [c[0] for c in calls] == ["beta", "plain"]
    assert "tools" not in calls[-1][1]  # it can only answer
    assert usage.cost_usd is not None

    p.client.messages = SimpleNamespace(create=lambda **kw: reply("refusal", ""))
    with pytest.raises(ProviderError):
        p.chat("Regeln", [{"role": "user", "content": "Hi"}])


def test_answers_are_escaped_but_bold_stays():
    html = str(notfall.leicht("Bis **30.11.** zahlen <script>alert(1)</script>"))
    assert "<strong>30.11.</strong>" in html
    assert "<script>" not in html


def test_freier_chat_sends_a_redacted_question_without_any_document(client, ai):
    start = client.get("/chat")
    assert start.status_code == 200 and "Freier Chat" in start.text
    assert 'href="/chat" aria-current="page"' in start.text
    assert 'href="/chat"' in client.get("/").text  # in "Mehr"
    response = post(
        client,
        "/chat/neu",
        frage="Wie schreibe ich Jonas Albrecht wegen der IBAN DE89370400440532013000?",
        action="senden",
    )
    assert response.status_code == 200
    system, messages = ai.sent[-1]
    assert system == notfall.SYSTEM_FREI != notfall.SYSTEM
    assert "Albrecht" not in messages[0]["content"] and "DE89" not in messages[0]["content"]
    assert "<dokument" not in messages[0]["content"]
    assert "Schreiben Sie Jonas Albrecht eine" in response.text  # names back in, here
    assert "Ohne Dokument" in response.text and "Freier Chat" in response.text
    g = next(iter(client.app_state.notfall.values()))
    assert g.frei and g.title.startswith("Wie schreibe ich")


def test_freier_chat_with_a_document_shows_it_before_sending(client, ai):
    response = post(
        client,
        "/chat/neu",
        frage="Was heißt das?",
        files={"files": ("brief.txt", BRIEF.encode(), "text/plain")},
        action="senden",
    )
    assert not ai.sent  # first Fritz sees the redacted document
    assert "Das sieht Claude" in response.text and "Was heißt das?" in response.text
    assert post(client, "/chat/neu", frage=" ").text.count("Schreib kurz") == 1


def test_a_steuer_beleg_opens_a_chat_already_redacted(client, ai, tmp_path):
    """ "Mit KI besprechen" im Belegbuch: der Beleg kommt geschwärzt in einen neuen Chat."""
    beleg = tmp_path / "Nebenkosten.txt"
    beleg.write_text(BRIEF, encoding="utf-8")
    key, problems = client.app_state.notfall_mit_datei(beleg, "Nebenkosten.txt")
    assert key and problems == []
    page = client.get(f"/notfall/{key}")
    assert "Das sieht Claude" in page.text and "Nebenkosten.txt" in page.text
    assert not ai.sent  # nothing goes out until a question is asked
    g = client.app_state.notfall[key]
    assert all(s not in g.docs[0].redacted for s in ("Gartenweg", "DE89"))
    leer = tmp_path / "leer.txt"
    leer.write_text("   ", encoding="utf-8")
    key, problems = client.app_state.notfall_mit_datei(leer, "leer.txt")
    assert key is None and problems[0].startswith("leer.txt: ")

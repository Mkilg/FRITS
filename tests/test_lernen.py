import json
import re

from test_web import client, mails, post, provider, ws  # noqa: F401  (pytest fixtures)

from frits import lernen
from frits.customers import Customer
from frits.redact import Mapping


def shown(response, box: str) -> str:
    """The text inside one copy box on the page."""
    found = re.search(rf'<pre id="text-{box}"[^>]*>(.*?)</pre>', response.text, re.DOTALL)
    return found.group(1)


def test_every_chapter_renders_with_its_neighbours(client):  # noqa: F811
    for slug in lernen.SLUGS:
        response = client.get(f"/lernen/{slug}")
        assert response.status_code == 200, slug
        assert lernen.kapitel_info(slug)["title"] in response.text
        assert 'href="/lernen"' in response.text  # in the main navigation
    assert client.get("/lernen").status_code == 200
    assert client.get("/lernen/gibts-nicht").status_code == 404


def test_every_prompt_is_on_a_page_with_a_copy_button(client):  # noqa: F811
    pages = "".join(client.get(f"/lernen/{slug}").text for slug in lernen.SLUGS)
    for key in lernen.PROMPTS:
        assert f'id="text-{key}"' in pages, key
        assert f'data-kopieren="#text-{key}"' in pages, key


def test_the_settings_file_is_valid_json_and_locks_the_originals():
    settings = json.loads(lernen.PROMPTS["claude_settings"].text)
    deny = settings["permissions"]["deny"]
    assert "Read(~/Papierkram/**)" in deny
    assert settings["permissions"]["defaultMode"] == "default"
    assert settings["permissions"]["disableBypassPermissionsMode"] == "disable"
    assert settings["sandbox"]["enabled"] is True


def test_prompts_contain_no_real_looking_personal_data():
    for key, p in lernen.PROMPTS.items():
        assert not re.search(r"\bDE\d{2}[ \d]{10,}", p.text), key  # no IBAN
        assert "@" not in p.text, key


def test_extra_words_get_their_own_placeholder(nlp):
    stueck = lernen.schwaerzen(
        "Aktenzeichen NK-2025-0815, bitte an Frau Albrecht.", [], nlp, extra="nk-2025-0815"
    )
    assert "NK-2025-0815" not in stueck.redacted
    assert "[GEHEIM_1]" in stueck.redacted
    assert "Albrecht" not in stueck.redacted
    assert stueck.redacted in stueck.prompt


def test_extra_words_never_break_a_placeholder(nlp):
    stueck = lernen.schwaerzen("Bei Frau Albrecht 2 Std.", [], nlp, extra="PERSON")
    assert re.search(r"\[PERSON_\d+\]", stueck.redacted)
    assert "[GEHEIM_" not in stueck.redacted


def test_task_goes_into_the_prompt(nlp):
    stueck = lernen.schwaerzen("Bei Frau Albrecht.", [], nlp, task="Schreib eine Antwort.")
    assert "Aufgabe: Schreib eine Antwort." in stueck.prompt
    assert "HIER DEN GESCHWÄRZTEN TEXT" not in stueck.prompt


def test_names_come_back_into_the_answer():
    mapping = Mapping({"[PERSON_1]": {"kind": "PERSON", "value": "Erika Meier", "surface": []}})
    assert lernen.zuruecksetzen("Liebe [PERSON_1],", mapping) == "Liebe Erika Meier,"


def test_workbench_redacts_and_puts_names_back(client):  # noqa: F811
    text = "Liebe Frau Albrecht, Ihre IBAN DE89 3704 0044 0532 0130 00 ist notiert."
    response = post(client, "/lernen/werkbank", action="schwaerzen", text=text, extra="")
    assert response.status_code == 200
    redacted = shown(response, "fertig")
    assert "Albrecht" not in redacted
    assert "DE89" not in redacted
    key = re.search(r'name="stueck" value="([^"]+)"', response.text).group(1)

    response = post(
        client, "/lernen/werkbank", action="zurueck", stueck=key, antwort="Hallo [PERSON_1]!"
    )
    back = shown(response, "zurueck")
    assert "Albrecht" in back


def test_workbench_knows_customers_and_fritz(client):  # noqa: F811
    response = post(
        client,
        "/lernen/werkbank",
        action="schwaerzen",
        text="Erika Meier hat angerufen. Gruß, Fritz Beispiel",
    )
    redacted = shown(response, "fertig")
    assert "Meier" not in redacted
    assert "Fritz Beispiel" not in redacted


def test_workbench_needs_the_token(client):  # noqa: F811
    response = client.post("/lernen/werkbank", data={"token": "falsch", "text": "x"})
    assert response.status_code == 403


def test_forgotten_piece_gives_a_calm_message(client):  # noqa: F811
    response = post(client, "/lernen/werkbank", action="zurueck", stueck="weg", antwort="x")
    assert response.status_code == 200
    assert "noch einmal" in response.text


def test_every_piece_gets_its_own_key(nlp):
    people = [Customer(id="x", name="Erika Meier")]
    keys = {lernen.schwaerzen("Erika Meier", people, nlp).key for _ in range(3)}
    assert len(keys) == 3


def test_a_surname_hiding_elsewhere_is_flagged(nlp):
    stueck = lernen.schwaerzen(
        "Mit freundlichen Grüßen\nPetra König, Hausverwaltung König", [], nlp
    )
    if "König" in stueck.redacted:  # the engine missed it: FRITS must say so
        assert "König" in stueck.suspicious
    again = lernen.schwaerzen(stueck.original, [], nlp, extra="\n".join(stueck.suspicious))
    assert "König" not in again.redacted
    assert again.suspicious == ()

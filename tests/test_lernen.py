import json
import re

from test_web import TODAY, client, mails, post, provider, ws  # noqa: F401  (pytest fixtures)

from frits import lernen
from frits.customers import Customer
from frits.redact import Mapping


def shown(response, box: str) -> str:
    """The text inside one copy box on the page."""
    found = re.search(rf'<pre id="text-{box}"[^>]*>(.*?)</pre>', response.text, re.DOTALL)
    return found.group(1)


def choose(client, tool):  # noqa: F811
    data = {"token": client.token, "werkzeug": tool, "weiter": "/lernen/agent"}
    return client.post("/lernen/wahl", data=data, follow_redirects=False)


def every_bite(client):  # noqa: F811
    """Every bite of every stage for every tool: (tool, address, page)."""
    for tool in lernen.WERKZEUGE:
        choose(client, tool)
        for e in lernen.ETAPPEN:
            for h in e.fuer(tool):
                url = f"/lernen/{e.slug}/{h.slug}"
                yield tool, url, client.get(url)


def test_every_bite_renders_for_every_tool(client):  # noqa: F811
    seen = set()
    for tool, url, response in every_bite(client):
        assert response.status_code == 200, (tool, url)
        assert 'href="/lernen"' in response.text  # in the main navigation
        assert "So funktionieren KI-Agenten" in response.text
        seen.add(url)
    # every bite belongs to at least one tool
    assert seen == {f"/lernen/{e.slug}/{h.slug}" for e in lernen.ETAPPEN for h in e.happen}
    assert client.get("/lernen").status_code == 200
    assert client.get("/lernen/gibts-nicht").status_code == 404
    assert client.get("/lernen/agent/gibts-nicht").status_code == 404


def test_old_chapter_addresses_still_work(client):  # noqa: F811
    for old in (
        "start",
        "treppe",
        "schleuse",
        "claude-code",
        "andere-tools",
        "prompts",
        "werkbank",
        "papierkram",
    ):
        assert client.get(f"/lernen/{old}").status_code == 200, old


def test_the_menu_uses_the_new_name(client):  # noqa: F811
    page = client.get("/").text
    assert "So funktionieren KI-Agenten" in page
    assert "KI-Werkstatt" not in page


def test_fritte_explains_the_goal_at_the_start(client):  # noqa: F811
    page = client.get("/lernen").text
    assert "außerhalb von FRITS" in page
    assert "Los geht's" in page
    first = client.get("/lernen/start").text
    assert "außerhalb von FRITS" in first or "ohne mich" in first


def test_the_tool_choice_filters_the_bites(client):  # noqa: F811
    choose(client, "codex")
    page = client.get("/lernen/einstellen").text
    assert "Zwei Schalter" in page and "--sandbox" in page
    assert "Du lernst mit: <strong>Codex</strong>" in page
    choose(client, "chat")
    page = client.get("/lernen/einstellen").text
    assert "Training ausschalten" in page
    assert "Zwei Schalter" not in page
    # a bite for another tool still opens (old links), it just says so
    assert "Für ein anderes Werkzeug" in client.get("/lernen/einstellen/codex-schalter").text


def test_the_tool_choice_needs_the_token_and_a_known_tool(client):  # noqa: F811
    response = client.post("/lernen/wahl", data={"token": "falsch", "werkzeug": "codex"})
    assert response.status_code == 403
    assert post(client, "/lernen/wahl", werkzeug="gibts-nicht").status_code == 400
    # the way back can only be inside the course
    response = client.post(
        "/lernen/wahl",
        data={"token": client.token, "werkzeug": "codex", "weiter": "https://example.org/"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/lernen/agent"


def test_bites_are_small(client):  # noqa: F811
    """Marie: "zu viel Text pro Seite". Fritte says one or two sentences, a bite has little prose."""
    for e in lernen.ETAPPEN:
        for h in e.happen:
            assert len(h.sagt) <= 220, h.slug
    for tool, url, response in every_bite(client):
        if url.endswith("/werkbank"):
            continue
        bite = re.search(
            r'<section class="card happen" id="happen">(.*?)</section>\s*<div class="row kurs-weiter">',
            response.text,
            re.DOTALL,
        ).group(1)
        prose = re.sub(r"<pre.*?</pre>|<svg.*?</svg>|<[^>]+>", " ", bite, flags=re.DOTALL)
        words = len(prose.split())
        assert words <= 330, (tool, url, words)


def test_finishing_a_stage_stamps_it_and_gives_a_card(client, ws):  # noqa: F811
    choose(client, "claude-code")
    client.get("/lernen/agent/haende")
    # Without the exercise there is no stamp: FRITS sends Fritz to it
    response = post(client, "/lernen/agent/geschafft")
    assert response.url.path == "/lernen/agent/wer-kann-was"
    assert "Erst die Übung" in response.text
    solve(client, "agent/wer-kann-was")
    response = post(client, "/lernen/agent/geschafft")
    assert "Etappe 2 geschafft" in response.text
    assert "Salzkörner" in response.text
    assert "Er arbeitet in Runden" not in response.text  # moved on to the next stage
    assert response.url.path == "/lernen/treppe"
    karte = client.get("/lernen").text
    assert "Gelernt" in karte
    # The game sees the finished stage as a card, once
    from frits import store
    from frits.spiel import stand

    conn = store.connect(ws.db)
    stand.abgleichen(conn, TODAY)
    post(client, "/lernen/agent/geschafft")
    stand.abgleichen(conn, TODAY)
    cards = conn.execute("SELECT schluessel FROM spiel_karten WHERE art = 'lernen'").fetchall()
    # picking the tool finished stage 1, the button stage 2; pressing it again adds nothing
    assert sorted(c[0] for c in cards) == ["lernen:agent", "lernen:start"]
    cards = conn.execute("SELECT schluessel FROM spiel_karten WHERE art = 'uebung'").fetchall()
    assert [c[0] for c in cards] == ["uebung:agent/wer-kann-was"]


def solve(client, key):  # noqa: F811
    response = client.post("/lernen/uebung", data={"token": client.token, "happen": key})
    assert response.status_code == 200, key
    return response.json()


def test_the_last_stage_finishes_the_course(client):  # noqa: F811
    for e in lernen.ETAPPEN:
        for h in e.fuer(lernen.STANDARD_WERKZEUG):
            if h.uebung:
                solve(client, f"{e.slug}/{h.slug}")
        response = post(client, f"/lernen/{e.slug}/geschafft")
    assert "Kurs geschafft" in response.text
    assert "Du hast alles geschafft" in client.get("/lernen").text


def test_the_map_remembers_where_you_were(client):  # noqa: F811
    choose(client, "chat")
    assert "Los geht's" in client.get("/lernen").text
    client.get("/lernen/start/hallo")
    post(client, "/lernen/start/geschafft")  # the first stage has no exercise
    page = client.get("/lernen").text
    assert "Weiter, wo du warst" in page
    assert 'href="/lernen/agent"' in page


def test_the_workbench_takes_a_task_from_the_paperwork_examples(client):  # noqa: F811
    page = client.get("/lernen/werkbank?aufgabe=Was+wollen+die+von+mir").text
    assert 'value="Was wollen die von mir"' in page


def test_every_prompt_is_on_a_page_with_a_copy_button(client):  # noqa: F811
    pages = "".join(response.text for _, _, response in every_bite(client))
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


def test_picking_a_tool_finishes_the_first_stage(client):  # noqa: F811
    response = post(client, "/lernen/wahl", werkzeug="cursor", weiter="/lernen/agent")
    assert "Etappe 1 geschafft" in response.text
    assert response.url.path == "/lernen/agent"
    # Changing the tool later just says so, and a bite the new tool lacks leads to its stage
    response = post(
        client, "/lernen/wahl", werkzeug="chat", weiter="/lernen/einstellen/cursor-modi"
    )
    assert "Du lernst jetzt mit Chat im Browser" in response.text
    assert response.url.path == "/lernen/einstellen"
    assert "Training ausschalten" in response.text


def test_a_painted_picture_replaces_the_drawn_graphic(client):  # noqa: F811
    drawn = client.get("/lernen/start/hallo").text
    assert "<svg" in drawn and 'class="kurs-bild"' not in drawn
    # Painted pictures for the course are named kurs-<name>, see frits/bilder/kurs-bild-prompts.md
    assert all(h.bild.startswith("kurs-") for e in lernen.ETAPPEN for h in e.happen if h.bild)


def test_an_exercise_counts_once_and_opens_the_stamp(client):  # noqa: F811
    choose(client, "chat")
    page = client.get("/lernen/treppe/welche-stufe").text
    assert "data-uebung" in page and "1 Salzkorn" in page
    assert "data-stempel-form hidden" in page  # the stamp waits for the exercises
    first = solve(client, "treppe/schwaerzen-ueben")
    assert first == {"neu": True, "salz": True, "offen": ["treppe/welche-stufe"]}
    assert solve(client, "treppe/schwaerzen-ueben")["neu"] is False
    assert solve(client, "treppe/welche-stufe")["offen"] == []
    page = client.get("/lernen/treppe/welche-stufe").text
    assert "✓ Übung gelöst" in page and "data-stempel-form hidden" not in page
    # only real exercises, and only with the token
    assert (
        client.post(
            "/lernen/uebung", data={"token": client.token, "happen": "treppe/stufen"}
        ).status_code
        == 400
    )
    assert (
        client.post("/lernen/uebung", data={"token": client.token, "happen": "x/y"}).status_code
        == 400
    )
    assert (
        client.post(
            "/lernen/uebung", data={"token": "falsch", "happen": "treppe/welche-stufe"}
        ).status_code
        == 403
    )


def test_the_workbench_counts_as_its_exercise(client):  # noqa: F811
    post(client, "/lernen/werkbank", action="schwaerzen", text="Liebe Frau Albrecht, danke.")
    assert "✓ Übung gelöst" in client.get("/lernen/werkbank").text


def test_long_prompts_are_folded_but_still_copyable(client):  # noqa: F811
    choose(client, "chat")
    page = client.get("/lernen/prompts/hausordnung").text
    assert '<details class="prompt-falte">' in page
    assert 'id="text-regeln"' in page and 'data-kopieren="#text-regeln"' in page

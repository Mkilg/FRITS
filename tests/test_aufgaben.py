"""Deine Liste on "Heute": points from free text, ticked off for Fritte's cards."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from test_notfall import FakeChat
from test_web import TODAY, mails, ws  # noqa: F401  (pytest fixtures)

from frits import aufgaben, store
from frits.spiel import kassensturz, stand
from frits.web.app import create_app


@pytest.fixture
def ai():
    chat = FakeChat()
    chat.reply = '{"aufgaben": ["Rechnung für [PERSON_1] schreiben", "Bahnticket eintragen"]}'
    return chat


@pytest.fixture
def client(ws, ai, nlp, mails):  # noqa: F811
    app = create_app(ws, provider_factory=ai, nlp_loader=lambda: nlp, today=lambda: TODAY)
    with TestClient(app) as c:
        c.token = app.state.token
        c.ws = ws
        yield c


def post(client, url, **data):
    return client.post(url, data={"token": client.token, **data}, follow_redirects=True)


def punkte(client):
    conn = store.connect(client.ws.db)
    offen, fertig = aufgaben.liste(conn, TODAY)
    return [p.text for p in offen], [p.text for p in fertig]


def test_typed_lines_bullets_and_short_comma_lists_become_points():
    assert aufgaben.aufteilen("- Rechnung schreiben\n* Ticket eintragen\n\n3. Mama anrufen") == [
        "Rechnung schreiben",
        "Ticket eintragen",
        "Mama anrufen",
    ]
    assert aufgaben.aufteilen("[ ] Steuer; [x] Bahn") == ["Steuer", "Bahn"]
    assert aufgaben.aufteilen("Rechnung Medialab, Ticket eintragen, Mama anrufen") == [
        "Rechnung Medialab",
        "Ticket eintragen",
        "Mama anrufen",
    ]
    # A sentence with a comma stays one point.
    satz = "Hallo, ich muss morgen unbedingt noch die Rechnung für den Workshop schreiben"
    assert aufgaben.aufteilen(satz) == [satz]
    assert aufgaben.braucht_ki([satz])
    assert not aufgaben.braucht_ki(["kurz"])
    assert aufgaben.aufteilen("   \n - \n") == []


def test_paperwork_points_link_to_the_right_place():
    assert aufgaben.ziel("Bahnticket eintragen")[0] == "/expenses"
    assert aufgaben.ziel("Rechnung für Medialab schreiben")[0] == "/drafts"
    assert aufgaben.ziel("Steuerbescheid ansehen")[0] == "/steuer"
    assert aufgaben.ziel("Zahlungserinnerung an Meier")[0] == "/invoices"
    assert aufgaben.ziel("Brief vom Vermieter beantworten")[0] == "/notfall"
    assert aufgaben.ziel("Versicherung kündigen")[0] == "/notfall"  # not the backup
    assert aufgaben.ziel("Backup auf den USB-Stick")[0] == "/sicherung"
    assert aufgaben.ziel("Mama anrufen") == (None, None)


def test_list_on_heute_add_tick_untick_and_strike(client, ai):
    home = client.get("/").text
    assert "Deine Liste" in home and "Tagesziel: 0 von 3 geschafft" in home
    page = post(client, "/aufgaben", text="Bahnticket eintragen\nMama anrufen")
    assert "2 Punkte stehen auf der Liste." in page.text
    assert 'href="/expenses">Zu den Ausgaben ›' in page.text
    assert not ai.sent  # short lines are split here, nothing goes out
    assert punkte(client) == (["Bahnticket eintragen", "Mama anrufen"], [])

    conn = store.connect(client.ws.db)
    pid = conn.execute("SELECT id FROM aufgaben WHERE text = 'Mama anrufen'").fetchone()[0]
    page = post(client, f"/aufgaben/{pid}/abhaken")
    assert "Erledigt: Mama anrufen. Für Fritte liegt eine Karte bereit." in page.text
    assert 'data-cue="done"' in page.text or "data-cue" not in page.text  # with sound only
    assert "Tagesziel: 1 von 3 geschafft" in page.text  # one goal for the day
    assert punkte(client) == (["Bahnticket eintragen"], ["Mama anrufen"])
    karten = kassensturz.offene_karten(conn)
    assert [(k.art, k.detail, k.koerner) for k in karten if k.art == "aufgabe"] == [
        ("aufgabe", "Mama anrufen", 1)
    ]

    # Taken back: open again, and the waiting card goes with it.
    post(client, f"/aufgaben/{pid}/zurueck")
    assert punkte(client) == (["Bahnticket eintragen", "Mama anrufen"], [])
    assert not [k for k in kassensturz.offene_karten(conn) if k.art == "aufgabe"]

    # Struck off, and back with "Rückgängig".
    page = post(client, f"/aufgaben/{pid}/streichen")
    assert "Gestrichen: Mama anrufen." in page.text and "Rückgängig" in page.text
    assert punkte(client) == (["Bahnticket eintragen"], [])
    post(client, "/rueckgaengig")
    assert punkte(client) == (["Bahnticket eintragen", "Mama anrufen"], [])


def test_three_cards_a_day_and_no_second_card_for_the_same_point(client):
    post(client, "/aufgaben", text="\n".join(f"Punkt {i}" for i in range(1, 6)))
    conn = store.connect(client.ws.db)
    ids = [r[0] for r in conn.execute("SELECT id FROM aufgaben ORDER BY id")]
    for pid in ids[:3]:
        post(client, f"/aufgaben/{pid}/abhaken")
    page = post(client, f"/aufgaben/{ids[3]}/abhaken")
    assert "Fürs Spiel zählen 3 am Tag" in page.text
    assert "Tagesziel geschafft" in page.text
    assert (
        conn.execute("SELECT COUNT(*) FROM spiel_karten WHERE art = 'aufgabe'").fetchone()[0] == 3
    )

    # After the Kassensturz, unticking and ticking again brings nothing new.
    assert "Kassensturz Nr. 1" in post(client, "/fritte/kassensturz").text
    post(client, f"/aufgaben/{ids[0]}/zurueck")
    post(client, f"/aufgaben/{ids[0]}/abhaken")
    assert stand.wartende(conn) == 0
    # The next day there are three new cards.
    assert aufgaben.karten_uebrig(conn, TODAY + timedelta(days=1)) == 3


def test_ticks_are_no_paperwork_for_mood_season_or_spices():
    from frits.spiel import gewuerze

    karten = [{"art": "aufgabe"} for _ in range(10)]
    assert gewuerze.NACH_ID["chili"].plus_wuerze(karten, {}) == 0
    mix = [{"art": "aufgabe"}, {"art": "feierabend"}, {"art": "rechnung"}]
    assert gewuerze.NACH_ID["pfeffer"].plus_wuerze(mix, {}) == 0
    from frits.spiel import saison

    assert "aufgabe" in saison.NICHT_GEZAEHLT


def test_tick_card_only_in_pflegetage_never(tmp_path):
    conn = store.connect(tmp_path / "frits.db")
    stand.vorbereiten(conn)
    aufgaben.hinzufuegen(conn, ["Mama anrufen"], TODAY)
    assert aufgaben.abhaken(conn, 1, TODAY, spiel=True) == "karte"
    assert TODAY not in stand.pflegetage(conn, TODAY)
    assert aufgaben.abhaken(conn, 1, TODAY, spiel=True) is None  # already ticked


def test_a_long_sentence_is_split_by_the_ai_and_redacted_first(client, ai):
    satz = (
        "Morgen muss ich unbedingt die Rechnung für Erika Meier schreiben und danach noch "
        "das Bahnticket eintragen"
    )
    page = post(client, "/aufgaben", text=satz)
    assert "Die KI hat deinen Satz aufgeteilt" in page.text
    sent = "".join(m["content"] for _, msgs in ai.sent for m in msgs)
    assert "Erika" not in sent and "Meier" not in sent and "[PERSON_1]" in sent
    # The real name is back in here.
    assert punkte(client)[0] == ["Rechnung für Erika Meier schreiben", "Bahnticket eintragen"]


def test_without_ai_or_with_a_strange_answer_the_sentence_stays_one_point(client, ai):
    ai.reply = "Gern! Hier ist deine Liste: erstens …"
    satz = "Ich sollte heute endlich mal die ganzen Quittungen vom Oktober zusammensuchen und sortieren"
    page = post(client, "/aufgaben", text=satz)
    assert "Die KI war gerade nicht erreichbar" in page.text
    assert punkte(client)[0] == [satz]
    ai.fail = "Keine Verbindung"
    post(client, "/aufgaben", text=satz + " bitte")
    assert len(punkte(client)[0]) == 2


def test_at_most_twenty_open_points(client):
    post(client, "/aufgaben", text="\n".join(f"Punkt {i}" for i in range(1, 19)))
    page = post(client, "/aufgaben", text="A\nB\nC\nD")
    assert "2 von 4 Punkten stehen jetzt auf der Liste" in page.text
    assert ">C\nD</textarea>" in page.text  # the rest waits in the field
    page = post(client, "/aufgaben", text="E")
    assert "Mehr als 20 offene Punkte" in page.text and ">E</textarea>" in page.text


def test_the_same_open_point_is_not_written_twice(client):
    post(client, "/aufgaben", text="Mama anrufen")
    page = post(client, "/aufgaben", text="mama anrufen")
    assert "Das steht schon auf deiner Liste." in page.text
    page = post(
        client, "/aufgaben", text="Mama anrufen\nBahnticket eintragen\nBahnticket eintragen"
    )
    assert "1 Punkt steht auf der Liste." in page.text
    assert punkte(client)[0] == ["Mama anrufen", "Bahnticket eintragen"]


def test_list_works_without_fritte(client, ws):  # noqa: F811
    from frits.settings import Settings

    current = Settings.load(ws.settings_file)
    current.haustier = False
    current.save(ws.settings_file)
    post(client, "/aufgaben", text="Mama anrufen")
    home = client.get("/").text
    assert "Deine Liste" in home and "Karten für Fritte" not in home
    page = post(client, "/aufgaben/1/abhaken")
    assert (
        "Erledigt: Mama anrufen." in page.text
        and "Karte" not in page.text.split("Erledigt:")[1][:80]
    )
    conn = store.connect(ws.db)
    assert (
        conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'spiel_karten'").fetchone()[0]
        == 0
        or not conn.execute("SELECT * FROM spiel_karten").fetchall()
    )


def test_other_websites_cannot_change_the_list(client):
    r = client.post("/aufgaben", data={"text": "Hack"}, follow_redirects=False)
    assert r.status_code == 403


def test_course_stages_are_no_paperwork_either(tmp_path):
    """Learning in the course brings its own cards, but it doesn't stand in for bookkeeping."""
    from frits.spiel import gewuerze, saison

    assert gewuerze.NACH_ID["chili"].plus_wuerze([{"art": "lernen"}] * 10, {}) == 0
    mix = [{"art": "lernen"}, {"art": "feierabend"}, {"art": "rechnung"}]
    assert gewuerze.NACH_ID["pfeffer"].plus_wuerze(mix, {}) == 0
    assert "lernen" in saison.NICHT_GEZAEHLT
    conn = store.connect(tmp_path / "frits.db")
    stand.vorbereiten(conn)
    conn.execute(
        "INSERT INTO spiel_karten (schluessel, art, gesehen) VALUES ('lernen:x', 'lernen', ?)",
        (TODAY.isoformat(),),
    )
    assert TODAY not in stand.pflegetage(conn, TODAY)

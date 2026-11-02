"""Round 7 after Lena's review: jars say what they do, a daily dish to choose, Salbei,
the list notices finished work, and Fritte asks once how to celebrate."""

from datetime import timedelta

import pytest
from test_haustier import add_expense, add_invoice, started
from test_web import TODAY, client, mails, post, provider, ws  # noqa: F401

from frits import aufgaben, store
from frits.settings import Settings
from frits.spiel import gewuerze as gw
from frits.spiel import kassensturz, stand
from frits.web.haustier import markt


@pytest.fixture
def conn(tmp_path):
    return started(store.connect(tmp_path / "frits.db"))


def karte(art, **facts):
    return {"art": art, "koerner": stand.GRUND.get(art, 1), **facts}


def test_every_jar_says_what_it_brings_or_what_is_missing():
    pfeffer, muskat, chili = gw.NACH_ID["pfeffer"], gw.NACH_ID["muskat"], gw.NACH_ID["chili"]
    zwei = [karte("steuer"), karte("steuerjahr"), karte("lernen")]
    assert gw.wirkung(pfeffer, zwei, {}) == (
        False,
        "2 von 3 Kartenarten (Abgehakt und Gelernt zählen nicht)",
    )
    assert gw.wirkung(pfeffer, [*zwei, karte("ausgabe")], {}) == (True, "+1 Würze")
    assert gw.wirkung(muskat, [karte("ausgabe"), karte("ausgabe")], {}) == (True, "+2 Körner")
    assert gw.wirkung(muskat, [karte("rechnung")], {}) == (False, "wartet auf eine Ausgabe")
    assert gw.wirkung(chili, [karte("rechnung")] * 3, {})[1] == "3 von 5 Karten"
    for g in gw.GEWUERZE:  # every spice can explain itself
        assert g.id in gw.FEHLT


def test_salbei_multiplies_real_paperwork_only():
    salbei = gw.NACH_ID["salbei"]
    papier = [karte("rechnung"), karte("ausgabe"), karte("bezahlt")]
    assert gw.wirkung(salbei, papier, {}) == (True, "Würze ×1,5")
    klicks = [karte("aufgabe"), karte("lernen"), karte("feierabend"), karte("rechnung")]
    assert gw.wirkung(salbei, klicks, {}) == (False, "1 von 3 Karten echter Papierkram")
    steuer = [karte("steuer")] * 7  # tax documents are real paperwork too
    assert gw.wirkung(salbei, steuer, {}) == (True, "Würze ×1,5")
    ergebnis = kassensturz.berechnen(papier, [salbei], {})
    assert (ergebnis["koerner"], ergebnis["wuerze"], ergebnis["summe"]) == (6, 1.5, 9)


def test_the_first_market_brings_salbei_once(conn):
    assert "salbei" in [g.id for g in markt(conn, TODAY)]
    stand.neu_wuerfeln(conn, TODAY, 0)
    assert stand.einstellung(conn, "salbei_markt") == "1"  # only the first market


def test_daily_dishes_are_a_real_choice(conn):
    for n in range(3):
        add_expense(conn, TODAY - timedelta(days=n), vendor=f"Laden {n}")
    add_invoice(conn, "2026-0001", TODAY)
    stand.abgleichen(conn, TODAY, None)
    gerichte = kassensturz.tagesgerichte(conn, TODAY)
    assert len(gerichte) == 3 and gerichte[-1].id == gw.EINKOCHEN
    assert gerichte == kassensturz.tagesgerichte(conn, TODAY)  # the same all day
    # Both dishes of the day do something with these cards.
    assert {g.id for g in gerichte[:2]} <= {"belegsuppe", "rechnungsbraten", "bunter_teller"}
    ohne = kassensturz.vorschau(conn, TODAY, False)[1]["summe"]
    mit = {g.id: kassensturz.vorschau(conn, TODAY, False, g.id)[1]["summe"] for g in gerichte}
    assert mit[gw.EINKOCHEN] == ohne and all(mit[g.id] > ohne for g in gerichte[:2])


def test_a_dish_counts_once_and_only_from_todays_offer(conn):
    add_expense(conn, TODAY, vendor="Bahn")
    add_expense(conn, TODAY, vendor="Post")
    stand.abgleichen(conn, TODAY, None)
    angebot = {g.id for g in kassensturz.tagesgerichte(conn, TODAY)}
    fremd = next(r.id for r in gw.REZEPTE if r.id not in angebot)
    nummer = kassensturz.durchfuehren(conn, TODAY, False, fremd)
    assert "rezept" not in kassensturz.laden(conn, nummer)  # not on today's menu: ignored

    add_expense(conn, TODAY, vendor="Bäcker")
    stand.abgleichen(conn, TODAY, None)
    gericht = next(g.id for g in kassensturz.tagesgerichte(conn, TODAY) if g.id != "einkochen")
    nummer = kassensturz.durchfuehren(conn, TODAY, False, gericht)
    k = kassensturz.laden(conn, nummer)
    assert k["rezept"]["id"] == gericht
    assert k["gewuerze"][0]["id"] == gericht and "wirkung" in k["gewuerze"][0]


def test_einkochen_saves_one_wuerze_for_next_time(conn):
    add_expense(conn, TODAY)
    stand.abgleichen(conn, TODAY, None)
    eins = kassensturz.durchfuehren(conn, TODAY, False, gw.EINKOCHEN)
    assert kassensturz.laden(conn, eins)["summe"] == 1  # nothing extra today
    assert kassensturz.eingekocht(conn)
    assert gw.EINKOCHEN not in [g.id for g in kassensturz.tagesgerichte(conn, TODAY)]
    add_expense(conn, TODAY, vendor="Post")
    stand.abgleichen(conn, TODAY, None)
    zwei = kassensturz.laden(conn, kassensturz.durchfuehren(conn, TODAY, False))
    assert zwei["wuerze"] == 2 and zwei["summe"] == 2
    assert zwei["gewuerze"][0]["id"] == gw.EINKOCHEN
    assert not kassensturz.eingekocht(conn)  # used up


def test_fritte_page_shows_dishes_and_what_each_jar_does(client, ws):  # noqa: F811
    conn = store.connect(ws.db)
    add_invoice(conn, "2026-0001", TODAY - timedelta(days=30))
    page = client.get("/fritte").text
    assert "Wie soll Fritte feiern?" in page  # asked once, before the first Kassensturz
    assert "Was kocht Fritte heute dazu?" not in page  # the first round stays simple
    post(client, "/fritte/stil", stil="verspielt")
    current = Settings.load(ws.settings_file)
    assert current.effects == "verspielt" and current.sound
    assert "Wie soll Fritte feiern?" not in client.get("/fritte").text
    show = post(client, "/fritte/kassensturz").text
    assert "Pfeffer" in show and 'class="glas-wirkung"' in show
    assert "kocht Fritte jedes Mal ein Gericht dazu" in show

    add_invoice(conn, "2026-0002", TODAY)
    page = client.get("/fritte").text
    assert "Was kocht Fritte heute dazu?" in page and "Einkochen" in page
    assert 'name="rezept"' in page and "required" in page
    assert "Pfeffer:</strong> 1 von 3 Kartenarten" in page


def test_fritte_does_not_ask_again_what_the_settings_already_chose(client, ws):  # noqa: F811
    """Lena: chose "Verspielt" in the settings, and Fritte's welcome asked again."""
    conn = store.connect(ws.db)
    add_invoice(conn, "2026-0001", TODAY - timedelta(days=30))
    post(client, "/settings", effects="verspielt")
    page = client.get("/fritte").text
    assert "Willkommen bei Fritte" in page and "Wie soll Fritte feiern?" not in page
    assert "Fritte feiert verspielt" in page


def test_the_list_notices_finished_work(client, ws):  # noqa: F811
    conn = store.connect(ws.db)
    add_invoice(conn, "2026-0001", TODAY - timedelta(days=30))
    client.get("/fritte")
    post(client, "/fritte/kassensturz")
    post(client, "/aufgaben", text="Bahnticket eintragen\nMama anrufen")
    client.get("/")  # FRITS has looked once: older work is not news
    add_expense(conn, TODAY, vendor="DB Fernverkehr")
    page = client.get("/expenses").text
    assert "Passt das zu „Bahnticket eintragen“ auf deiner Liste?" in page
    pid = aufgaben.liste(conn, TODAY)[0][0].id
    assert f'action="/aufgaben/{pid}/abhaken"' in page and 'name="back" value="/expenses"' in page
    assert "Passt das zu" not in client.get("/expenses").text  # once
    page = post(client, f"/aufgaben/{pid}/abhaken", back="/expenses")
    assert "Erledigt: Bahnticket eintragen." in page.text
    assert [p.text for p in aufgaben.liste(conn, TODAY)[0]] == ["Mama anrufen"]


def test_several_open_points_need_a_shared_word(conn):
    aufgaben.hinzufuegen(conn, ["Quittung Bäcker eintragen", "Ticket Bahn eintragen"], TODAY)
    assert aufgaben.vorschlaege(conn) == []  # first look
    add_expense(conn, TODAY, vendor="Bahn")
    stand.abgleichen(conn, TODAY, None)
    assert [p.text for p in aufgaben.vorschlaege(conn)] == ["Ticket Bahn eintragen"]
    add_expense(conn, TODAY, vendor="Kiosk")
    stand.abgleichen(conn, TODAY, None)
    assert aufgaben.vorschlaege(conn) == []  # no word in common: better say nothing

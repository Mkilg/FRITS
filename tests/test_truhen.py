"""Frittes Truhen: luck from real paperwork, with open odds and a guarantee."""

from collections import Counter
from datetime import timedelta

import pytest
from test_haustier import add_expense, add_invoice, started
from test_web import TODAY, client, mails, post, provider, ws  # noqa: F401

from frits import aufgaben, haustier, store
from frits.spiel import gewuerze as gw
from frits.spiel import kassensturz, stand, truhen


@pytest.fixture
def conn(tmp_path):
    return started(store.connect(tmp_path / "frits.db"))


def ausgaben(conn, n, start=0):
    for i in range(n):
        add_expense(conn, TODAY - timedelta(days=i % 5), vendor=f"Laden {start + i}")
    stand.abgleichen(conn, TODAY, None)


def kassensturz_machen(conn):
    return kassensturz.laden(conn, kassensturz.durchfuehren(conn, TODAY, False))


def nur(monkeypatch, fund):
    """Make the next chests hold exactly this (the draw itself is tested on its own)."""
    monkeypatch.setattr(truhen, "_fund", lambda conn, rng, s, gold, sachen=False: dict(fund))


def test_five_paperwork_cards_fill_a_chest_ticks_do_not(conn, monkeypatch):
    monkeypatch.setattr(truhen, "ist_glueck", lambda key: False)
    ausgaben(conn, 3)
    aufgaben.hinzufuegen(conn, ["Mama anrufen", "Blumen gießen"], TODAY)
    for p in aufgaben.liste(conn, TODAY)[0]:
        aufgaben.abhaken(conn, p.id, TODAY, spiel=True)
    k = kassensturz_machen(conn)
    # The first Kassensturz brings one chest as a hello; the three expenses fill the next.
    assert k["truhen"]["neu"] == ["Fritte hat eine Truhe für dich gefunden"]
    assert (k["truhen"]["dazu"], k["truhen"]["jetzt"]) == (3, 3)
    ausgaben(conn, 2, start=10)
    k = kassensturz_machen(conn)
    assert k["truhen"]["neu"] == ["Die Truhe ist voll"]
    assert truhen.fuellung(conn) == 0
    ausgaben(conn, 10, start=20)
    k = kassensturz_machen(conn)
    assert k["truhen"]["neu"] == ["2 volle Truhen"] and k["truhen"]["anzahl"] == 2
    assert [t.grund for t in truhen.geschlossen(conn)][-2:] == ["voll:2", "voll:3"]
    assert [t.grund for t in truhen.geschlossen(conn)][:2] == ["willkommen", "voll:1"]


def test_the_welcome_round_does_not_fill_chests(tmp_path):
    conn = store.connect(tmp_path / "frits.db")
    for n in range(8):
        add_invoice(conn, f"2026-{n:04}", TODAY - timedelta(days=40 + n))
    stand.abgleichen(conn, TODAY, None)  # the first time: old paperwork, the welcome round
    k = kassensturz_machen(conn)
    assert k["truhen"]["dazu"] == 0 and len(truhen.geschlossen(conn)) == 1


def test_a_lucky_card_brings_its_own_chest_and_is_golden(conn, monkeypatch):
    ausgaben(conn, 1)
    kassensturz_machen(conn)  # the hello chest
    key = next(f"ausgabe:{i}" for i in range(10_000) if truhen.ist_glueck(f"ausgabe:{i}"))
    assert truhen.ist_glueck(key) and truhen.ist_glueck(key)  # always the same answer
    share = sum(truhen.ist_glueck(f"rechnung:{i}") for i in range(4000)) / 4000
    assert 0.1 < share < 0.15  # about one in eight
    monkeypatch.setattr(truhen, "ist_glueck", lambda k: k.startswith("ausgabe:"))
    ausgaben(conn, 1, start=50)
    k = kassensturz_machen(conn)
    assert k["truhen"]["neu"] == ["Glückskarte: Ausgabe"]
    assert k["karten"][0]["glueck"]
    assert len(truhen.geschlossen(conn)) == 2


def test_lucky_ticks_bring_nothing(conn, monkeypatch):
    monkeypatch.setattr(truhen, "ist_glueck", lambda key: True)
    aufgaben.hinzufuegen(conn, ["Mama anrufen"], TODAY)
    aufgaben.abhaken(conn, 1, TODAY, spiel=True)
    k = kassensturz_machen(conn)
    assert k["truhen"]["glueck"] == [] and k["truhen"]["dazu"] == 0


def test_give_is_once_per_reason_and_keeps_its_least_rarity(conn):
    assert truhen.truhe_geben(conn, "drachensieg:2025", "selten", "Der Drache ist besiegt!")
    assert not truhen.truhe_geben(conn, "drachensieg:2025", "selten")
    t = truhen.oeffnen(conn, TODAY)
    assert t.inhalt["seltenheit"] in ("selten", "legendär")
    assert truhen.oeffnen(conn, TODAY) is None  # nothing closed any more
    again = truhen.laden(conn, t.id)
    assert again.inhalt == t.inhalt  # saved once: looking again changes nothing


def test_odds_and_the_guarantee(conn):
    for n in range(240):
        truhen.truhe_geben(conn, f"test:{n}")
    reihe = []
    for _ in range(240):
        reihe.append(truhen.oeffnen(conn, TODAY).inhalt["seltenheit"])
    zaehlung = Counter(reihe)
    assert 0.45 < zaehlung["gewöhnlich"] / 240 < 0.68
    assert zaehlung["legendär"] >= 240 // 12
    ohne_selten = ohne_legendaer = 0
    for s in reihe:
        ohne_selten = 0 if s != "gewöhnlich" else ohne_selten + 1
        ohne_legendaer = 0 if s == "legendär" else ohne_legendaer + 1
        assert ohne_selten < truhen.SELTEN_SPAETESTENS
        assert ohne_legendaer < truhen.LEGENDAER_SPAETESTENS
    c = truhen.chancen(conn)
    assert 1 <= c["selten_in"] <= 4 and 1 <= c["legendaer_in"] <= 12 and c["geoeffnet"] == 240


def test_what_a_chest_can_hold(conn, monkeypatch):
    nur(monkeypatch, {"art": "koerner", "wert": 8})
    truhen.truhe_geben(conn, "a")
    vorher = stand.guthaben(conn)
    truhen.oeffnen(conn, TODAY)
    assert stand.guthaben(conn) == vorher + 8

    nur(monkeypatch, {"art": "joker", "wert": 8})
    truhen.truhe_geben(conn, "b")
    truhen.oeffnen(conn, TODAY)
    nur(monkeypatch, {"art": "wuerze", "wert": 1.0})
    truhen.truhe_geben(conn, "c")
    truhen.oeffnen(conn, TODAY)
    k = kassensturz_machen(conn)
    joker = [c for c in k["karten"] if c["art"] == "joker"]
    assert joker and joker[0]["koerner"] == 8
    assert k["gewuerze"][0]["id"] == "truhenwuerze" and k["wuerze"] == 2.0
    assert k["truhen"]["dazu"] == 0  # a Jokerkarte is no paperwork
    assert truhen.truhenwuerze(conn) == 0  # used up
    assert not [c for c in kassensturz.offene_karten(conn) if c.art == "joker"]

    nur(monkeypatch, {"art": "ding", "id": "kleeblatt"})
    truhen.truhe_geben(conn, "d")
    truhen.oeffnen(conn, TODAY)
    assert stand.besitzt(conn, "kleeblatt")
    nur(monkeypatch, {"art": "gewuerz", "id": "chili"})
    truhen.truhe_geben(conn, "e")
    truhen.oeffnen(conn, TODAY)
    assert "chili" in [g.id for g in kassensturz.regal(conn)]


def test_the_draw_only_offers_what_fits(conn):
    import random

    for g in gw.GEWUERZE[: kassensturz.plaetze(conn)]:
        stand.nimm(conn, g.id, "gewuerz", TODAY.isoformat(), 0)
    arten = {truhen._fund(conn, random.Random(n), "selten", False)["art"] for n in range(200)}
    assert "gewuerz" not in arten  # the shelf is full
    assert "gold" not in arten  # no Steuer-Quest in use
    mit_gold = {truhen._fund(conn, random.Random(n), "selten", True)["art"] for n in range(200)}
    assert "gold" in mit_gold
    gewoehnlich = {
        truhen._fund(conn, random.Random(n), "gewöhnlich", True)["art"] for n in range(200)
    }
    assert "gold" not in gewoehnlich
    funde = [truhen._fund(conn, random.Random(n), "legendär", False) for n in range(300)]
    dinge = {f["id"] for f in funde if f["art"] == "ding"}
    assert dinge <= {"zylinder", "katze", "doktorhut", "krone", "waage", "sternenhut"}


def test_the_first_chest_holds_something_to_keep(conn):
    truhen.truhe_geben(conn, truhen.WILLKOMMEN)
    assert truhen.oeffnen(conn, TODAY).inhalt["art"] in ("ding", "gewuerz")


def test_chest_treasures_are_not_for_sale(conn):
    stand.buche(conn, TODAY.isoformat(), 500, "start", "Test")
    with pytest.raises(stand.SpielFehler):
        haustier.kaufen(conn, TODAY, "waage")


def test_gold_is_credited_once_after_the_chest_is_saved(conn, monkeypatch):
    nur(monkeypatch, {"art": "gold", "wert": 8})
    truhen.truhe_geben(conn, "g")
    t = truhen.oeffnen(conn, TODAY, gold_jahr=2025)
    gebucht = []

    def gutschreiben(c, jahr, betrag, text, grund):
        gebucht.append((jahr, betrag, grund))
        return betrag

    truhen.gold_buchen(conn, t, gutschreiben)
    truhen.gold_buchen(conn, truhen.laden(conn, t.id), gutschreiben)
    assert gebucht == [(2025, 8, f"fritte-truhe:{t.id}")]
    assert truhen.laden(conn, t.id).inhalt["gutgeschrieben"]


def test_gold_reaches_the_real_quest(conn, monkeypatch):
    from frits.steuer import quest

    nur(monkeypatch, {"art": "gold", "wert": 15})
    truhen.truhe_geben(conn, "g")
    t = truhen.oeffnen(conn, TODAY, gold_jahr=2025)
    vorher = quest.gold(conn, 2025)
    truhen.gold_buchen(conn, t, quest.gold_gutschreiben)
    truhen.gold_buchen(conn, t, quest.gold_gutschreiben)
    assert quest.gold(conn, 2025) == vorher + 15


def test_fritte_page_and_opening_in_the_browser(client, ws):  # noqa: F811
    conn = store.connect(ws.db)
    add_invoice(conn, "2026-0001", TODAY - timedelta(days=30))
    client.get("/fritte")
    show = post(client, "/fritte/kassensturz").text
    assert "Eine neue Truhe!" in show and "Truhe öffnen" in show
    page = client.get("/fritte").text
    assert "Frittes Truhen" in page and "1 Truhe wartet" in page
    assert "60 % gewöhnlich, 30 % selten, 10 % legendär" in page
    assert "Nächste Truhe: <strong>0 von 5</strong>" in page

    r = client.post("/fritte/truhe/oeffnen", data={"token": client.token}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("?neu=1")
    page = client.get(r.headers["location"]).text
    assert "data-neu" in page and "fund-name" in page and "/static/truhe.js" in page
    t = truhen.geoeffnete(conn)[0]
    assert t.inhalt["name"] in page
    assert "data-neu" not in client.get(f"/fritte/truhe/{t.id}").text  # again: no show
    assert "Gerade ist keine Truhe zu" in client.get("/fritte").text
    assert "Gerade wartet keine Truhe" in post(client, "/fritte/truhe/oeffnen").text


def test_other_websites_cannot_open_chests(client):  # noqa: F811
    r = client.post("/fritte/truhe/oeffnen", follow_redirects=False)
    assert r.status_code == 403


def test_drachengold_becomes_wuerze_only_after_the_dragon(client, ws):  # noqa: F811
    from frits.steuer import quest

    conn = store.connect(ws.db)
    add_invoice(conn, "2026-0001", TODAY - timedelta(days=30))
    client.get("/fritte")
    post(client, "/fritte/kassensturz")
    jahr = TODAY.year - 1
    quest.gold_gutschreiben(conn, jahr, 50, "Test", "test:1")
    assert 'id="drachengold"' not in client.get("/fritte").text  # no victory yet
    assert "Gerade geht das nicht" in post(client, "/fritte/drachengold").text
    assert quest.gold(conn, jahr) == 50

    quest.notiere(conn, jahr, "sieg", "Der Drache ist besiegt")
    page = client.get("/fritte").text
    assert "noch <strong>50 Gold</strong>" in page and "20 Gold einkochen" in page
    post(client, "/fritte/drachengold")
    page = post(client, "/fritte/drachengold").text
    assert quest.gold(conn, jahr) == 10
    assert "2 Gläser Drachenwürze warten" in page and "20 Gold einkochen" not in page
    assert "Gerade geht das nicht" in post(client, "/fritte/drachengold").text  # at most two

    add_invoice(conn, "2026-0002", TODAY)
    k = kassensturz_machen(conn)
    assert k["gewuerze"][0]["id"] == "drachenwuerze" and k["wuerze"] >= 3
    assert kassensturz.drachenglaeser(conn) == 0
    assert 'id="drachengold"' not in client.get("/fritte").text  # 10 gold: not enough

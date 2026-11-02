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
    monkeypatch.setattr(truhen, "_fund", lambda conn, rng, s, sachen=False: dict(fund))


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
    arten = {truhen._fund(conn, random.Random(n), "selten")["art"] for n in range(200)}
    assert "gewuerz" not in arten  # the shelf is full
    # Only what FRITS already knows (Lena): no Joker, no extra jars, no quest gold.
    assert arten <= {"ding", "koerner"}
    funde = [truhen._fund(conn, random.Random(n), "legendär") for n in range(300)]
    dinge = {f["id"] for f in funde if f["art"] == "ding"}
    assert dinge and dinge <= {"waage", "sternenhut"}  # only chest treasures, never shop wares


def test_chest_things_come_only_from_the_treasures_and_never_twice(conn):
    import random

    gewoehnlich = [truhen._fund(conn, random.Random(n), "gewöhnlich") for n in range(300)]
    dinge = {f["id"] for f in gewoehnlich if f["art"] == "ding"}
    assert dinge == {"muschel", "postkarte", "schleife"}
    assert not {d.id for d in haustier.LADEN} & dinge
    for d in haustier.SCHAETZE:
        if d.truhe == "gewöhnlich":
            stand.nimm(conn, d.id, d.art, TODAY.isoformat(), 0)
    nochmal = [truhen._fund(conn, random.Random(n), "gewöhnlich") for n in range(300)]
    assert "ding" not in {f["art"] for f in nochmal}  # all found: something else instead


def test_the_best_find_is_the_rarest_and_something_to_keep():
    def t(i, seltenheit, art):
        return truhen.Truhe(i, f"g{i}", "", None, "", None, {"seltenheit": seltenheit, "art": art})

    assert truhen.bester([t(1, "gewöhnlich", "ding"), t(2, "selten", "koerner")]).id == 2
    assert truhen.bester([t(1, "selten", "koerner"), t(2, "selten", "gewuerz")]).id == 2
    assert truhen.bester([t(1, "selten", "koerner"), t(2, "selten", "koerner")]).id == 1


def test_the_first_chest_holds_something_to_keep(conn):
    truhen.truhe_geben(conn, truhen.WILLKOMMEN)
    assert truhen.oeffnen(conn, TODAY).inhalt["art"] in ("ding", "gewuerz")


def test_chest_treasures_are_not_for_sale(conn):
    stand.buche(conn, TODAY.isoformat(), 500, "start", "Test")
    with pytest.raises(stand.SpielFehler):
        haustier.kaufen(conn, TODAY, "waage")


def test_leftover_dragon_gold_becomes_salzkoerner_once_a_year(conn):
    vorher = stand.guthaben(conn)
    text = "Restgold aus der Steuer-Quest 2025"
    assert haustier.salz_aus_quest(conn, 2025, 37, text, TODAY) == 18  # 2 gold, 1 Korn
    assert stand.guthaben(conn) == vorher + 18
    assert stand.kassenbuch(conn)[0]["text"] == text
    assert haustier.salz_aus_quest(conn, 2025, 37, "nochmal", TODAY) == 0  # once per year
    assert haustier.salz_aus_quest(conn, 2024, 1, "zu wenig", TODAY) == 0
    assert stand.guthaben(conn) == vorher + 18
    # a victory helps with the house but doesn't buy it (Lena: the Stube costs 150)
    assert haustier.salz_aus_quest(conn, 2023, 500, "", TODAY) == haustier.SALZ_PRO_SIEG == 30
    assert haustier.salz_fuer_gold(37) == 18 and haustier.salz_fuer_gold(-3) == 0


def test_salz_from_the_quest_works_before_fritte_was_ever_opened(tmp_path):
    conn = store.connect(tmp_path / "frits.db")
    assert haustier.salz_aus_quest(conn, 2025, 12, "Restgold", TODAY) == 6
    assert stand.guthaben(conn) == 6


def test_old_games_get_grains_for_jokers_and_jars_once(conn):
    truhen.truhe_geben(conn, "alt")
    t = truhen.oeffnen(conn, TODAY)
    conn.execute(
        "UPDATE spiel_truhen SET inhalt = ? WHERE id = ?",
        ('{"art": "joker", "wert": 8, "seltenheit": "selten", "name": "Jokerkarte"}', t.id),
    )
    conn.execute(
        "INSERT INTO spiel_karten (schluessel, art, gesehen) VALUES (?, 'joker', ?)",
        (f"joker:{t.id}", TODAY.isoformat()),
    )
    stand.setze(conn, "truhen_wuerze", "1.5")
    stand.setze(conn, "drachen_wuerze", "1")
    vorher = stand.guthaben(conn)
    assert haustier.altlasten(conn, TODAY) == 8 + 25
    assert stand.guthaben(conn) == vorher + 33
    assert "Ausgleich" in stand.kassenbuch(conn)[0]["text"]
    assert not conn.execute("SELECT 1 FROM spiel_karten WHERE art = 'joker'").fetchone()
    assert stand.einstellung(conn, "truhen_wuerze") == "0"
    assert haustier.altlasten(conn, TODAY) == 0  # only once
    assert stand.guthaben(conn) == vorher + 33


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


def test_open_all_shows_the_best_find_big_and_the_rest_beside_it(client, ws):  # noqa: F811
    conn = store.connect(ws.db)
    client.get("/fritte")
    for i in range(4):
        truhen.truhe_geben(conn, f"probe:{i}")
    page = client.get("/fritte").text
    assert "Alle 4 auf einmal öffnen" in page
    r = client.post("/fritte/truhe/alle", data={"token": client.token}, follow_redirects=False)
    assert r.status_code == 303 and "?neu=1&mit=" in r.headers["location"]
    offen = truhen.geoeffnete(conn, 10)
    assert len(offen) == 4 and not truhen.geschlossen(conn)
    best = truhen.bester(offen)
    assert r.headers["location"].startswith(f"/fritte/truhe/{best.id}?")
    page = client.get(r.headers["location"]).text
    assert "Außerdem in den anderen 3 Truhen" in page
    for t in offen:
        assert t.inhalt["name"] in page
    assert "Gerade wartet keine Truhe" in post(client, "/fritte/truhe/alle").text


def test_the_shop_shows_the_bridge_to_the_dragon(client, ws):  # noqa: F811
    conn = store.connect(ws.db)
    add_invoice(conn, "2026-0001", TODAY - timedelta(days=30))
    client.get("/fritte")
    post(client, "/fritte/kassensturz")
    page = client.get("/fritte/laden").text
    assert len(kassensturz.regal(conn)) == 1  # the first spice
    # The same number the fight's banner shows before the first Beleg (Lena: ×2 here, ×4 there)
    assert "drachen-bruecke" in page and "erste Beleg <strong>×4</strong>, ohne Gewürze ×2" in page
    assert "🐉</span> Drachenkampf: erster Beleg ×5 statt ×4" in page  # on spices one could add
    assert "Sanduhr" not in page.split("drachen-bruecke")[1].split("</p>")[0]  # ×2 is under its cap
    for g in gw.GEWUERZE:
        if len(kassensturz.regal(conn)) >= kassensturz.plaetze(conn):
            break
        if not stand.besitzt(conn, g.id):
            stand.nimm(conn, g.id, "gewuerz", TODAY.isoformat(), 0)
    assert len(kassensturz.regal(conn)) == 3
    page = client.get("/fritte/laden").text
    assert (
        "erste Beleg <strong>×6</strong>, ohne" in page and "Sanduhr" not in page
    )  # the fight shows it
    assert "drachen-plus" not in page  # a full bridge: no more promises


def test_the_bridge_counts_the_quest_helpers_like_the_fight():
    from frits.steuer import spiel
    from frits.web.haustier import drachen_serie

    for n in range(5):
        assert drachen_serie(n)["jetzt"] == spiel.multiplikator(spiel.start_serie(n) + 2)
    assert drachen_serie(1, frozenset({"trommel"}))["jetzt"] == 5  # the drum: one more
    assert drachen_serie(3, frozenset({"sanduhr"}))["jetzt"] == 3  # the hourglass caps


def test_other_websites_cannot_open_chests(client):  # noqa: F811
    r = client.post("/fritte/truhe/oeffnen", follow_redirects=False)
    assert r.status_code == 403
    r = client.post("/fritte/truhe/alle", follow_redirects=False)
    assert r.status_code == 403


def test_no_drachengold_jar_any_more(client):  # noqa: F811
    r = client.post("/fritte/drachengold", data={"token": client.token})
    assert r.status_code in (404, 405)

"""Fritte and the everyday game: gentle moods, cards from real paperwork, the Kassensturz."""

import json
from datetime import date, timedelta

import pytest
from test_web import TODAY, client, mails, post, provider, ws  # noqa: F401

from frits import haustier, store
from frits.settings import Settings
from frits.spiel import kassensturz, stand
from frits.spiel.gewuerze import NACH_ID


def add_invoice(conn, number, issue, paid=None, due=None, work=None, cancels=None):
    draft_id = None
    if work:
        lines = [{"description": "Arbeit", "date": work.strftime("%d.%m.%Y")}]
        draft_id = conn.execute(
            "INSERT INTO drafts (created, data, status) VALUES (?, ?, 'approved')",
            (issue.isoformat(), json.dumps({"lines": lines})),
        ).lastrowid
    conn.execute(
        "INSERT INTO invoices (number, customer, issue_date, paid_date, due_date, net, vat, gross,"
        " draft_id, cancels) VALUES (?, 'Erika Meier', ?, ?, ?, '100', '0', '100', ?, ?)",
        (
            number,
            issue.isoformat(),
            paid.isoformat() if paid else None,
            due.isoformat() if due else None,
            draft_id,
            cancels,
        ),
    )


def add_expense(conn, paid):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS expenses (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT,"
        " vendor TEXT, description TEXT, gross TEXT, vat_rate TEXT, category TEXT,"
        " source_file TEXT)"
    )
    return conn.execute(
        "INSERT INTO expenses (date, vendor, description, gross, category)"
        " VALUES (?, 'Bahn', 'Fahrt', '23.80', 'reisekosten')",
        (paid.isoformat(),),
    ).lastrowid


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "frits.db")


def started(conn):
    """A game that is already set up, so new paperwork becomes cards."""
    stand.abgleichen(conn, TODAY)
    return conn


def give(conn, n):
    stand.buche(conn, TODAY.isoformat(), n, "start", "Test")


# ---------- Fritte ----------


def test_new_fritte_is_curious_and_has_nothing(conn):
    f = haustier.ansehen(conn, TODAY)
    assert f.laune == "neu" and f.guthaben == 0 and f.wartend == 0
    assert f.wochen == [False] * haustier.WOCHEN
    assert f.zimmer == [] and f.hut is None


@pytest.mark.parametrize(
    "days_ago, laune, bild",
    [(0, "munter", "welcome"), (3, "munter", "welcome"), (7, "gemuetlich", "thinking"),
     (30, "doest", "feierabend")],
)  # fmt: skip
def test_mood_follows_the_rhythm_and_never_gets_sad(conn, days_ago, laune, bild):
    add_invoice(conn, "2026-0001", TODAY - timedelta(days=days_ago))
    f = haustier.ansehen(conn, TODAY)
    assert (f.laune, f.bild) == (laune, bild)
    assert not any(w in f.satz.lower() for w in ("traurig", "vermisst", "endlich", "schon lange"))


def test_older_work_is_a_welcome_gift_not_a_card(conn):
    add_invoice(conn, "2026-0001", date(2026, 1, 5), paid=date(2026, 1, 20))
    add_invoice(conn, "2026-0002", date(2026, 2, 5))
    f = haustier.ansehen(conn, TODAY)
    assert f.guthaben == 3 + 2 + 3 and f.wartend == 0
    assert f.laune == "doest"  # the gift doesn't make today a care day
    assert stand.kassenbuch(conn)[0]["text"].startswith("Willkommensgeschenk")


def test_the_first_version_moves_in(conn, tmp_path):
    old = tmp_path / "fritte.toml"
    old.write_text(
        'gekauft = ["teetasse", "muetze"]\ntraegt = "muetze"\nausgegeben = 20\nfeierabende = 2\n'
        'pflegetage = ["2026-10-30"]\n',
        encoding="utf-8",
    )
    for n in range(8):
        add_invoice(conn, f"2026-{n:04}", date(2026, 1, 5))
    f = haustier.ansehen(conn, TODAY, old)
    assert f.guthaben == 8 * 3 + 2 - 20
    assert [d.id for d in f.zimmer] == ["teetasse"] and f.hut.id == "muetze"
    assert f.tage_ruhig == (TODAY - date(2026, 10, 30)).days


def test_buying_things_and_hats(conn):
    started(conn)
    give(conn, 30)
    with pytest.raises(stand.SpielFehler, match="fehlen noch 10"):
        haustier.kaufen(conn, TODAY, "zylinder")
    haustier.kaufen(conn, TODAY, "teetasse")
    haustier.kaufen(conn, TODAY, "muetze")
    with pytest.raises(stand.SpielFehler, match="schon"):
        haustier.kaufen(conn, TODAY, "teetasse")
    with pytest.raises(stand.SpielFehler):
        haustier.kaufen(conn, TODAY, "../../etc")
    f = haustier.ansehen(conn, TODAY)
    assert f.guthaben == 30 - 5 - 15
    assert [d.id for d in f.zimmer] == ["teetasse"] and f.hut.id == "muetze"
    haustier.aufsetzen(conn, "muetze")  # the same hat again takes it off
    assert haustier.ansehen(conn, TODAY).hut is None
    haustier.aufsetzen(conn, "zylinder")  # not bought: nothing goes on
    assert haustier.ansehen(conn, TODAY).hut is None


# ---------- cards and the Kassensturz ----------


def test_new_paperwork_becomes_cards_and_is_scored_once(conn):
    started(conn)
    add_invoice(conn, "2026-0001", TODAY, paid=TODAY)
    assert stand.feierabend(conn, TODAY) and not stand.feierabend(conn, TODAY)
    assert stand.abgleichen(conn, TODAY) == 3
    nummer = kassensturz.durchfuehren(conn, TODAY, aufgeraeumt=False)
    k = kassensturz.laden(conn, nummer)
    assert (k["koerner"], k["wuerze"], k["summe"]) == (3 + 2 + 1, 1.0, 6)
    assert stand.guthaben(conn) == 6
    assert stand.abgleichen(conn, TODAY) == 0
    with pytest.raises(stand.SpielFehler):
        kassensturz.durchfuehren(conn, TODAY, aufgeraeumt=False)


def test_undoing_work_never_earns(conn):
    started(conn)
    add_invoice(conn, "2026-0001", TODAY)
    add_invoice(conn, "2026-0002", TODAY, cancels="2026-0001")  # the Storno
    eid = add_expense(conn, TODAY)
    stand.abgleichen(conn, TODAY)
    conn.execute("DELETE FROM expenses WHERE id = ?", (eid,))
    assert kassensturz.offene_karten(conn) == []
    assert stand.abgleichen(conn, TODAY) == 0  # gone for good


def test_spice_facts_follow_bookkeeping_rules(conn):
    started(conn)
    add_invoice(conn, "2026-0001", TODAY, work=TODAY - timedelta(days=3))
    add_invoice(conn, "2026-0002", TODAY, work=TODAY - timedelta(days=40))
    add_invoice(conn, "2026-0003", TODAY - timedelta(days=20), paid=TODAY - timedelta(days=5),
                due=TODAY - timedelta(days=6))  # fmt: skip
    add_expense(conn, TODAY - timedelta(days=4))
    add_expense(conn, TODAY - timedelta(days=60))
    stand.abgleichen(conn, TODAY)
    facts = {k.schluessel: k.fakten for k in kassensturz.offene_karten(conn)}
    assert facts["rechnung:2026-0001"]["frisch"] and not facts["rechnung:2026-0002"]["frisch"]
    assert not facts["bezahlt:2026-0003"]["puenktlich"]
    zeitnah = [f["zeitnah"] for key, f in facts.items() if key.startswith("ausgabe")]
    assert zeitnah == [True, False]


def card(art, **facts):
    return {"art": art, "koerner": stand.GRUND[art], **facts}


def test_scoring_with_spices_and_order():
    cards = [card("rechnung", frisch=True), card("ausgabe", zeitnah=True), card("feierabend")]
    spices = [NACH_ID[i] for i in ("meersalz", "rosmarin", "paprika", "pfeffer", "trueffelsalz")]
    result = kassensturz.berechnen(cards, spices, {"rhythmus_wochen": 3})
    # Körner: 3+2+4 (Rechnung) + 1+3 (Ausgabe) + 1 = 14; Würze (1 + 1) × 2 = 4
    assert (result["koerner"], result["wuerze"], result["summe"]) == (14, 4.0, 56)
    # ×2 before +1 is worth less: the order on the shelf matters.
    spices = [NACH_ID[i] for i in ("trueffelsalz", "pfeffer")]
    assert kassensturz.berechnen(cards, spices, {"rhythmus_wochen": 3})["wuerze"] == 3.0
    assert kassensturz.berechnen(cards, spices, {"rhythmus_wochen": 2})["wuerze"] == 2.0


def test_malzessig_matures_on_the_shelf(conn):
    started(conn)
    give(conn, 20)
    stand.nimm(conn, "malzessig", "gewuerz", TODAY.isoformat(), 20)
    for n in range(3):
        stand.feierabend(conn, TODAY + timedelta(days=n))
        kassensturz.durchfuehren(conn, TODAY + timedelta(days=n), aufgeraeumt=False)
    assert [kassensturz.laden(conn, n)["wuerze"] for n in (1, 2, 3)] == [1.0, 1.2, 1.4]


def test_market_is_stable_all_week_and_rolling_costs(conn):
    started(conn)
    ids, weights = [g for g in NACH_ID], [1] * len(NACH_ID)
    week = [stand.angebot(ids, weights, TODAY + timedelta(days=d), 0, 3) for d in range(-1, 3)]
    monday = TODAY - timedelta(days=TODAY.weekday())
    same_week = [stand.angebot(ids, weights, monday + timedelta(days=d), 0, 3) for d in range(7)]
    assert all(o == same_week[0] for o in same_week) and len(set(same_week[0])) == 3
    assert week  # different weeks may differ; just make sure it runs
    with pytest.raises(stand.SpielFehler):
        stand.neu_wuerfeln(conn, TODAY, 2)
    give(conn, 5)
    stand.neu_wuerfeln(conn, TODAY, 2)
    assert stand.wuerfe(conn, TODAY) == 1 and stand.guthaben(conn) == 3


# ---------- the pages ----------


def test_pages_and_a_whole_round(client, ws):  # noqa: F811
    conn = store.connect(ws.db)
    add_invoice(conn, "2026-0001", TODAY - timedelta(days=30))
    home = client.get("/").text
    assert "Fritte zuhause" in home and "Kassensturz machen" not in home  # old work is a gift

    add_invoice(conn, "2026-0002", TODAY, work=TODAY - timedelta(days=1))
    post(client, "/feierabend")
    assert "2 Karten warten" in client.get("/").text
    assert "Kassensturz machen" in client.get("/feierabend").text

    page = post(client, "/fritte/kassensturz")
    assert "Kassensturz Nr. 1" in page.text and 'id="ks-daten"' in page.text
    assert stand.guthaben(conn) == 3 + 3 + 1

    laden = client.get("/fritte/laden").text
    assert "Gewürzregal" in laden and "Markttag" in laden and "Frittes Kassenbuch" in laden
    page = post(client, "/fritte/gewuerz/kaufen", gewuerz="meersalz")
    assert "Meersalz steht jetzt im Gewürzregal" in page.text
    assert stand.guthaben(conn) == 2
    page = post(client, "/fritte/gewuerz/kaufen", gewuerz="safran")
    assert "fehlen noch 23" in page.text
    page = post(client, "/fritte/gewuerz/verkaufen", gewuerz="meersalz")
    assert "verkauft für 2" in page.text and stand.guthaben(conn) == 4

    give(conn, 1)
    page = post(client, "/fritte/kaufen", ding="teetasse")
    assert "Die Teetasse hat jetzt einen schönen Platz" in page.text
    assert client.get("/fritte/kassensturz/99").status_code == 404
    assert "Chronik" in client.get("/fritte").text


def test_the_shelf_has_limited_places(client, ws):  # noqa: F811
    conn = store.connect(ws.db)
    client.get("/")
    give(conn, 100)
    for gid in ("meersalz", "muskat", "schnittlauch"):
        post(client, "/fritte/gewuerz/kaufen", gewuerz=gid)
    page = post(client, "/fritte/gewuerz/kaufen", gewuerz="paprika")
    assert "Das Regal ist voll" in page.text
    post(client, "/fritte/gewuerz/links", gewuerz="schnittlauch")
    assert [g.id for g in kassensturz.regal(conn)] == ["meersalz", "schnittlauch", "muskat"]


def test_fritte_can_be_hidden(client, ws):  # noqa: F811
    current = Settings.load(ws.settings_file)
    current.haustier = False
    current.save(ws.settings_file)
    assert "Fritte zuhause" not in client.get("/").text
    post(client, "/settings", checkboxes=["haustier"], haustier="on")
    assert Settings.load(ws.settings_file).haustier


# ---------- Frittes Haus ----------


def test_building_out_the_house(conn):
    from frits.spiel import ausbau

    started(conn)
    assert ausbau.stufe(conn).name == "Zimmer" and kassensturz.plaetze(conn) == 3
    with pytest.raises(stand.SpielFehler, match="fehlen noch 80"):
        ausbau.ausbauen(conn, TODAY)
    give(conn, 100)
    assert ausbau.ausbauen(conn, TODAY).name == "Stube"
    with pytest.raises(stand.SpielFehler, match="schon gebaut"):
        ausbau.ausbauen(conn, TODAY)
    f = haustier.ansehen(conn, TODAY)
    assert f.bau == "Stube" and f.stufe == 1 and f.guthaben == 20
    # The next Kassensturz finishes the building work.
    stand.feierabend(conn, TODAY)
    k = kassensturz.laden(conn, kassensturz.durchfuehren(conn, TODAY, aufgeraeumt=False))
    assert k["ausbau"]["name"] == "Stube"
    assert ausbau.stufe(conn).name == "Stube" and kassensturz.plaetze(conn) == 4
    assert "selten" in ausbau.stufe(conn).seltenheiten
    assert haustier.ansehen(conn, TODAY).bau == ""


def test_later_stages_start_with_more_wuerze(conn):
    from frits.spiel import ausbau

    started(conn)
    stand.setze(conn, "ausbau", "4")
    stand.feierabend(conn, TODAY)
    k = kassensturz.laden(conn, kassensturz.durchfuehren(conn, TODAY, aufgeraeumt=False))
    assert (k["koerner"], k["wuerze"], k["summe"]) == (1, 1.5, 1)
    assert ausbau.naechste(conn).name == "Frittenbude am Markt"

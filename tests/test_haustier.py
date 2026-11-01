"""Fritte and the everyday game: gentle moods, cards from real paperwork, the Kassensturz."""

import json
from datetime import date, timedelta

import pytest
from test_web import TODAY, client, mails, post, provider, ws  # noqa: F401

from frits import haustier, store
from frits.settings import Settings
from frits.spiel import kassensturz, stand
from frits.spiel.gewuerze import NACH_ID
from frits.web.haustier import markt


def add_job(conn, filename, received):
    return conn.execute(
        "INSERT INTO jobs (sha256, filename, received, status) VALUES (?, ?, ?, 'drafted')",
        (filename + received.isoformat(), filename, received.isoformat() + "T09:00:00"),
    ).lastrowid


def add_invoice(conn, number, issue, paid=None, due=None, came=None, cancels=None, work=None):
    """came: the day the note about the work arrived in the inbox. work: the date Fritz
    typed on the line (the game must not care about it)."""
    draft_id = None
    if came or work:
        lines = [{"description": "Arbeit", "date": (work or issue).strftime("%d.%m.%Y")}]
        job = add_job(conn, f"notiz-{number}.txt", came) if came else None
        draft_id = conn.execute(
            "INSERT INTO drafts (job_id, created, data, status) VALUES (?, ?, ?, 'approved')",
            (job, issue.isoformat(), json.dumps({"lines": lines})),
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


def add_expense(conn, paid, vendor="Bahn", gross="23.80", came=None):
    """came: the day the receipt file arrived in FRITS (None: typed in by hand)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS expenses (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT,"
        " vendor TEXT, description TEXT, gross TEXT, vat_rate TEXT, category TEXT,"
        " source_file TEXT)"
    )
    source = None
    if came:
        source = f"beleg-{vendor}-{paid.isoformat()}-{came.isoformat()}.pdf"
        add_job(conn, source, came)
    return conn.execute(
        "INSERT INTO expenses (date, vendor, description, gross, category, source_file)"
        " VALUES (?, ?, 'Fahrt', ?, 'reisekosten', ?)",
        (paid.isoformat(), vendor, gross, source),
    ).lastrowid


def add_reminder(conn, number, created, level=1):
    return conn.execute(
        "INSERT INTO reminders (invoice_number, created, level, new_due_date, pdf_path)"
        " VALUES (?, ?, ?, ?, 'x.pdf')",
        (number, created.isoformat() + "T10:00:00", level, created.isoformat()),
    ).lastrowid


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "frits.db")


def started(conn):
    """A game that is already set up (without the welcome round), so new paperwork
    becomes cards."""
    stand.vorbereiten(conn)
    stand.setze(conn, "eingerichtet", TODAY.isoformat())
    stand.setze(conn, "schluessel_v2", "1")
    return conn


def give(conn, n):
    stand.buche(conn, TODAY.isoformat(), n, "start", "Test")


# ---------- Fritte ----------


def test_new_fritte_is_curious_and_brings_a_present(conn):
    f = haustier.ansehen(conn, TODAY)
    assert f.laune == "neu" and f.guthaben == 0
    assert f.wartend == 1  # "Fritte zieht ein": the first Kassensturz is always possible
    assert f.wochen == [False] * haustier.WOCHEN
    assert f.zimmer == [] and f.hut is None
    assert [g.id for g in kassensturz.regal(conn)] == ["pfeffer"]


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


def test_older_work_is_the_first_kassensturz(conn):
    add_invoice(conn, "2026-0001", date(2026, 1, 5), paid=date(2026, 1, 20))
    add_invoice(conn, "2026-0002", date(2026, 2, 5))
    f = haustier.ansehen(conn, TODAY)
    assert f.guthaben == 0 and f.wartend == 2 + 1 + 1  # two invoices, a payment, the move-in
    assert f.laune == "doest"  # old work doesn't make today a care day
    k = kassensturz.laden(conn, kassensturz.durchfuehren(conn, TODAY, aufgeraeumt=False))
    # Rechnungen count double in autumn; three kinds of cards: the pepper adds +1 Würze.
    assert (k["koerner"], k["wuerze"]) == (6 + 6 + 2 + 3, 2.0) and k["summe"] == 34
    assert not any(c.get("zeitnah") or c.get("frisch") for c in k["karten"])


def test_a_long_history_is_a_welcome_gift_instead(conn):
    for n in range(stand.WILLKOMMEN_KARTEN):
        add_invoice(conn, f"2026-{n:04}", date(2026, 1, 5))
    f = haustier.ansehen(conn, TODAY)
    assert f.wartend == 0 and f.guthaben == 3 * stand.WILLKOMMEN_KARTEN
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
    # Autumn's theme: invoices bring double.
    assert (k["koerner"], k["wuerze"], k["summe"]) == (6 + 2 + 1, 1.0, 9)
    assert {c["art"] for c in k["karten"] if c.get("saison")} == {"rechnung"}
    assert stand.guthaben(conn) == 9
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
    add_invoice(conn, "2026-0001", TODAY, came=TODAY - timedelta(days=3))
    add_invoice(conn, "2026-0002", TODAY, came=TODAY - timedelta(days=40))
    add_invoice(conn, "2026-0003", TODAY - timedelta(days=20), paid=TODAY - timedelta(days=5),
                due=TODAY - timedelta(days=6))  # fmt: skip
    add_expense(conn, TODAY - timedelta(days=30), "Bahn", came=TODAY - timedelta(days=4))
    add_expense(conn, TODAY - timedelta(days=30), "Post", came=TODAY - timedelta(days=60))
    add_expense(conn, TODAY, "Copyshop")  # typed by hand, no file: no bonus
    stand.abgleichen(conn, TODAY)
    facts = {k.schluessel: k.fakten for k in kassensturz.offene_karten(conn)}
    assert facts["rechnung:2026-0001"]["frisch"] and not facts["rechnung:2026-0002"]["frisch"]
    assert not facts["bezahlt:2026-0003"]["puenktlich"]
    zeitnah = {key.split("|")[1]: f["zeitnah"] for key, f in facts.items() if "|" in key}
    assert zeitnah == {"bahn": True, "post": False, "copyshop": False}


def test_a_typed_date_never_pays_off(conn):
    """GoBD: the bonus must not depend on a date Fritz types in."""
    started(conn)
    came = TODAY - timedelta(days=30)
    add_invoice(conn, "2026-0001", TODAY, came=came, work=TODAY)  # "work date" moved: no matter
    add_expense(conn, TODAY, "Bahn", came=came)  # payment date set to today: no matter
    stand.abgleichen(conn, TODAY)
    assert not any(k.fakten.get("frisch") or k.fakten.get("zeitnah")
                   for k in kassensturz.offene_karten(conn))  # fmt: skip


def test_the_same_expense_never_brings_a_second_card(conn):
    started(conn)
    eid = add_expense(conn, TODAY, "Churn AG", "12.50")
    assert stand.abgleichen(conn, TODAY) == 1
    kassensturz.durchfuehren(conn, TODAY, aufgeraeumt=False)
    conn.execute("DELETE FROM expenses WHERE id = ?", (eid,))
    add_expense(conn, TODAY, "churn  ag", "12.5")  # entered again, a little differently
    assert stand.abgleichen(conn, TODAY) == 0
    # Two identical bus tickets are still two expenses.
    add_expense(conn, TODAY, "Busbetrieb", "2.90")
    add_expense(conn, TODAY, "Busbetrieb", "2.90")
    assert stand.abgleichen(conn, TODAY) == 2


def test_old_expense_cards_move_to_the_new_key(conn):
    started(conn)
    stand.setze(conn, "schluessel_v2", "")
    eid = add_expense(conn, TODAY)
    conn.execute(
        "INSERT INTO spiel_karten (schluessel, art, gesehen, kassensturz)"
        " VALUES (?, 'ausgabe', ?, 0)",
        (f"ausgabe:{eid}", TODAY.isoformat()),
    )
    assert stand.abgleichen(conn, TODAY) == 0  # the old card was moved, not doubled


def test_reminders_count_once_a_week_and_not_after_a_storno(conn):
    started(conn)
    add_invoice(conn, "2026-0007", TODAY - timedelta(days=40), due=TODAY - timedelta(days=10))
    for level in range(1, 5):
        add_reminder(conn, "2026-0007", TODAY, level)  # four clicks in a row
    add_reminder(conn, "2026-0007", TODAY + timedelta(days=8), 5)
    stand.abgleichen(conn, TODAY + timedelta(days=8))
    arten = [k.art for k in kassensturz.offene_karten(conn)]
    assert arten.count("erinnerung") == 2
    add_invoice(conn, "2026-0008", TODAY, cancels="2026-0007")
    assert [k.art for k in kassensturz.offene_karten(conn)] == []


def test_garlic_rewards_a_payment_after_a_reminder(conn):
    started(conn)
    add_invoice(conn, "2026-0001", TODAY - timedelta(days=40), paid=TODAY,
                due=TODAY - timedelta(days=10))  # fmt: skip
    add_invoice(conn, "2026-0002", TODAY - timedelta(days=40), paid=TODAY,
                due=TODAY - timedelta(days=10))  # fmt: skip
    add_reminder(conn, "2026-0001", TODAY - timedelta(days=5))
    stand.abgleichen(conn, TODAY)
    karten = [k.als_dict() for k in kassensturz.offene_karten(conn) if k.art == "bezahlt"]
    assert [k["nach_erinnerung"] for k in karten] == [True, False]
    result = kassensturz.berechnen(karten, [NACH_ID["knoblauch"]], {})
    assert result["koerner"] == 2 + 4 + 2


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
    for day in (0, 1, 7, 14):  # two rounds in the first week mature it only once
        stand.feierabend(conn, TODAY + timedelta(days=day))
        kassensturz.durchfuehren(conn, TODAY + timedelta(days=day), aufgeraeumt=False)
    assert [kassensturz.laden(conn, n)["wuerze"] for n in (1, 2, 3, 4)] == [1.0, 1.2, 1.2, 1.4]


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
    assert "Fritte zuhause" in home and "2 Karten warten" in home  # old work + moving in
    fritte = client.get("/fritte").text
    assert "Willkommen bei Fritte" in fritte and "Ersten Kassensturz machen" in fritte
    assert "Vorschau:" in fritte and "Herbst 2026" in fritte
    page = post(client, "/fritte/kassensturz")
    assert "Kassensturz Nr. 1" in page.text and 'id="ks-daten"' in page.text
    assert stand.guthaben(conn) == 6 + 3  # an autumn invoice counts double, plus moving in
    assert "Willkommen bei Fritte" not in client.get("/fritte").text

    # New work: a quiet note says Fritte got a card for it.
    add_invoice(conn, "2026-0002", TODAY, came=TODAY - timedelta(days=1))
    assert "Fritte hat eine neue Karte" in client.get("/invoices").text
    assert "Fritte hat eine neue Karte" not in client.get("/invoices").text  # only once
    post(client, "/feierabend")
    assert "2 Karten warten" in client.get("/").text
    feierabend = client.get("/feierabend").text
    assert '<button class="primary">FRITS beenden</button>' in feierabend
    assert "Kassensturz machen" not in feierabend  # the shutdown starts nothing new
    post(client, "/fritte/kassensturz")
    assert stand.guthaben(conn) == 9 + 6 + 1

    laden = client.get("/fritte/laden").text
    assert "Gewürzregal" in laden and "Markttag" in laden and "Frittes Kassenbuch" in laden
    assert "Beim letzten Kassensturz" in laden  # what a spice would have brought
    g = min(markt(conn, TODAY), key=lambda g: g.preis)
    assert g.id != "pfeffer"  # what is on the shelf is not offered again
    page = post(client, "/fritte/gewuerz/kaufen", gewuerz=g.id)
    assert f"{g.name} steht jetzt im Gewürzregal" in page.text
    assert stand.guthaben(conn) == 16 - g.preis
    page = post(client, "/fritte/gewuerz/kaufen", gewuerz="safran")
    assert "gibt es diese Woche nicht" in page.text  # legendary: not at this stage's market
    page = post(client, "/fritte/gewuerz/verkaufen", gewuerz=g.id)
    assert f"verkauft für {max(1, g.preis // 2)}" in page.text
    give(conn, 10)
    page = post(client, "/fritte/kaufen", ding="teetasse")
    assert "Die Teetasse hat jetzt einen schönen Platz" in page.text
    assert client.get("/fritte/kassensturz/99").status_code == 404
    assert "Chronik" in client.get("/fritte").text


def test_the_shelf_has_limited_places(client, ws):  # noqa: F811
    conn = store.connect(ws.db)
    client.get("/")  # Fritte moves in with a jar of pepper
    give(conn, 100)
    offer = markt(conn, TODAY)
    commons = ("meersalz", "muskat", "schnittlauch", "paprika", "rosmarin")
    others = [i for i in commons if NACH_ID[i] not in offer]
    for gid in others[:2]:
        stand.nimm(conn, gid, "gewuerz", TODAY.isoformat(), 5)
    page = post(client, "/fritte/gewuerz/kaufen", gewuerz=offer[0].id)
    assert "Das Regal ist voll" in page.text
    post(client, "/fritte/gewuerz/links", gewuerz=others[1])
    assert [g.id for g in kassensturz.regal(conn)] == ["pfeffer", others[1], others[0]]


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
    assert ausbau.naechste(conn).name == "Frittenbude"


def test_album_stickers_come_once(conn):
    from frits.spiel import album

    started(conn)
    stand.feierabend(conn, TODAY)
    k = kassensturz.laden(conn, kassensturz.durchfuehren(conn, TODAY, aufgeraeumt=False))
    assert [e["name"] for e in k["erfolge"]] == ["Der erste Kassensturz"]
    assert album.neue(conn, TODAY) == []
    seiten = album.seiten(conn, TODAY)
    assert sum(1 for s in seiten if s["seit"]) == 1 and len(seiten) == len(album.ERFOLGE)


def test_album_page(client, ws):  # noqa: F811
    conn = store.connect(ws.db)
    client.get("/")
    stand.setze(conn, "ausbau", "3")
    stand.nimm(conn, "trueffelsalz", "gewuerz", TODAY.isoformat(), 22)
    page = client.get("/fritte/album")
    assert "Frittes Album" in page.text and "3 von" in page.text  # Stube, Werkstatt, Legendär


# ---------- market, prices, seasons, new places ----------


def test_market_skips_owned_spices_and_grows_with_the_house(conn):
    from frits.spiel import ausbau

    started(conn)
    for gid in ("meersalz", "muskat", "schnittlauch"):
        stand.nimm(conn, gid, "gewuerz", TODAY.isoformat(), 5)
    offer = [g.id for g in markt(conn, TODAY)]
    assert len(offer) == 3 and not {"meersalz", "muskat", "schnittlauch"} & set(offer)
    assert all(NACH_ID[i].seltenheit == "gewöhnlich" for i in offer)
    stand.setze(conn, "ausbau", "2")  # the Stube is finished mid-week
    grown = [g.id for g in markt(conn, TODAY)]
    assert grown[:3] == offer and len(grown) == 4  # nothing reshuffled, one rare spice joins
    assert NACH_ID[grown[3]].seltenheit == "selten"
    assert [g.id for g in markt(conn, TODAY + timedelta(days=2))] == grown
    assert ausbau.stufe(conn).name == "Stube"


def test_prices_grow_with_the_house_and_selling_gives_half_back(client, ws):  # noqa: F811
    from frits.web.haustier import preis, wurf_preis

    conn = store.connect(ws.db)
    client.get("/")
    stand.setze(conn, "ausbau", "3")
    g = markt(conn, TODAY)[0]
    assert preis(conn, g) == g.preis * 2 and wurf_preis(conn, 0) == 6
    give(conn, 200)
    post(client, "/fritte/gewuerz/kaufen", gewuerz=g.id)
    page = post(client, "/fritte/gewuerz/verkaufen", gewuerz=g.id)
    assert f"verkauft für {g.preis}" in page.text  # half of the doubled price


def test_spice_preview_counts_the_waiting_cards(conn):
    from frits.web.haustier import wirkung

    started(conn)
    add_invoice(conn, "2026-0001", TODAY)
    add_invoice(conn, "2026-0002", TODAY)
    stand.abgleichen(conn, TODAY)
    assert wirkung(conn, TODAY, NACH_ID["meersalz"], False) == (4, 2, "warten")
    assert wirkung(conn, TODAY, NACH_ID["muskat"], False) == (0, 2, "warten")


def test_a_season_goal_brings_a_cup(conn):
    from frits.spiel import saison

    started(conn)
    s = saison.jetzt(conn, TODAY)
    assert (s.name, s.ziel, s.thema) == ("Herbst 2026", 120, "rechnung")
    stand.setze(conn, "ausbau", "3")  # building out mid-season keeps the goal
    assert saison.jetzt(conn, TODAY).ziel == 120
    for n in range(20):
        add_invoice(conn, f"2026-{n:04}", TODAY)
    stand.abgleichen(conn, TODAY)
    k = kassensturz.laden(conn, kassensturz.durchfuehren(conn, TODAY, aufgeraeumt=False))
    assert k["summe"] == 20 * 6 and k["saison"]["name"] == "Herbst 2026"
    assert stand.guthaben(conn) == 120 + 30 and saison.pokale(conn) == ["Herbst 2026"]
    assert haustier.ansehen(conn, TODAY).pokale == ["Herbst 2026"]
    stand.feierabend(conn, TODAY)
    k = kassensturz.laden(conn, kassensturz.durchfuehren(conn, TODAY, aufgeraeumt=False))
    assert "saison" not in k  # only once
    winter = saison.jetzt(conn, date(2027, 1, 4))
    assert winter.name == "Winter 2027" and winter.erreicht == 0 and winter.thema == "ausgabe"


def test_the_steuer_quest_brings_cards(conn):
    started(conn)
    conn.execute(
        "CREATE TABLE steuer_stuecke (id INTEGER PRIMARY KEY, jahr INTEGER, gebiet TEXT,"
        " key TEXT, name TEXT, status TEXT)"
    )
    conn.execute("CREATE TABLE steuer_chronik (id INTEGER PRIMARY KEY, jahr INTEGER, art TEXT)")
    conn.execute(
        "INSERT INTO steuer_stuecke (jahr, gebiet, key, name, status) VALUES"
        " (2025, 'a', 'lohn', 'Lohnsteuerbescheinigung', 'gesammelt'),"
        " (2025, 'a', 'miete', 'Mietvertrag', 'gibts_nicht')"
    )
    conn.execute("INSERT INTO steuer_chronik (jahr, art) VALUES (2025, 'sieg'), (2025, 'sieg')")
    stand.abgleichen(conn, TODAY)
    karten = {k.art: k for k in kassensturz.offene_karten(conn)}
    assert set(karten) == {"steuer", "steuerjahr"} and karten["steuerjahr"].koerner == 25
    conn.execute("UPDATE steuer_stuecke SET status = 'offen'")  # taken back: the card goes
    assert [k.art for k in kassensturz.offene_karten(conn)] == ["steuerjahr"]


def test_a_new_place_after_the_frittenbude(conn):
    from frits.spiel import album, ausbau

    started(conn)
    give(conn, 5000)
    with pytest.raises(stand.SpielFehler, match="Erst wenn"):
        ausbau.umziehen(conn, TODAY)
    stand.setze(conn, "ausbau", "5")
    assert kassensturz.plaetze(conn) == 7 and ausbau.grundwuerze(conn) == 2.0
    assert ausbau.umziehen(conn, TODAY).name == "Am Hafen"
    assert stand.guthaben(conn) == 5000 - 1200
    assert ausbau.stufe(conn).name == "Zimmer" and ausbau.ruf(conn) == 1
    # She keeps the big shelf and the market; the Ruf adds Würze; building costs more.
    assert kassensturz.plaetze(conn) == 7 and ausbau.grundwuerze(conn) == 1.5
    assert "legendär" in ausbau.seltenheiten(conn)
    assert ausbau.preis(conn, ausbau.naechste(conn)) == 120
    assert ausbau.umzug_preis(conn) == 2400
    assert {e.id for e in album.neue(conn, TODAY)} >= {"umzug", "bude"}


def test_the_new_parts_show_on_the_pages(client, ws):  # noqa: F811
    conn = store.connect(ws.db)
    client.get("/")
    stand.setze(conn, "ausbau", "5")
    give(conn, 1500)
    page = client.get("/fritte").text
    assert "Neue Bude für 1200" in page and "eine neue Bude am Hafen" in page
    page = post(client, "/fritte/umziehen")
    assert "Fritte eröffnet eine neue Bude: Am Hafen" in page.text
    assert "ort-2" in page.text and "Ruf 1" in page.text

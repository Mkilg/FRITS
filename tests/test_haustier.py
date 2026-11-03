"""Fritte and the everyday game: gentle moods, cards from real paperwork, the Kassensturz."""

import json
from datetime import date, timedelta

import pytest
from test_web import TODAY, client, mails, post, provider, ws  # noqa: F401

from frits import haustier, store
from frits.settings import Settings
from frits.spiel import kassensturz, stand
from frits.spiel.gewuerze import NACH_ID, REZEPT_NACH_ID
from frits.web.haustier import markt


def add_job(conn, filename, received):
    return conn.execute(
        "INSERT INTO jobs (sha256, filename, received, status) VALUES (?, ?, ?, 'drafted')",
        (filename + received.isoformat(), filename, received.isoformat() + "T09:00:00"),
    ).lastrowid


def add_invoice(
    conn, number, issue, paid=None, due=None, came=None, cancels=None, work=None, draft=None,
    gross="100", customer="Erika Meier",
):  # fmt: skip
    """came: the day the note about the work arrived in the inbox. work: the date Fritz
    typed on the line (the game must not care about it)."""
    draft_id = draft
    if came or work:
        lines = [{"description": "Arbeit", "date": (work or issue).strftime("%d.%m.%Y")}]
        job = add_job(conn, f"notiz-{number}.txt", came) if came else None
        draft_id = conn.execute(
            "INSERT INTO drafts (job_id, created, data, status) VALUES (?, ?, ?, 'approved')",
            (job, issue.isoformat(), json.dumps({"lines": lines})),
        ).lastrowid
    conn.execute(
        "INSERT INTO invoices (number, customer, issue_date, paid_date, due_date, net, vat, gross,"
        " draft_id, cancels) VALUES (?, ?, ?, ?, ?, ?, '0', ?, ?, ?)",
        (
            number,
            customer,
            issue.isoformat(),
            paid.isoformat() if paid else None,
            due.isoformat() if due else None,
            gross,
            gross,
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
    stand.vorbereiten(conn)  # not the first meeting (then she is always awake and curious)
    conn.execute("INSERT INTO spiel_kassenstuerze (tag, summe, ergebnis) VALUES ('', 0, '{}')")
    f = haustier.ansehen(conn, TODAY)
    assert (f.laune, f.bild) == (laune, bild)
    assert not any(w in f.satz.lower() for w in ("traurig", "vermisst", "endlich", "schon lange"))


def test_older_work_is_the_first_kassensturz(conn):
    add_invoice(conn, "2026-0001", date(2026, 1, 5), paid=date(2026, 1, 20))
    add_invoice(conn, "2026-0002", date(2026, 2, 5))
    f = haustier.ansehen(conn, TODAY)
    assert f.guthaben == 0 and f.wartend == 2 + 1 + 1  # two invoices, a payment, the move-in
    assert f.laune == "neu"  # awake and curious at the first meeting, however old the work
    k = kassensturz.laden(conn, kassensturz.durchfuehren(conn, TODAY, aufgeraeumt=False))
    # Old invoices don't get the autumn bonus. Moving in brings +1 Würze, and with three
    # kinds of cards the pepper adds +1 more.
    assert (k["koerner"], k["wuerze"]) == (3 + 3 + 2 + 3, 3.0) and k["summe"] == 33
    assert not any(c.get("zeitnah") or c.get("frisch") for c in k["karten"])


def test_a_long_history_is_a_mixed_table_and_a_gift(conn):
    for n in range(30):
        add_invoice(conn, f"2026-{n:04}", date(2026, 1, 5), paid=date(2026, 1, 9))
    f = haustier.ansehen(conn, TODAY)
    assert f.wartend == stand.WILLKOMMEN_KARTEN + 1  # plus Fritte moving in
    arten = [k.art for k in kassensturz.offene_karten(conn)]
    assert arten.count("rechnung") == arten.count("bezahlt") == stand.WILLKOMMEN_KARTEN // 2
    assert f.guthaben == stand.WILLKOMMEN_GESCHENK  # 24 × 3 + 24 × 2, capped
    assert stand.kassenbuch(conn)[0]["text"] == "Willkommensgeschenk für 48 ältere Vorgänge"


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
    conn.execute(
        "UPDATE expenses SET struck = '2026-11-02T10:00:00' WHERE id = ?", (eid,)
    )  # deleted in the app
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
    assert "puenktlich" not in facts["bezahlt:2026-0003"]  # a typed date, so no mark
    zeitnah = {
        k.detail.split(" · ")[0]: k.fakten["zeitnah"]
        for k in kassensturz.offene_karten(conn)
        if k.art == "ausgabe"
    }
    assert zeitnah == {"Bahn": True, "Post": False, "Copyshop": False}


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
    conn.execute(
        "UPDATE expenses SET struck = '2026-11-02T10:00:00' WHERE id = ?", (eid,)
    )  # deleted in the app
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


def test_a_refund_is_not_a_payment(conn):
    """A Storno whose money went back to the customer ("erstattet") carries a paid_date.
    It is neither an invoice nor a payment card, and the original drops out too."""
    started(conn)
    add_invoice(conn, "2026-0001", TODAY - timedelta(days=9), paid=TODAY - timedelta(days=5))
    add_invoice(conn, "2026-0002", TODAY, paid=TODAY, cancels="2026-0001")
    stand.abgleichen(conn, TODAY)
    assert kassensturz.offene_karten(conn) == []


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
    assert "etwa <strong>+12 Salzkörner</strong>" in home  # what they'd bring, right away
    assert "Kassensturz machen" not in home  # "Heute" is for work: no game button there
    fritte = client.get("/fritte").text
    assert "Willkommen bei Fritte" in fritte and "Ersten Kassensturz machen" in fritte
    assert "Wie soll Fritte feiern?" in fritte  # never chosen: Fritte asks once
    assert "Vorschau:" in fritte and "Herbst 2026" in fritte
    page = post(client, "/fritte/kassensturz")
    assert "Kassensturz Nr. 1" in page.text and 'id="ks-daten"' in page.text
    # Lena: old paperwork does not fill the chest, so the first round says it is a present
    assert "Dein erster Kassensturz ist ein Geschenk" in page.text
    # An old invoice plus moving in, and moving in brings +1 Würze.
    assert stand.guthaben(conn) == (3 + 3) * 2
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
    assert stand.guthaben(conn) == 12 + 6 + 1

    laden = client.get("/fritte/laden").text
    assert "Gewürzregal" in laden and "Markttag" in laden
    # Lena (phone): ten screens of shop. Shelves fold; without the quest only the market is open.
    assert '<details class="card laden-fach" id="markt" open>' in laden
    assert '<details class="card laden-fach" id="regal">' in laden
    assert "Beim letzten Kassensturz" in laden  # what a spice would have brought
    g = min(markt(conn, TODAY), key=lambda g: g.preis)
    assert g.id != "pfeffer"  # what is on the shelf is not offered again
    page = post(client, "/fritte/gewuerz/kaufen", gewuerz=g.id)
    assert f"{g.name} steht jetzt im Gewürzregal" in page.text
    assert stand.guthaben(conn) == 19 - g.preis
    page = post(client, "/fritte/gewuerz/kaufen", gewuerz="safran")
    assert "gibt es diese Woche nicht" in page.text  # legendary: not at this stage's market
    page = post(client, "/fritte/gewuerz/verkaufen", gewuerz=g.id)
    assert f"verkauft für {max(1, g.preis // 2)}" in page.text
    give(conn, 10)
    page = post(client, "/fritte/kaufen", ding="teetasse")
    assert "Die Teetasse hat jetzt einen schönen Platz" in page.text
    assert client.get("/fritte/kassensturz/99").status_code == 404
    seite = client.get("/fritte").text  # the Kassenbuch is the chronicle too
    assert "Frittes Kassenbuch" in seite and 'href="/fritte/kassensturz/1"' in seite


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
    with pytest.raises(stand.SpielFehler, match="fehlen noch 250"):
        ausbau.ausbauen(conn, TODAY)
    give(conn, 270)
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
    page = client.get("/fritte/album")  # old links lead to the one showcase
    assert str(page.url).endswith("/sammlung")
    assert "Alltag mit Fritte" in page.text and "3 von" in page.text  # Stube, Werkstatt, Legendär


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
    s = saison.jetzt(conn, TODAY)  # Fritte moved in on 2 November: 9 weeks are left
    assert (s.name, s.wochen, s.ziel, s.bonus, s.thema) == ("Herbst 2026", 9, 7, 30, "rechnung")
    stand.setze(conn, "ausbau", "3")  # building out mid-season keeps goal and bonus
    assert (saison.jetzt(conn, TODAY).ziel, saison.jetzt(conn, TODAY).bonus) == (7, 30)
    for woche in range(7):  # one invoice in each of seven weeks
        tag = TODAY + timedelta(weeks=woche)
        add_invoice(conn, f"2026-{woche:04}", tag)
        stand.abgleichen(conn, tag)
        if woche == 5:
            assert saison.jetzt(conn, tag).erreicht == 6
    tag = TODAY + timedelta(weeks=6)
    k = kassensturz.laden(conn, kassensturz.durchfuehren(conn, tag, aufgeraeumt=False))
    assert k["saison"]["name"] == "Herbst 2026" and k["saison"]["bonus"] == 30
    assert stand.guthaben(conn) == k["summe"] + 30 and saison.pokale(conn) == ["Herbst 2026"]
    assert haustier.ansehen(conn, tag).pokale == ["Herbst 2026"]
    stand.feierabend(conn, tag)
    k = kassensturz.laden(conn, kassensturz.durchfuehren(conn, tag, aufgeraeumt=False))
    assert "saison" not in k  # only once
    winter = saison.jetzt(conn, date(2027, 1, 4))
    assert winter.name == "Winter 2027" and winter.erreicht == 0 and winter.thema == "ausgabe"
    assert (winter.wochen, winter.ziel) == (13, 10)  # a whole season


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
    assert set(karten) == {"steuer", "steuerjahr"} and karten["steuerjahr"].koerner == 5
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
    assert kassensturz.plaetze(conn) == 7 and ausbau.grundwuerze(conn) == 2.5  # never down
    stand.setze(conn, "ausbau", "3")
    assert ausbau.grundwuerze(conn) == 2.7  # each stage in the new place adds +0.1
    stand.setze(conn, "ausbau", "1")
    assert "legendär" in ausbau.seltenheiten(conn)
    assert ausbau.preis(conn, ausbau.naechste(conn)) == 375  # 250 × 1.5 with Ruf 1
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


# ---------- round 2 of the playtest: corrections never pay more ----------


def kassensturz_jetzt(conn, day=TODAY):
    stand.abgleichen(conn, day)
    return kassensturz.laden(conn, kassensturz.durchfuehren(conn, day, aufgeraeumt=False))


def test_a_cancelled_and_rewritten_invoice_is_a_correction(conn):
    started(conn)
    add_invoice(conn, "2026-0005", TODAY - timedelta(days=3))
    kassensturz_jetzt(conn)
    add_invoice(conn, "2026-0006", TODAY, cancels="2026-0005")  # the Storno
    add_invoice(conn, "2026-0007", TODAY)  # written again, correctly
    stand.abgleichen(conn, TODAY)
    [karte] = kassensturz.offene_karten(conn)
    assert karte.art == "korrektur" and karte.koerner == 0 and "Korrektur" in karte.titel
    # A second, unrelated invoice for the same customer is a normal card again.
    add_invoice(conn, "2026-0008", TODAY)
    stand.abgleichen(conn, TODAY)
    assert sorted(k.art for k in kassensturz.offene_karten(conn)) == ["korrektur", "rechnung"]


def test_a_corrected_expense_or_the_same_receipt_twice_is_one_card(conn):
    started(conn)
    eid = add_expense(conn, TODAY, "Copyshop", "12.34")
    kassensturz_jetzt(conn)
    conn.execute("UPDATE expenses SET struck = '2026-11-02T10:00:00' WHERE id = ?", (eid,))
    add_expense(conn, TODAY, "Copyshop", "12.43")  # the amount fixed
    assert stand.abgleichen(conn, TODAY) == 0
    came = TODAY - timedelta(days=1)
    add_expense(conn, TODAY - timedelta(days=5), "Bahn", came=came)
    conn.execute(  # the same receipt file entered a second time
        "INSERT INTO expenses (date, vendor, description, gross, category, source_file)"
        " SELECT date, vendor, description, gross, category, source_file FROM expenses"
        " WHERE vendor = 'Bahn'"
    )
    assert stand.abgleichen(conn, TODAY) == 1


def test_a_quote_counts_once_it_became_an_invoice(conn):
    started(conn)
    conn.execute(
        "INSERT INTO quotes (number, created, valid_until, data, status) VALUES"
        " ('AN-1', ?, ?, '{}', 'open'), ('AN-2', ?, ?, '{}', 'declined')",
        (TODAY.isoformat(), TODAY.isoformat()) * 2,
    )
    assert stand.abgleichen(conn, TODAY) == 0
    # "Angenommen" clicked, the invoice draft thrown away: no card.
    draft = conn.execute(
        "INSERT INTO drafts (created, data, status) VALUES (?, '{}', 'discarded')",
        (TODAY.isoformat(),),
    ).lastrowid
    conn.execute(
        "UPDATE quotes SET status = 'accepted', draft_id = ? WHERE number = 'AN-1'", (draft,)
    )
    assert stand.abgleichen(conn, TODAY) == 0
    # It became a real invoice: now it is a card (plus the invoice's own).
    conn.execute("UPDATE drafts SET status = 'approved' WHERE id = ?", (draft,))
    add_invoice(conn, "2026-0001", TODAY, draft=draft)
    assert stand.abgleichen(conn, TODAY) == 2
    # The invoice cancelled before the Kassensturz: the quote's card leaves the table.
    add_invoice(conn, "2026-0002", TODAY, cancels="2026-0001")
    stand.abgleichen(conn, TODAY)
    assert "angebot" not in {k.art for k in kassensturz.offene_karten(conn)}


def test_reminders_count_only_when_really_overdue(conn):
    started(conn)
    # Payment term of minus 30 days: still no card before two weeks after the invoice.
    add_invoice(conn, "2026-0001", TODAY - timedelta(days=2), due=TODAY - timedelta(days=32))
    add_reminder(conn, "2026-0001", TODAY)
    add_invoice(conn, "2026-0002", TODAY - timedelta(days=5), due=TODAY + timedelta(days=9))
    add_reminder(conn, "2026-0002", TODAY)  # not overdue yet
    assert stand.abgleichen(conn, TODAY) == 2  # just the two invoice cards


def test_curry_rewards_payments_not_early_clicks():
    cards = [card("bezahlt", puenktlich=True), card("bezahlt", puenktlich=False)]
    assert kassensturz.berechnen(cards, [NACH_ID["curry"]], {})["wuerze"] == 2.0


def test_malzessig_is_ripe_after_ten_weeks():
    cards = [card("feierabend")]
    result = kassensturz.berechnen(cards, [NACH_ID["malzessig"]], {"reife": {"malzessig": 30}})
    assert result["wuerze"] == 3.0


def test_a_feierabend_click_alone_is_no_paperwork(conn):
    started(conn)
    stand.feierabend(conn, TODAY)
    assert stand.pflegetage(conn, TODAY) == set()


def test_cards_keep_the_theme_of_the_season_they_came_in(conn):
    started(conn)
    add_invoice(conn, "2026-0001", date(2026, 12, 30))
    stand.abgleichen(conn, date(2026, 12, 30))  # autumn: invoices count double
    k = kassensturz_jetzt(conn, date(2027, 1, 4))  # scored in winter
    assert k["koerner"] == 6


def test_the_season_goal_counts_weeks_not_grains(conn):
    """Fair for a freelancer: a month off is fine, saving cards up or spices change
    nothing, and only days FRITS noticed itself count, never a typed date."""
    from frits.spiel import saison

    started(conn)
    winter = saison.jetzt(conn, date(2027, 1, 4))
    assert winter.ziel == 10
    montage = [date(2027, 1, 4) + timedelta(weeks=w) for w in range(13)]
    for n, tag in enumerate(montage[4:]):  # January off, then paperwork every week
        add_invoice(conn, f"2027-{n:04}", date(2026, 6, 1))  # an old date typed in
        stand.abgleichen(conn, tag)
    assert saison.jetzt(conn, montage[-1]).erreicht == 9  # one short of 10, no grains needed
    add_expense(conn, date(2027, 1, 20))
    stand.abgleichen(conn, montage[-1])  # a second card in the same week adds no week
    assert saison.jetzt(conn, montage[-1]).erreicht == 9
    assert stand.feierabend(conn, montage[1])  # a shutdown alone in January: no week
    assert saison.jetzt(conn, montage[-1]).erreicht == 9


def test_a_season_reached_in_its_last_days_counts_after_new_year(conn):
    from frits.spiel import saison

    started(conn)
    saison.jetzt(conn, TODAY)
    for woche in range(7):
        tag = TODAY + timedelta(weeks=woche)
        add_invoice(conn, f"2026-{woche:04}", tag)
        stand.abgleichen(conn, tag)
    k = kassensturz_jetzt(conn, date(2027, 1, 5))  # scored only after New Year
    assert k["saison"]["name"] == "Herbst 2026"


def test_the_welcome_round_always_shows_the_wuerze(conn):
    k = kassensturz_jetzt(conn)  # a brand-new FRITS, nothing done yet
    assert (k["koerner"], k["wuerze"], k["summe"]) == (3, 2.0, 6)
    from frits.spiel import saison

    assert saison.jetzt(conn, TODAY).erreicht == 0  # moving in is no paperwork week


def test_no_card_note_inside_the_steuer_quest(client, ws):  # noqa: F811
    """Nearly every step there is a card; the note would cover the basket."""
    conn = store.connect(ws.db)
    client.get("/")  # Fritte moves in
    post(client, "/fritte/kassensturz")
    add_invoice(conn, "2026-0003", TODAY)
    steuer = client.get("/steuer", follow_redirects=True)
    assert steuer.status_code == 200 and "Fritte hat" not in steuer.text
    assert "Fritte hat" not in client.get("/invoices").text  # counted, not shown later
    assert stand.wartende(conn) == 1  # still a card, just no note


# ---------- Gutschriften ----------


def add_credit(conn, number, issue=TODAY, paid=None, customer="sender", gross="595.00"):
    from decimal import Decimal

    from frits import gutschriften

    gutschriften.prepare(conn)
    return gutschriften.add(
        conn, customer_id=customer, customer="Sender GmbH", their_number=number,
        issue_date=issue, gross=Decimal(gross), vat_rate=Decimal(19), paid_date=paid,
    )  # fmt: skip


def strike_credit(conn, cid):
    from frits import gutschriften

    assert gutschriften.strike(conn, cid)


def test_a_gutschrift_counts_like_an_invoice_and_its_payment(conn):
    started(conn)
    add_credit(conn, "GS-2026-114", paid=TODAY)
    add_credit(conn, "GS-2026-115")
    stand.abgleichen(conn, TODAY)
    karten = {k.schluessel: k for k in kassensturz.offene_karten(conn)}
    assert sorted(k.art for k in karten.values()) == ["bezahlt", "rechnung", "rechnung"]
    gs = karten["rechnung:gs:sender|gs-2026-114"]
    assert gs.titel == "Gutschrift GS-2026-114" and gs.koerner == 3
    assert not gs.fakten["frisch"]  # no note arrived for it
    assert karten["bezahlt:gs:sender|gs-2026-114"].fakten == {
        "nach_erinnerung": False,
    }  # fmt: skip


def test_a_struck_gutschrift_never_earns_twice(conn):
    started(conn)
    cid = add_credit(conn, "GS-1", paid=TODAY)
    kassensturz_jetzt(conn)
    strike_credit(conn, cid)
    add_credit(conn, "gs-1 ", paid=TODAY, gross="559.00")  # the same one, amount fixed
    assert stand.abgleichen(conn, TODAY) == 0
    # Entered again under a new number (a typo fixed): a correction without Körner.
    strike_credit(conn, conn.execute("SELECT MAX(id) FROM credits").fetchone()[0])
    add_credit(conn, "GS-2", paid=TODAY)
    stand.abgleichen(conn, TODAY)
    karten = kassensturz.offene_karten(conn)
    assert [k.art for k in karten] == ["korrektur", "korrektur"]
    assert all(k.koerner == 0 and "Gutschrift" in k.detail for k in karten)
    # The next real one for the same customer is a normal card again.
    add_credit(conn, "GS-3")
    stand.abgleichen(conn, TODAY)
    assert sorted(k.art for k in kassensturz.offene_karten(conn)) == [
        "korrektur", "korrektur", "rechnung",
    ]  # fmt: skip


def test_a_gutschrift_struck_before_scoring_waits_again_once_fixed(conn):
    started(conn)
    cid = add_credit(conn, "GS-7", gross="100.00")
    assert stand.abgleichen(conn, TODAY) == 1
    strike_credit(conn, cid)
    assert kassensturz.offene_karten(conn) == []  # struck: off the table
    add_credit(conn, "GS-7", gross="110.00")  # entered again, right this time
    assert stand.abgleichen(conn, TODAY) == 1
    [karte] = kassensturz.offene_karten(conn)
    assert karte.art == "rechnung" and "110,00" in karte.detail


# ---------- round 4: playtest 3 ----------


def test_a_correction_does_not_depend_on_the_order(conn):
    """Write the right invoice first and cancel the wrong one afterwards: still a
    correction. And it may go to another customer (the wrong address was the mistake)."""
    started(conn)
    add_invoice(conn, "2026-0008", TODAY - timedelta(days=2), gross="450", customer="BR")
    kassensturz_jetzt(conn, TODAY - timedelta(days=2))
    add_invoice(conn, "2026-0009", TODAY, gross="450", customer="DLF")  # the right one
    assert stand.abgleichen(conn, TODAY) == 1
    add_invoice(conn, "2026-0010", TODAY, cancels="2026-0008", gross="-450", customer="BR")
    assert stand.abgleichen(conn, TODAY) == 0  # a correction alone needs no Kassensturz
    [karte] = kassensturz.offene_karten(conn)
    assert karte.art == "korrektur" and karte.detail.startswith("ersetzt eine stornierte")
    with pytest.raises(stand.SpielFehler):
        kassensturz.durchfuehren(conn, TODAY, aufgeraeumt=False)


def test_new_work_for_the_same_customer_is_no_correction(conn):
    started(conn)
    add_invoice(conn, "2026-0005", TODAY - timedelta(days=3), gross="450", customer="BR")
    kassensturz_jetzt(conn, TODAY - timedelta(days=3))
    add_invoice(conn, "2026-0006", TODAY, cancels="2026-0005", gross="-450", customer="BR")
    add_invoice(conn, "2026-0007", TODAY, gross="1200", customer="BR")  # other work
    stand.abgleichen(conn, TODAY)
    assert [k.art for k in kassensturz.offene_karten(conn)] == ["rechnung"]


def test_a_cancelled_invoice_already_replaced_and_scored_waits_for_the_next(conn):
    """Right one written and scored, only then the wrong one cancelled: the next similar
    invoice within 30 days evens it out."""
    started(conn)
    add_invoice(conn, "2026-0001", TODAY - timedelta(days=4), gross="300")
    kassensturz_jetzt(conn, TODAY - timedelta(days=4))
    add_invoice(conn, "2026-0002", TODAY - timedelta(days=2), gross="310")
    kassensturz_jetzt(conn, TODAY - timedelta(days=2))
    add_invoice(conn, "2026-0003", TODAY, cancels="2026-0001", gross="-300")
    stand.abgleichen(conn, TODAY)
    add_invoice(conn, "2026-0004", TODAY + timedelta(days=5), gross="290")
    stand.abgleichen(conn, TODAY + timedelta(days=5))
    assert [k.art for k in kassensturz.offene_karten(conn)] == ["korrektur"]
    add_invoice(conn, "2026-0005", TODAY + timedelta(days=6), gross="300")
    stand.abgleichen(conn, TODAY + timedelta(days=6))
    assert sorted(k.art for k in kassensturz.offene_karten(conn)) == ["korrektur", "rechnung"]


def test_a_struck_expense_typed_again_differently_is_a_correction(conn):
    started(conn)
    eid = add_expense(conn, TODAY, "Baumarkt Schluz", "12.34")
    kassensturz_jetzt(conn)
    conn.execute("UPDATE expenses SET struck = '2026-11-02T10:00:00' WHERE id = ?", (eid,))
    add_expense(conn, TODAY, "Baumarkt Schulz", "12.34")  # the shop's name fixed
    assert stand.abgleichen(conn, TODAY) == 0
    [karte] = kassensturz.offene_karten(conn)
    assert karte.art == "korrektur" and "gestrichene Ausgabe" in karte.detail
    add_expense(conn, TODAY, "Bahn", "23.80")  # the next real one is a card again
    assert stand.abgleichen(conn, TODAY) == 1


def test_garlic_needs_a_reminder_before_fritz_saw_the_payment(conn):
    """Only FRITS' own days count: reminding and then clicking "Bezahlt" right away (with
    whatever date) gives nothing, a real reminder days before does."""
    started(conn)
    for n in ("2026-0001", "2026-0002"):
        add_invoice(conn, n, TODAY - timedelta(days=40), paid=TODAY - timedelta(days=9),
                    due=TODAY - timedelta(days=20))  # fmt: skip
    add_reminder(conn, "2026-0001", TODAY - timedelta(days=5))
    add_reminder(conn, "2026-0002", TODAY - timedelta(days=1))  # payment was already there
    stand.abgleichen(conn, TODAY)
    fakten = {k.detail[:9]: k.fakten for k in kassensturz.offene_karten(conn) if k.art == "bezahlt"}
    assert fakten["2026-0001"]["nach_erinnerung"] and not fakten["2026-0002"]["nach_erinnerung"]


def test_saving_cards_up_brings_no_more_wuerze():
    chili, curry = NACH_ID["chili"], NACH_ID["curry"]
    woche = [card("rechnung"), card("bezahlt"), card("bezahlt")] + [card("ausgabe")] * 7
    quartal = woche * 13
    w1 = kassensturz.berechnen(woche, [chili, curry], {})["wuerze"]
    w13 = kassensturz.berechnen(quartal, [chili, curry], {})["wuerze"]
    assert w1 == 1 + 2 + 1 and w13 == 1 + 2 + 2  # capped at +2 each


def test_safran_counts_a_draft_thrown_away_this_week(conn):
    started(conn)
    conn.execute("INSERT INTO drafts (created, data, status) VALUES (?, '{}', 'draft')",
                 (TODAY.isoformat(),))  # fmt: skip
    stand.abgleichen(conn, TODAY)
    assert not stand.kuerzlich_verworfen(conn, TODAY)
    conn.execute("UPDATE drafts SET status = 'discarded'")
    stand.abgleichen(conn, TODAY)
    assert stand.kuerzlich_verworfen(conn, TODAY + timedelta(days=6))
    assert not stand.kuerzlich_verworfen(conn, TODAY + timedelta(days=7))


def test_five_shutdown_cards_a_week_at_most(conn):
    started(conn)
    neu = [stand.feierabend(conn, TODAY + timedelta(days=n)) for n in range(7)]
    assert neu == [True] * 5 + [False, False]
    assert stand.feierabend(conn, TODAY + timedelta(days=7))  # a new week


def test_prices_do_not_drop_after_moving(conn):
    from frits.spiel import ausbau
    from frits.web.haustier import preis

    started(conn)
    stand.setze(conn, "ausbau", "5")
    vorher = preis(conn, NACH_ID["chili"])
    stand.setze(conn, "ort", "2")  # moved on: the house starts again as a room
    stand.setze(conn, "ausbau", "1")
    assert ausbau.ruf(conn) == 1 and preis(conn, NACH_ID["chili"]) >= vorher


def test_a_season_festival_after_the_first_bude(conn):
    from frits.spiel import saison

    started(conn)
    with pytest.raises(stand.SpielFehler):
        saison.feiern(conn, TODAY)  # not yet: only from the first Frittenbude on
    stand.setze(conn, "ausbau", "5")
    give(conn, saison.fest_preis(conn))
    s = saison.feiern(conn, TODAY)
    assert s.fest == "Am Markt" and stand.guthaben(conn) == 0
    with pytest.raises(stand.SpielFehler):
        saison.feiern(conn, TODAY)  # once a season
    assert saison.feste(conn) == [("Herbst 2026", "Herbstfest", "Am Markt")]


def test_festival_and_album_pages(client, ws):  # noqa: F811
    from frits.spiel import saison

    conn = store.connect(ws.db)
    client.get("/")  # Fritte moves in
    assert "/fritte/feiern" not in client.get("/fritte").text  # not before the first Bude
    stand.setze(conn, "ausbau", "5")
    give(conn, saison.fest_preis(conn))
    assert "Herbstfest feiern für 400" in client.get("/fritte").text
    page = post(client, "/fritte/feiern")
    assert "Fritte feiert das Herbstfest am Markt" in page.text
    assert 'class="wimpel"' in client.get("/fritte").text
    album = client.get("/sammlung").text
    assert "Saisons und Feste" in album and "Herbstfest" in album


def test_a_deleted_expense_is_not_counted_as_waiting(conn):
    started(conn)
    add_invoice(conn, "2026-0001", TODAY)
    eid = add_expense(conn, TODAY)
    assert haustier.ansehen(conn, TODAY).wartend == 2
    conn.execute("UPDATE expenses SET struck = '2026-11-02T10:00:00' WHERE id = ?", (eid,))
    assert haustier.ansehen(conn, TODAY).wartend == 1


def test_painted_rooms_give_wall_things_their_own_place(client, ws):  # noqa: F811
    """From stage 3 Marie's painting has its own tool wall and framed picture: the bought
    picture covers the painted frame instead of hanging over the tools."""
    from frits.haustier import DINGE, GEMALT_PLATZ

    bild, tasse = DINGE["bild"], DINGE["teetasse"]
    assert bild.platz() == (bild.x, bild.y, bild.groesse)
    assert bild.platz(3) == (39.6, 65, 2.6) and tasse.platz(3) == tasse.platz()
    assert all(i in DINGE and DINGE[i].art == "zimmer" for p in GEMALT_PLATZ.values() for i in p)

    conn = store.connect(ws.db)
    client.get("/")
    stand.nimm(conn, "bild", "ding", TODAY.isoformat(), 25)
    stand.setze(conn, "ausbau", "3")
    conn.commit()
    page = client.get("/fritte").text
    assert "left: 39.6%; bottom: 65%" in page and f"left: {bild.x}%" not in page


def test_the_card_note_counts_only_cards_that_really_wait(client, ws):  # noqa: F811
    """Lena: a waiting card whose paperwork is gone made the note say 11 instead of 10."""
    conn = store.connect(ws.db)
    add_invoice(conn, "2026-0001", TODAY - timedelta(days=30))
    client.get("/")
    post(client, "/fritte/kassensturz")
    conn.execute(
        "INSERT INTO spiel_karten (schluessel, art, gesehen) VALUES ('steuer:999', 'steuer', ?)",
        (TODAY.isoformat(),),
    )
    add_invoice(conn, "2026-0002", TODAY, came=TODAY)
    note = client.get("/invoices").text
    assert "Fritte hat eine neue Karte für den Kassensturz: 1 wartet" in note


def test_hats_and_dishes_show_painted_pictures_not_emoji(client, ws):  # noqa: F811
    """Lena R43: emoji hats in the cupboard and an emoji pot next to painted pepper."""
    from pathlib import Path

    from frits.web import app as web_app

    conn = store.connect(ws.db)
    client.get("/")
    stand.nimm(conn, "zylinder", "ding", TODAY.isoformat(), 0)
    conn.commit()
    laden = client.get("/fritte/laden").text
    assert '<img class="zeichen-bild" src="/static/bilder/ding-zylinder.webp"' in laden

    # a dish without its own picture sits on the shelf as a preserving jar
    env = web_app._templates().env
    env.globals["picture"] = web_app.painted_pictures(Path(web_app.__file__).parent / "static").get
    spiel = env.get_template("_spiel.html").module
    glas = str(spiel.glas(REZEPT_NACH_ID["belegsuppe"]))
    assert "symbol-einkochen.webp" in glas and "🍲" not in glas
    assert "gewuerz-pfeffer.webp" in str(spiel.glas(NACH_ID["pfeffer"]))
    assert "ding-postkarte.webp" in str(spiel.bild_zeichen("ding", "postkarte", "💌"))
    assert "symbol-salz.webp" in str(
        spiel.bild_zeichen("koerner", None, "🧂")
    )  # Lena: 🧂 next to painted pictures
    assert str(spiel.bild_zeichen("etwas", "", "★")) == "★"


def test_serie_counts_kept_vorsaetze_in_a_row():
    from datetime import date, timedelta

    from frits.haustier import serie, serie_text

    mi = date(2026, 11, 4)  # a Wednesday
    wochen = {mi - timedelta(weeks=n) for n in (1, 2, 3)}
    assert serie(wochen, mi) == 3  # this week isn't over: the streak still stands
    assert serie(wochen | {mi}, mi) == 4
    assert serie(wochen - {mi - timedelta(weeks=2)}, mi) == 1  # a gap: calmly from the start
    assert serie(wochen, mi, "zweimal") == 0  # once a week isn't twice
    tage = {mi - timedelta(days=n) for n in range(5)}
    assert serie(tage, mi, "taeglich") == 5
    assert serie_text(1, "taeglich") == "1 Tag in Folge"
    assert serie_text(3) == "3 Wochen in Folge"

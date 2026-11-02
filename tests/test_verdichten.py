"""Runde 10: fewer systems, the same decisions (Lena). One daily goal, one season strip,
one Kassenbuch, the house only when it matters."""

from datetime import timedelta

import pytest
from test_haustier import add_expense, add_invoice, started
from test_web import TODAY, client, mails, post, provider, ws  # noqa: F401

from frits import aufgaben, store
from frits.spiel import kassensturz, saison, sammlung, stand, tagesziel


@pytest.fixture
def conn(tmp_path):
    return started(store.connect(tmp_path / "frits.db"))


def quest_stueck(conn, status, tag):
    from frits.steuer import quest

    quest.ensure_schema(conn)
    n = conn.execute("SELECT COUNT(*) FROM steuer_stuecke").fetchone()[0]
    conn.execute(
        "INSERT INTO steuer_stuecke (jahr, gebiet, key, name, status, geaendert)"
        " VALUES (2025, 'gehoeft-1', ?, 'Test', ?, ?)",
        (f"k{n}", status, f"{tag.isoformat()} 10:00:00"),
    )


def test_one_daily_goal_counts_paperwork_and_quest_not_ticks(conn):
    z = tagesziel.zaehlen(conn, TODAY)
    assert (z["erledigt"], z["ziel"], z["geschafft"]) == (0, 3, False)
    assert z["text"] == "Tagesziel: 0 von 3 geschafft"

    add_expense(conn, TODAY, came=TODAY)
    stand.abgleichen(conn, TODAY, None)
    assert tagesziel.zaehlen(conn, TODAY)["erledigt"] == 1

    aufgaben.hinzufuegen(conn, ["Mama anrufen"], TODAY)
    pid = conn.execute("SELECT id FROM aufgaben").fetchone()[0]
    aufgaben.abhaken(conn, pid, TODAY, spiel=True)
    assert tagesziel.zaehlen(conn, TODAY)["erledigt"] == 1  # like chests: a tick is no paperwork

    quest_stueck(conn, "gibts_nicht", TODAY)  # stamping away is no progress
    quest_stueck(conn, "gesammelt", TODAY - timedelta(days=1))  # yesterday
    assert tagesziel.zaehlen(conn, TODAY)["erledigt"] == 1
    quest_stueck(conn, "fragen", TODAY)
    quest_stueck(conn, "gesammelt", TODAY)
    z = tagesziel.zaehlen(conn, TODAY)
    assert (z["erledigt"], z["geschafft"], z["text"]) == (3, True, "Tagesziel geschafft")


def test_a_tax_find_counts_once_not_also_as_fritte_card(conn):
    quest_stueck(conn, "gesammelt", TODAY)
    conn.execute(
        "INSERT INTO spiel_karten (schluessel, art, gesehen) VALUES ('steuer:1', 'steuer', ?)",
        (TODAY.isoformat(),),
    )
    assert tagesziel.zaehlen(conn, TODAY)["erledigt"] == 1


def test_the_season_strip_has_a_dot_per_week(conn):
    s = saison.jetzt(conn, TODAY)
    punkte = saison.streifen(conn, s, TODAY)
    assert len(punkte) == s.wochen
    assert punkte.count("jetzt") == 1 and "voll" not in punkte
    add_invoice(conn, "2026-0001", TODAY, came=TODAY)
    stand.abgleichen(conn, TODAY, None)
    punkte = saison.streifen(conn, saison.jetzt(conn, TODAY), TODAY)
    assert punkte.count("voll") == 1 and "jetzt" not in punkte


def test_fritte_page_is_calmer_at_the_start(client, ws):  # noqa: F811
    conn = store.connect(ws.db)
    add_invoice(conn, "2026-0001", TODAY - timedelta(days=30))
    client.get("/fritte")
    post(client, "/fritte/kassensturz")
    page = client.get("/fritte").text
    assert "Büro-Wochen" not in page  # the season strip says it once
    assert 'class="saison-streifen"' in page
    assert 'id="haus"' not in page  # house stages come after the third Kassensturz
    assert "Seltene Gewürze gibt es erst" not in client.get("/fritte/laden").text
    assert "Frittes Kassenbuch" in page and "Chronik" not in page
    assert 'href="/fritte/kassensturz/1"' in page
    assert "Kassenbuch" not in client.get("/fritte/laden").text

    for n in (2, 3):
        add_invoice(conn, f"2026-000{n}", TODAY)
        stand.abgleichen(conn, TODAY, None)
        post(client, "/fritte/kassensturz")
    assert len(kassensturz.chronik(conn)) == 3
    page = client.get("/fritte").text
    assert 'id="haus"' in page
    haus = page[page.index('id="haus"') :]
    haus = haus[: haus.index("</ol>")]
    assert haus.count("<li") == 2  # where Fritte is and the next stage, not all five


def test_one_showcase_with_stickers_orden_and_their_zierde(client, ws, monkeypatch):  # noqa: F811
    from frits.steuer import quest

    orden = [
        {"jahr": 2024, "key": "erster-fund", "name": "Erster Fund", "zeichen": "🔎",
         "beschreibung": "Die erste Unterlage gefunden.", "erreicht": True, "zierde": "laternen"},
        {"jahr": 2025, "key": "erster-fund", "name": "Erster Fund", "zeichen": "🔎",
         "beschreibung": "Die erste Unterlage gefunden.", "erreicht": True, "zierde": "laternen"},
        {"jahr": None, "key": "erste-ernte", "name": "Erste Ernte", "zeichen": "🌾",
         "beschreibung": "Ein Gebiet ganz gesammelt.", "erreicht": False, "zierde": "windmuehle"},
    ]  # fmt: skip
    monkeypatch.setattr(quest, "orden_fuer_sammlung", lambda conn: orden, raising=False)
    conn = store.connect(ws.db)
    client.get("/fritte")
    faecher = sammlung.faecher(conn, TODAY)
    assert [f["id"] for f in faecher] == ["steuer", "schaetze", "alltag", "saisons"]
    assert "Drache besiegt" not in [k["name"] for k in faecher[2]["kacheln"]]  # the Orden says it
    fund, ernte = faecher[0]["kacheln"]
    assert fund["erreicht"] and fund["jahre"] == [2024, 2025]
    assert not ernte["erreicht"] and ernte["zierde"] == "Windmühle"
    page = client.get("/sammlung").text
    assert "Sammlung" in page and "Steuer-Quest" in page and "Alltag mit Fritte" in page
    assert "Bringt auf die Insel: Windmühle" in page and "2024, 2025" in page
    assert 'href="/sammlung" aria-current="page"' in page


def test_the_showcase_without_quest_or_fritte(client, ws, monkeypatch):  # noqa: F811
    from dataclasses import replace

    from frits.settings import Settings
    from frits.steuer import quest

    def kaputt(conn):
        raise RuntimeError("quest not ready")

    monkeypatch.setattr(quest, "orden_fuer_sammlung", kaputt, raising=False)
    conn = store.connect(ws.db)
    assert [f["id"] for f in sammlung.faecher(conn, TODAY)] == ["schaetze", "alltag", "saisons"]
    s = Settings.load(ws.settings_file)
    replace(s, haustier=False).save(ws.settings_file)
    page = client.get("/sammlung")
    assert page.status_code == 200 and "Alltag mit Fritte" not in page.text


def test_a_won_tax_year_is_one_card_not_a_fortune(conn):
    """Lena: two dragon victories × Würze 3 gave 150 salt on day one, the Stube costs 150.
    The victory pays in the quest (rest gold, chest, Orden); the card is a small one."""
    assert stand.GRUND["steuerjahr"] <= stand.GRUND["rechnung"] * 2


def test_old_chest_loot_is_not_listed_as_found(client, ws):  # noqa: F811
    import json

    conn = store.connect(ws.db)
    client.get("/fritte")
    for n, inhalt in enumerate(
        [{"art": "gold", "name": "8 Drachengold", "zeichen": "🪙", "seltenheit": "selten"},
         {"art": "wuerze", "name": "Glas Truhenwürze", "zeichen": "🫙", "seltenheit": "gewöhnlich"},
         {"art": "koerner", "name": "12 Salzkörner", "zeichen": "🧂", "seltenheit": "gewöhnlich",
          "wert": 12}]
    ):  # fmt: skip
        conn.execute(
            "INSERT INTO spiel_truhen (grund, text, mindestens, erhalten, geoeffnet, reihe, stufe,"
            " inhalt) VALUES (?, '', NULL, ?, ?, ?, 0, ?)",
            (f"alt:{n}", TODAY.isoformat(), TODAY.isoformat(), n + 1, json.dumps(inhalt)),
        )
    page = client.get("/fritte").text
    funde = page[page.index("Zuletzt gefunden") :]
    funde = funde[: funde.index("</div>")]
    assert "12 Salzkörner" in funde
    assert "Drachengold" not in funde and "Truhenwürze" not in funde


def test_chest_treasures_live_in_the_showcase(client, ws):  # noqa: F811
    from frits import haustier

    conn = store.connect(ws.db)
    client.get("/fritte")
    stand.nimm(conn, "muschel", "ding", TODAY.isoformat(), 0)
    faecher = {f["id"]: f for f in sammlung.faecher(conn, TODAY)}
    kacheln = faecher["schaetze"]["kacheln"]
    assert [k["name"] for k in kacheln] == [d.name for d in haustier.SCHAETZE]
    assert [k["name"] for k in kacheln if k["erreicht"]] == ["Muschel"]
    page = client.get("/fritte").text
    assert 'href="/sammlung#schaetze"' in page and 'class="schatz' not in page
    assert "1 von" in page[page.index("schaetze-zeile") :][:300]
    assert 'id="schaetze"' in client.get("/sammlung").text
    assert "schaetze" not in [f["id"] for f in sammlung.faecher(conn, TODAY, alltag=False)]


def test_the_showcase_puts_the_newest_on_top_and_few_empty_places(client, ws):  # noqa: F811
    """Lena: not a form of 34 equal boxes. Newest first, Orden first, few empty places."""
    from frits.spiel import album

    conn = store.connect(ws.db)
    client.get("/fritte")
    stand.nimm(conn, "muschel", "ding", "2026-03-01", 0)
    stand.nimm(conn, "kleeblatt", "ding", "2026-04-02", 0)
    stand.setze(conn, f"erfolg:{album.ERFOLGE[0].id}", "2026-03-15")
    faecher = sammlung.faecher(conn, TODAY)
    assert [k["name"] for k in sammlung.zuletzt(faecher)][:3] == [
        "Glücksklee",
        album.ERFOLGE[0].name,
        "Muschel",
    ]
    schaetze = next(f for f in faecher if f["id"] == "schaetze")
    assert len(schaetze["da"]) == 2 and len(schaetze["als_naechstes"]) == 3
    assert len(schaetze["verborgen"]) == len(schaetze["kacheln"]) - 5
    page = client.get("/sammlung").text
    assert page.index("Zuletzt verdient") < page.index('id="schaetze"')
    assert "Noch 3 Plätze frei" in page and "Als Nächstes" in page
    assert 'class="praegung"' in page  # one minted coin style for every sign


def test_the_showcase_after_lenas_look(client, ws, monkeypatch):  # noqa: F811
    """Lena on #117: an earned Orden whose sign is "?" looked empty, chest finds later the
    same day did not come first, and there was no season shelf to find."""
    from frits.steuer import quest

    orden = [{"jahr": 2025, "key": "mutig-gefragt", "name": "Mutig gefragt", "zeichen": "?",
              "beschreibung": "Eine Frage gestellt.", "erreicht": True, "zierde": "kirschbaum"}]  # fmt: skip
    monkeypatch.setattr(quest, "orden_fuer_sammlung", lambda conn: orden, raising=False)
    conn = store.connect(ws.db)
    client.get("/fritte")
    quest.ensure_schema(conn)
    conn.execute(
        "INSERT INTO steuer_chronik (jahr, zeit, art, text) VALUES"
        f" (2025, '{TODAY.isoformat()}T00:00:01', 'erfolg', 'Mutig gefragt')"
    )
    stand.nimm(conn, "muschel", "ding", stand.zeitstempel(TODAY), 0)  # a chest find, later
    faecher = sammlung.faecher(conn, TODAY)
    oben = sammlung.zuletzt(faecher)
    assert oben[0]["id"] == "schatz:muschel" and oben[0]["seit"] == TODAY.isoformat()
    assert any(k["id"] == "orden:mutig-gefragt" for k in oben)
    page = client.get("/sammlung").text
    assert '<span class="praegung">?</span>' in page  # earned: its own sign in the coin
    assert 'class="muenze" aria-hidden="true"></span>' in page  # open places: an empty coin, no "?"
    saisons = next(f for f in faecher if f["id"] == "saisons")
    assert saisons["als_naechstes"][0]["name"].startswith("Pokal ")
    assert 'id="saisons"' in page


def test_feierabend_shows_what_the_game_made_of_today(client, ws):  # noqa: F811
    """A tester (as Marie): the Feierabend as closing credits is what she'd show Fritz."""
    from frits.spiel import abspann

    conn = store.connect(ws.db)
    add_invoice(conn, "2026-0001", TODAY, came=TODAY)
    client.get("/fritte")
    post(client, "/fritte/kassensturz")
    quest_stueck(conn, "gesammelt", TODAY)
    aufgaben.hinzufuegen(conn, ["Mama anrufen"], TODAY)
    aufgaben.abhaken(conn, conn.execute("SELECT id FROM aufgaben").fetchone()[0], TODAY, spiel=True)
    zeilen = abspann.heute(conn, TODAY)
    assert "1 Punkt auf deiner Liste abgehakt" in zeilen
    assert any(z.startswith("Kassensturz: ") and "Salzkörner" in z for z in zeilen)
    assert "1 Unterlage für die Steuer gesammelt" in zeilen
    assert any(z.startswith("Tagesziel") for z in zeilen)
    page = client.get("/feierabend").text
    assert "Alles von heute" in page and "Unterlage für die Steuer" in page
    bilanz = abspann.bilanz(conn, TODAY)
    assert (
        bilanz["schlagzeile"]
        and bilanz["zeichen"]
        and [k["titel"] for k in bilanz["kacheln"]]
        == [
            "Arbeit",
            "Steuer",
            "Fritte",
        ]
    )
    assert 'class="abspann-schlagzeile"' in page
    # The credits come first: the old "Heute" counts move into "Alles von heute" (tester).
    assert "<h2>Heute</h2>" not in page and "erstellt</li>" in page.split("Alles von heute")[1]
    assert all("Salzkörner" not in z for z in abspann.heute(conn, TODAY, mit_fritte=False))
    # Salt the Steuer-Quest paid into Fritte's purse today counts too (one purse).
    vorher = dict(next(k["zahlen"] for k in bilanz["kacheln"] if k["titel"] == "Fritte"))
    stand.buche(conn, TODAY.isoformat(), 12, "quest", "Steuer-Quest")
    assert "Aus der Steuer-Quest: 12 Salzkörner" in abspann.heute(conn, TODAY)
    nachher = dict(
        next(k["zahlen"] for k in abspann.bilanz(conn, TODAY)["kacheln"] if k["titel"] == "Fritte")
    )
    assert {v: k for k, v in nachher.items()}["Salzkörner"] == {v: k for k, v in vorher.items()}[
        "Salzkörner"
    ] + 12


def test_salbei_counts_tax_documents_as_paperwork():
    from frits.spiel import gewuerze as gw

    assert {"steuer", "steuerjahr"} <= gw.ECHTE_ARBEIT and gw.PAPIERKRAM < gw.ECHTE_ARBEIT

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
    assert [f["id"] for f in faecher] == ["alltag", "steuer"]
    assert "Drache besiegt" not in [k["name"] for k in faecher[0]["kacheln"]]  # the Orden says it
    fund, ernte = faecher[1]["kacheln"]
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
    assert [f["id"] for f in sammlung.faecher(conn, TODAY)] == ["alltag"]
    s = Settings.load(ws.settings_file)
    replace(s, haustier=False).save(ws.settings_file)
    page = client.get("/sammlung")
    assert page.status_code == 200 and "Alltag mit Fritte" not in page.text

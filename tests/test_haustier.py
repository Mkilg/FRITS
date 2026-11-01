"""Fritte als Haustier: gentle moods, Salzkörner from real paperwork, a small shop."""

from datetime import date, timedelta

import pytest
from test_web import TODAY, client, mails, post, provider, ws  # noqa: F401

from frits import haustier, store
from frits.settings import Settings
from frits.web.haustier import fritte_datei


def add_invoice(conn, number, issue, paid=None):
    conn.execute(
        "INSERT INTO invoices (number, customer, issue_date, paid_date, net, vat, gross)"
        " VALUES (?, 'Erika Meier', ?, ?, '100', '0', '100')",
        (number, issue.isoformat(), paid.isoformat() if paid else None),
    )


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "frits.db")


def test_new_fritte_is_curious_and_has_nothing(conn, tmp_path):
    f = haustier.ansehen(conn, tmp_path / "fritte.toml", TODAY)
    assert f.laune == "neu"
    assert f.guthaben == 0
    assert f.wochen == [False] * haustier.WOCHEN
    assert f.zimmer == [] and f.hut is None


@pytest.mark.parametrize(
    "days_ago, laune, bild",
    [(0, "munter", "welcome"), (3, "munter", "welcome"), (7, "gemuetlich", "thinking"),
     (30, "doest", "feierabend")],
)  # fmt: skip
def test_mood_follows_the_rhythm_and_never_gets_sad(conn, tmp_path, days_ago, laune, bild):
    add_invoice(conn, "2026-0001", TODAY - timedelta(days=days_ago))
    f = haustier.ansehen(conn, tmp_path / "fritte.toml", TODAY)
    assert (f.laune, f.bild) == (laune, bild)
    assert "warning" not in f.bild  # she is never worried or sad
    assert not any(
        word in f.satz.lower() for word in ("traurig", "vermisst", "endlich", "schon lange")
    )


def test_salzkoerner_count_real_paperwork_including_older_work(conn, tmp_path):
    add_invoice(conn, "2026-0001", date(2026, 1, 5), paid=date(2026, 1, 20))
    add_invoice(conn, "2026-0002", date(2026, 2, 5))
    path = tmp_path / "fritte.toml"
    f = haustier.ansehen(conn, path, TODAY)
    assert f.verdient == 3 + 2 + 3
    assert f.erster_besuch and f.neu_verdient == 8
    # Older work is a welcome gift: it doesn't make today a care day.
    assert f.laune == "doest"

    add_invoice(conn, "2026-0003", date(2026, 3, 5))  # an old date, entered today
    f = haustier.ansehen(conn, path, TODAY)
    assert not f.erster_besuch and f.neu_verdient == 3
    assert f.laune == "munter"  # new paperwork today counts, whatever date it carries
    assert haustier.ansehen(conn, path, TODAY).neu_verdient == 0


def test_this_weeks_dot(conn, tmp_path):
    add_invoice(conn, "2026-0001", TODAY - timedelta(weeks=2))
    f = haustier.ansehen(conn, tmp_path / "fritte.toml", TODAY)
    assert f.wochen == [False, True, False, False]


def test_buying_spends_grains_and_hats_go_on(conn, tmp_path):
    path = tmp_path / "fritte.toml"
    for n in range(10):
        add_invoice(conn, f"2026-{n:04}", TODAY)
    haustier.ansehen(conn, path, TODAY)  # 30 grains
    with pytest.raises(haustier.LadenFehler, match="fehlen noch 10"):
        haustier.kaufen(conn, path, "zylinder")
    haustier.kaufen(conn, path, "teetasse")
    haustier.kaufen(conn, path, "muetze")
    with pytest.raises(haustier.LadenFehler, match="schon"):
        haustier.kaufen(conn, path, "teetasse")
    with pytest.raises(haustier.LadenFehler):
        haustier.kaufen(conn, path, "../../etc")
    f = haustier.ansehen(conn, path, TODAY)
    assert f.guthaben == 30 - 5 - 15
    assert [d.id for d in f.zimmer] == ["teetasse"]
    assert f.hut.id == "muetze"
    haustier.aufsetzen(path, "muetze")  # the same hat again takes it off
    assert haustier.ansehen(conn, path, TODAY).hut is None
    haustier.aufsetzen(path, "zylinder")  # not bought: nothing goes on
    assert haustier.ansehen(conn, path, TODAY).hut is None


def test_feierabend_brings_one_grain_a_day(conn, tmp_path):
    path = tmp_path / "fritte.toml"
    assert haustier.feierabend(path, TODAY)
    assert not haustier.feierabend(path, TODAY)
    f = haustier.ansehen(conn, path, TODAY)
    assert f.verdient == 1 and f.laune == "munter"


def test_a_broken_file_never_breaks_heute(conn, tmp_path):
    path = tmp_path / "fritte.toml"
    path.write_text("this is = = not toml", encoding="utf-8")
    assert haustier.ansehen(conn, path, TODAY).laune == "neu"


def test_pages(client, ws):  # noqa: F811
    conn = store.connect(ws.db)
    for n in range(4):
        add_invoice(conn, f"2026-{n:04}", TODAY - timedelta(days=1))
    home = client.get("/").text
    assert "Fritte zuhause" in home and "Willkommensgeschenk: +12" in home

    page = client.get("/fritte").text
    assert "Der kleine Laden" in page and "Kaufen für 5" in page

    page = post(client, "/fritte/kaufen", ding="pflanze")
    assert "Topfpflanze gekauft" in page.text
    assert "Die Topfpflanze hat jetzt einen schönen Platz" in page.text
    assert 'class="ding neu"' in page.text
    assert client.get("/").text.count("🪴") == 1

    page = post(client, "/fritte/kaufen", ding="krone")
    assert "fehlen noch 58 Salzkörner" in page.text

    post(client, "/feierabend")
    assert haustier.Zustand.laden(fritte_datei(ws.root)).feierabende == 1


def test_fritte_can_be_hidden(client, ws):  # noqa: F811
    current = Settings.load(ws.settings_file)
    current.haustier = False
    current.save(ws.settings_file)
    assert "Fritte zuhause" not in client.get("/").text
    post(client, "/settings", checkboxes=["haustier"], haustier="on")
    assert Settings.load(ws.settings_file).haustier

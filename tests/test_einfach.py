"""FRITS einfacher: five places in the menu, tabs inside a place, one next step on Heute,
settings in folds. (Research behind it: /mnt/project-files/frits/einfacher/ in the project.)"""

import re

import pytest
from fastapi.testclient import TestClient
from test_web import TODAY, Recorded, ws  # noqa: F401  (ws is a fixture)

from frits.settings import Settings
from frits.web.app import create_app


@pytest.fixture
def client(ws):  # noqa: F811
    app = create_app(
        ws, provider_factory=Recorded(ws), nlp_loader=lambda: None, today=lambda: TODAY
    )
    with TestClient(app) as c:
        c.token = app.state.token
        yield c


def post(client, url, **data):
    return client.post(url, data={"token": client.token, **data}, follow_redirects=True)


def header_nav(page: str) -> list[str]:
    header = page[page.index('<header class="top">') : page.index("</header>")]
    row = header[header.index("<nav>") : header.index('<details class="mehr">')]
    return re.findall(r'<a href="([^"]+)"', row)


def test_menu_has_five_places_and_a_chat_button(client):
    page = client.get("/").text
    assert header_nav(page) == ["/", "/invoices", "/expenses", "/customers", "/steuer"]
    assert 'class="button frag" href="/chat"' in page
    header = page[page.index('<header class="top">') : page.index("</header>")]
    assert 'href="/notfall"' not in page and 'href="/drafts"' not in header


def test_drafts_and_quotes_live_under_rechnungen(client):
    page = client.get("/drafts").text
    assert '<a href="/invoices" aria-current="page">Rechnungen</a>' in page  # the place is lit
    tabs = page[page.index('<nav class="reiter"') :]
    tabs = tabs[: tabs.index("</nav>")]
    assert re.findall(r'href="([^"]+)"', tabs) == ["/invoices", "/drafts", "/quotes"]
    assert '<a href="/drafts" aria-current="page">Entwürfe</a>' in tabs
    euer = client.get("/euer").text
    assert '<a href="/expenses" aria-current="page">Ausgaben</a>' in euer
    assert '<a href="/euer" aria-current="page">EÜR</a>' in euer
    assert (
        "/ustva" not in euer.split('<nav class="reiter"')[1].split("</nav>")[0]
    )  # Kleinunternehmer
    assert 'class="reiter"' not in client.get("/customers").text


def test_heute_puts_one_next_step_first(client):
    assert "Leg los" in client.get("/").text  # no quest yet: Heute shows how to start it
    client.app.state.quest_auftrag = lambda conn: None  # every Fundkiste packed
    calm = client.get("/").text
    assert "Nichts, was eilt" in calm
    assert "Keine offenen Entwürfe" not in calm and "Nichts offen" not in calm  # no empty boxes
    post(client, "/drafts/new")
    page = client.get("/").text
    nxt = page[page.index('id="als-naechstes"') :]
    nxt = nxt[: nxt.index("</section>")]
    assert "Ein Entwurf braucht dich" in nxt and 'href="/drafts/' in nxt
    # the page's only primary button is the next step (the Einwurf's folded text box aside)
    assert page.count('class="button primary"') == 1 and 'class="button primary"' in nxt


def test_settings_sit_in_folds(client, ws):  # noqa: F811
    page = client.get("/settings").text
    assert '<details class="card fach" id="ki">' in page  # /settings#ki opens it (frits.js)
    assert "Nächste Nummer:" in page and "IBAN endet auf" in page
    assert '<details class="card fach" id="ueber-dich">' in page  # complete: closed
    current = Settings.load(ws.settings_file)
    Settings(**{**current.__dict__, "address": ""}).save(ws.settings_file)
    assert '<details class="card fach" id="ueber-dich" open>' in client.get("/settings").text
    saved = post(client, "/settings", name="Fritz Neu", address="Weg 2, 12345 Ort")
    assert saved.status_code == 200
    assert Settings.load(ws.settings_file).name == "Fritz Neu"


# ---------- one purse for Fritte and the Steuer-Quest ----------


def test_one_purse_books_each_reason_once_and_never_goes_below_zero(tmp_path):
    from frits import store
    from frits.spiel import stand

    conn = store.connect(tmp_path / "frits.db")
    assert stand.beutel_gutschreiben(conn, "quest:2025:fund:1", 3, "Fund: Kontoauszug") == 3
    assert stand.beutel_gutschreiben(conn, "quest:2025:fund:1", 3, "noch einmal") == 0
    assert stand.guthaben(conn) == 3
    assert stand.beutel_gebucht(conn, "quest:2025:fund:1") == 3
    assert stand.beutel_gebucht(conn, "quest:2025:fund:2") is None
    stand.beutel_bezahlen(conn, 2, "Helfer: Spürnase")
    assert stand.guthaben(conn) == 1
    with pytest.raises(stand.SpielFehler, match="fehlen noch"):
        stand.beutel_bezahlen(conn, 5, "zu teuer")
    assert stand.guthaben(conn) == 1  # nothing booked
    # A card put back takes its grains back, but at most what is still there
    assert stand.beutel_gutschreiben(conn, "quest:2025:zurueck:1", -3, "Fund zurückgelegt") == -1
    assert stand.guthaben(conn) == 0
    arten = {z["art"] for z in stand.kassenbuch(conn)}
    assert arten == {"quest", "kauf"}


def test_the_shop_shows_the_quest_shelf_when_the_quest_offers_one(client):
    assert 'id="insel"' not in client.get("/fritte/laden").text
    client.app.state.helfer_regal = lambda conn: {"jahr": 2025, "helfer": []}
    page = client.get("/fritte/laden").text
    assert 'id="insel"' in page
    client.app.state.helfer_regal = lambda conn: 1 / 0  # a hiccup in the quest
    assert client.get("/fritte/laden").status_code == 200


def test_kassenbuch_groups_quest_bookings_of_one_day(tmp_path):
    from frits import store
    from frits.spiel import stand

    conn = store.connect(tmp_path / "frits.db")
    tag = "2026-11-02"
    stand.vorbereiten(conn)
    stand.buche(conn, tag, 5, "start", "Willkommen")
    for i, betrag in enumerate((3, 4, -2)):
        stand.buche(conn, tag, betrag, "quest", f"Steuer-Quest 2025: Zettel {i}")
    stand.buche(conn, tag, 1, "quest", "Steuer-Quest 2024: Zettel x")
    zeilen = stand.kassenbuch(conn)
    assert [z["text"] for z in zeilen] == [
        "Steuer-Quest 2024: Zettel x",
        "Steuer-Quest 2025: 3 Buchungen",
        "Willkommen",
    ]
    assert zeilen[1]["betrag"] == 5 and len(zeilen[1]["teile"]) == 3
    # the head names the quest year, the lines inside don't repeat it (Lena, round 34)
    assert [t["text"] for t in zeilen[1]["teile"]] == ["Zettel 2", "Zettel 1", "Zettel 0"]
    assert sum(z["betrag"] for z in zeilen) == stand.guthaben(conn)


def test_heute_shows_the_quest_step_when_the_desk_is_clear(client):
    client.app.state.quest_auftrag = lambda conn: {
        "jahr": 2025, "art": "sieg", "titel": "Hol dir deine Beute",
        "text": "Pack die Fundkiste.", "knopf": "Zur Fundkiste", "ziel": "/steuer/2025/sieg",
    }  # fmt: skip
    page = client.get("/").text
    nxt = page[page.index('id="als-naechstes"') :]
    nxt = nxt[: nxt.index("</section>")]
    assert "Steuer 2025: Hol dir deine Beute" in nxt
    assert 'class="button primary" href="/steuer/2025/sieg"' in nxt
    assert page.count('class="button primary"') == 1
    post(client, "/drafts/new")  # work on the desk comes first
    assert "Ein Entwurf braucht dich" in client.get("/").text
    client.app.state.quest_auftrag = lambda conn: 1 / 0  # a hiccup in the quest
    assert client.get("/").status_code == 200


def test_fritte_is_under_mehr_on_the_desktop_too(client):
    page = client.get("/").text
    header = page[page.index('<header class="top">') : page.index("</header>")]
    mehr = header[header.index('<details class="mehr">') :]
    assert 'href="/fritte"' in mehr
    tabs = page[page.index('<nav class="tabs"') :]
    assert tabs.count('href="/fritte"') == 1  # not twice on the phone


def test_kassenbuch_shows_the_currency_move_as_one_row(tmp_path):
    from frits import store
    from frits.spiel import stand

    conn = store.connect(tmp_path / "frits.db")
    stand.vorbereiten(conn)
    tag = "2026-11-02"
    stand.buche(conn, tag, 30, "drachensieg", "Restgold aus der Steuer-Quest 2025")
    stand.buche(conn, tag, 7, "quest", "Steuer-Quest 2026: Gold von früher, jetzt Salzkörner")
    stand.buche(conn, tag, 2, "quest", "Steuer-Quest 2026: Fund")
    zeilen = stand.kassenbuch(conn)
    assert [(z["text"], z["betrag"]) for z in zeilen] == [
        ("Steuer-Quest 2026: Fund", 2),
        ("Umstellung auf eine Währung", 37),
    ]


def test_kassenbuch_keeps_one_quest_row_per_day_around_a_purchase(tmp_path):
    from frits import store
    from frits.spiel import stand

    conn = store.connect(tmp_path / "frits.db")
    stand.vorbereiten(conn)
    tag = "2026-11-02"
    stand.buche(conn, tag, 8, "quest", "Steuer-Quest 2025: Steuer-ID (×4)")
    stand.buche(conn, tag, -3, "kauf", "Steuer-Quest 2025: Gekauft: Brieftaube")
    stand.buche(conn, tag, 16, "quest", "Steuer-Quest 2025: Bescheid (×8)")
    zeilen = stand.kassenbuch(conn)
    assert [(z["text"], z["betrag"]) for z in zeilen] == [
        ("Steuer-Quest 2025: 2 Buchungen · Serie bis ×8", 24),
        ("Steuer-Quest 2025: Gekauft: Brieftaube", -3),
    ]


def test_a_quest_bundle_counts_its_whole_day_even_past_the_page_limit(tmp_path):
    from frits import store
    from frits.spiel import stand

    conn = store.connect(tmp_path / "frits.db")
    stand.vorbereiten(conn)
    tag = "2026-11-02"
    for i in range(12):
        stand.buche(conn, tag, 2, "quest", f"Steuer-Quest 2026: Zettel {i}")
    stand.buche(conn, tag, 1, "kassensturz", "Kassensturz Nr. 1")
    zeilen = stand.kassenbuch(conn, limit=2)  # the first window only sees 6 rows
    assert [z["text"] for z in zeilen] == ["Kassensturz Nr. 1", "Steuer-Quest 2026: 12 Buchungen"]
    assert zeilen[1]["betrag"] == 24 and len(zeilen[1]["teile"]) == 12

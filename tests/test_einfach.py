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
    app = create_app(ws, provider_factory=Recorded(ws), nlp_loader=lambda: None, today=lambda: TODAY)
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
    assert "/ustva" not in euer.split('<nav class="reiter"')[1].split("</nav>")[0]  # Kleinunternehmer
    assert 'class="reiter"' not in client.get("/customers").text


def test_heute_puts_one_next_step_first(client):
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

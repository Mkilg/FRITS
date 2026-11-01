"""The browser version's Python side (frits.browser), tested on a normal computer.

In the browser the same code runs in Pyodide; browser/tests/e2e.cjs drives it in a real browser.
"""

import asyncio
import io
import json
import zipfile
from datetime import date

import pytest

from frits import umgebung
from frits.browser import asgi, runtime
from frits.customers import Customer
from frits.redact import redact
from frits.settings import Settings, Workspace
from frits.web.app import create_app

PREFIX = "/FRITS/app"


@pytest.fixture
def app(tmp_path):
    ws = Workspace(tmp_path / "FRITS").ensure()
    Settings(name="Fritz Beispiel", setup_done=True).save(ws.settings_file)
    return create_app(ws=ws, nlp_loader=lambda: None, today=lambda: date(2026, 11, 2))


def get(app, path, method="GET", body=b"", headers=()):
    return asyncio.run(asgi.handle(app, method, PREFIX + path, list(headers), body, PREFIX))


def test_links_get_the_prefix_and_requests_lose_it(app):
    answer = get(app, "/")
    assert answer["status"] == 200
    html = answer["body"].decode()
    assert f'href="{PREFIX}/invoices"' in html
    assert f'href="{PREFIX}/static/frits.css"' in html
    assert 'href="/invoices"' not in html
    assert dict(answer["headers"])["content-length"] == str(len(answer["body"]))


def test_any_host_header_is_this_tab(app):
    # The middleware refuses foreign host names (DNS rebinding); inside the browser tab there is
    # no network, so the bridge always calls the app "localhost".
    answer = get(app, "/", headers=[("host", "mkilg.github.io")])
    assert answer["status"] == 200


def test_redirects_stay_inside_the_frame(app):
    answer = get(app, "/sound", method="POST", body=b"token=falsch",
                 headers=[("content-type", "application/x-www-form-urlencoded")])
    assert answer["status"] in (303, 403)
    location = dict(answer["headers"]).get("location")
    if location:
        assert location.startswith(PREFIX + "/")


def test_add_prefix_leaves_text_and_external_links_alone():
    html = (
        '<a href="/invoices">R</a> <a href="https://example.com/x">E</a> '
        '<link href="//cdn.example.com/a.css"> <p>Preis: 60 €/Stunde, und/oder (/Monat)</p> '
        '<div style="background:url(/static/bild.webp)"></div> '
        f'<a href="{PREFIX}/schon">S</a> <a href="/">Start</a>'
    )
    out = asgi.add_prefix(html, PREFIX)
    assert f'href="{PREFIX}/invoices"' in out
    assert 'href="https://example.com/x"' in out
    assert 'href="//cdn.example.com/a.css"' in out
    assert "60 €/Stunde, und/oder (/Monat)" in out
    assert f"url({PREFIX}/static/bild.webp)" in out
    assert f'href="{PREFIX}/schon"' in out
    assert f'href="{PREFIX}/"' in out


def test_strip_prefix():
    assert asgi.strip_prefix(PREFIX, PREFIX) == "/"
    assert asgi.strip_prefix(PREFIX + "/steuer/1", PREFIX) == "/steuer/1"
    assert asgi.strip_prefix("/FRITS/apple", PREFIX) == "/FRITS/apple"


class FakeServices:
    """Stands in for the helper worker: answers like the browser model would (JSON text)."""

    def __init__(self, entities):
        self.entities = entities
        self.calls = []

    def ner(self, text):
        self.calls.append(text)
        found = []
        for word, label in self.entities.items():
            start = text.find(word)
            while start >= 0:
                found.append({"start": start, "end": start + len(word), "label": label})
                start = text.find(word, start + 1)
        return json.dumps({"entities": found})


def test_browser_ner_feeds_the_normal_redaction():
    text = "Termin mit Jana Kowalski in Lüneburg, Firma Gartenbau Nord.\n3 Std Hecke."
    nlp = runtime.BrowserNER(
        FakeServices({"Jana Kowalski": "PER", "Lüneburg": "LOC", "Gartenbau Nord": "ORG"})
    )
    result = redact(text, [], nlp)
    assert "Kowalski" not in result.text and "Lüneburg" not in result.text
    assert "Gartenbau" not in result.text
    assert "[PERSON_1]" in result.text and "3 Std Hecke" in result.text
    assert result.mapping.reidentify(result.text) == text


def test_browser_ner_keeps_the_stopword_rules():
    # The model may tag work words; the same filters as with spaCy drop them.
    nlp = runtime.BrowserNER(FakeServices({"Garten": "LOC", "Stunden": "PER"}))
    result = redact("3 Stunden im Garten gearbeitet.", [], nlp)
    assert result.text == "3 Stunden im Garten gearbeitet."


def test_missing_model_stops_redaction_instead_of_sending_half():
    class Broken:
        def ner(self, text):
            return json.dumps({"error": "kein Internet"})

    with pytest.raises(RuntimeError, match="nichts an eine KI"):
        redact("Frau Berger, 3 Std.", [Customer(id="b", name="Martina Berger")],
               runtime.BrowserNER(Broken()))


def test_doc_from_entities_ignores_bad_spans():
    doc = runtime.doc_from_entities("Hallo Berger", [
        {"start": 6, "end": 12, "label": "PER"},
        {"start": 0, "end": 99, "label": "PER"},
        {"start": 0, "end": 5, "label": "DATE"},
    ])
    assert len(doc.ents) == 1
    assert (doc.ents[0].text, doc.ents[0].label_, doc.ents[0].start_char) == ("Berger", "PER", 6)
    (token,) = doc.ents[0]
    assert (token.idx, token.text, token.tag_) == (6, "Berger", "NE")


def test_key_store_is_outside_the_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "KEYS_FILE", tmp_path / "geheim" / "keys.json")
    monkeypatch.setattr(runtime, "WORKSPACE", tmp_path / "frits")
    keyring = runtime._keyring_module()
    assert keyring.get_password("FRITS", "claude") is None
    keyring.set_password("FRITS", "claude", "sk-test")
    assert keyring.get_password("FRITS", "claude") == "sk-test"
    assert runtime.has_any_key()
    (tmp_path / "frits").mkdir()
    (tmp_path / "frits" / "settings.toml").write_text("name = 'x'")
    names = zipfile.ZipFile(io.BytesIO(runtime.backup_zip())).namelist()
    assert names == ["FRITS/settings.toml"]


def test_backup_and_restore_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "WORKSPACE", tmp_path / "frits")
    monkeypatch.setattr(umgebung, "IM_BROWSER", False)
    ws = Workspace(tmp_path / "frits").ensure()
    Settings(name="Fritz Beispiel", setup_done=True).save(ws.settings_file)
    (ws.customers / "berger.md").write_text("# Martina Berger\n")
    runtime.start(PREFIX)
    data = runtime.backup_zip()

    (ws.customers / "berger.md").unlink()
    (ws.customers / "neu.md").write_text("# Neu\n")
    count = runtime.restore_zip(data)
    assert count >= 2
    assert (ws.customers / "berger.md").exists()
    assert not (ws.customers / "neu.md").exists()
    assert umgebung.IM_BROWSER  # start() switched the wording to the browser


def test_restore_refuses_paths_outside_the_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "WORKSPACE", tmp_path / "frits")
    (tmp_path / "frits").mkdir()
    (tmp_path / "frits" / "settings.toml").write_text("bleibt")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("FRITS/../../boese.txt", "x")
    with pytest.raises(ValueError):
        runtime.restore_zip(buf.getvalue())
    assert (tmp_path / "frits" / "settings.toml").read_text() == "bleibt"
    assert not (tmp_path / "boese.txt").exists()


def test_key_wording_follows_where_frits_runs(monkeypatch):
    monkeypatch.setattr(umgebung, "IM_BROWSER", False)
    assert "Schlüsselbund" in umgebung.schluessel_ort()
    monkeypatch.setattr(umgebung, "IM_BROWSER", True)
    assert "Browser" in umgebung.schluessel_ort()


def test_own_details_reads_names_from_the_browser_model():
    from frits import setup

    text = "Fritz Beispiel\nMusterweg 1\n20095 Hamburg\nfritz@example.de"
    nlp = runtime.BrowserNER(FakeServices({"Fritz Beispiel": "PER", "Hamburg": "LOC"}))
    details = setup.own_details(text, nlp)
    assert details.get("name") == "Fritz Beispiel"

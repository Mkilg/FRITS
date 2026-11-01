"""Der Stapel: viele Dokumente auf einmal hinein, lokal vorsortiert, die KI sieht nur Geschwärztes,
Fritz bestätigt, danach sagt FRITS, was fehlt."""

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from test_steuer import fragebogen
from test_web import TODAY, mails, ws  # noqa: F401  (pytest fixtures)

from frits import expenses, stapel
from frits.customers import load_customers
from frits.providers import Usage
from frits.steuer import quest, spiel
from frits.web.app import create_app

BON = (
    "Baumarkt Hammer\nHauptstr. 5\n\nSchrauben 4x40      3,99\nAkkuschrauber      45,00\n"
    "SUMME EUR          48,99\nMwSt 19 %\nEC-Karte\n12.10.2026 14:31\n"
)
NOTIZ = (
    "Moderation für Bäckerei Sonnenschein am 3.10.2026, 4 Stunden.\n"
    "Bitte Rechnung an Karl Sonnenschein schicken.\n"
)
VCARD = (
    "BEGIN:VCARD\nVERSION:3.0\nFN:Paula Neumann\nORG:Neumann Design\n"
    "EMAIL:paula@neumann-design.example\nTEL:0151 1234567\nEND:VCARD\n"
)
BESCHEID = (
    "Finanzamt Musterstadt\nBescheid für 2024 über Einkommensteuer\n"
    "Herrn Fritz Beispiel, Werkstattweg 1, 12345 Musterstadt\nSteuernummer 123/456/78901\n"
    "Die Einkommensteuer wird festgesetzt auf 1.234,00 €.\n"
)
NEBENKOSTEN = (
    "Hausverwaltung Klee\nNebenkostenabrechnung 2025 für die Wohnung Lindenstraße 4\n"
    "Mieter: Jonas Albrecht\nGrundsteuer 210,00 €\nNachzahlung 88,40 €\n"
)


class FakeChat:
    """Sortiert wie eine KI und merkt sich genau, was rausging."""

    def __init__(self):
        self.sent = []
        self.reply = None
        self.fail = None

    def __call__(self, settings):
        return self

    def extract(self, text):  # pragma: no cover - der Stapel extrahiert nicht selbst
        raise AssertionError("not used")

    def chat(self, system, messages):
        if self.fail:
            from frits.providers import ProviderError

            raise ProviderError(self.fail)
        self.sent.append((system, [dict(m) for m in messages]))
        return self.reply(messages[0]["content"]), Usage("claude", "claude-opus-5-5", 3000, 400)


@pytest.fixture
def ai():
    return FakeChat()


@pytest.fixture
def client(ws, ai, nlp, mails):  # noqa: F811
    app = create_app(ws, provider_factory=ai, nlp_loader=lambda: nlp, today=lambda: TODAY)
    with TestClient(app) as c:
        c.token = app.state.token
        c.ws = ws
        yield c


def post(client, url, files=None, headers=None, **data):
    return client.post(
        url,
        data={"token": client.token, **data},
        files=files,
        headers=headers,
        follow_redirects=True,
    )


def einwerfen(client, dateien: dict[str, str | bytes]):
    files = [
        ("files", (name, inhalt.encode() if isinstance(inhalt, str) else inhalt))
        for name, inhalt in dateien.items()
    ]
    page = post(client, "/stapel/neu", files=files)
    assert page.status_code == 200
    (s,) = stapel.alle(client.ws.root)
    return s, page


def lesen(client, s):
    page = post(client, f"/stapel/{s.id}/lesen")
    assert page.status_code == 200
    return stapel.laden(client.ws.root, s.id), page


def nr_von(s, name):
    return next(d.nr for d in s.docs if d.name.endswith(name))


def alles_uebernehmen(client, s, **extra):
    data = {f"nimm_{d.nr}": "on" for d in s.offen}
    data.update(extra)
    return post(client, f"/stapel/{s.id}/uebernehmen", **data)


def test_menue_und_startseite(client):
    page = client.get("/")
    assert 'href="/stapel"' in page.text
    page = client.get("/stapel")
    assert page.status_code == 200
    assert "Ordner, ZIP oder viele Dateien" in page.text
    assert "webkitdirectory" in page.text  # ganzer Ordner auswählbar


def test_lokal_vorsortiert_ohne_ki(client, ai):
    s, page = einwerfen(
        client,
        {"bon.txt": BON, "notiz.txt": NOTIZ, "paula.vcf": VCARD, "bescheid.txt": BESCHEID},
    )
    assert "Ich lese" in page.text
    s, page = lesen(client, s)
    assert not s.ungelesen
    ziele = {d.name: d.ziel for d in s.docs}
    assert ziele == {
        "bon.txt": "ausgabe",
        "notiz.txt": "rechnung",
        "paula.vcf": "kunde",
        "bescheid.txt": "steuer",
    }
    bon = s.dok(nr_von(s, "bon.txt"))
    assert bon.felder["gross"] == "48,99" and bon.felder["date"] == "2026-10-12"
    assert ai.sent == []  # ohne Klick geht nichts raus
    assert "Wohin damit?" in page.text and "Genauer sortieren mit" in page.text
    # Was die KI sehen würde, ist schon geschwärzt, aber nicht verschickt
    notiz = s.dok(nr_von(s, "notiz.txt"))
    assert "Sonnenschein" not in notiz.geschwaerzt and "[" in notiz.geschwaerzt


def test_uebernehmen_legt_alles_an_seinen_platz(client, ws):  # noqa: F811
    s, _ = einwerfen(
        client,
        {"bon.txt": BON, "notiz.txt": NOTIZ, "paula.vcf": VCARD, "bescheid.txt": BESCHEID},
    )
    s, _ = lesen(client, s)
    bon = nr_von(s, "bon.txt")
    page = alles_uebernehmen(client, s, **{f"category_{bon}": "waren"})
    assert "Erledigt:" in page.text and "Was noch fehlt" in page.text
    # Notiz im Eingang, für einen Rechnungsentwurf
    assert (ws.inbox / "notiz.txt").is_file()
    # Beleg gebucht, mit Datei
    (e,) = expenses.list_expenses(ws.db, year=2026)
    assert str(e.gross) == "48.99" and e.category == "waren" and e.source_file == "bon.txt"
    assert (ws.done / "bon.txt").is_file()
    # Kunde angelegt
    assert any(c.name == "Paula Neumann" for c in load_customers(ws.customers))
    # Steuerbrief im Lager der Steuer-Quest
    assert [p.name for p in quest.lager_dateien(ws.root)] == ["bescheid.txt"]
    s = stapel.laden(ws.root, s.id)
    assert not s.offen and len(s.erledigt) == 4
    # Die Steuer-Quest läuft noch nicht: freundlich hinführen
    assert "Starte die Steuer-Quest 2025" in page.text


def test_ausgabe_ohne_kategorie_wartet_bei_ausgaben(client, ws):  # noqa: F811
    s, _ = einwerfen(client, {"bon.txt": BON.replace("Akkuschrauber", "Etwas")})
    s, _ = lesen(client, s)
    nr = nr_von(s, "bon.txt")
    s.dok(nr).felder.pop("category", None)
    stapel.speichern(ws.root, s)
    page = alles_uebernehmen(client, s, **{f"category_{nr}": ""})
    assert expenses.list_expenses(ws.db, year=2026) == []
    assert "wartet noch auf Betrag, Datum oder Kategorie" in page.text
    assert client.get("/expenses").text.count("bon.txt") >= 1


def test_ki_sieht_nur_geschwaerztes_und_der_code_prueft(client, ai, ws):  # noqa: F811
    s, _ = einwerfen(client, {"brief-von-karl.txt": NOTIZ, "kassenzettel.txt": BON})
    s, _ = lesen(client, s)
    notiz, bon = nr_von(s, "brief-von-karl.txt"), nr_von(s, "kassenzettel.txt")

    def antwort(content):
        return json.dumps(
            {
                "dokumente": [
                    {
                        "nr": notiz,
                        "ziel": "rechnung",
                        "titel": "Moderation für [PERSON_1]",
                        "fehlt": "Der Stundensatz steht nicht drin.",
                    },
                    # 99,99 steht nirgends im Beleg: der Betrag bleibt bei FRITS' eigener Vermutung
                    {
                        "nr": bon,
                        "ziel": "ausgabe",
                        "betrag": "99,99",
                        "datum": "12.10.2026",
                        "laden": "Baumarkt Hammer",
                        "kategorie": "gwg",
                        "titel": "Akkuschrauber",
                    },
                    {"nr": 77, "ziel": "steuer"},  # gibt es nicht: wird ignoriert
                ],
                "fehlt_insgesamt": ["Zur Moderation fehlt die Fahrtkostenabrechnung."],
            }
        )

    ai.reply = antwort
    page = client.get(f"/stapel/{s.id}/ki")
    assert "Das geht an" in page.text and "Jetzt an" in page.text
    assert ai.sent == []
    page = post(client, f"/stapel/{s.id}/ki")
    assert len(ai.sent) == 1
    system, messages = ai.sent[0]
    raus = system + messages[0]["content"]
    for geheim in ("Sonnenschein", "Karl", "brief-von-karl", "kassenzettel", "Fritz Beispiel"):
        assert geheim not in raus
    s = stapel.laden(ws.root, s.id)
    b = s.dok(bon)
    assert b.quelle == "ki" and b.felder["gross"] == "48,99" and b.felder["category"] == "gwg"
    n = s.dok(notiz)
    assert n.ziel == "rechnung" and n.fehlt
    # Platzhalter kommen hier wieder zu Namen
    assert "Moderation für Karl Sonnenschein" in page.text
    assert "Fahrtkostenabrechnung" in page.text
    # Ein Audit-Eintrag pro KI-Anfrage, ohne Text
    from frits import store

    rows = store.connect(ws.db).execute("SELECT model FROM audit").fetchall()
    assert [r["model"] for r in rows] == ["claude-opus-5-5"]


def test_ki_schritt_fuer_schritt_und_kaputte_antwort(client, ai, ws):  # noqa: F811
    s, _ = einwerfen(client, {"notiz.txt": NOTIZ})
    s, _ = lesen(client, s)
    ai.reply = lambda content: "Ich habe leider kein JSON für dich."
    r = client.post(
        f"/stapel/{s.id}/ki", data={"token": client.token}, headers={"X-Frits-Leise": "1"}
    )
    assert r.json()["fertig"] is True and "lesbare Antwort" in r.json()["fehler"]
    s = stapel.laden(ws.root, s.id)
    assert s.dok(1).ziel == "rechnung"  # FRITS' eigene Vermutung bleibt
    ai.fail = "Claude meldet: Auf deinem Konto ist kein Guthaben."
    s.ki_gesendet = []
    stapel.speichern(ws.root, s)
    page = post(client, f"/stapel/{s.id}/ki")
    assert "kein Guthaben" in page.text


def test_hochladen_in_paketen_und_lesen_mit_fortschritt(client, ws):  # noqa: F811
    leise = {"X-Frits-Leise": "1"}
    r = client.post(
        "/stapel/neu",
        data={"token": client.token, "pfade": ["Belege/Oktober/bon.txt"]},
        files=[("files", ("bon.txt", BON.encode()))],
        headers=leise,
    )
    sid = r.json()["id"]
    r = client.post(
        f"/stapel/{sid}/dazu",
        data={"token": client.token, "pfade": ["Notizen/notiz.txt"]},
        files=[("files", ("notiz.txt", NOTIZ.encode()))],
        headers=leise,
    )
    assert r.json()["anzahl"] == 2
    s = stapel.laden(ws.root, sid)
    assert [d.name for d in s.docs] == ["Belege/Oktober/bon.txt", "Notizen/notiz.txt"]
    fertig = False
    while not fertig:
        a = client.post(f"/stapel/{sid}/lesen", data={"token": client.token}, headers=leise).json()
        fertig = a["fertig"]
    assert a["gelesen"] == 2
    page = client.get(f"/stapel/{sid}")
    assert "Belege/Oktober/bon.txt" in page.text


def test_zip_wird_sicher_ausgepackt(client, ws):  # noqa: F811
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        z.writestr("Ordner/bon.txt", BON)
        z.writestr("../../ausbruch.txt", NOTIZ)
        z.writestr("__MACOSX/Ordner/._bon.txt", "x")
        z.writestr("Ordner/.DS_Store", "x")
        z.writestr("Ordner/tabelle.xlsx", "x")
    s, page = einwerfen(client, {"Steuer 2025.zip": puffer.getvalue()})
    names = sorted(d.name for d in s.docs)
    assert names == ["Steuer 2025/Ordner/bon.txt", "Steuer 2025/ausbruch.txt"]
    for d in s.docs:
        assert (stapel.ordner(ws.root, s.id) / d.datei).is_file()
    assert (
        not (ws.root / "ausbruch.txt").exists() and not (ws.root.parent / "ausbruch.txt").exists()
    )
    assert s.uebersprungen == ["Steuer 2025/Ordner/tabelle.xlsx"]
    s, page = lesen(client, s)
    assert "kann ich nicht lesen" in page.text


def test_doppelte_datei_wird_erkannt(client, ws):  # noqa: F811
    from frits import store
    from frits.pipeline import file_hash

    (ws.done / "alt.txt").write_text(BON, encoding="utf-8")
    store.add_job(store.connect(ws.db), file_hash(ws.done / "alt.txt"), "alt.txt", "receipt")
    s, _ = einwerfen(client, {"bon-nochmal.txt": BON})
    s, page = lesen(client, s)
    d = s.docs[0]
    assert d.doppelt and d.ziel == "beiseite"
    assert "Kenne ich schon" in page.text


def test_was_fehlt_und_die_quest_passt_sich_an(client, ws):  # noqa: F811
    fragebogen(client, teil1={"name": "Fritz"}, teil2={"m_freiberuflich_0": "1"})
    c = quest.ensure_schema(__import__("frits.store", fromlist=["connect"]).connect(ws.db))
    vorher = quest.stuecke(c, 2025)
    assert vorher
    s, _ = einwerfen(client, {"nk.txt": NEBENKOSTEN, "bescheid-2024.txt": BESCHEID})
    s, page = lesen(client, s)
    nk = s.dok(nr_von(s, "nk.txt"))
    assert nk.ziel == "steuer" and nk.provinz == "gehoeft"
    # Vermietung ist in der Quest noch aus: FRITS schlägt vor, sie einzuschalten
    assert "Deine Steuer-Quest wächst mit" in page.text and "Vermietete Wohnung" in page.text
    assert "/steuer/2025/fragebogen" in page.text
    # Offene Pflichtkarten werden genannt
    assert "offene Karten" in page.text
    # Der Bescheid geht mit Kartenvorschlag in den Eingangskorb der Quest
    bescheid = s.dok(nr_von(s, "bescheid-2024.txt"))
    assert bescheid.ziel == "steuer"
    karte = bescheid.karte or next(st["id"] for st in vorher if st["gebiet"] == "rathaus")
    page = alles_uebernehmen(client, s, **{f"karte_{bescheid.nr}": str(karte)})
    name = next(st["name"] for st in vorher if st["id"] == karte)
    lager = [p.name for p in quest.lager_dateien(ws.root)]
    datei = next(n for n in lager if n.endswith("bescheid-2024.txt"))
    assert datei.startswith(name)
    # Der Korb der Quest schlägt genau diese Karte vor ("✓ Ja, passt")
    assert spiel.ahnung(datei, quest.stuecke(c, 2025)).stueck["id"] == karte


def test_schliessen_loescht_nichts(client, ws):  # noqa: F811
    s, _ = einwerfen(client, {"irgendwas.txt": "Einkaufsliste: Milch, Brot"})
    s, _ = lesen(client, s)
    page = post(client, f"/stapel/{s.id}/schliessen")
    assert "Nichts ist gelöscht" in page.text
    assert (stapel.wurzel(ws.root) / "beiseite gelegt" / "irgendwas.txt").is_file()
    assert not stapel.ordner(ws.root, s.id).exists()
    assert client.get(f"/stapel/{s.id}").status_code == 404


def test_detailseite_zeigt_was_die_ki_saehe(client, ws):  # noqa: F811
    s, _ = einwerfen(client, {"notiz.txt": NOTIZ})
    s, _ = lesen(client, s)
    page = client.get(f"/stapel/{s.id}/dok/1")
    assert page.status_code == 200
    assert "Gelesen (bleibt hier)" in page.text and "Das sähe" in page.text
    assert client.get(f"/stapel/{s.id}/datei/1").text.startswith("Moderation")
    assert client.get(f"/stapel/{s.id}/datei/9").status_code == 404
    assert client.get("/stapel/../../etc").status_code == 404


def test_vermuten_ohne_web():
    d = stapel.Dok(1, "x.txt", "x.txt", "0", 1, art="text")
    assert (
        stapel.vermuten(
            d,
            "Rechnung Nr. 5 ... IBAN DE89 3704 0044 0532 0130 00",
            eigene_iban="DE89370400440532013000",
        ).ziel
        == "beiseite"
    )
    assert stapel.vermuten(d, "Lohnsteuerbescheinigung 2025 Arbeitgeber").ziel == "steuer"
    assert stapel.vermuten(d, "Liebe Grüße aus dem Urlaub").ziel == "beiseite"

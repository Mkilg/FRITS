"""Die Steuer-Quest: Fragebogen, Sammeln, Drache, Beraterin-Mail und Schatztruhe."""

from datetime import date
from decimal import Decimal

import pytest
import pyzipper
from fastapi.testclient import TestClient

from frits import store, system
from frits.settings import Settings, Workspace
from frits.steuer import beraterin, quest, spiel, truhe
from frits.steuer.katalog import Profil, katalog, reich
from frits.web.app import create_app

MARIE = Profil(
    jahr=2025,
    selbststaendig=True,
    gesellschaft=True,
    wohnungen=2,
    depot_ausland=True,
    name="Marie Beispiel",
    beraterin_email="beraterin@example.com",
)

MAIL = """Liebe Marie,
für die Erklärung 2025 bräuchte ich noch:
- die Versicherungsnachweise für beide Wohnungen
- eine Übersicht der Dividenden aus deinem US-Depot
- die Anlage zum geänderten GbR-Vertrag
- deinen Bausparvertrag
Viele Grüße"""


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path / "FRITS").ensure()


@pytest.fixture
def conn(ws):
    return quest.ensure_schema(store.connect(ws.db))


def datei(tmp_path, name, inhalt):
    path = tmp_path / name
    path.write_text(inhalt, encoding="utf-8")
    return path


# ---------- Katalog und Fragebogen ----------


def test_fragebogen_schaltet_provinzen_frei():
    ids = [g.id for g in reich(MARIE)]
    assert ids == ["werkstatt", "gilde", "gehoeft-1", "gehoeft-2", "hafen", "burg"]


def test_burg_ist_immer_dabei():
    assert [g.id for g in reich(Profil(jahr=2025))] == ["burg"]


def test_katalog_ist_vollstaendig():
    for provinz in katalog().values():
        assert provinz.stuecke, provinz.key
        assert len({s.key for s in provinz.stuecke}) == len(provinz.stuecke)


def test_profil_speichern_und_laden(tmp_path):
    MARIE.save(tmp_path / "f.toml")
    assert Profil.load(tmp_path / "f.toml") == MARIE


# ---------- Sammeln ----------


def test_start_legt_sammelstuecke_an_und_ist_wiederholbar(conn):
    quest.start(conn, MARIE)
    anzahl = len(quest.stuecke(conn, 2025))
    quest.start(conn, MARIE)
    assert len(quest.stuecke(conn, 2025)) == anzahl
    assert len(quest.stuecke(conn, 2025, "gehoeft-2")) == 6


def test_weniger_wohnungen_raeumt_leere_provinzen_weg(conn, ws, tmp_path):
    quest.start(conn, MARIE)
    s = quest.stuecke(conn, 2025, "gehoeft-1")[0]
    quest.sammeln(conn, ws.root, s["id"], datei(tmp_path, "miete.pdf", "x"))
    quest.start(conn, Profil(jahr=2025, wohnungen=0))
    gebiete = {s["gebiet"] for s in quest.stuecke(conn, 2025)}
    assert "gehoeft-2" not in gebiete
    assert "gehoeft-1" in gebiete  # da liegt schon eine Datei, die geht nicht verloren


def test_sammeln_benennt_um_und_erkennt_doppelte(conn, ws, tmp_path):
    quest.start(conn, MARIE)
    s = next(s for s in quest.stuecke(conn, 2025, "gehoeft-2") if s["key"] == "grundsteuer")
    fund = quest.sammeln(conn, ws.root, s["id"], datei(tmp_path, "scan 17.PDF", "bescheid"))
    assert fund.neu
    assert fund.pfad.name == "2025_Gehoeft-2_Grundsteuerbescheid.pdf"
    assert fund.stueck["status"] == "gesammelt"
    zweimal = quest.sammeln(conn, ws.root, s["id"], datei(tmp_path, "nochmal.pdf", "bescheid"))
    assert not zweimal.neu
    assert len(quest.dateien(conn, 2025)) == 1


def test_datei_zurueck_ins_lager(conn, ws, tmp_path):
    quest.start(conn, MARIE)
    s = quest.stuecke(conn, 2025, "burg")[0]
    quest.sammeln(conn, ws.root, s["id"], datei(tmp_path, "kasse.pdf", "kv"))
    d = quest.dateien(conn, 2025)[0]
    quest.entferne_datei(conn, d["id"])
    assert quest.stueck(conn, s["id"])["status"] == "offen"
    assert [p.name for p in quest.lager_dateien(ws.root)] == ["kasse.pdf"]


def test_vergleich_mit_dem_vorjahr(conn, ws, tmp_path):
    vorjahr = Profil(jahr=2024, wohnungen=1)
    quest.start(conn, vorjahr)
    alt = quest.stuecke(conn, 2024, "gehoeft-1")
    quest.sammeln(conn, ws.root, alt[0]["id"], datei(tmp_path, "a.pdf", "a"))
    quest.sammeln(conn, ws.root, alt[2]["id"], datei(tmp_path, "b.pdf", "b"))
    quest.start(conn, Profil(jahr=2025, wohnungen=1))
    vergleich = quest.vergleich(conn, 2025)
    assert [v["jetzt"] for v in vergleich] == ["offen", "offen"]
    quest.setze_status(conn, vergleich[0]["stueck_id"], "gibts_nicht")
    assert [v["jetzt"] for v in quest.vergleich(conn, 2025)] == ["gibts_nicht", "offen"]


def test_eigene_stuecke_vom_vorjahr_kommen_wieder(conn, ws, tmp_path):
    quest.start(conn, Profil(jahr=2024))
    sid = quest.neues_stueck(conn, 2024, "burg", "Bausparvertrag")
    quest.sammeln(conn, ws.root, sid, datei(tmp_path, "bauspar.pdf", "b"))
    quest.start(conn, Profil(jahr=2025))
    neu = [s for s in quest.stuecke(conn, 2025) if s["name"] == "Bausparvertrag"]
    assert neu and neu[0]["herkunft"] == "vorjahr"


# ---------- Spielregeln ----------


def stuecke_mit(*status):
    return [
        {
            "id": i,
            "gebiet": "burg",
            "status": s,
            "wunsch": 0,
            "dateien": 1 if s == "gesammelt" else 0,
        }
        for i, s in enumerate(status)
    ]


def test_stufen_wachsen_und_bluehen_erst_wenn_alles_da_ist():
    assert spiel.stand(stuecke_mit("offen", "offen", "offen")).stufe == 0
    assert spiel.stand(stuecke_mit("gesammelt", "offen", "offen")).stufe == 1
    assert spiel.stand(stuecke_mit("gesammelt", "fragen", "offen")).stufe == 2
    voll = spiel.stand(stuecke_mit("gesammelt", "gibts_nicht", "fragen"))
    assert voll.stufe == 3 and voll.siegel


def test_zeitalter():
    assert spiel.zeitalter(0.1)[1] == "Zeitalter des Chaos"
    assert spiel.zeitalter(0.5)[1] == "Zeitalter der Ordnung"
    assert spiel.zeitalter(1.0)[1] == "Goldenes Zeitalter"
    assert spiel.naechstes_zeitalter(0.3) == ("Zeitalter der Ordnung", 10)


def test_drache_ist_schwaecher_je_mehr_gesammelt_wurde():
    viel = spiel.drache(stuecke_mit("gesammelt", "gesammelt", "gesammelt", "offen"))
    wenig = spiel.drache(stuecke_mit("gesammelt", "offen", "offen", "offen"))
    assert viel.lp == 1 and wenig.lp == 3
    assert spiel.drache(stuecke_mit("gesammelt", "fragen")).besiegt


def test_drache_wirft_wuensche_der_beraterin_zuerst():
    karten = stuecke_mit("offen", "offen")
    karten[1]["wunsch"] = 1
    assert spiel.drache(karten).karte["id"] == 1


def test_combo_und_kritische_treffer():
    combo, ruf = spiel.treffer(0, kritisch=False)
    assert (combo, ruf) == (1, "Treffer!")
    combo, ruf = spiel.treffer(combo, kritisch=True)
    assert combo == 3 and ruf.startswith("Kritischer Treffer!")


def test_erfolge():
    stuecke = stuecke_mit("gesammelt", "fragen")
    staende = spiel.provinzen(reich(Profil(jahr=2025)), stuecke)
    erreicht = {e.key for e in spiel.erfolge(stuecke, staende) if e.erreicht}
    assert {"erster-fund", "mutig-gefragt", "drachenbaendigerin"} <= erreicht
    assert "brieftaube" not in erreicht


# ---------- Mail der Beraterin ----------


def test_beraterin_mail_wird_zu_auftraegen(conn):
    quest.start(conn, MARIE)
    wuensche = beraterin.zuordnen(MAIL, reich(MARIE), quest.stuecke(conn, 2025))
    assert len(wuensche) == 4
    versicherung, dividenden, vertrag, bauspar = wuensche
    assert versicherung.gebiete == ("gehoeft-1", "gehoeft-2")
    assert dividenden.gebiete == ("hafen",)
    assert dividenden.stueck_id is not None  # passt zur "Dividenden-Liste"
    assert vertrag.gebiete == ("gilde",)
    assert vertrag.stueck_id is not None  # "Änderungen am Gesellschaftsvertrag"
    assert bauspar.gebiete == ("burg",)  # unbekannt: landet in der Burg


def test_beraterin_ohne_aufzaehlung():
    text = "Hallo Marie, mir fehlt noch der Grundsteuerbescheid für Wohnung 2. Danke!"
    assert beraterin.wuensche_aus_text(text) == [
        "mir fehlt noch der Grundsteuerbescheid für Wohnung 2"
    ]
    w = beraterin.zuordnen(text, reich(MARIE), [])
    assert w[0].gebiete == ("gehoeft-2",)


# ---------- Schatztruhe ----------


def test_passwort_ist_lang_und_zufaellig():
    a, b = truhe.passwort(), truhe.passwort()
    assert a != b and len(a.split("-")) == 6


def test_truhe_ist_verschluesselt_und_geordnet(conn, ws, tmp_path):
    quest.start(conn, MARIE)
    s = quest.stuecke(conn, 2025, "hafen")[0]
    quest.sammeln(conn, ws.root, s["id"], datei(tmp_path, "jahr.pdf", "depot"))
    quest.setze_status(
        conn, quest.stuecke(conn, 2025, "burg")[0]["id"], "fragen", "Brauchst du das?"
    )
    ziel = truhe.packen(
        tmp_path / "out" / "t.zip",
        MARIE,
        reich(MARIE),
        quest.stuecke(conn, 2025),
        quest.dateien(conn, 2025),
        "Wiese-Anker-42",
    )
    with pyzipper.AESZipFile(ziel) as zf:
        namen = zf.namelist()
        with pytest.raises(RuntimeError):
            zf.read(namen[0])  # ohne Passwort geht nichts
        zf.setpassword(b"Wiese-Anker-42")
        inhalt = zf.read("00 Inhaltsverzeichnis.txt").decode()
    assert "05 Depot im Ausland/2025_Hafen_Jahresuebersicht-des-Depots.pdf" in namen
    assert "Frage: Brauchst du das?" in inhalt


def test_mail_nennt_fragen_und_luecken_aber_kein_passwort(conn):
    quest.start(conn, MARIE)
    alle = quest.stuecke(conn, 2025)
    quest.setze_status(conn, alle[0]["id"], "fragen", "Reicht der Export?")
    quest.setze_status(conn, alle[1]["id"], "gibts_nicht")
    betreff, text = truhe.mail(MARIE, reich(MARIE), quest.stuecke(conn, 2025))
    assert betreff == "Steuerunterlagen 2025"
    assert "Selbstständig: Reicht der Export?" in text
    assert "Gibt es dieses Jahr nicht: Belege und Quittungen." in text
    assert "Noch nicht gefunden habe ich:" in text
    assert "Passwort schicke ich dir getrennt" in text


# ---------- Die Seiten ----------


@pytest.fixture
def client(ws, monkeypatch):
    Settings(name="Marie Beispiel", hourly_rate=Decimal(1), setup_done=True).save(ws.settings_file)
    sent = []
    monkeypatch.setattr(
        system,
        "compose_email",
        lambda to, subject, body, attachment: (
            sent.append((to, subject, attachment)) or system.Composed(True, "")
        ),
    )
    monkeypatch.setattr(system, "open_path", lambda path: True)
    app = create_app(ws, today=lambda: date(2026, 11, 1))
    with TestClient(app) as c:
        c.token = app.state.token
        c.sent = sent
        yield c


def post(client, url, files=None, **data):
    return client.post(
        url, data={"token": client.token, **data}, files=files, follow_redirects=True
    )


def test_ganze_quest_im_browser(client, ws):
    # Ohne Fragebogen geht es zum Fragebogen, für das Vorjahr
    page = client.get("/steuer", follow_redirects=True)
    assert "Gründe dein Reich für 2025" in page.text

    page = client.post(
        "/steuer/2025/fragebogen",
        data={
            "token": client.token,
            "ja": ["vermietung"],
            "wohnungen": "1",
            "beraterin_email": "b@example.com",
            "name": "Marie",
        },
        follow_redirects=True,
    )
    assert "Dein Reich steht: 2 Provinzen" in page.text
    assert "Gehöft 1" in page.text

    conn = store.connect(ws.db)
    stuecke = quest.stuecke(conn, 2025)
    erstes = stuecke[0]
    page = post(
        client,
        f"/steuer/2025/stueck/{erstes['id']}/datei",
        files={"datei": ("miete.pdf", b"mieten", "application/pdf")},
    )
    assert "+1 Beleg!" in page.text
    assert "Erfolg freigeschaltet: Erster Fund" in page.text

    # Etwas ins Lager legen und zuordnen
    (quest.lager(ws.root) / "kasse.pdf").write_bytes(b"kv")
    burg = next(s for s in stuecke if s["gebiet"] == "burg")
    page = post(client, "/steuer/2025/lager/zuordnen", name="kasse.pdf", stueck_id=str(burg["id"]))
    assert "kasse.pdf → Kranken- und Pflegeversicherung" in page.text

    page = post(
        client, "/steuer/2025/beraterin", text="- die Gebäudeversicherung\n- Bausparvertrag"
    )
    assert "2 Aufträge" in page.text

    # Zur Sieg-Seite geht es nur über den Drachen
    assert "Papierdrache" in client.get("/steuer/2025/sieg", follow_redirects=True).text
    for _ in range(40):
        karte = spiel.drache(quest.stuecke(conn, 2025)).karte
        if karte is None:
            break
        page = post(
            client, "/steuer/2025/drache/schlag", stueck_id=str(karte["id"]), aktion="gibts_nicht"
        )
    assert "SIEG!" in page.text
    assert "Goldenes Zeitalter erreicht!" in page.text

    page = post(client, "/steuer/2025/truhe")
    assert "Passwort:" in page.text
    zip_pfad = quest.ordner(ws.root, 2025) / "Steuer-2025-Marie.zip"
    assert zip_pfad.exists()

    page = post(client, "/steuer/2025/mail", betreff="Unterlagen", text="Hallo")
    assert client.sent == [("b@example.com", "Unterlagen", zip_pfad)]


def test_notausgang(client):
    post(client, "/steuer/2025/fragebogen", ja="selbststaendig")
    page = post(client, "/steuer/2025/drache/notausgang")
    assert "Bereit zum Senden" in page.text


def test_steuer_knoepfe_brauchen_das_token(client):
    response = client.post("/steuer/2025/fragebogen", data={"token": "falsch"})
    assert response.status_code == 403


def test_seiten_rendern(client):
    post(client, "/steuer/2025/fragebogen", ja=["selbststaendig", "gesellschaft"])
    for url in (
        "/steuer/2025",
        "/steuer/2025/gebiet/gilde",
        "/steuer/2025/beraterin",
        "/steuer/2025/drache",
        "/steuer/2025/fragebogen",
    ):
        assert client.get(url).status_code == 200, url
    assert client.get("/steuer/2025/gebiet/gibtsnicht").status_code == 404

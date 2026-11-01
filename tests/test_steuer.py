"""Die Steuer-Quest: Fragebogen, Sammeln, Drache, Beraterin-Mail, FRITS-Ernte und Schatztruhe."""

import sqlite3
from datetime import date
from decimal import Decimal

import pytest
import pyzipper
from fastapi.testclient import TestClient

from frits import store, system
from frits.expenses import Expense, add_expense
from frits.settings import Settings, Workspace
from frits.steuer import beraterin, ernte, quest, spiel, truhe
from frits.steuer.katalog import HAKEN, Profil, ist_kern, katalog, reich
from frits.web.app import create_app
from frits.web.steuer import inselplan

MARIE = Profil(
    jahr=2025,
    name="Marie Beispiel",
    beraterin_email="beraterin@example.com",
    mengen={"freiberuflich": [1, 0], "gesellschaft": [1, 0], "wohnungen": [2, 0]}
    | {"depots_ausland": [1, 0]},
    haken=["vorsorge"],
)

PAAR = Profil(
    jahr=2025,
    name="Alex",
    partner="Sam",
    familienstand="verheiratet",
    zusammen=True,
    kinder=1,
    mengen={"angestellt": [1, 2], "wohnungen": [1, 0]},
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


def ids(profil):
    return [g.id for g in reich(profil)]


def test_fragebogen_schaltet_provinzen_frei():
    assert ids(MARIE) == [
        "rathaus",
        "werkstatt-1",
        "gilde-1",
        "gehoeft-1",
        "gehoeft-2",
        "hafen-1",
        "burg",
    ]
    titel = [g.titel for g in reich(MARIE)]
    assert titel[3:6] == ["Gehöft 1", "Gehöft 2", "Überseehafen"]  # Nummer nur, wenn mehrere


def test_ohne_einnahmen_bleibt_nur_das_rathaus():
    assert ids(Profil(jahr=2025)) == ["rathaus"]


def test_nur_gehalt():
    assert ids(Profil(jahr=2025, mengen={"angestellt": [2, 0]})) == [
        "rathaus",
        "kaserne-1",
        "kaserne-2",
    ]


def test_gemeinsame_erklaerung_gibt_jeder_person_ein_wappen():
    gebiete = reich(PAAR)
    assert [(g.id, g.titel) for g in gebiete] == [
        ("rathaus", "Rathaus"),
        ("kaserne-1", "Kaserne (Alex)"),
        ("kaserne-p2-1", "Kaserne 1 (Sam)"),
        ("kaserne-p2-2", "Kaserne 2 (Sam)"),
        ("gehoeft-1", "Gehöft"),
        ("dorf", "Dorf"),
    ]
    assert gebiete[2].person == 1 and gebiete[2].wappen == "Sam"
    assert gebiete[4].wappen == ""  # die Wohnung gehört beiden


def test_partner_zaehlt_nur_bei_gemeinsamer_erklaerung():
    allein = Profil(jahr=2025, mengen={"angestellt": [1, 2]})
    assert ids(allein) == ["rathaus", "kaserne-1"]


def test_haken_schalten_einzelne_stuecke_frei():
    def keys(profil, gebiet_id):
        return [s.key for s in next(g for g in reich(profil) if g.id == gebiet_id).stuecke]

    selbst = Profil(jahr=2025, mengen={"freiberuflich": [1, 0], "angestellt": [1, 0]})
    assert "umsatzsteuer" not in keys(selbst, "werkstatt-1")
    assert "fahrten" not in keys(selbst, "kaserne-1")
    selbst.umsatzsteuer = "quartal"
    selbst.haken = ["arbeit"]
    assert "umsatzsteuer" in keys(selbst, "werkstatt-1")
    assert "fahrten" in keys(selbst, "kaserne-1")
    assert "burg" not in ids(Profil(jahr=2025))
    assert "burg" in ids(Profil(jahr=2025, haken=["spenden"]))


def test_katalog_ist_vollstaendig():
    erlaubt = set(HAKEN) | {"umsatzsteuer"}
    for provinz in katalog().values():
        assert provinz.stuecke, provinz.key
        assert len({s.key for s in provinz.stuecke}) == len(provinz.stuecke)
        for s in provinz.stuecke:
            assert s.art in ("pflicht", "bonus"), s.key
            assert not s.wenn or s.wenn in erlaubt, s.wenn


def test_profil_speichern_und_laden(tmp_path):
    PAAR.save(tmp_path / "f.toml")
    assert Profil.load(tmp_path / "f.toml") == PAAR


def test_inselplan_passt_fuer_jede_groesse():
    for n in range(21):
        lagen = inselplan(n)
        assert len(lagen) == n
        assert len(set(lagen)) == n
        assert all(5 <= x <= 95 and 5 <= y <= 90 for x, y in lagen)


# ---------- Sammeln ----------


def test_start_legt_sammelstuecke_an_und_ist_wiederholbar(conn):
    quest.start(conn, MARIE)
    anzahl = len(quest.stuecke(conn, 2025))
    quest.start(conn, MARIE)
    assert len(quest.stuecke(conn, 2025)) == anzahl
    assert len(quest.stuecke(conn, 2025, "gehoeft-2")) == 8


def test_weniger_wohnungen_raeumt_leere_provinzen_weg(conn, ws, tmp_path):
    quest.start(conn, MARIE)
    s = quest.stuecke(conn, 2025, "gehoeft-1")[0]
    quest.sammeln(conn, ws.root, s["id"], datei(tmp_path, "miete.pdf", "x"))
    quest.start(conn, Profil(jahr=2025))
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
    vorjahr = Profil(jahr=2024, mengen={"wohnungen": [1, 0]})
    quest.start(conn, vorjahr)
    alt = quest.stuecke(conn, 2024, "gehoeft-1")
    quest.sammeln(conn, ws.root, alt[0]["id"], datei(tmp_path, "a.pdf", "a"))
    quest.sammeln(conn, ws.root, alt[2]["id"], datei(tmp_path, "b.pdf", "b"))
    quest.start(conn, Profil(jahr=2025, mengen={"wohnungen": [1, 0]}))
    vergleich = quest.vergleich(conn, 2025)
    assert [v["jetzt"] for v in vergleich] == ["offen", "offen"]
    quest.setze_status(conn, vergleich[0]["stueck_id"], "gibts_nicht")
    assert [v["jetzt"] for v in quest.vergleich(conn, 2025)] == ["gibts_nicht", "offen"]


def test_eigene_stuecke_vom_vorjahr_kommen_wieder(conn, ws, tmp_path):
    quest.start(conn, Profil(jahr=2024))
    sid = quest.neues_stueck(conn, 2024, "rathaus", "Bausparvertrag")
    quest.sammeln(conn, ws.root, sid, datei(tmp_path, "bauspar.pdf", "b"))
    quest.start(conn, Profil(jahr=2025))
    neu = [s for s in quest.stuecke(conn, 2025) if s["name"] == "Bausparvertrag"]
    assert neu and neu[0]["herkunft"] == "vorjahr"


# ---------- Spielregeln ----------


def stuecke_mit(*status):
    return [
        {
            "id": i,
            "gebiet": "rathaus",
            "status": s,
            "art": "pflicht",
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


def test_bonus_schaetze_blockieren_nie_den_sieg():
    stuecke = stuecke_mit("gesammelt", "offen", "gesammelt")
    stuecke[1]["art"] = "bonus"
    stuecke[2]["art"] = "bonus"
    assert spiel.drache(stuecke).besiegt
    assert spiel.gesamtstand(stuecke).prozent == 100
    provinz = spiel.provinzen(reich(Profil(jahr=2025)), stuecke)[0]
    assert provinz.stand.siegel and provinz.schaetze == 1
    assert spiel.ressourcen(stuecke, [provinz]).schaetze == 1


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
    assert dividenden.gebiete == ("hafen-1",)
    assert dividenden.stueck_id is not None  # passt zur "Dividenden-Liste"
    assert vertrag.gebiete == ("gilde-1",)
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
    s = quest.stuecke(conn, 2025, "hafen-1")[0]
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
    assert "06 Depot im Ausland/2025_Hafen-1_Jahresuebersicht-des-Depots.pdf" in namen
    assert "Frage: Brauchst du das?" in inhalt
    assert truhe.verschluesselt(ziel)


def test_truhe_ohne_passwort_oeffnet_sich_ueberall(conn, ws, tmp_path):
    import zipfile

    quest.start(conn, MARIE)
    s = quest.stuecke(conn, 2025, "hafen-1")[0]
    quest.sammeln(conn, ws.root, s["id"], datei(tmp_path, "jahr.pdf", "depot"))
    ziel = truhe.packen(
        tmp_path / "t.zip",
        MARIE,
        reich(MARIE),
        quest.stuecke(conn, 2025),
        quest.dateien(conn, 2025),
        None,
    )
    with zipfile.ZipFile(ziel) as zf:  # das normale Python-Zip, ganz ohne Passwort
        assert "Steuerunterlagen 2025" in zf.read("00 Inhaltsverzeichnis.txt").decode()
    assert not truhe.verschluesselt(ziel)
    _, text = truhe.mail(MARIE, reich(MARIE), quest.stuecke(conn, 2025), mit_passwort=False)
    assert "Passwort" not in text and "7-Zip" not in text


def test_mail_nennt_fragen_und_luecken_aber_kein_passwort(conn):
    quest.start(conn, MARIE)
    alle = quest.stuecke(conn, 2025)
    quest.setze_status(conn, alle[0]["id"], "fragen", "Reicht der Export?")
    quest.setze_status(conn, alle[1]["id"], "gibts_nicht")
    betreff, text = truhe.mail(MARIE, reich(MARIE), quest.stuecke(conn, 2025))
    assert betreff == "Steuerunterlagen 2025"
    assert "Persönliches und Finanzamt: Reicht der Export?" in text
    assert "Gibt es dieses Jahr nicht: Letzter Steuerbescheid." in text
    assert "Noch nicht gefunden habe ich:" in text
    assert "Passwort schicke ich dir getrennt" in text


# ---------- FRITS-Ernte: Rechnungen, Belege und EÜR aus dem normalen FRITS ----------


def rechnung(ws, nummer, ausgestellt, bezahlt=None):
    pdf = ws.invoices / f"{nummer}.pdf"
    pdf.write_bytes(f"%PDF {nummer}".encode())
    store.connect(ws.db).close()  # legt die FRITS-Tabellen an
    db = sqlite3.connect(ws.db)
    db.execute(
        "INSERT INTO invoices (number, customer, issue_date, paid_date, net, vat, gross, pdf_path)"
        " VALUES (?, 'Kunde', ?, ?, '100.00', '0', '100.00', ?)",
        (nummer, ausgestellt, bezahlt, str(pdf)),
    )
    db.commit()
    db.close()


def ausgabe(ws, name, tag):
    (ws.done / name).write_bytes(name.encode())
    add_expense(
        ws.db,
        Expense(
            date=tag,
            vendor="Laden",
            description="Papier",
            gross=Decimal("11.90"),
            vat_rate=Decimal("0.19"),
            category="waren",
            source_file=name,
        ),
    )


@pytest.fixture
def frits_jahr(ws, conn):
    rechnung(ws, "RE-2025-0001", "2025-03-01", "2025-03-20")
    rechnung(ws, "RE-2024-0009", "2024-12-20", "2025-01-10")  # bezahlt 2025: zählt auch
    rechnung(ws, "RE-2024-0001", "2024-05-01", "2024-05-10")  # anderes Jahr
    ausgabe(ws, "quittung.jpg", date(2025, 2, 3))
    ausgabe(ws, "alt.jpg", date(2024, 2, 3))
    return ws


def test_ernte_findet_was_frits_im_jahr_hat(frits_jahr, conn):
    funde = ernte.finden(conn, frits_jahr, 2025, "kleinunternehmer")
    assert [p.name for p in funde.rechnungen] == ["RE-2024-0009.pdf", "RE-2025-0001.pdf"]
    assert [p.name for p in funde.belege] == ["quittung.jpg"]
    assert funde.euer is not None and funde.gewinn == "188,10 €"
    assert funde.neu == {"rechnungen": 2, "belege": 1, "euer": 1}
    assert ernte.hat_rechnungen(frits_jahr, 2025)
    assert not ernte.hat_rechnungen(frits_jahr, 2023)


def test_ernte_einbringen_kopiert_und_ist_wiederholbar(frits_jahr, conn):
    quest.start(conn, MARIE)
    assert [g.id for g in ernte.ziele(reich(MARIE))] == ["werkstatt-1", "gilde-1"]
    neu = ernte.einbringen(conn, frits_jahr, 2025, "werkstatt-1", "kleinunternehmer")
    assert neu == {"rechnungen": 2, "belege": 1, "euer": 1}
    werkstatt = {s["key"]: s for s in quest.stuecke(conn, 2025, "werkstatt-1")}
    for key in ("ausgangsrechnungen", "belege", "euer"):
        assert werkstatt[key]["status"] == "gesammelt", key
    assert (frits_jahr.invoices / "RE-2025-0001.pdf").exists()  # kopiert, nicht verschoben
    nochmal = ernte.einbringen(conn, frits_jahr, 2025, "werkstatt-1", "kleinunternehmer")
    assert nochmal == {"rechnungen": 0, "belege": 0, "euer": 0}

    # Eine neue Ausgabe: neuer Beleg, und die EÜR wird ersetzt statt verdoppelt
    ausgabe(frits_jahr, "tinte.jpg", date(2025, 6, 1))
    neu = ernte.einbringen(conn, frits_jahr, 2025, "werkstatt-1", "kleinunternehmer")
    assert neu == {"rechnungen": 0, "belege": 1, "euer": 1}
    euer_dateien = quest.dateien(conn, 2025, werkstatt["euer"]["id"])
    assert sorted(d["original"] for d in euer_dateien) == ["EUER_2025.csv", "EUER_2025.pdf"]


def test_ernte_legt_fehlende_faecher_an(frits_jahr, conn):
    quest.start(conn, MARIE)
    ernte.einbringen(conn, frits_jahr, 2025, "gilde-1", "kleinunternehmer")
    namen = {s["name"]: s["status"] for s in quest.stuecke(conn, 2025, "gilde-1")}
    assert namen["Rechnungen der Gesellschaft"] == "gesammelt"
    assert namen["EÜR-Übersicht"] == "gesammelt"


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


def fragebogen(client, jahr=2025, teil1=None, teil2=None, teil3=None):
    """Füllt die drei Teile des Fragebogens aus und gibt die letzte Seite zurück."""
    url = f"/steuer/{jahr}/fragebogen"
    post(client, url, teil="1", **(teil1 or {}))
    post(client, url, teil="2", **(teil2 or {}))
    return post(client, url, teil="3", **(teil3 or {}))


def test_ganze_quest_im_browser(client, ws):
    # Ohne Fragebogen beginnt es mit dem Intro, für das Vorjahr; von dort zum Fragebogen
    page = client.get("/steuer", follow_redirects=True)
    assert "Papierdrache" in page.text and "Mein Reich gründen" in page.text
    page = client.get("/steuer/2025/fragebogen")
    assert "Wer lebt in deinem Reich?" in page.text
    assert "Teil 1 von 3" in page.text

    page = post(
        client,
        "/steuer/2025/fragebogen",
        teil="1",
        name="Marie",
        familienstand="ledig",
        beraterin_email="b@example.com",
    )
    assert "Welche Provinzen hat deine Insel?" in page.text
    page = post(client, "/steuer/2025/fragebogen", teil="2", m_wohnungen_0="1")
    assert "Welche Schätze" in page.text
    page = post(client, "/steuer/2025/fragebogen", teil="3", haken="vorsorge")
    assert "Dein Reich steht!" in page.text and "Deine Insel hat 3 Provinzen" in page.text
    assert "Gehöft" in page.text and "Burg" in page.text

    conn = store.connect(ws.db)
    stuecke = quest.stuecke(conn, 2025)
    erstes = stuecke[0]
    page = post(
        client,
        f"/steuer/2025/stueck/{erstes['id']}/datei",
        files={"datei": ("miete.pdf", b"mieten", "application/pdf")},
    )
    assert "+1 Beleg!" in page.text
    assert "Neuer Orden: Erster Fund" in page.text

    # Etwas in den Eingangskorb legen: es erscheint als Zettel und lässt sich einsortieren
    (quest.lager(ws.root) / "kasse.pdf").write_bytes(b"kv")
    assert 'data-name="kasse.pdf"' in client.get("/steuer/2025").text
    burg = next(s for s in stuecke if s["gebiet"] == "burg")
    page = post(client, "/steuer/2025/lager/zuordnen", name="kasse.pdf", stueck_id=str(burg["id"]))
    assert "kasse.pdf → Kranken- und Pflegeversicherung" in page.text

    page = post(
        client, "/steuer/2025/beraterin", text="- die Gebäudeversicherung\n- Bausparvertrag"
    )
    assert "2 Aufträge" in page.text

    # Zur Sieg-Seite geht es nur über den Drachen
    assert "Papierdrache" in client.get("/steuer/2025/sieg", follow_redirects=True).text

    # Der Drache schläft, bis das Reich im Zeitalter der Ordnung ist (40 %)
    assert "Der Papierdrache schläft noch" in client.get("/steuer/2025/drache").text
    offen = [s for s in spiel.pflicht(quest.stuecke(conn, 2025)) if s["status"] == "offen"]
    karte = spiel.drache(quest.stuecke(conn, 2025)).karte
    page = post(
        client, "/steuer/2025/drache/schlag", stueck_id=str(karte["id"]), aktion="gibts_nicht"
    )
    assert "schläft noch" in page.text
    while not spiel.drache_wach(quest.stuecke(conn, 2025)):
        quest.setze_status(conn, offen.pop()["id"], "gesammelt")
    wach = client.get("/steuer/2025/drache").text
    assert "schläft noch" not in wach and 'id="karte"' in wach

    page = None
    for _ in range(40):
        karte = spiel.drache(quest.stuecke(conn, 2025)).karte
        if karte is None:
            break
        page = post(
            client, "/steuer/2025/drache/schlag", stueck_id=str(karte["id"]), aktion="gibts_nicht"
        )
        if spiel.drache(quest.stuecke(conn, 2025)).lp:
            assert 'data-treffer="treffer"' in page.text  # die Seite wackelt nach dem Treffer
    # Der letzte Treffer: Der Drache faltet sich auf seiner Seite, dann geht es zur Truhe
    assert str(page.url).endswith("/drache")
    assert "sq-arena-sieg finale" in page.text and 'data-spaet="1900"' in page.text
    assert "Silber-Sieg!" in page.text
    nochmal = client.get("/steuer/2025/drache").text  # der Film läuft nur einmal
    assert "sq-arena-sieg" in nochmal and "sq-arena-sieg finale" not in nochmal
    # Wichtige Unterlagen als "Gibt's nicht": Sieg ja, aber Silber und "Bitte prüfen"
    page = client.get("/steuer/2025/sieg")
    assert "Bitte prüfen" in page.text
    assert 'class="sq-sieg-titel silber"' in page.text

    page = post(client, "/steuer/2025/truhe")
    assert "Passwort:" in page.text
    zip_pfad = quest.ordner(ws.root, 2025) / "Steuer-2025-Marie.zip"
    assert zip_pfad.exists()

    page = post(client, "/steuer/2025/mail", betreff="Unterlagen", text="Hallo")
    assert client.sent == [("b@example.com", "Unterlagen", zip_pfad)]

    # Neu packen, diesmal ohne Passwort: kein Passwort, und die Mail erwähnt keins
    page = post(client, "/steuer/2025/truhe", schloss="ohne")
    assert "gepackt, ohne Passwort" in page.text
    assert "Passwort:" not in page.text
    assert "Passwort schicke ich dir getrennt" not in page.text
    assert not truhe.verschluesselt(zip_pfad)


def test_gemeinsame_erklaerung_im_fragebogen(client, ws):
    page = fragebogen(
        client,
        teil1={
            "name": "Alex",
            "familienstand": "verheiratet",
            "zusammen": "on",
            "partner": "Sam",
            "kinder": "2",
        },
        teil2={"m_angestellt_0": "1", "m_angestellt_1": "1"},
        teil3={"haken": ["kinder_betreuung", "unsinn"]},
    )
    assert "Deine Insel hat 4 Provinzen" in page.text
    profil = Profil.load(quest.profil_datei(ws.root, 2025))
    assert profil.personen == ["Alex", "Sam"]
    assert profil.haken == ["kinder_betreuung"]
    assert [g.id for g in reich(profil)] == ["rathaus", "kaserne-1", "kaserne-p2-1", "dorf"]


def test_ohne_ehe_keine_gemeinsame_erklaerung(client, ws):
    fragebogen(client, teil1={"familienstand": "ledig", "zusammen": "on", "partner": "Sam"})
    profil = Profil.load(quest.profil_datei(ws.root, 2025))
    assert not profil.zusammen and profil.partner == ""


def test_fragebogen_kennt_die_rechnungen_aus_frits(client, ws):
    rechnung(ws, "RE-2025-0001", "2025-03-01")
    page = client.get("/steuer/2025/fragebogen?teil=2")
    assert 'name="m_freiberuflich_0" min="0" max="20" value="1"' in page.text


def test_frits_ernte_im_browser(client, ws):
    rechnung(ws, "RE-2025-0001", "2025-03-01", "2025-03-02")
    fragebogen(client, teil2={"m_freiberuflich_0": "1"})
    page = client.get("/steuer/2025")
    assert "FRITS-Ernte" in page.text and "+1 neu" in page.text
    page = post(client, "/steuer/2025/ernte", gebiet="werkstatt-1")
    assert "FRITS-Ernte eingebracht: 1 Rechnung, die EÜR → Werkstatt" in page.text
    assert "Alles aus FRITS ist schon auf der Insel" in page.text
    page = post(client, "/steuer/2025/ernte", gebiet="gehoeft-1")
    assert "Wähle aus, zu welchem Betrieb" in page.text


def test_notausgang(client):
    fragebogen(client, teil2={"m_freiberuflich_0": "1"})
    page = post(client, "/steuer/2025/drache/notausgang")
    assert "Bereit zum Senden" in page.text


def test_steuer_knoepfe_brauchen_das_token(client):
    response = client.post("/steuer/2025/fragebogen", data={"token": "falsch"})
    assert response.status_code == 403


def test_seiten_rendern(client):
    fragebogen(
        client,
        teil1={"name": "Marie"},
        teil2={"m_freiberuflich_0": "1", "m_gesellschaft_0": "1", "m_wohnungen_0": "2"},
        teil3={"haken": "spenden"},
    )
    for url in (
        "/steuer/2025",
        "/steuer/2025/gebiet/gilde-1",
        "/steuer/2025/gebiet/gehoeft-2",
        "/steuer/2025/beraterin",
        "/steuer/2025/drache",
        "/steuer/2025/fragebogen",
        "/steuer/2025/fragebogen?teil=2",
        "/steuer/2025/fragebogen?teil=3",
    ):
        assert client.get(url).status_code == 200, url
    unbekannt = client.get("/steuer/2025/gebiet/gibtsnicht")
    assert unbekannt.status_code == 200 and "gibt es auf deiner Insel nicht" in unbekannt.text
    karte = client.get("/steuer/2025").text
    assert 'class="sq-insel' in karte and "Drachenhort" in karte


def test_gemalte_bilder_und_leihgaben():
    from frits.web.steuer import provinzbild, wiesenplan

    assert provinzbild("werkstatt", 0) == ("/static/steuer/werkstatt-1.webp", "")
    assert provinzbild("werkstatt", 3)[0] == "/static/steuer/werkstatt-3.webp"
    bild, farbe = provinzbild("rathaus", 2)  # hat noch kein eigenes Bild: leiht sich eins
    assert bild == "/static/steuer/gilde-2.webp" and farbe
    for n in range(21):
        lagen = wiesenplan(n)
        assert len(set(lagen)) == n
        assert all(5 <= x <= 95 and 0 < y < 100 for x, y in lagen)


# ---------- Runde 2: Auftrag, Zwischenspeichern, Abbrechen ----------


def test_auftrag_zeigt_immer_einen_naechsten_schritt(conn, ws):
    quest.start(conn, MARIE)
    alle = quest.stuecke(conn, 2025)
    staende = spiel.provinzen(reich(MARIE), alle)
    a = spiel.auftrag(2025, staende, spiel.drache(alle), 0, alle)
    assert a.art == "provinz" and a.ziel.startswith("/steuer/2025/gebiet/")
    assert "Gold-Siegel" in a.belohnungen and "Orden „Erste Ernte“" in a.belohnungen

    # Angefangene Provinzen gehen vor: dort ist das Ziel am nächsten
    ziel = next(s for s in alle if s["gebiet"] == "gehoeft-2" and s["art"] != "bonus")
    quest.setze_status(conn, ziel["id"], "gibts_nicht")
    alle = quest.stuecke(conn, 2025)
    a = spiel.auftrag(2025, spiel.provinzen(reich(MARIE), alle), spiel.drache(alle), 0, alle)
    assert a.gebiet_id == "gehoeft-2" and a.erledigt == 1

    # Liegen Zettel im Eingangskorb, kommen die zuerst
    a = spiel.auftrag(2025, spiel.provinzen(reich(MARIE), alle), spiel.drache(alle), 3, alle)
    assert a.art == "korb" and "3 Zettel" in a.text

    # Alles erledigt: zur Beute
    for s in alle:
        quest.setze_status(conn, s["id"], "gibts_nicht")
    alle = quest.stuecke(conn, 2025)
    a = spiel.auftrag(2025, spiel.provinzen(reich(MARIE), alle), spiel.drache(alle), 0, alle)
    assert a.art == "sieg" and a.ziel == "/steuer/2025/sieg"


def test_fragebogen_merkt_sich_alles_auch_ohne_weiter(client, ws):
    # Tippen speichert sofort (z. B. vor einem Tab-Wechsel), ohne die Seite zu verlassen
    r = client.post(
        "/steuer/2025/fragebogen",
        data={"token": client.token, "teil": "1", "name": "Fritz", "kinder": "2", "merken": "1"},
    )
    assert r.status_code == 204
    assert 'value="Fritz"' in client.get("/steuer/2025/fragebogen").text
    # Auch nach einem Neustart von FRITS ist der Entwurf noch da
    client.app.state.steuer_entwurf.clear()
    assert 'value="Fritz"' in client.get("/steuer/2025/fragebogen").text
    # Wer im Intro zurückkommt, macht weiter, wo er war
    assert "Weiter, wo ich war" in client.get("/steuer/2025/intro").text


def test_neues_jahr_laesst_sich_abbrechen(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    karte = client.get("/steuer/2025").text
    assert "Neue Quest für 2024" in karte and "Dein Auftrag" in karte
    assert "Burg?" in karte  # die Burg wartet als Nebenquest
    intro = client.get("/steuer/2024/intro").text
    assert "Neue Insel für 2024?" in intro and 'href="/steuer/2025"' in intro
    assert "Zurück zu 2025" in client.get("/steuer/2024/fragebogen").text


def test_siegel_kommt_als_belohnung_mit_naechstem_auftrag(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    page = None
    for s in quest.stuecke(quest.ensure_schema(store.connect(ws.db)), 2025, "rathaus"):
        page = post(
            client,
            f"/steuer/2025/stueck/{s['id']}/status",
            status="gibts_nicht",
            back="/steuer/2025/gebiet/rathaus",
        )
    # Mit dem Rathaus ist das Reich bei 50 %: Der Drache erwacht, das Siegel feiert mit
    assert "data-belohnung" in page.text and "Der Drache erwacht!" in page.text
    assert "Rathaus ist komplett." in page.text and "Stell dich dem Drachen" in page.text
    # Beim Erwachen lockt nichts weg vom Drachen: kein "Nächster Auftrag: Provinz …"
    assert "Nächster Auftrag" not in page.text
    # Ab jetzt ist der Drache der Auftrag, mit Leuchtfeuer auf dem Hort
    karte = client.get("/steuer/2025").text
    assert "Besiege den Papierdrachen" in karte and "sq-hort-rand wach auftragsziel" in karte
    # Stempel zurücknehmen und neu stempeln: kein zweites Fest
    briefe = next(
        s
        for s in quest.stuecke(quest.ensure_schema(store.connect(ws.db)), 2025, "rathaus")
        if s["key"] == "briefe"
    )
    post(client, f"/steuer/2025/stueck/{briefe['id']}/status", status="offen")
    page = post(client, f"/steuer/2025/stueck/{briefe['id']}/status", status="gibts_nicht")
    assert "Rathaus ist wieder komplett." in page.text and "Gold-Siegel!" not in page.text


def test_gibts_nicht_haelt_die_serie_zaehlt_aber_nicht_hoch():
    combo, _ruf = spiel.treffer(3, kritisch=False, zaehlt=False)
    assert combo == 3
    assert spiel.treffer(3, kritisch=False)[0] > 3


def test_wichtige_karten_fragen_bei_gibts_nicht_nach():
    assert ist_kern("rathaus", "steuer-id")
    assert ist_kern("werkstatt-2", "belege")
    assert not ist_kern("rathaus", "briefe")


def test_reine_bonus_provinz_zaehlt_nicht_zu_den_siegeln():
    stuecke = [
        {"gebiet": "burg", "art": "bonus", "status": "gesammelt", "wunsch": 0},
        {"gebiet": "rathaus", "art": "pflicht", "status": "offen", "wunsch": 0},
    ]
    gebiete = [gb for gb in reich(MARIE) if gb.id in ("burg", "rathaus")]
    assert len(gebiete) == 2
    staende = spiel.provinzen(gebiete, stuecke)
    burg = next(p for p in staende if p.gebiet.id == "burg")
    assert burg.nur_bonus and burg.stand.siegel
    assert spiel.ressourcen(stuecke, staende).siegel == 0


def test_journalisten_haken_nur_mit_freiberuf():
    ohne = Profil(jahr=2025, haken=["ksk", "sender"])
    assert not ohne.zeigt_haken("ksk") and not ohne.hat("ksk")
    frei = Profil(jahr=2025, mengen={"freiberuflich": [1, 0]}, haken=["ksk", "sender"])
    assert frei.zeigt_haken("ksk") and frei.hat("sender")
    keys = {(g.id, s.key) for g in reich(frei) for s in g.stuecke}
    assert ("werkstatt-1", "sender") in keys and ("burg", "ksk") in keys
    assert ("werkstatt-1", "vgwort") not in keys  # Haken "verwertung" nicht gesetzt


def test_neuer_katalogtext_kommt_auch_in_alte_quests(conn):
    quest.start(conn, MARIE)
    conn.execute("UPDATE steuer_stuecke SET hinweis = 'alt' WHERE gebiet = 'rathaus'")
    quest.start(conn, MARIE)
    hinweise = {s["hinweis"] for s in quest.stuecke(conn, MARIE.jahr, "rathaus")}
    assert "alt" not in hinweise


def test_dateien_lassen_sich_auf_den_eingangskorb_ziehen(client, ws):
    fragebogen(client, teil1={"name": "Marie"})
    karte = client.get("/steuer/2025").text
    assert "/steuer/2025/lager/hochladen" in karte and "data-dropzone" in karte
    assert client.get("/steuer/2025/lager/anzahl").json() == {"anzahl": 0}
    dateien = [("files", ("bescheid.pdf", b"%PDF-1", "application/pdf"))] * 2
    seite = post(client, "/steuer/2025/lager/hochladen", files=dateien)
    assert "2 neue Zettel im Eingangskorb" in seite.text
    namen = sorted(p.name for p in quest.lager_dateien(ws.root))
    assert namen == ["bescheid (1).pdf", "bescheid.pdf"]
    assert client.get("/steuer/2025/lager/anzahl").json() == {"anzahl": 2}
    # Ein Pfad im Namen wird nie ein Pfad auf der Platte
    post(client, "/steuer/2025/lager/hochladen", files=[("files", ("../../boese.txt", b"x"))])
    assert (quest.lager(ws.root) / "boese.txt").is_file()
    # Auch Bankexporte (CSV) dürfen in den Korb, und eingefügter Text wird ein Zettel
    post(client, "/steuer/2025/lager/hochladen", files=[("files", ("konto.csv", b"a;b"))])
    seite = post(client, "/steuer/2025/lager/hochladen", text="Mail vom Finanzamt: bitte Belege")
    assert "1 neuer Zettel" in seite.text
    namen = [p.name for p in quest.lager_dateien(ws.root)]
    assert "konto.csv" in namen and "Mail vom Finanzamt bitte Belege.txt" in namen


def test_beraterin_steht_nicht_mehr_im_ersten_teil(client, ws):
    seite = client.get("/steuer/2025/fragebogen").text
    assert 'name="beraterin_email"' not in seite and "data-stapel" in seite
    fragebogen(client, teil1={"name": "Marie"})
    brief = client.get("/steuer/2025/beraterin").text
    assert 'name="beraterin_email"' in brief
    post(
        client,
        "/steuer/2025/beraterin/daten",
        beraterin_email="b@example.org",
        beraterin_termin="2026-03-12",
    )
    p = Profil.load(quest.profil_datei(ws.root, 2025))
    assert p.beraterin_email == "b@example.org" and p.beraterin_termin == "2026-03-12"
    assert "12. März 2026" in client.get("/steuer/2025").text
    # Teil 1 erneut speichern löscht die Beraterin nicht
    post(client, "/steuer/2025/fragebogen", teil="1", name="Marie K.")
    post(client, "/steuer/2025/fragebogen", teil="3")
    p = Profil.load(quest.profil_datei(ws.root, 2025))
    assert p.beraterin_email == "b@example.org"
    # Kaputtes Datum wird nicht gespeichert
    post(client, "/steuer/2025/beraterin/daten", beraterin_termin="morgen")
    assert Profil.load(quest.profil_datei(ws.root, 2025)).beraterin_termin == ""


def test_tagwerk_feiert_drei_karten_am_tag(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = store.connect(ws.db)
    karte = client.get("/steuer/2025").text
    assert "Tagwerk heute" in karte and "0/3" in karte
    offen = [s for s in quest.stuecke(conn, 2025) if s["status"] == "offen"]
    seite = None
    # "Gibt's nicht" zählt nicht fürs Tagwerk, sonst wäre Wegstempeln der schnellste Weg
    nicht = next(s for s in offen if not ist_kern(s["gebiet"], s["key"]))
    post(client, f"/steuer/2025/stueck/{nicht['id']}/status", status="gibts_nicht")
    assert spiel.tagwerk(quest.stuecke(conn, 2025), date.today())[0] == 0
    for s in [s for s in offen if s["id"] != nicht["id"]][:3]:
        seite = post(client, f"/steuer/2025/stueck/{s['id']}/status", status="fragen")
    # Ein kleiner Zettel statt eines Vollbilds, das einen hinausbittet
    assert "Tagwerk geschafft: 3 Karten heute" in seite.text and "Für heute Schluss" in seite.text
    assert "Tagwerk geschafft!" not in seite.text
    assert "Tagwerk geschafft" in client.get("/steuer/2025").text
    heute, ziel = spiel.tagwerk(quest.stuecke(conn, 2025), date.today())
    assert heute == 3 and ziel == 3


def test_steuer_id_gibt_es_immer_und_kernunterlagen_kommen_zum_pruefen(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    rathaus = client.get("/steuer/2025/gebiet/rathaus").text
    assert "Finde ich nicht" in rathaus
    sid = next(s for s in quest.stuecke(conn, 2025, "rathaus") if s["key"] == "steuer-id")
    post(client, f"/steuer/2025/stueck/{sid['id']}/status", status="gibts_nicht")
    assert quest.stueck(conn, sid["id"])["status"] == "fragen"  # nie "gibt's nicht"
    werkstatt = client.get("/steuer/2025/gebiet/werkstatt-1").text
    # Echte Rückfrage mit einem Grund pro Unterlage, und "Dann frag ich lieber" als Vorschlag
    assert "Ohne Belege zählen deine Ausgaben nicht" in werkstatt
    assert "FRITS rechnet sie aus deinen Rechnungen" in werkstatt
    assert 'data-rueckfrage-frage="Ich finde' in werkstatt
    belege = next(s for s in quest.stuecke(conn, 2025) if s["key"] == "belege")
    quest.setze_status(conn, belege["id"], "gibts_nicht")
    assert [s["key"] for s in spiel.zweifel(quest.stuecke(conn, 2025))] == ["belege"]
    p = Profil.load(quest.profil_datei(ws.root, 2025))
    _betreff, text = truhe.mail(p, reich(p), quest.stuecke(conn, 2025))
    assert "Bitte prüfen" in text and "Belege und Quittungen" in text.split("Bitte prüfen")[1]


def _bis_wach(conn, jahr=2025):
    """Stempelt Karten per Datenbank, bis der Drache gerade erwacht ist."""
    offen = [s for s in spiel.pflicht(quest.stuecke(conn, jahr)) if s["status"] == "offen"]
    while not spiel.drache_wach(quest.stuecke(conn, jahr)):
        quest.setze_status(conn, offen.pop(0)["id"], "gesammelt")


def test_drache_faellt_auch_aus_der_ferne_und_der_sieg_kommt_immer(client, ws):
    # Lena (Runde 1c): Wer nach dem Erwachen in den Provinzen weiterstempelt, tötete den
    # Drachen nebenbei, und es gab nie einen Sieg. Jetzt: Treffer aus der Ferne, und der
    # letzte Treffer führt immer zur Drachenseite mit dem Falt-Moment.
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    rest = [s for s in spiel.pflicht(quest.stuecke(conn, 2025)) if s["status"] == "offen"]
    assert len(rest) >= 2
    seite = post(client, f"/steuer/2025/stueck/{rest[0]['id']}/status", status="fragen")
    assert "Treffer aus der Ferne!" in seite.text
    for s in rest[1:]:
        seite = post(client, f"/steuer/2025/stueck/{s['id']}/status", status="fragen")
    assert str(seite.url).endswith("/steuer/2025/drache")
    assert "sq-arena-sieg finale" in seite.text and "Sieg!" in seite.text
    assert "Zur Schatztruhe" in seite.text
    assert quest.hat_notiz(conn, 2025, "sieg")


def test_eingangskorb_ist_die_hand_im_drachenkampf(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "kontoauszug.pdf").write_bytes(b"%PDF-1 konto")
    seite = client.get("/steuer/2025/drache").text
    assert 'class="sq-hand"' in seite and 'value="kontoauszug.pdf"' in seite
    karte = spiel.drache(quest.stuecke(conn, 2025)).karte
    seite = post(
        client,
        "/steuer/2025/drache/schlag",
        stueck_id=str(karte["id"]),
        aktion="zettel",
        zettel="kontoauszug.pdf",
    )
    assert 'data-treffer="kritisch"' in seite.text
    assert quest.stueck(conn, karte["id"])["status"] == "gesammelt"
    assert not (quest.lager(ws.root) / "kontoauszug.pdf").exists()
    # Ein Zettel, den es nicht (mehr) gibt, trifft nicht
    seite = post(
        client,
        "/steuer/2025/drache/schlag",
        stueck_id=str(spiel.drache(quest.stuecke(conn, 2025)).karte["id"]),
        aktion="zettel",
        zettel="../weg.pdf",
    )
    assert "finde ich nicht mehr" in seite.text


def test_silber_ist_ehrlich_und_feste_gibt_es_nur_einmal(client, ws):
    assert spiel.aufstieg("Goldenes Zeitalter") == "Aufstieg ins Goldene Zeitalter"
    assert spiel.aufstieg("Zeitalter der Ordnung") == "Aufstieg ins Zeitalter der Ordnung"
    assert spiel.zeitalter(1.0, silber=True)[1] == "Silbernes Zeitalter"
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    werkstatt = [s for s in quest.stuecke(conn, 2025) if s["gebiet"] == "werkstatt-1"]
    belege = next(s for s in werkstatt if s["key"] == "belege")
    post(client, f"/steuer/2025/stueck/{belege['id']}/status", status="gibts_nicht")
    seite = None
    for s in werkstatt:
        if s["id"] != belege["id"] and s["art"] != "bonus":
            seite = post(client, f"/steuer/2025/stueck/{s['id']}/status", status="fragen")
    assert (
        "Silber-Siegel! Werkstatt ist komplett." in seite.text and "Gold-Siegel" not in seite.text
    )
    assert "siegel silber" in client.get("/steuer/2025").text  # das Wachssiegel auf der Insel
    # Zurücknehmen und neu stempeln: kein zweites großes Fest
    letzte = next(s for s in reversed(werkstatt) if s["art"] != "bonus")
    post(client, f"/steuer/2025/stueck/{letzte['id']}/status", status="offen")
    seite = post(client, f"/steuer/2025/stueck/{letzte['id']}/status", status="fragen")
    assert "ist wieder komplett" in seite.text and "data-belohnung" not in seite.text
    # Mit "Bitte prüfen" heißt das letzte Zeitalter Silber
    for s in quest.stuecke(conn, 2025):
        if s["status"] == "offen" and s["art"] != "bonus":
            quest.setze_status(conn, s["id"], "fragen")
    assert "Silbernes Zeitalter" in client.get("/steuer/2025").text


def test_falsche_adressen_sind_freundlich(client, ws):
    seite = client.get("/steuer/abc")
    assert seite.status_code == 404 and "Diese Seite gibt es nicht" in seite.text
    seite = client.get("/steuer/1999/drache", follow_redirects=False)
    assert seite.status_code == 303 and seite.headers["location"] == "/steuer/1999/intro"


def test_eingefuegter_text_heisst_nach_seinen_ersten_woertern():
    from frits.web.steuer import textname

    assert textname("Sehr geehrter Herr Espenlaub, bitte senden Sie …") == (
        "Sehr geehrter Herr Espenlaub bitte senden.txt"
    )
    assert textname("  …  ") == "Eingefügter Text.txt"


def test_drache_geht_vor_dem_korb_und_zettel_lassen_sich_beiseitelegen(client, ws):
    # Lena (1d): Ein Zettel, der nirgends passt, blockierte den Drachen-Auftrag für immer
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "urlaubsfoto.png").write_bytes(b"strand")
    assert "Sortiere den Eingangskorb" in client.get("/steuer/2025").text
    _bis_wach(conn)
    karte = client.get("/steuer/2025").text
    assert "Besiege den Papierdrachen" in karte and "Zettel aus dem Korb hast du als Hand" in karte
    assert "Gehört nirgends hin" in karte
    seite = post(client, "/steuer/2025/lager/beiseite", name="urlaubsfoto.png", back="/steuer/2025")
    assert "Nichts ist gelöscht" in seite.text
    assert not (quest.lager(ws.root) / "urlaubsfoto.png").exists()
    assert (quest.beiseite(ws.root) / "urlaubsfoto.png").read_bytes() == b"strand"
    # Ein zweites Mal gibt es den Zettel nicht mehr, und fremde Pfade gehen nicht
    seite = post(client, "/steuer/2025/lager/beiseite", name="../../settings.toml")
    assert "finde ich nicht mehr" in seite.text


def test_hand_fragt_nach_und_ein_wurf_laesst_sich_zuruecknehmen(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "urlaubsfoto.png").write_bytes(b"strand")
    karte = spiel.drache(quest.stuecke(conn, 2025)).karte
    seite = client.get("/steuer/2025/drache").text
    assert 'data-rueckfrage-ja="Ja, passt"' in seite and "data-beiseite=" in seite
    seite = post(
        client,
        "/steuer/2025/drache/schlag",
        stueck_id=str(karte["id"]),
        aktion="zettel",
        zettel="urlaubsfoto.png",
    )
    assert "„urlaubsfoto.png“ liegt jetzt bei" in seite.text and "Nächste Karte" in seite.text
    datei = quest.dateien(conn, 2025, karte["id"])[0]
    seite = post(client, f"/steuer/2025/datei/{datei['id']}/entfernen", back="/steuer/2025/drache")
    assert (quest.lager(ws.root) / "urlaubsfoto.png").exists()
    assert quest.stueck(conn, karte["id"])["status"] == "offen"
    assert "Der Drache hat wieder" in seite.text  # Zurücklegen ist nicht stumm


def test_aus_silber_wird_gold_und_zuruecknehmen_ist_nicht_stumm(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    belege = next(s for s in quest.stuecke(conn, 2025) if s["key"] == "belege")
    quest.setze_status(conn, belege["id"], "gibts_nicht")
    for s in quest.stuecke(conn, 2025):
        if s["status"] == "offen" and s["art"] != "bonus":
            quest.setze_status(conn, s["id"], "fragen")
    assert spiel.drache(quest.stuecke(conn, 2025)).besiegt
    seite = post(
        client,
        f"/steuer/2025/stueck/{belege['id']}/datei",
        files=[("datei", ("quittung.pdf", b"%PDF quittung"))],
    )

    assert "Aus Silber wird Gold!" in seite.text and "art-gold" in seite.text
    # Zurücknehmen sagt etwas, und der Drache schläft hörbar wieder ein
    andere = [s for s in quest.stuecke(conn, 2025) if s["status"] == "fragen"]
    for s in andere:
        seite = post(client, f"/steuer/2025/stueck/{s['id']}/status", status="offen")
    assert "Stempel zurückgenommen" in seite.text
    assert quest.hat_notiz(conn, 2025, "gold")


def test_im_kampf_keine_grossen_dialoge_auf_provinzseiten(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    offen = [s for s in spiel.pflicht(quest.stuecke(conn, 2025)) if s["status"] == "offen"]
    seite = post(client, f"/steuer/2025/stueck/{offen[0]['id']}/status", status="fragen")
    assert "Neuer Orden: Mutig gefragt" in seite.text  # als kleiner Zettel
    assert "data-belohnung" not in seite.text


# ---------- Runde 8: Gold-Wirtschaft ----------


def _karte(gebiet="werkstatt-1", key="belege", art="pflicht", status="offen", id_=1, name="Belege"):
    return {"id": id_, "gebiet": gebiet, "key": key, "art": art, "status": status, "name": name}


def test_gold_regeln_belohnen_finden_mehr_als_wegstempeln():
    fund = _karte()
    schatz = _karte(key="spende", art="bonus", name="Spendenquittung")
    assert spiel.gold_fuer(fund, "gesammelt") == spiel.GOLD_FUND
    assert spiel.gold_fuer(schatz, "gesammelt") == spiel.GOLD_SCHATZ
    assert spiel.gold_fuer(fund, "fragen") == 1
    kern = next(
        (g.provinz.key + "-1", s.key)
        for g in reich(MARIE)
        for s in g.stuecke
        if ist_kern(g.id, s.key)
    )
    assert spiel.gold_fuer(_karte(gebiet=kern[0], key=kern[1]), "gibts_nicht") == 0
    # Wünsche der Beraterin zahlen erst mit Datei
    wunsch = _karte(key="wunsch-3", name="Bausparvertrag")
    assert spiel.gold_fuer(wunsch, "fragen") == 0 and spiel.gold_fuer(wunsch, "gibts_nicht") == 0
    assert spiel.gold_fuer(wunsch, "gesammelt") == spiel.GOLD_FUND
    # Siegel nach Funden gestaffelt
    assert spiel.siegel_gold(0) == 2 and spiel.siegel_gold(2) == 6 and spiel.siegel_gold(9) == 10
    # Die Serie multipliziert, gedeckelt; die Kriegstrommel legt eins drauf
    assert spiel.multiplikator(0) == 1 and spiel.multiplikator(3) == 3
    assert spiel.multiplikator(40) == spiel.MAX_MULT
    assert spiel.multiplikator(2, {"trommel"}) == 3 and spiel.multiplikator(0, {"trommel"}) == 1


def test_jeder_helfer_hat_einen_haken():
    fund, schatz = _karte(), _karte(key="spende", art="bonus", name="Spende")
    assert all(w.haken for w in spiel.MARKT if w.art == "helfer")
    assert spiel.gold_fuer(fund, "gesammelt", {"spuernase"}) == 2
    assert spiel.gold_fuer(fund, "fragen", {"brieftaube"}) == 2
    assert spiel.gold_fuer(fund, "gibts_nicht", {"brieftaube"}) == 0
    assert spiel.gold_fuer(schatz, "gesammelt", {"goldwaage"}) == 20
    assert spiel.gold_fuer(fund, "fragen", {"goldwaage", "brieftaube"}) == 0
    assert spiel.tagwerk_gold({"sanduhr"}) == 15 and spiel.multiplikator(8, {"sanduhr"}) == 3


def test_verdienste_haengen_an_der_karte_und_fragen_ohne_serie():
    vorher = [_karte(id_=1), _karte(id_=2, key="kasse", status="fragen")]
    nachher = [_karte(id_=1, status="gesammelt"), _karte(id_=2, key="kasse", status="gesammelt")]
    v = spiel.verdienste(vorher, nachher, mult=2)
    assert [(x.grund, x.betrag) for x in v] == [
        ("fund:werkstatt-1:belege", 6),
        ("fund:werkstatt-1:kasse", 6),
    ]
    # Neu angelegt (andere ID), gleicher Grund: zahlt also nicht doppelt
    neu = [_karte(id_=99, status="gesammelt")]
    assert spiel.verdienste([], neu)[0].grund == "fund:werkstatt-1:belege"
    # Fragen bekommen keinen Multiplikator
    frage = spiel.verdienste([_karte()], [_karte(status="fragen")], mult=8)
    assert frage[0].betrag == 1
    # Zurück auf offen: Rückbuchung für Fund und Antwort
    assert [g for g, _ in spiel.rueckbuchungen(nachher, vorher[:1] + nachher[1:])] == [
        "fund:werkstatt-1:belege",
        "antwort:werkstatt-1:belege",
    ]


def test_goldbuch_zahlt_jeden_grund_einmal_und_bucht_ehrlich_zurueck(conn):
    assert quest.verdiene(conn, 2025, "fund:a:b", 6, "Belege") == 6
    assert quest.verdiene(conn, 2025, "fund:a:b", 6, "Belege") == 0
    assert quest.verdiene(conn, 2024, "fund:a:b", 3, "Belege") == 3  # anderes Jahr, eigenes Gold
    assert quest.gold(conn, 2025) == 6
    assert quest.storniere(conn, 2025, "fund:a:b", "Belege") == 6
    assert quest.gold(conn, 2025) == 0 and quest.storniere(conn, 2025, "fund:a:b", "x") == 0
    assert quest.verdiene(conn, 2025, "fund:a:b", 2, "Belege") == 2  # neu verdient
    assert len(quest.goldbuch(conn, 2025)) == 3  # plus, minus, plus: nichts verschwindet
    assert quest.goldsummen(conn, 2025) == (2, 0)


def test_markt_kaufen_helferplaetze_tauschgebuehr_und_zierde_fuer_alle_jahre(conn):
    with pytest.raises(quest.QuestError, match="fehlen dir noch"):
        quest.kaufe(conn, 2025, "laternen")
    quest.verdiene(conn, 2025, "test", 200, "Testgold")
    assert "Insel" in quest.kaufe(conn, 2025, "laternen")
    with pytest.raises(quest.QuestError, match="schon"):
        quest.kaufe(conn, 2025, "laternen")
    assert "laternen" in quest.besitz(conn, 2026)  # Zierde bleibt stehen
    quest.kaufe(conn, 2025, "spuernase")
    quest.kaufe(conn, 2025, "brieftaube")
    assert "Pause" in quest.kaufe(conn, 2025, "sanduhr")  # nur zwei Plätze
    assert quest.helfer(conn, 2025) == {"spuernase", "brieftaube"}
    assert quest.helfer(conn, 2026) == frozenset()  # Helfer gelten pro Jahr
    with pytest.raises(quest.QuestError, match="Pause"):
        quest.lege_helfer(conn, 2025, "sanduhr", True)
    quest.lege_helfer(conn, 2025, "brieftaube", False)
    vorher = quest.gold(conn, 2025)
    quest.lege_helfer(conn, 2025, "sanduhr", True)
    assert quest.gold(conn, 2025) == vorher - spiel.TAUSCH_PREIS  # Tauschen kostet
    assert quest.helfer(conn, 2025) == {"spuernase", "sanduhr"}
    preise = sum(spiel.ware(k).preis for k in ("laternen", "spuernase", "brieftaube", "sanduhr"))
    assert quest.goldsummen(conn, 2025) == (200, preise + spiel.TAUSCH_PREIS)
    with pytest.raises(quest.QuestError):
        quest.kaufe(conn, 2025, "gibtsnicht")


def test_spuernase_raet_nach_dem_dateinamen():
    stuecke = [
        _karte(id_=1, key="kontoauszuege", name="Kontoauszüge"),
        _karte(id_=2, key="ausgangsrechnungen", name="Ausgangsrechnungen"),
        _karte(id_=3, key="kv-beitraege", name="Beiträge zur Krankenversicherung"),
    ]
    assert spiel.vermute("Kontoauszug_März.pdf", stuecke)["id"] == 1
    assert spiel.vermute("rechnung-mai.pdf", stuecke)["id"] == 2
    assert spiel.vermute("krankenversicherung 2025.jpg", stuecke)["id"] == 3
    assert spiel.vermute("IMG_2034.jpg", stuecke) is None
    assert spiel.vermute("scan.pdf", stuecke) is None


def test_fragebogen_farm_bringt_kein_zweites_gold(client, ws):
    """Bericht 2a, P1-B: Karten über den Fragebogen löschen und neu anlegen zahlt nicht doppelt."""
    fragebogen(
        client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1", "m_depots_inland_0": "1"}
    )
    conn = quest.ensure_schema(store.connect(ws.db))
    depot = next(s for s in quest.stuecke(conn, 2025) if s["gebiet"].startswith("schatzkammer"))
    post(
        client,
        f"/steuer/2025/stueck/{depot['id']}/datei",
        files=[("datei", ("bescheinigung.pdf", b"%PDF depot"))],
    )
    stand = quest.gold(conn, 2025)
    assert stand > 0
    datei = quest.dateien(conn, 2025, depot["id"])[0]
    post(client, f"/steuer/2025/datei/{datei['id']}/entfernen")
    assert quest.gold(conn, 2025) < stand  # zurückgebucht
    post(client, "/steuer/2025/fragebogen", teil="2", m_freiberuflich_0="1", m_depots_inland_0="0")
    post(client, "/steuer/2025/fragebogen", teil="3")
    quest.neues_stueck(conn, 2025, "rathaus", "Platzhalter", hinweis="schiebt die IDs")
    post(client, "/steuer/2025/fragebogen", teil="2", m_freiberuflich_0="1", m_depots_inland_0="1")
    post(client, "/steuer/2025/fragebogen", teil="3")
    neu = next(
        s
        for s in quest.stuecke(conn, 2025)
        if s["gebiet"] == depot["gebiet"] and s["key"] == depot["key"]
    )
    assert neu["id"] != depot["id"]  # wirklich neu angelegt
    post(
        client,
        f"/steuer/2025/stueck/{neu['id']}/datei",
        files=[("datei", ("bescheinigung.pdf", b"%PDF depot"))],
    )
    assert quest.gold(conn, 2025) <= stand  # höchstens wieder auf dem alten Stand


def test_gold_im_kampf_mit_serie_und_markt_im_browser(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    gold_vorher = quest.gold(conn, 2025)
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "beleg-eins.pdf").write_bytes(b"%PDF eins")
    karte = spiel.drache(quest.stuecke(conn, 2025)).karte
    werfen = {"stueck_id": str(karte["id"]), "aktion": "zettel", "zettel": "beleg-eins.pdf"}
    seite = post(client, "/steuer/2025/drache/schlag", **werfen)
    # Kritischer Treffer: Serie 2, also 3 Gold × 2
    assert 'class="sq-rechnung"' in seite.text and "+6 Gold" in seite.text
    assert quest.gold(conn, 2025) == gold_vorher + 6
    # Zurücklegen bucht das Gold zurück; danach zahlt die Karte als Frage nur ihre 1
    datei = quest.dateien(conn, 2025, karte["id"])[0]
    seite = post(client, f"/steuer/2025/datei/{datei['id']}/entfernen", back="/steuer/2025/drache")
    assert quest.gold(conn, 2025) == gold_vorher and "Gold zurückgebucht" in seite.text
    seite = post(client, "/steuer/2025/drache/schlag", stueck_id=str(karte["id"]), aktion="fragen")
    assert quest.gold(conn, 2025) == gold_vorher + 1
    assert f"„{karte['name']}“ kommt als Frage in die Mail" in seite.text
    assert 'class="sq-rechnung"' in seite.text

    # Der Markt: Zierde kaufen springt zur Insel, Helfer haben einen Haken
    quest.verdiene(conn, 2025, "test", 100, "Testgold")
    markt = client.get("/steuer/2025/markt")
    assert markt.status_code == 200 and "Goldbuch" in markt.text and "Haken:" in markt.text
    seite = post(client, "/steuer/2025/markt/kaufen", ware="windmuehle")
    assert "Windmühle steht jetzt auf deiner Insel" in seite.text
    assert 'class="sq-zier windmuehle neu"' in seite.text and 'class="sq-hafen"' in seite.text
    post(client, "/steuer/2025/markt/kaufen", ware="spuernase")
    (quest.lager(ws.root) / "kontoauszug-juni.pdf").write_bytes(b"%PDF juni")
    assert "Spürnase:" in client.get("/steuer/2025").text
    assert "Spürnase" in client.get("/steuer/2025/drache").text  # Helferreihe im Kampf


def test_letzter_treffer_zeigt_seine_rechnung_im_sieg(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    while spiel.drache(quest.stuecke(conn, 2025)).lp > 1:
        k = spiel.drache(quest.stuecke(conn, 2025)).karte
        post(client, "/steuer/2025/drache/schlag", stueck_id=str(k["id"]), aktion="fragen")
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "letzter.pdf").write_bytes(b"%PDF letzter")
    k = spiel.drache(quest.stuecke(conn, 2025)).karte
    seite = post(
        client,
        "/steuer/2025/drache/schlag",
        stueck_id=str(k["id"]),
        aktion="zettel",
        zettel="letzter.pdf",
    )
    assert "Letzter Treffer: 3 ×" in seite.text


def test_gold_nur_fuer_die_letzten_steuerjahre_und_wuensche_ohne_serie():
    """Bericht 2b, P2-B: Erfundene alte Jahre und erfundene Wünsche bringen kein Gold-Feuerwerk."""
    heute = date(2026, 11, 1)
    assert spiel.gold_jahr(2026, heute) and spiel.gold_jahr(2025, heute)
    assert spiel.gold_jahr(2024, heute)  # mit Beraterin oft erst im übernächsten Frühjahr
    assert not spiel.gold_jahr(2023, heute) and not spiel.gold_jahr(2027, heute)
    wunsch = _karte(key="wunsch-1", name="Bausparvertrag")
    v = spiel.verdienste([wunsch], [{**wunsch, "status": "gesammelt"}], mult=6)
    assert v[0].mult == 1 and v[0].betrag == spiel.GOLD_FUND


def test_wuensche_zahlen_nur_bis_zum_deckel(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    for n in range(spiel.WUNSCH_DECKEL + 2):
        quest.neues_stueck(conn, 2025, "rathaus", f"Wunsch {n}", hinweis="Beraterin")
    wuensche = [s for s in quest.stuecke(conn, 2025) if s["key"].startswith("wunsch-")]
    assert len(wuensche) == spiel.WUNSCH_DECKEL + 2
    for s in wuensche:
        post(
            client,
            f"/steuer/2025/stueck/{s['id']}/datei",
            files=[("datei", (f"{s['key']}.pdf", f"%PDF {s['key']}".encode()))],
        )
    assert quest.wunsch_funde(conn, 2025) == spiel.WUNSCH_DECKEL


def test_gold_faellt_nie_unter_null_letzter_kauf_geht_in_den_hafen(conn):
    """Bericht 2b, P2-A: Erst bei ×8 verdienen, ausgeben, dann zurücklegen darf nichts bringen."""
    quest.verdiene(conn, 2025, "fund:a:b", 20, "Belege")
    quest.verdiene(conn, 2025, "test", 30, "Testgold")
    quest.kaufe(conn, 2025, "windmuehle")  # 35
    quest.kaufe(conn, 2025, "laternen")  # 10
    assert quest.gold(conn, 2025) == 5
    quest.storniere(conn, 2025, "fund:a:b", "Belege")
    assert quest.gold(conn, 2025) == -15
    assert quest.decke_schulden(conn, 2025) == ["Laternen", "Windmühle"]
    assert quest.gold(conn, 2025) == 30
    assert "windmuehle" not in quest.besitz(conn, 2025)
    assert "laternen" not in quest.besitz(conn, 2026)
    assert quest.goldsummen(conn, 2025) == (30, 0)
    assert quest.decke_schulden(conn, 2025) == []  # nichts mehr zu tun
    quest.kaufe(conn, 2025, "laternen")  # zurückgegeben heißt: wieder kaufbar

    # Steckt das Gold nur noch in Helfer-Wechseln, wird auf null ausgeglichen
    quest.verdiene(conn, 2024, "fund:a:b", 20, "Belege")
    quest.kaufe(conn, 2024, "spuernase")
    quest.lege_helfer(conn, 2024, "spuernase", False)
    quest.lege_helfer(conn, 2024, "spuernase", True)
    quest.storniere(conn, 2024, "fund:a:b", "Belege")
    assert quest.decke_schulden(conn, 2024) == ["Spürnase"]
    assert quest.gold(conn, 2024) == 0 and quest.helfer(conn, 2024) == frozenset()


def test_zurueckbuchen_mit_ausgegebenem_gold_im_browser(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    karte = next(s for s in quest.stuecke(conn, 2025) if s["status"] == "offen")
    post(
        client,
        f"/steuer/2025/stueck/{karte['id']}/datei",
        files=[("datei", ("beleg.pdf", b"%PDF beleg"))],
    )
    verdient = quest.gold(conn, 2025)
    assert 0 < verdient < 50
    quest.verdiene(conn, 2025, "test", 50 - verdient, "Testgold")
    post(client, "/steuer/2025/markt/kaufen", ware="leuchtturm")
    assert quest.gold(conn, 2025) == 0
    datei = quest.dateien(conn, 2025, karte["id"])[0]
    seite = post(client, f"/steuer/2025/datei/{datei['id']}/entfernen")
    assert "Leuchtturm zurück im Hafen" in seite.text
    assert quest.gold(conn, 2025) >= 0 and "leuchtturm" not in quest.besitz(conn, 2025)
    markt = client.get("/steuer/2025/markt").text
    assert "Zurück in den Hafen: Leuchtturm" in markt

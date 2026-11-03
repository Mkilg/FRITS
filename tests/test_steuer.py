"""Die Steuer-Quest: Fragebogen, Sammeln, Drache, Beraterin-Mail, FRITS-Ernte und Schatztruhe."""

import html
import re
import shutil
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import pyzipper
from fastapi.testclient import TestClient

from frits import store, system
from frits.expenses import Expense, add_expense
from frits.settings import Settings, Workspace
from frits.steuer import beraterin, ernte, pruefung, quest, spiel, truhe
from frits.steuer.katalog import (
    HAKEN,
    MACHT_BERATERIN,
    Profil,
    fehlt_nie,
    ist_kern,
    katalog,
    reich,
)
from frits.web.app import create_app
from frits.web.steuer import inselplan

KIM = Profil(
    jahr=2025,
    name="Kim Beispiel",
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

MAIL = """Liebe Kim,
für die Erklärung 2025 bräuchte ich noch:
- die Versicherungsnachweise für beide Wohnungen
- eine Übersicht der Dividenden aus deinem Depot im Ausland
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
    assert ids(KIM) == [
        "rathaus",
        "werkstatt-1",
        "gilde-1",
        "gehoeft-1",
        "gehoeft-2",
        "hafen-1",
        "burg",
    ]
    titel = [g.titel for g in reich(KIM)]
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
    quest.start(conn, KIM)
    anzahl = len(quest.stuecke(conn, 2025))
    quest.start(conn, KIM)
    assert len(quest.stuecke(conn, 2025)) == anzahl
    assert len(quest.stuecke(conn, 2025, "gehoeft-2")) == 9


def test_weniger_wohnungen_raeumt_leere_provinzen_weg(conn, ws, tmp_path):
    quest.start(conn, KIM)
    s = quest.stuecke(conn, 2025, "gehoeft-1")[0]
    quest.sammeln(conn, ws.root, s["id"], datei(tmp_path, "miete.pdf", "x"))
    quest.start(conn, Profil(jahr=2025))
    gebiete = {s["gebiet"] for s in quest.stuecke(conn, 2025)}
    assert "gehoeft-2" not in gebiete
    assert "gehoeft-1" in gebiete  # da liegt schon eine Datei, die geht nicht verloren


def test_sammeln_benennt_um_und_erkennt_doppelte(conn, ws, tmp_path):
    quest.start(conn, KIM)
    s = next(s for s in quest.stuecke(conn, 2025, "gehoeft-2") if s["key"] == "grundsteuer")
    fund = quest.sammeln(conn, ws.root, s["id"], datei(tmp_path, "scan 17.PDF", "bescheid"))
    assert fund.neu
    assert fund.pfad.name == "2025_Vermietete-Wohnung-2_Grundsteuerbescheid.pdf"
    assert fund.stueck["status"] == "gesammelt"
    zweimal = quest.sammeln(conn, ws.root, s["id"], datei(tmp_path, "nochmal.pdf", "bescheid"))
    assert not zweimal.neu
    assert len(quest.dateien(conn, 2025)) == 1


def test_datei_zurueck_ins_lager(conn, ws, tmp_path):
    quest.start(conn, KIM)
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
    assert combo == 3 and ruf.startswith("Volltreffer mit Beleg!")


def test_erfolge():
    stuecke = stuecke_mit("gesammelt", "fragen")
    staende = spiel.provinzen(reich(Profil(jahr=2025)), stuecke)
    erreicht = {e.key for e in spiel.erfolge(stuecke, staende) if e.erreicht}
    assert {"erster-fund", "mutig-gefragt", "drachenbaendigerin"} <= erreicht
    assert "brieftaube" not in erreicht


# ---------- Mail der Beraterin ----------


def test_beraterin_mail_wird_zu_auftraegen(conn):
    quest.start(conn, KIM)
    wuensche = beraterin.zuordnen(MAIL, reich(KIM), quest.stuecke(conn, 2025))
    assert len(wuensche) == 4
    versicherung, dividenden, vertrag, bauspar = wuensche
    assert versicherung.gebiete == ("gehoeft-1", "gehoeft-2")
    assert dividenden.gebiete == ("hafen-1",)
    assert dividenden.stueck_id is not None  # passt zur "Dividenden-Liste"
    assert vertrag.gebiete == ("gilde-1",)
    assert vertrag.stueck_id is not None  # "Änderungen am Gesellschaftsvertrag"
    assert bauspar.gebiete == ("burg",)  # unbekannt: landet in der Burg


def test_beraterin_ohne_aufzaehlung():
    text = "Hallo Kim, mir fehlt noch der Grundsteuerbescheid für Wohnung 2. Danke!"
    assert beraterin.wuensche_aus_text(text) == [
        "mir fehlt noch der Grundsteuerbescheid für Wohnung 2"
    ]
    w = beraterin.zuordnen(text, reich(KIM), [])
    assert w[0].gebiete == ("gehoeft-2",)


# ---------- Schatztruhe ----------


def test_passwort_ist_lang_und_zufaellig():
    a, b = truhe.passwort(), truhe.passwort()
    teile = a.split("-")
    assert a != b and len(teile) == 7
    # zwei Code-Blöcke ohne verwechselbare Zeichen: zusammen über 70 Bit
    assert all(len(t) == 4 and set(t) <= set(truhe._ZEICHEN) for t in teile[5:])
    assert not set("01OIL") & set("".join(teile[5:]))


def test_truhe_ist_verschluesselt_und_geordnet(conn, ws, tmp_path):
    quest.start(conn, KIM)
    s = quest.stuecke(conn, 2025, "hafen-1")[0]
    quest.sammeln(conn, ws.root, s["id"], datei(tmp_path, "jahr.pdf", "depot"))
    quest.setze_status(
        conn, quest.stuecke(conn, 2025, "burg")[0]["id"], "fragen", "Brauchst du das?"
    )
    ziel = truhe.packen(
        tmp_path / "out" / "t.zip",
        KIM,
        reich(KIM),
        quest.stuecke(conn, 2025),
        quest.dateien(conn, 2025),
        "Wiese-Anker-42",
    )
    import io
    import zipfile

    with pyzipper.AESZipFile(ziel) as zf:
        # Außen steht nur ein neutraler Name: keine Ordner, keine "Lohnsteuerbescheinigung"
        assert zf.namelist() == ["Unterlagen-2025.zip"]
        with pytest.raises(RuntimeError):
            zf.read("Unterlagen-2025.zip")  # ohne Passwort geht nichts
        zf.setpassword(b"Wiese-Anker-42")
        innen = zf.read("Unterlagen-2025.zip")
    with zipfile.ZipFile(io.BytesIO(innen)) as zf:
        namen = zf.namelist()
        inhalt = zf.read("00 Inhaltsverzeichnis.txt").decode()
    assert "06 Depot im Ausland/2025_Depot-im-Ausland-1_Jahresuebersicht-des-Depots.pdf" in namen
    assert not list(ziel.parent.glob("*.tmp"))  # nichts Unverschlüsseltes bleibt liegen
    assert "Frage: Brauchst du das?" in inhalt
    assert truhe.verschluesselt(ziel)


def test_truhe_ohne_passwort_oeffnet_sich_ueberall(conn, ws, tmp_path):
    import zipfile

    quest.start(conn, KIM)
    s = quest.stuecke(conn, 2025, "hafen-1")[0]
    quest.sammeln(conn, ws.root, s["id"], datei(tmp_path, "jahr.pdf", "depot"))
    ziel = truhe.packen(
        tmp_path / "t.zip",
        KIM,
        reich(KIM),
        quest.stuecke(conn, 2025),
        quest.dateien(conn, 2025),
        None,
    )
    with zipfile.ZipFile(ziel) as zf:  # das normale Python-Zip, ganz ohne Passwort
        assert "Steuerunterlagen 2025" in zf.read("00 Inhaltsverzeichnis.txt").decode()
    assert not truhe.verschluesselt(ziel)
    _, text = truhe.mail(KIM, reich(KIM), quest.stuecke(conn, 2025), mit_passwort=False)
    assert "Passwort" not in text and "7-Zip" not in text


def test_mail_nennt_fragen_und_luecken_aber_kein_passwort(conn):
    quest.start(conn, KIM)
    alle = quest.stuecke(conn, 2025)
    quest.setze_status(conn, alle[0]["id"], "fragen", "Reicht der Export?")
    quest.setze_status(conn, alle[1]["id"], "gibts_nicht")
    betreff, text = truhe.mail(KIM, reich(KIM), quest.stuecke(conn, 2025))
    assert betreff == "Steuerunterlagen 2025"
    erste, zweite = alle[0]["name"], alle[1]["name"]
    assert f"1. Persönliches und Finanzamt, {erste}: Reicht der Export?" in text
    assert f"Gibt es dieses Jahr nicht:\n- Persönliches und Finanzamt: {zweite}" in text
    assert "Noch nicht gefunden habe ich:\n- " in text
    assert "Passwort schicke ich dir getrennt" in text and text.startswith("Hallo,")


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
    assert funde.neu == {
        "rechnungen": 2,
        "belege": 1,
        "euer": 1,
        "buchungsliste": 1,
        "verfahrensdoku": 1,
    }
    assert ernte.hat_rechnungen(frits_jahr, 2025)
    assert not ernte.hat_rechnungen(frits_jahr, 2023)


def test_ernte_einbringen_kopiert_und_ist_wiederholbar(frits_jahr, conn):
    quest.start(conn, KIM)
    assert [g.id for g in ernte.ziele(reich(KIM))] == ["werkstatt-1", "gilde-1"]
    neu = ernte.einbringen(conn, frits_jahr, 2025, "werkstatt-1", "kleinunternehmer")
    assert neu == {"rechnungen": 2, "belege": 1, "euer": 1, "buchungsliste": 1, "verfahrensdoku": 1}
    werkstatt = {s["key"]: s for s in quest.stuecke(conn, 2025, "werkstatt-1")}
    for key in ("ausgangsrechnungen", "belege", "euer"):
        assert werkstatt[key]["status"] == "gesammelt", key
    assert (frits_jahr.invoices / "RE-2025-0001.pdf").exists()  # kopiert, nicht verschoben
    nochmal = ernte.einbringen(conn, frits_jahr, 2025, "werkstatt-1", "kleinunternehmer")
    assert nochmal == {
        "rechnungen": 0,
        "belege": 0,
        "euer": 0,
        "buchungsliste": 0,
        "verfahrensdoku": 0,
    }

    # Eine neue Ausgabe: neuer Beleg, und die EÜR wird ersetzt statt verdoppelt
    ausgabe(frits_jahr, "tinte.jpg", date(2025, 6, 1))
    neu = ernte.einbringen(conn, frits_jahr, 2025, "werkstatt-1", "kleinunternehmer")
    assert neu == {"rechnungen": 0, "belege": 1, "euer": 1, "buchungsliste": 1, "verfahrensdoku": 0}
    euer_dateien = quest.dateien(conn, 2025, werkstatt["euer"]["id"])
    assert sorted(d["original"] for d in euer_dateien) == ["EUER_2025.csv", "EUER_2025.pdf"]
    # Buchungsliste und Verfahrensdoku liegen bei ihren Karten, die Liste nur einmal
    for key, datei in (
        ("buchungsliste", "Buchungsliste_2025.csv"),
        ("verfahrensdoku", "Verfahrensdokumentation_2025.pdf"),
    ):
        assert werkstatt[key]["art"] == "bonus"
        dateien = quest.dateien(conn, 2025, werkstatt[key]["id"])
        assert [d["original"] for d in dateien] == [datei]
        assert quest.stueck(conn, werkstatt[key]["id"])["status"] == "gesammelt"


def test_ernte_legt_fehlende_faecher_an(frits_jahr, conn):
    quest.start(conn, KIM)
    ernte.einbringen(conn, frits_jahr, 2025, "gilde-1", "kleinunternehmer")
    namen = {s["name"]: s["status"] for s in quest.stuecke(conn, 2025, "gilde-1")}
    assert namen["Rechnungen der Gesellschaft"] == "gesammelt"
    assert namen["EÜR der Gesellschaft"] == "gesammelt"  # die Karte aus dem Katalog
    assert namen["Buchungsliste"] == "gesammelt"  # neue Karte, die Gilde hat keine


# ---------- Die Seiten ----------


@pytest.fixture
def client(ws, monkeypatch):
    Settings(name="Kim Beispiel", hourly_rate=Decimal(1), setup_done=True).save(ws.settings_file)
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
        name="Kim",
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
        files={"datei": ("scan-01.pdf", b"mieten", "application/pdf")},
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
    zip_pfad = quest.ordner(ws.root, 2025) / "Steuer-2025-Kim.zip"
    assert zip_pfad.exists()

    page = post(client, "/steuer/2025/mail", oeffnen="1", betreff="Unterlagen", text="Hallo")
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
    fragebogen(client, teil2={"m_freiberuflich_0": "1", "m_gesellschaft_0": "1"})
    page = client.get("/steuer/2025")
    # Zwei Betriebe: einmal fragen, wohin es gehört
    assert "FRITS-Ernte" in page.text and "+1 neu" in page.text
    assert "Sag einmal, zu welchem Betrieb" in page.text
    page = post(client, "/steuer/2025/ernte", gebiet="werkstatt-1")
    assert (
        "Aus FRITS dazugekommen: 1 Rechnung, die EÜR mit Buchungsliste und Verfahrensdoku → Werkstatt"
        in page.text
    )
    assert "Sag einmal, zu welchem Betrieb" not in page.text  # erledigt: der Zettel verschwindet
    page = post(client, "/steuer/2025/ernte", gebiet="gehoeft-1")
    assert "Wähle aus, zu welchem Betrieb" in page.text
    # Danach kommt Neues von selbst dorthin, ohne Knopf
    rechnung(ws, "RE-2025-0002", "2025-04-01", "2025-04-02")
    page = client.get("/steuer/2025")
    assert "Aus FRITS dazugekommen: 1 Rechnung, die EÜR mit Buchungsliste → Werkstatt!" in page.text
    assert "+1 neu" not in page.text


def test_frits_ernte_kommt_von_selbst(client, ws):
    """Marie (2.11.): „Wo ist der Kartentisch und wie starte ich den Prozess?“ Gar nicht:
    Mit einem Betrieb landen FRITS-Rechnungen beim Öffnen der Insel von selbst dort."""
    rechnung(ws, "RE-2025-0001", "2025-03-01", "2025-03-02")
    fragebogen(client, teil2={"m_freiberuflich_0": "1"})
    page = client.get("/steuer/2025")  # der Fragebogen führte schon einmal auf die Insel
    assert 'action="/steuer/2025/ernte"' not in page.text  # kein Knopf, kein Zettel
    assert "+1 neu" not in page.text
    # Die Chronik steht jetzt im Belegbuch
    assert "Aus FRITS dazugekommen: 1 Rechnung" in client.get("/steuer/2025/belege").text
    assert "Kartentisch" not in page.text
    page = client.get("/steuer/2025")
    assert "Verfahrensdoku → Werkstatt!" not in page.text  # die Meldung kommt nur einmal


def test_notausgang(client):
    fragebogen(client, teil2={"m_freiberuflich_0": "1"})
    page = post(client, "/steuer/2025/drache/notausgang")
    assert "Heute nur senden" in page.text and "Karten erledigt" in page.text


def test_steuer_knoepfe_brauchen_das_token(client):
    response = client.post("/steuer/2025/fragebogen", data={"token": "falsch"})
    assert response.status_code == 403


def test_seiten_rendern(client):
    fragebogen(
        client,
        teil1={"name": "Kim"},
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
    quest.start(conn, KIM)
    alle = quest.stuecke(conn, 2025)
    staende = spiel.provinzen(reich(KIM), alle)
    a = spiel.auftrag(2025, staende, spiel.drache(alle), 0, alle)
    assert a.art == "provinz" and a.ziel.startswith("/steuer/2025/gebiet/")
    assert "Gold-Siegel" in a.belohnungen and "Orden „Erste Ernte“" in a.belohnungen

    # Angefangene Provinzen gehen vor: dort ist das Ziel am nächsten
    ziel = next(s for s in alle if s["gebiet"] == "gehoeft-2" and s["art"] != "bonus")
    quest.setze_status(conn, ziel["id"], "gibts_nicht")
    alle = quest.stuecke(conn, 2025)
    a = spiel.auftrag(2025, spiel.provinzen(reich(KIM), alle), spiel.drache(alle), 0, alle)
    assert a.gebiet_id == "gehoeft-2" and a.erledigt == 1

    # Liegen Zettel im Eingangskorb, kommen die zuerst
    a = spiel.auftrag(2025, spiel.provinzen(reich(KIM), alle), spiel.drache(alle), 3, alle)
    assert a.art == "korb" and "3 Zettel" in a.text

    # Alles beantwortet, aber kein einziges Dokument: Der Drache will einen echten Beleg
    for s in alle:
        quest.setze_status(conn, s["id"], "gibts_nicht")
    alle = quest.stuecke(conn, 2025)
    d = spiel.drache(alle)
    assert d.will_beleg and not d.besiegt and d.lp == 1
    a = spiel.auftrag(2025, spiel.provinzen(reich(KIM), alle), d, 0, alle, drache_wach=True)
    assert a.art == "drache" and "echten Beleg" in a.titel

    # Mit einem Dokument: zur Beute
    quest.setze_status(conn, alle[0]["id"], "gesammelt")
    alle = quest.stuecke(conn, 2025)
    a = spiel.auftrag(2025, spiel.provinzen(reich(KIM), alle), spiel.drache(alle), 0, alle)
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
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    karte = client.get("/steuer/2025").text
    assert "Neue Quest für 2024" in karte and "Dein Auftrag" in karte
    assert "Burg?" in karte  # die Burg wartet als Nebenquest
    intro = client.get("/steuer/2024/intro").text
    assert "Neue Insel für 2024?" in intro and 'href="/steuer/2025"' in intro
    assert "Zurück zu 2025" in client.get("/steuer/2024/fragebogen").text


def test_siegel_kommt_als_belohnung_mit_naechstem_auftrag(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    page = None
    for s in quest.stuecke(quest.ensure_schema(store.connect(ws.db)), 2025, "rathaus"):
        if s["art"] == "bonus":
            continue
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
    gebiete = [gb for gb in reich(KIM) if gb.id in ("burg", "rathaus")]
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
    quest.start(conn, KIM)
    conn.execute("UPDATE steuer_stuecke SET hinweis = 'alt' WHERE gebiet = 'rathaus'")
    quest.start(conn, KIM)
    hinweise = {s["hinweis"] for s in quest.stuecke(conn, KIM.jahr, "rathaus")}
    assert "alt" not in hinweise


def test_dateien_lassen_sich_auf_den_eingangskorb_ziehen(client, ws):
    fragebogen(client, teil1={"name": "Kim"})
    karte = client.get("/steuer/2025").text
    assert "/steuer/2025/lager/hochladen" in karte and "data-dropzone" in karte
    assert client.get("/steuer/2025/lager/anzahl").json() == {"anzahl": 0}
    dateien = [
        ("files", ("bescheid.pdf", b"%PDF-1", "application/pdf")),
        ("files", ("bescheid.pdf", b"%PDF-2", "application/pdf")),
        ("files", ("kopie.pdf", b"%PDF-1", "application/pdf")),  # dieselbe Datei noch einmal
    ]
    seite = post(client, "/steuer/2025/lager/hochladen", files=dateien)
    assert "2 neue Zettel im Eingangskorb" in seite.text
    assert "kopie.pdf: Genau diese Datei liegt schon im Eingangskorb" in seite.text
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
    fragebogen(client, teil1={"name": "Kim"})
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


def test_tagwerk_feiert_drei_karten_am_tag(client, ws, monkeypatch):
    import sys

    # Das Quest-eigene Tagwerk (ohne Frittes gemeinsames Tagesziel, siehe Runde 20)
    monkeypatch.setitem(sys.modules, "frits.spiel.tagesziel", None)
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = store.connect(ws.db)
    karte = client.get("/steuer/2025").text
    assert "Tagesziel heute" in karte and "0/3" in karte
    offen = [s for s in quest.stuecke(conn, 2025) if s["status"] == "offen"]
    seite = None
    # "Gibt's nicht" zählt nicht fürs Tagwerk, sonst wäre Wegstempeln der schnellste Weg
    nicht = next(s for s in offen if not ist_kern(s["gebiet"], s["key"]))
    post(client, f"/steuer/2025/stueck/{nicht['id']}/status", status="gibts_nicht")
    assert spiel.tagwerk(quest.stuecke(conn, 2025), date.today())[0] == 0
    for s in [s for s in offen if s["id"] != nicht["id"]][:3]:
        seite = post(client, f"/steuer/2025/stueck/{s['id']}/status", status="fragen")
    # Ein kleiner Zettel statt eines Vollbilds, das einen hinausbittet
    assert "Tagesziel geschafft: 3 Karten heute" in seite.text and "Für heute Schluss" in seite.text
    assert "Tagesziel geschafft!" not in seite.text
    assert "Tagesziel geschafft" in client.get("/steuer/2025").text
    assert "Tagwerk" not in client.get("/steuer/2025/markt").text  # ein Name für ein Ziel
    heute, ziel = spiel.tagwerk(quest.stuecke(conn, 2025), date.today())
    assert heute == 3 and ziel == 3


def test_steuer_id_gibt_es_immer_und_kernunterlagen_kommen_zum_pruefen(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    rathaus = client.get("/steuer/2025/gebiet/rathaus").text
    assert "Finde ich nicht" in rathaus
    sid = next(s for s in quest.stuecke(conn, 2025, "rathaus") if s["key"] == "steuer-id")
    post(client, f"/steuer/2025/stueck/{sid['id']}/status", status="gibts_nicht")
    assert quest.stueck(conn, sid["id"])["status"] == "fragen"  # nie "gibt's nicht"
    werkstatt = client.get("/steuer/2025/gebiet/werkstatt-1").text
    # Echte Rückfrage mit einem Grund pro Unterlage, und "Dann frag ich lieber" als Vorschlag
    assert "Ohne Belege zählen deine Ausgaben nicht" in werkstatt
    assert "Ausrechnen musst du sie nicht" in werkstatt
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
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
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
    assert "Zur Fundkiste" in seite.text
    assert quest.hat_notiz(conn, 2025, "sieg")


def test_eingangskorb_ist_die_hand_im_drachenkampf(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "scan-02.pdf").write_bytes(b"%PDF-1 konto")
    seite = client.get("/steuer/2025/drache").text
    assert 'class="sq-hand"' in seite and 'value="scan-02.pdf"' in seite
    karte = spiel.drache(quest.stuecke(conn, 2025)).karte
    seite = post(
        client,
        "/steuer/2025/drache/schlag",
        stueck_id=str(karte["id"]),
        aktion="zettel",
        zettel="scan-02.pdf",
    )
    assert 'data-treffer="kritisch"' in seite.text
    assert quest.stueck(conn, karte["id"])["status"] == "gesammelt"
    assert not (quest.lager(ws.root) / "scan-02.pdf").exists()
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
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
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
    # Mit "Bitte prüfen" heißt das letzte Zeitalter Silber (mit wenigstens einem Dokument)
    offen = [s for s in quest.stuecke(conn, 2025) if s["status"] == "offen" and s["art"] != "bonus"]
    for s in offen:
        quest.setze_status(conn, s["id"], "fragen")
    assert "Silbernes Zeitalter" not in client.get("/steuer/2025").text  # noch kein Beleg
    quest.setze_status(conn, offen[0]["id"], "gesammelt")
    assert "Silbernes Zeitalter" in client.get("/steuer/2025").text


def test_falsche_adressen_sind_freundlich(client, ws):
    seite = client.get("/steuer/abc")
    assert seite.status_code == 404 and "Diese Seite gibt es nicht" in seite.text
    seite = client.get("/steuer/1999/drache", follow_redirects=False)
    assert seite.status_code == 303 and seite.headers["location"] == "/steuer/1999/intro"


def test_eingefuegter_text_heisst_nach_seinen_ersten_woertern():
    from frits.web.steuer import textname

    assert textname("Sehr geehrter Herr Bauer, bitte senden Sie …") == (
        "Sehr geehrter Herr Bauer bitte senden.txt"
    )
    assert textname("  …  ") == "Eingefügter Text.txt"


def test_drache_geht_vor_dem_korb_und_zettel_lassen_sich_beiseitelegen(client, ws):
    # Lena (1d): Ein Zettel, der nirgends passt, blockierte den Drachen-Auftrag für immer
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "urlaubsfoto.png").write_bytes(b"strand")
    # Lena (Runde 57): Im frischen Jahr kommt die Steuer-ID zuerst, der Korb steht dabei
    insel = client.get("/steuer/2025").text
    assert "mit deiner Steuer-ID" in insel and "Im Korb liegt noch ein Zettel" in insel
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
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "scan-03.png").write_bytes(b"strand")
    karte = spiel.drache(quest.stuecke(conn, 2025)).karte
    seite = client.get("/steuer/2025/drache").text
    assert 'data-rueckfrage-ja="Ja, passt"' in seite and "data-beiseite=" in seite
    seite = post(
        client,
        "/steuer/2025/drache/schlag",
        stueck_id=str(karte["id"]),
        aktion="zettel",
        zettel="scan-03.png",
    )
    assert "„scan-03.png“ liegt jetzt bei" in seite.text and "Nächste Karte" in seite.text
    datei = quest.dateien(conn, 2025, karte["id"])[0]
    seite = post(client, f"/steuer/2025/datei/{datei['id']}/entfernen", back="/steuer/2025/drache")
    assert (quest.lager(ws.root) / "scan-03.png").exists()
    assert quest.stueck(conn, karte["id"])["status"] == "offen"
    assert "Der Drache hat wieder" in seite.text  # Zurücklegen ist nicht stumm


def test_aus_silber_wird_gold_und_zuruecknehmen_ist_nicht_stumm(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    belege = next(s for s in quest.stuecke(conn, 2025) if s["key"] == "belege")
    quest.setze_status(conn, belege["id"], "gibts_nicht")
    rechnungen = next(s for s in quest.stuecke(conn, 2025) if s["key"] == "ausgangsrechnungen")
    quest.setze_status(conn, rechnungen["id"], "gesammelt")
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
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
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
        for g in reich(KIM)
        for s in g.stuecke
        if ist_kern(g.id, s.key)
    )
    assert spiel.gold_fuer(_karte(gebiet=kern[0], key=kern[1]), "gibts_nicht") == 0
    # Wünsche der Beraterin zahlen erst mit Datei
    wunsch = _karte(key="wunsch-3", name="Bausparvertrag")
    assert spiel.gold_fuer(wunsch, "fragen") == 0 and spiel.gold_fuer(wunsch, "gibts_nicht") == 0
    assert spiel.gold_fuer(wunsch, "gesammelt") == spiel.GOLD_FUND
    # Siegel nach Funden gestaffelt
    assert spiel.siegel_gold(0) == 1 and spiel.siegel_gold(2) == 3 and spiel.siegel_gold(9) == 4
    # Die Serie multipliziert, gedeckelt; die Kriegstrommel legt eins drauf
    assert spiel.multiplikator(0) == 1 and spiel.multiplikator(3) == 3
    assert spiel.multiplikator(40) == spiel.MAX_MULT
    assert spiel.multiplikator(2, {"trommel"}) == 3 and spiel.multiplikator(0, {"trommel"}) == 1


def test_jeder_helfer_hat_einen_haken():
    fund, schatz = _karte(), _karte(key="spende", art="bonus", name="Spende")
    assert all(w.haken for w in spiel.MARKT if w.art == "helfer")
    assert spiel.gold_fuer(fund, "gesammelt", {"spuernase"}) == 1
    assert spiel.gold_fuer(fund, "fragen", {"brieftaube"}) == 2
    assert spiel.gold_fuer(fund, "gibts_nicht", {"brieftaube"}) == 0
    assert spiel.gold_fuer(schatz, "gesammelt", {"goldwaage"}) == 8
    assert spiel.gold_fuer(fund, "fragen", {"goldwaage", "brieftaube"}) == 0
    assert spiel.tagwerk_gold({"sanduhr"}) == 6 and spiel.multiplikator(8, {"sanduhr"}) == 3
    # Die Texte nennen die echten Zahlen (eine Währung: Salzkörner)
    assert "1 Salzkorn statt 2" in spiel.ware("spuernase").haken
    assert "8 Salzkörner statt 4" in spiel.ware("goldwaage").text


def test_verdienste_haengen_an_der_karte_und_fragen_ohne_serie():
    vorher = [_karte(id_=1), _karte(id_=2, key="kasse", status="fragen")]
    nachher = [_karte(id_=1, status="gesammelt"), _karte(id_=2, key="kasse", status="gesammelt")]
    v = spiel.verdienste(vorher, nachher, mult=2)
    assert [(x.grund, x.betrag) for x in v] == [
        ("fund:werkstatt-1:belege", 4),
        ("fund:werkstatt-1:kasse", 4),
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
    assert quest.verdiene(conn, 2024, "fund:a:b", 3, "Belege") == 3  # anderes Jahr, eigener Grund
    assert quest.gold(conn, 2025) == 9  # ein Beutel für alle Jahre (und für Fritte)
    assert quest.storniere(conn, 2025, "fund:a:b", "Belege") == 6
    assert quest.gold(conn) == 3 and quest.storniere(conn, 2025, "fund:a:b", "x") == 0
    assert quest.verdiene(conn, 2025, "fund:a:b", 2, "Belege") == 2  # neu verdient
    assert len(quest.goldbuch(conn, 2025)) == 3  # plus, minus, plus: nichts verschwindet
    assert quest.goldsummen(conn, 2025) == (2, 0)


def test_markt_kaufen_helferplaetze_tauschgebuehr_und_zierde_fuer_alle_jahre(conn):
    with pytest.raises(quest.QuestError, match="fehlen dir noch"):
        quest.kaufe(conn, 2025, "trommel")
    quest.verdiene(conn, 2025, "test", 200, "Testgold")  # gedeckelt: 120 im Jahr
    # Runde 21: Zierden gibt es nur noch für Orden, und sie gelten für alle Jahre
    with pytest.raises(quest.QuestError, match="Orden"):
        quest.kaufe(conn, 2025, "laternen")
    assert quest.zierde_freischalten(conn, "laternen")
    assert not quest.zierde_freischalten(conn, "laternen")
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
    preise = sum(spiel.ware(k).preis for k in ("spuernase", "brieftaube", "sanduhr"))
    assert quest.goldsummen(conn, 2025) == (spiel.SALZ_DECKEL, preise + spiel.TAUSCH_PREIS)
    assert quest.gold(conn) == spiel.SALZ_DECKEL - preise - spiel.TAUSCH_PREIS
    with pytest.raises(quest.QuestError):
        quest.kaufe(conn, 2025, "gibtsnicht")


def test_spuernase_raet_nach_dem_dateinamen():
    stuecke = [
        _karte(id_=1, key="kontoauszuege", name="Kontoauszüge"),
        _karte(id_=2, key="ausgangsrechnungen", name="Ausgangsrechnungen"),
        _karte(id_=3, key="kv-beitraege", name="Beiträge zur Krankenversicherung"),
    ]
    assert spiel.vermute("Kontoauszug_März.pdf", stuecke)["id"] == 1
    # "Rechnung" allein sagt nicht, ob sie von dir oder an dich ist: lieber kein Vorschlag
    assert spiel.vermute("rechnung-mai.pdf", stuecke) is None
    assert spiel.vermute("Ausgangsrechnung-mai.pdf", stuecke)["id"] == 2
    assert spiel.vermute("krankenversicherung 2025.jpg", stuecke)["id"] == 3
    assert spiel.vermute("IMG_2034.jpg", stuecke) is None
    assert spiel.vermute("scan.pdf", stuecke) is None


def test_fragebogen_farm_bringt_kein_zweites_gold(client, ws):
    """Bericht 2a, P1-B: Karten über den Fragebogen löschen und neu anlegen zahlt nicht doppelt."""
    fragebogen(
        client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1", "m_depots_inland_0": "1"}
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
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    # Das Stempeln per Datenbank war "gestern": Hier geht es um die Serie, nicht um das
    # gemeinsame Tagesziel (das zählt mit Fritte jeden heutigen Fund mit).
    conn.execute("UPDATE steuer_stuecke SET geaendert = '2026-01-01 10:00:00'")
    gold_vorher = quest.gold(conn, 2025)
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "scan-eins.pdf").write_bytes(b"%PDF eins")
    karte = spiel.drache(quest.stuecke(conn, 2025)).karte
    werfen = {"stueck_id": str(karte["id"]), "aktion": "zettel", "zettel": "scan-eins.pdf"}
    seite = post(client, "/steuer/2025/drache/schlag", **werfen)
    # Volltreffer: Serie 2, also 2 Salzkörner × 2
    # the salt sign is the painted shaker, or the drawn one when the picture is missing
    assert 'class="sq-rechnung"' in seite.text
    assert re.search(r'\+4 <(svg|img) class="salz-zeichen', seite.text)
    assert quest.gold(conn, 2025) == gold_vorher + 4
    # Zurücklegen bucht das Gold zurück; danach zahlt die Karte als Frage nichts mehr
    # (Lena, Runde 39: sonst brachte Zurück, „Lieber fragen“, Datei wieder dran +1)
    datei = quest.dateien(conn, 2025, karte["id"])[0]
    seite = post(client, f"/steuer/2025/datei/{datei['id']}/entfernen", back="/steuer/2025/drache")
    assert quest.gold(conn, 2025) == gold_vorher and "Salzkörner zurückgebucht" in seite.text
    seite = post(client, "/steuer/2025/drache/schlag", stueck_id=str(karte["id"]), aktion="fragen")
    assert quest.gold(conn, 2025) == gold_vorher
    assert f"„{karte['name']}“ kommt als Frage in die Mail" in seite.text

    # Der Markt: Helfer haben einen Haken, Zierden gibt es für Orden (Runde 21)
    quest.verdiene(conn, 2025, "test", 100, "Testgold")
    markt = client.get("/steuer/2025/markt")
    assert markt.status_code == 200 and "Kassenbuch der Insel" in markt.text
    assert "Haken:" in markt.text
    assert "Belohnung für den Orden „Sammlerin“" in markt.text
    seite = post(client, "/steuer/2025/markt/kaufen", ware="windmuehle")
    assert "Zierden kann man nicht kaufen" in seite.text
    post(client, "/steuer/2025/markt/kaufen", ware="spuernase")
    (quest.lager(ws.root) / "kontoauszug-juni.pdf").write_bytes(b"%PDF juni")
    (quest.lager(ws.root) / "quittung-juni.pdf").write_bytes(b"%PDF quittung")
    assert "Spürnase:" in client.get("/steuer/2025").text
    assert "Spürnase" in client.get("/steuer/2025/drache").text  # Helferreihe im Kampf


def test_letzter_treffer_zeigt_seine_rechnung_im_sieg(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    while spiel.drache(quest.stuecke(conn, 2025)).lp > 1:
        k = spiel.drache(quest.stuecke(conn, 2025)).karte
        post(client, "/steuer/2025/drache/schlag", stueck_id=str(k["id"]), aktion="fragen")
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "scan-04.pdf").write_bytes(b"%PDF letzter")
    k = spiel.drache(quest.stuecke(conn, 2025)).karte
    seite = post(
        client,
        "/steuer/2025/drache/schlag",
        stueck_id=str(k["id"]),
        aktion="zettel",
        zettel="scan-04.pdf",
    )
    assert "Letzter Treffer: 2 ×" in seite.text


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
    """Bericht 2b, P2-A: Erst bei ×8 verdienen, ausgeben, dann zurücklegen darf nichts bringen.
    Eine Währung: Der Beutel ist Frittes, er fällt nie unter null."""
    quest.verdiene(conn, 2025, "fund:a:b", 10, "Belege")
    quest.verdiene(conn, 2025, "test", 30, "Testgold")
    quest.kaufe(conn, 2025, "trommel")  # 15
    quest.kaufe(conn, 2025, "goldwaage")  # 12
    quest.kaufe(conn, 2025, "spuernase")  # 10
    assert quest.gold(conn) == 3
    quest.storniere(conn, 2025, "fund:a:b", "Belege")
    assert quest.gold(conn) == 0  # nimmt nur, was da ist; 7 fehlen
    assert quest.decke_schulden(conn, 2025) == ["Spürnase"]
    assert quest.gold(conn) == 3  # die Spürnase deckte 7, der Rest kam zurück
    assert "spuernase" not in quest.besitz(conn, 2025)
    assert "goldwaage" in quest.besitz(conn, 2025)
    assert quest.goldsummen(conn, 2025) == (30, 27)
    assert quest.decke_schulden(conn, 2025) == []  # nichts mehr zu tun
    quest.verdiene(conn, 2025, "test2", 10, "Testgold")
    quest.kaufe(conn, 2025, "spuernase")  # zurückgegeben heißt: wieder kaufbar

    # Stecken die Körner nur noch in Helfer-Wechseln, bleibt es bei null
    quest.verdiene(conn, 2024, "fund:a:b", 20, "Belege")
    quest.kaufe(conn, 2024, "spuernase")
    quest.lege_helfer(conn, 2024, "spuernase", False)
    quest.lege_helfer(conn, 2024, "spuernase", True)
    quest.storniere(conn, 2024, "fund:a:b", "Belege")
    assert quest.decke_schulden(conn, 2024) == ["Spürnase"]
    assert quest.gold(conn) == 0 and quest.helfer(conn, 2024) == frozenset()


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
    assert 0 < verdient < 15
    quest.verdiene(conn, 2025, "test", 15 - verdient, "Testgold")
    post(client, "/steuer/2025/markt/kaufen", ware="trommel")
    assert quest.gold(conn, 2025) == 0
    datei = quest.dateien(conn, 2025, karte["id"])[0]
    seite = post(client, f"/steuer/2025/datei/{datei['id']}/entfernen")
    assert "Kriegstrommel zurück im Hafen" in seite.text
    assert quest.gold(conn, 2025) >= 0 and "trommel" not in quest.besitz(conn, 2025)
    markt = client.get("/steuer/2025/markt").text
    assert "Zurück in den Hafen: Kriegstrommel" in markt


# ---------- Runde 10: Übergabe an die Beraterin (Testbericht "Marie") ----------

MARIE_MAIL = """Hallo Marie,
für 2025 bräuchte ich noch:
- Jahresbescheinigung KSK
- Spendenquittungen
- Für beide Wohnungen: Hausgeld, Grundsteuer, Zinsbescheinigung
- Steuerbescheinigung US-Broker (1099)
Viele Grüße"""

MARIE = Profil(
    jahr=2025,
    name="Marie",
    mengen={"freiberuflich": [1, 0], "gesellschaft": [1, 0], "wohnungen": [2, 0]}
    | {"depots_ausland": [1, 0]},
    haken=["ksk", "spenden", "vorsorge"],
)


def test_beraterin_mail_markiert_vorhandene_karten_statt_doppelkarten(conn):
    """Bericht Marie, P1-1: KSK und Spenden in die Burg, Sammelzeilen zerlegt, keine Doppelkarten."""
    quest.start(conn, MARIE)
    alle = quest.stuecke(conn, 2025)
    name = {s["id"]: (s["gebiet"], s["key"]) for s in alle}
    wuensche = beraterin.zuordnen(MARIE_MAIL, reich(MARIE), alle)
    ziele = {w.text: [name.get(i) for i in w.stuecke] for w in wuensche}
    assert ziele["Jahresbescheinigung KSK"] == [("burg", "ksk")]
    assert ziele["Spendenquittungen"] == [("burg", "spenden")]
    assert ziele["Hausgeld"] == [("gehoeft-1", "nebenkosten"), ("gehoeft-2", "nebenkosten")]
    assert ziele["Grundsteuer"] == [("gehoeft-1", "grundsteuer"), ("gehoeft-2", "grundsteuer")]
    assert ziele["Zinsbescheinigung"] == [("gehoeft-1", "zinsen"), ("gehoeft-2", "zinsen")]
    # Ein Broker-Wort zieht in den Hafen, dort passt die Jahresübersicht
    assert ziele["Steuerbescheinigung US-Broker (1099)"] == [("hafen-1", "jahresuebersicht")]
    # "Steuer" allein reicht nicht: Die Steuer-ID ist kein Ziel für eine Steuerbescheinigung
    assert beraterin.zerlegen("Kontoauszüge Januar bis März") == [
        ("Kontoauszüge Januar bis März", "")
    ]


def test_beraterin_im_browser_merkt_adresse_und_nimmt_kein_siegel_gold(client, ws):
    fragebogen(
        client,
        teil1={"name": "Marie"},
        teil2={"m_freiberuflich_0": "1", "m_wohnungen_0": "2"},
        teil3={"haken": ["ksk", "spenden"]},
    )
    conn = quest.ensure_schema(store.connect(ws.db))
    vorher = len(quest.stuecke(conn, 2025))
    for s in quest.stuecke(conn, 2025, "burg"):
        quest.setze_status(conn, s["id"], "gesammelt")
    quest.verdiene(conn, 2025, "siegel:burg", 4, "Siegel: Burg")
    seite = post(
        client,
        "/steuer/2025/beraterin",
        text=MARIE_MAIL + "\n- Bausparvertrag",
        beraterin_email="weber@kanzlei.example",
        beraterin_name="Frau Weber",
        anrede="sie",
    )
    assert "markiert: Grundsteuerbescheid" in seite.text
    assert len(quest.stuecke(conn, 2025)) == vorher + 2  # nur Broker (kein Depot) und Bausparen
    p = Profil.load(quest.profil_datei(ws.root, 2025))
    assert (p.beraterin_email, p.beraterin_name, p.anrede) == (
        "weber@kanzlei.example",
        "Frau Weber",
        "sie",
    )
    assert quest.gold(conn, 2025) >= 4  # ein neuer Wunsch nimmt kein Siegel-Gold weg
    assert "Dein Siegel wartet" in seite.text
    # Automatisch speichern ohne Seitenwechsel
    r = client.post(
        "/steuer/2025/beraterin/daten",
        data={"token": client.token, "beraterin_termin": "2026-12-15"},
        headers={"x-frits-leise": "1"},
    )
    assert r.json() == {"ok": True}
    p = Profil.load(quest.profil_datei(ws.root, 2025))
    assert p.beraterin_termin == "2026-12-15" and p.beraterin_email == "weber@kanzlei.example"


def test_eine_datei_fuer_zwei_wohnungen_ist_ein_ehrlicher_verweis(conn, ws, tmp_path):
    """Bericht Marie, P1-2: dieselbe Mietliste für Wohnung 1 und 2."""
    quest.start(conn, MARIE)
    w1 = next(s for s in quest.stuecke(conn, 2025, "gehoeft-1") if s["key"] == "mieteinnahmen")
    w2 = next(s for s in quest.stuecke(conn, 2025, "gehoeft-2") if s["key"] == "mieteinnahmen")
    zins = next(s for s in quest.stuecke(conn, 2025, "gehoeft-2") if s["key"] == "zinsen")
    quest.sammeln(conn, ws.root, w1["id"], datei(tmp_path, "mieten.pdf", "beide"))
    fund = quest.sammeln(conn, ws.root, w2["id"], datei(tmp_path, "kopie.pdf", "beide"))
    assert fund.neu and fund.verweis["id"] == w1["id"]
    assert quest.stueck(conn, w2["id"])["status"] == "gesammelt"
    # Eine andere Unterlage darf die Datei nicht mitbenutzen
    fund = quest.sammeln(conn, ws.root, zins["id"], datei(tmp_path, "nochmal.pdf", "beide"))
    assert not fund.neu and quest.stueck(conn, zins["id"])["status"] == "offen"
    inhalt = truhe.inhaltsverzeichnis(
        MARIE,
        reich(MARIE),
        quest.stuecke(conn, 2025),
        quest.dateien(conn, 2025),
        quest.verweise(conn, 2025),
    )
    assert "dieselbe Datei wie bei Vermietete Wohnung · Nr. 1" in inhalt
    assert "(Original: mieten.pdf)" in inhalt
    # Wer die Datei bei Wohnung 1 zurücklegt, nimmt sie auch Wohnung 2 weg
    quest.entferne_datei(conn, quest.dateien(conn, 2025, w1["id"])[0]["id"])
    assert quest.stueck(conn, w2["id"])["status"] == "offen"
    assert quest.verweise(conn, 2025) == []


def test_verweis_im_browser_und_im_kampf_kein_falscher_treffer(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_wohnungen_0": "2"})
    conn = quest.ensure_schema(store.connect(ws.db))
    w1, w2 = (
        next(s for s in quest.stuecke(conn, 2025, g) if s["key"] == "mieteinnahmen")
        for g in ("gehoeft-1", "gehoeft-2")
    )
    liste = [("datei", ("mieten.pdf", b"%PDF mieten"))]
    post(client, f"/steuer/2025/stueck/{w1['id']}/datei", files=liste)
    seite = post(client, f"/steuer/2025/stueck/{w2['id']}/datei", files=liste)
    assert "dieselbe Datei wie bei Gehöft 1" in seite.text
    gebiet = client.get("/steuer/2025/gebiet/gehoeft-2").text
    assert "liegt bei Gehöft 1, zählt hier mit" in gebiet
    v = quest.verweise(conn, 2025)[0]
    post(client, f"/steuer/2025/verweis/{v['id']}/loesen")
    assert quest.stueck(conn, w2["id"])["status"] == "offen"
    assert quest.stueck(conn, w1["id"])["status"] == "gesammelt"  # die Datei bleibt


def test_mail_per_sie_nach_bereich_ohne_doppelte(conn):
    """Bericht Marie, P1-3: welche Wohnung, welche Karte, und Sie statt du."""
    p = Profil(**{**MARIE.__dict__, "anrede": "sie", "beraterin_name": "Frau Weber"})
    p.namen = {"gehoeft-1": "Lindenstraße", "gehoeft-2": "Gartenweg"}
    quest.start(conn, p)
    for g in ("gehoeft-1", "gehoeft-2"):
        s = next(x for x in quest.stuecke(conn, 2025, g) if x["key"] == "versicherung")
        quest.setze_status(conn, s["id"], "gibts_nicht")
    euer = next(s for s in quest.stuecke(conn, 2025, "werkstatt-1") if s["key"] == "euer")
    quest.setze_status(conn, euer["id"], "fragen", MACHT_BERATERIN)
    gv = next(s for s in quest.stuecke(conn, 2025, "gilde-1") if s["key"] == "verteilung")
    quest.setze_status(conn, gv["id"], "fragen", "50/50 wie im Vorjahr?")
    _, text = truhe.mail(p, reich(p), quest.stuecke(conn, 2025))
    assert text.startswith("Guten Tag Frau Weber,")
    assert "Das Passwort schicke ich Ihnen getrennt." in text and " dir " not in text
    assert "- Vermietete Wohnung · Lindenstraße: Versicherungen" in text
    assert "- Vermietete Wohnung · Gartenweg: Versicherungen" in text
    assert "Wie bisher erstellen Sie:\n- Freiberuflich: EÜR-Übersicht" in text
    assert "1. GbR / Gesellschaft, Gewinnverteilung: 50/50 wie im Vorjahr?" in text
    assert "Macht meine Beraterin" not in text
    assert spiel.zweifel(quest.stuecke(conn, 2025)) == []  # kein Silber dafür


def test_macht_meine_beraterin_und_sieg_seite_im_browser(client, ws, monkeypatch):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    euer = next(s for s in quest.stuecke(conn, 2025) if s["key"] == "euer")
    assert "Macht meine Beraterin" in client.get("/steuer/2025/gebiet/werkstatt-1").text
    seite = post(
        client, f"/steuer/2025/stueck/{euer['id']}/status", status="fragen", frage=MACHT_BERATERIN
    )
    assert "macht deine Beraterin" in seite.text
    assert "Macht die Beraterin" in client.get("/steuer/2025/gebiet/werkstatt-1").text
    # Sieg-Seite: Truhe im Finder zeigen, Passwort kopieren
    client.app.state.steuer_notausgang.add(2025)
    post(client, "/steuer/2025/truhe", schloss="passwort")
    belege = next(s for s in quest.stuecke(conn, 2025) if s["key"] == "belege")
    quest.markiere_wunsch(conn, belege["id"])
    sieg = client.get("/steuer/2025/sieg").text
    assert "Im Finder zeigen" in sieg and "data-kopieren=" in sieg
    assert "Noch offen von ihrer Liste" in sieg and belege["name"] in sieg
    gezeigt = []
    monkeypatch.setattr(system, "reveal_path", lambda p: gezeigt.append(p) or True)
    post(client, "/steuer/2025/truhe/zeigen")
    assert gezeigt and gezeigt[0].suffix == ".zip"


def test_eingefuegter_text_hat_keine_steuernummer_im_namen():
    from frits.web.steuer import textname

    name = textname("Finanzamt Prenzlauer Berg Steuernummer 31/456/78901 Bescheid")
    assert name == "Finanzamt Prenzlauer Berg Bescheid.txt"


def test_runde_11_sie_offene_wuensche_und_lange_zeilen(conn):
    """Nachtest Marie-2: Sie auch in vorformulierten Fragen, ihre Wünsche gehen nie verloren."""
    frage = "Hast du sie noch von deiner letzten Erklärung?"
    assert truhe.siezen(frage) == "Haben Sie sie noch von Ihrer letzten Erklärung?"
    p = Profil(**{**MARIE.__dict__, "anrede": "sie", "beraterin_name": "Frau Weber"})
    p.namen = {"gehoeft-1": "Lindenstraße"}
    quest.start(conn, p)
    kauf = next(s for s in quest.stuecke(conn, 2025, "gehoeft-1") if s["key"] == "kaufvertrag")
    quest.markiere_wunsch(conn, kauf["id"])
    _, text = truhe.mail(p, reich(p), quest.stuecke(conn, 2025))
    assert "Noch offen von Ihrer Liste:\n- Vermietete Wohnung · Lindenstraße: Kaufvertrag" in text
    assert " du " not in text and " dir " not in text
    # Der Name der Wohnung steht auch im Titel der Insel
    assert next(g for g in reich(p) if g.id == "gehoeft-1").titel.endswith("· Lindenstraße")
    # Zeilen mit "und" oder "inkl." werden ganz erkannt
    assert [
        t for t, _ in beraterin.zerlegen("Ausgangsrechnungen der GbR und die Gewinnverteilung")
    ] == [
        "Ausgangsrechnungen der GbR",
        "die Gewinnverteilung",
    ]
    teile = beraterin.zerlegen("Depot-Jahresübersicht inkl. Quellensteuer")
    assert [t for t, _ in teile] == ["Depot-Jahresübersicht", "Quellensteuer"]
    # Kein Zerlegen mitten im Wort
    assert len(beraterin.zerlegen("Kranken- und Pflegeversicherung")) == 1


def test_runde_12_inhaltsverzeichnis_per_sie_und_reisekosten(conn):
    """Nachtest Marie-2 nach Runde 11: gespeicherte Vorlage im Inhaltsverzeichnis, Reisekosten."""
    p = Profil(**{**MARIE.__dict__, "anrede": "sie"})
    quest.start(conn, p)
    sid = next(s for s in quest.stuecke(conn, 2025, "rathaus") if s["key"] == "steuer-id")
    quest.setze_status(conn, sid["id"], "fragen", "Ich finde sie nicht. Hast du sie noch?")
    inhalt = truhe.inhaltsverzeichnis(
        p, reich(p), quest.stuecke(conn, 2025), quest.dateien(conn, 2025)
    )
    assert "Frage: Ich finde sie nicht. Haben Sie sie noch?" in inhalt
    # "Belege zu" gilt für beide Teile, und Reisekosten gehören in die Werkstatt
    teile = beraterin.zerlegen("Belege zu Arbeitszimmer und Reisekosten")
    assert teile == [("Belege zu Arbeitszimmer", ""), ("Reisekosten", "Belege zu")]
    alle = quest.stuecke(conn, 2025)
    name = {s["id"]: (s["gebiet"], s["key"]) for s in alle}
    wuensche = beraterin.zuordnen("- Belege zu Arbeitszimmer und Reisekosten", reich(p), alle)
    assert [[name.get(i) for i in w.stuecke] for w in wuensche] == [
        [("werkstatt-1", "arbeitszimmer")],
        [("werkstatt-1", "reisen")],
    ]


def test_runde_13_vorschlaege_gratis_und_spuernase_stempelt_alle(client, ws):
    """Bericht Marie-1, P2-9/10: Vorschläge ab Start, ein Klick, alle Zettel sichtbar."""
    fragebogen(
        client,
        teil1={"name": "Marie"},
        teil2={"m_freiberuflich_0": "1", "m_gesellschaft_0": "1", "m_depots_ausland_0": "1"},
    )
    conn = quest.ensure_schema(store.connect(ws.db))
    alle = quest.stuecke(conn, 2025)
    wo = {n: spiel.ahnung(n, alle) for n in ("GbR_Rechnung_03.pdf", "US_Form1099-DIV.pdf")}
    assert (
        wo["GbR_Rechnung_03.pdf"].stueck["gebiet"],
        wo["GbR_Rechnung_03.pdf"].stueck["key"],
    ) == (
        "gilde-1",
        "rechnungen",
    )
    assert wo["US_Form1099-DIV.pdf"].stueck["key"] == "jahresuebersicht"
    assert spiel.ahnung("UStVA_Q1_2025.pdf", alle).stueck["key"] == "umsatzsteuer"
    assert spiel.ahnung("IMG_2034.jpg", alle).stueck is None
    lager = quest.lager(ws.root)
    for i in range(14):
        (lager / f"notiz-{i:02d}.txt").write_bytes(f"notiz {i}".encode())
    (lager / "GbR_Rechnung_03.pdf").write_bytes(b"%PDF gbr")
    (lager / "UStVA_Q1_2025.pdf").write_bytes(b"%PDF ustva")
    karte = client.get("/steuer/2025").text
    assert "notiz-13.txt" in karte and "weitere." not in karte  # alle Zettel, nicht nur 12
    assert "✓ Ja, passt" in karte and "Vielleicht: Rechnungen der Gesellschaft" in karte
    assert "alle 2 Vorschläge stempeln" not in karte  # das kann nur die Spürnase
    # Ein Klick legt ab
    gbr = next(s for s in alle if s["gebiet"] == "gilde-1" and s["key"] == "rechnungen")
    post(
        client, "/steuer/2025/lager/zuordnen", name="GbR_Rechnung_03.pdf", stueck_id=str(gbr["id"])
    )
    assert quest.stueck(conn, gbr["id"])["status"] == "gesammelt"
    # Spürnase: alle sicheren Vorschläge auf einmal, Notizen ohne Vorschlag bleiben liegen
    (lager / "Kontoauszug_Juni.pdf").write_bytes(b"%PDF konto")
    quest.verdiene(conn, 2025, "test", 50, "Testgold")
    post(client, "/steuer/2025/markt/kaufen", ware="spuernase")
    assert "alle 2 Vorschläge stempeln" in client.get("/steuer/2025").text
    seite = post(client, "/steuer/2025/lager/vorschlaege")
    assert "Die Spürnase hat 2 Zettel abgestempelt" in seite.text
    assert sorted(p.name for p in quest.lager_dateien(ws.root)) == [
        f"notiz-{i:02d}.txt" for i in range(14)
    ]


def test_runde_13_schnellschlaege_und_kein_sieg_ohne_beleg(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    rathaus = [s for s in quest.stuecke(conn, 2025) if s["gebiet"] == "rathaus"]
    for s in quest.stuecke(conn, 2025):
        if s["gebiet"] != "rathaus" and s["art"] != "bonus":
            quest.setze_status(conn, s["id"], "fragen")
    drache = client.get("/steuer/2025/drache").text
    assert "Schnellschläge in Rathaus" in drache
    # Ein Schnellschlag auf die zweite Karte der Welle zählt als Treffer
    zweite = spiel.drache(quest.stuecke(conn, 2025)).karten[1]
    post(
        client, "/steuer/2025/drache/schlag", stueck_id=str(zweite["id"]), aktion="fragen", frage=""
    )
    assert quest.stueck(conn, zweite["id"])["status"] == "fragen"
    # Alles nur gefragt: kein Sieg ohne ein einziges Dokument
    for s in rathaus:
        post(
            client, "/steuer/2025/drache/schlag", stueck_id=str(s["id"]), aktion="fragen", frage=""
        )
    drache = client.get("/steuer/2025/drache").text
    assert "Zeig ihm einen echten Beleg" in drache and "Sieg!" not in drache
    assert "Goldenes Zeitalter" not in client.get("/steuer/2025").text
    # Ein Dokument: Sieg
    seite = post(
        client,
        f"/steuer/2025/stueck/{rathaus[0]['id']}/datei",
        files=[("datei", ("steuer-id.pdf", b"%PDF id"))],
    )
    assert spiel.drache(quest.stuecke(conn, 2025)).besiegt
    assert "Der Drache faltet sich" in seite.text


def test_ustva_ohne_gbr_gehoert_zum_eigenen_betrieb(conn):
    quest.start(conn, Profil(**{**MARIE.__dict__, "umsatzsteuer": "quartal"}))
    alle = list(reversed(quest.stuecke(conn, 2025)))  # die Reihenfolge der Ids zählt nicht
    assert spiel.ahnung("UStVA_Q1_2025.pdf", alle).stueck["gebiet"] == "werkstatt-1"
    assert spiel.ahnung("GbR_UStVA_Q1_2025.pdf", alle).stueck["gebiet"] == "gilde-1"


def test_runde_14_eintippen_statt_datei_und_steuernummer_aus_den_einstellungen(client, ws):
    """Maries Feedback: Pendel-km und Steuernummer direkt eintippen, ohne Dokument."""
    from frits.settings import Settings

    s = Settings.load(ws.settings_file)
    s.steuernummer = "12/345/67890"
    s.save(ws.settings_file)
    fragebogen(
        client,
        teil1={"name": "Marie"},
        teil2={"m_angestellt_0": "1", "m_freiberuflich_0": "1"},
        teil3={"haken": ["arbeit"]},
    )
    conn = quest.ensure_schema(store.connect(ws.db))
    rathaus = client.get("/steuer/2025/gebiet/rathaus").text
    assert "Kein Dokument? Einfach eintippen" in rathaus and "Steuernummer: 12/345/67890" in rathaus
    fahrten = next(x for x in quest.stuecke(conn, 2025, "kaserne-1") if x["key"] == "fahrten")
    kaserne = client.get("/steuer/2025/gebiet/kaserne-1").text
    assert "14 km einfach" in kaserne  # das Beispiel im Feld
    seite = post(
        client, f"/steuer/2025/stueck/{fahrten['id']}/angabe", text="18 km einfach, 130 Tage"
    )
    assert "+1 Angabe!" in seite.text
    assert quest.stueck(conn, fahrten["id"])["status"] == "gesammelt"
    datei = quest.dateien(conn, 2025, fahrten["id"])[0]
    assert datei["original"] == "Angabe Fahrten zur Arbeit und Homeoffice-Tage.txt"
    assert "18 km einfach, 130 Tage" in Path(datei["pfad"]).read_text(encoding="utf-8")
    assert quest.lager_dateien(ws.root) == []  # nichts bleibt im Korb liegen
    # Dieselbe Angabe noch einmal: nichts doppelt
    seite = post(
        client, f"/steuer/2025/stueck/{fahrten['id']}/angabe", text="18 km einfach, 130 Tage"
    )
    assert "liegt schon bei der Karte" in seite.text
    # Leere Angabe: freundlicher Hinweis
    assert (
        "Ein Stichwort reicht" in post(client, f"/steuer/2025/stueck/{fahrten['id']}/angabe").text
    )


def test_runde_14_fragen_zeitalter_und_was_gehoert_wohin(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    karte = client.get("/steuer/2025").text
    buch = client.get("/steuer/2025/belege").text  # Runde 29: vom Tisch ins Belegbuch
    assert "Was gehört wohin?" in buch and "Sonstiges für die Beraterin" in buch
    assert "Alles vom Finanzamt" in buch
    assert "Nächstes: <strong>Zeitalter der Ordnung</strong>" in karte
    assert "Dann wacht der Papierdrache auf" in karte
    gebiet = client.get("/steuer/2025/gebiet/werkstatt-1").text
    assert "kommt in die Mail an deine Beraterin" in gebiet and 'href="/chat"' in gebiet
    stand = spiel.Stand(gesamt=10, gesammelt=1, gibts_nicht=1, fragen=0)
    assert spiel.weg_zum_zeitalter(stand) == (
        "Zeitalter der Ordnung",
        2,
        spiel.ZEITALTER_BRINGT["Zeitalter der Ordnung"],
    )
    assert spiel.weg_zum_zeitalter(spiel.Stand(10, 10, 0, 0)) is None


def test_runde_15_belegbuch_zeigt_alles_und_nimmt_fragen(client, ws):
    """Maries Wunsch: eine Übersicht aller Belege mit Vorschau, Fragen dazu und Summen."""
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    karten = quest.stuecke(conn, 2025, "werkstatt-1")
    bild, notiz = karten[0], karten[1]
    quelle = ws.root / "foto.png"
    quelle.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
    quest.sammeln(conn, ws.root, bild["id"], quelle, original="Quittung Kamera.png")
    post(client, f"/steuer/2025/stueck/{notiz['id']}/angabe", text="Arbeitszimmer 12 qm")
    buch = client.get("/steuer/2025/belege").text
    assert "Belegbuch 2025" in buch and "Quittung Kamera.png" in buch
    assert "Arbeitszimmer 12 qm" in buch  # die Angabe steht direkt da
    assert "Wer sieht deine Belege?" in buch and "Mit KI besprechen" in buch
    assert 'href="/steuer/2025/belege"' in buch  # die Belege-Marke oben führt hierher
    d = quest.dateien(conn, 2025, bild["id"])[0]
    antwort = client.get(f"/steuer/2025/datei/{d['id']}")
    assert antwort.status_code == 200 and antwort.headers["content-type"] == "image/png"
    assert antwort.headers["x-content-type-options"] == "nosniff"
    assert client.get("/steuer/2025/datei/99999").status_code == 404
    assert client.get(f"/steuer/2024/datei/{d['id']}").status_code == 404  # anderes Jahr
    # Eine Frage zu einem Beleg, der schon da ist: der Stempel bleibt, die Frage kommt in die Mail
    seite = post(client, f"/steuer/2025/stueck/{bild['id']}/frage", frage="Zählt die Kamera ganz?")
    assert "kommt in die Mail" in seite.text
    s = quest.stueck(conn, bild["id"])
    assert s["status"] == "gesammelt" and s["frage"] == "Zählt die Kamera ganz?"
    _, text = truhe.mail(
        Profil.load(quest.profil_datei(ws.root, 2025)),
        reich(Profil.load(quest.profil_datei(ws.root, 2025))),
        quest.stuecke(conn, 2025),
    )
    assert "Zählt die Kamera ganz?" in text


def test_runde_15_beleg_als_webseite_wird_nur_heruntergeladen(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    s = quest.stuecke(conn, 2025, "werkstatt-1")[0]
    quelle = ws.root / "boese.html"
    quelle.write_text("<script>alert(1)</script>", encoding="utf-8")
    quest.sammeln(conn, ws.root, s["id"], quelle)
    d = quest.dateien(conn, 2025, s["id"])[0]
    antwort = client.get(f"/steuer/2025/datei/{d['id']}")
    assert antwort.headers["content-type"] == "application/octet-stream"
    assert antwort.headers["content-disposition"].startswith("attachment")


def test_runde_15_beleg_mit_ki_oeffnet_den_freien_chat(client, ws):
    """Nichts geht raus: der Beleg landet geschwärzt im Chat, gefragt wird erst dort."""
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    s = quest.stuecke(conn, 2025, "werkstatt-1")[0]
    quelle = ws.root / "rechnung.txt"
    quelle.write_text("Rechnung über 120 Euro", encoding="utf-8")
    quest.sammeln(conn, ws.root, s["id"], quelle)
    d = quest.dateien(conn, 2025, s["id"])[0]
    gesehen = []

    def mit_datei(pfad, name):
        gesehen.append((pfad, name))
        return "abc", []

    client.app.state.notfall_mit_datei = mit_datei
    antwort = client.post(
        f"/steuer/2025/datei/{d['id']}/ki",
        data={"token": client.token},
        follow_redirects=False,
    )
    assert antwort.status_code in (302, 303)
    assert antwort.headers["location"] == "/chat/abc#unterlagen"
    assert gesehen and gesehen[0][1] == "rechnung.txt"


def test_runde_16_angabe_ist_antwort_aber_kein_beleg(client, ws):
    """Tester Marie-6, P1-4: Steuer-ID notieren und dreimal "Gibt's nicht" war schon der Sieg."""
    fragebogen(client, teil1={"name": "Marie"})
    conn = quest.ensure_schema(store.connect(ws.db))
    karten = quest.stuecke(conn, 2025, "rathaus")
    steuer_id = next(s for s in karten if s["key"] == "steuer-id")
    post(client, f"/steuer/2025/stueck/{steuer_id['id']}/angabe", text="Steuer-ID: 12 345 678 901")
    for s in karten:
        if s["id"] != steuer_id["id"] and s["art"] != "bonus":
            post(client, f"/steuer/2025/stueck/{s['id']}/status", status="gibts_nicht")
    alle = quest.stuecke(conn, 2025)
    drache = spiel.drache(alle)
    assert drache.will_beleg and not drache.besiegt
    erster = next(
        e
        for e in spiel.erfolge(alle, spiel.provinzen(reich(Profil(jahr=2025)), alle))
        if e.key == "erster-fund"
    )
    assert not erster.erreicht
    karte = client.get("/steuer/2025").text
    assert "ein echtes Dokument" in karte and ">99</b> %" in karte  # nie 100 % ohne Sieg
    # Ändern ersetzt die Angabe, Löschen macht keinen Zettel daraus
    seite = post(client, f"/steuer/2025/stueck/{steuer_id['id']}/angabe", text="Steuer-ID: 98 765")
    assert "Geändert" in seite.text
    angaben = quest.dateien(conn, 2025, steuer_id["id"])
    assert len(angaben) == 1 and "98 765" in Path(angaben[0]["pfad"]).read_text(encoding="utf-8")
    gebiet = client.get("/steuer/2025/gebiet/rathaus").text
    assert "Eingetippt:" in gebiet and "98 765" in gebiet and "Ändern" in gebiet
    seite = post(client, f"/steuer/2025/datei/{angaben[0]['id']}/entfernen")
    assert "Die Angabe ist gelöscht" in seite.text
    assert quest.lager_dateien(ws.root) == []


def test_runde_16_datei_zaehlt_auf_nachfrage_auch_fuer_eine_zweite_karte(conn, ws, client):
    """P1-2: die 1099 liegt bei der Jahresübersicht und soll auch für die Quellensteuer zählen."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_depots_ausland_0": "1"})
    c = quest.ensure_schema(store.connect(ws.db))
    hafen = {s["key"]: s for s in quest.stuecke(c, 2025, "hafen-1")}
    datei = [("datei", ("1099-DIV.pdf", b"%PDF 1099"))]
    post(client, f"/steuer/2025/stueck/{hafen['jahresuebersicht']['id']}/datei", files=datei)
    seite = post(client, f"/steuer/2025/stueck/{hafen['quellensteuer']['id']}/datei", files=datei)
    assert "Ja, hier auch zählen" in seite.text and "/steuer/2025/belege#karte-" in seite.text
    d = quest.dateien(c, 2025, hafen["jahresuebersicht"]["id"])[0]
    seite = post(
        client, f"/steuer/2025/stueck/{hafen['quellensteuer']['id']}/mitzaehlen", datei_id=d["id"]
    )
    assert "zählt mit" in seite.text
    s = quest.stueck(c, hafen["quellensteuer"]["id"])
    assert s["status"] == "gesammelt"
    assert spiel.echter_beleg(next(x for x in quest.stuecke(c, 2025) if x["id"] == s["id"]))
    # Über Provinzgrenzen hinweg nur auf Wunsch der Beraterin, sonst nicht
    rathaus = quest.stuecke(c, 2025, "rathaus")[0]
    with pytest.raises(quest.QuestError):
        quest.mitzaehlen(c, rathaus["id"], d["id"])


def test_runde_16_beraterin_grundsteuer_und_1099():
    """P1-3: "Wohnungen" sagt nur wo, nicht was; "1099" ist die Jahresübersicht des Depots."""
    gebiete = reich(KIM)
    stuecke = []
    for i, g in enumerate(gebiete):
        for j, s in enumerate(g.stuecke):
            stuecke.append(
                {
                    "id": i * 100 + j,
                    "gebiet": g.id,
                    "key": s.key,
                    "name": s.name,
                    "hinweis": s.hinweis,
                    "herkunft": "katalog",
                }
            )
    nach_id = {s["id"]: s for s in stuecke}
    w = beraterin.zuordnen("- Grundsteuerbescheid für beide Wohnungen", gebiete, stuecke)[0]
    assert set(w.gebiete) == {"gehoeft-1", "gehoeft-2"}
    assert {nach_id[i]["key"] for i in w.stuecke} == {"grundsteuer"}
    w = beraterin.zuordnen("- 1099 des US-Brokers", gebiete, stuecke)[0]
    assert w.gebiete == ("hafen-1",) and nach_id[w.stuecke[0]]["key"] == "jahresuebersicht"


def test_runde_16_vorschlaege_raten_vorsichtiger():
    """P1-5: KSK-Jahresbescheinigung ist nicht die Umsatzsteuer, ein Laptop keine Ausgangsrechnung."""
    p = Profil(jahr=2025, mengen={"freiberuflich": [1, 0]}, haken=["ksk"], umsatzsteuer="quartal")
    stuecke = []
    for i, g in enumerate(reich(p)):
        for j, s in enumerate(g.stuecke):
            stuecke.append(
                {"id": i * 100 + j, "gebiet": g.id, "key": s.key, "name": s.name, "status": "offen"}
            )
    assert spiel.vermute("KSK_Jahresbescheinigung_2025.pdf", stuecke)["key"] == "ksk"
    assert spiel.vermute("Rechnung_Laptop_2025.pdf", stuecke)["key"] == "anschaffungen"
    assert spiel.vermute("Kassenbon_Papier.jpg", stuecke)["key"] == "belege"


def test_runde_16_bekannte_datei_im_korb_wird_erkannt(client, ws):
    fragebogen(client, teil1={"name": "Marie"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    s = quest.stuecke(conn, 2025, "werkstatt-1")[0]
    post(client, f"/steuer/2025/stueck/{s['id']}/datei", files=[("datei", ("a.pdf", b"%PDF a"))])
    seite = post(client, "/steuer/2025/lager/hochladen", files=[("files", ("b.pdf", b"%PDF a"))])
    assert f"Kenne ich schon, liegt bei „{s['name']}“" in seite.text
    assert quest.lager_dateien(ws.root) == []


def test_runde_16_verschobener_frits_ordner_findet_seine_belege(tmp_path):
    """Tester Marie-6 (Runde 15): Nach Umzug oder Sicherung zeigten die Pfade ins Leere."""
    import shutil

    alt = Workspace(tmp_path / "alt")
    alt.ensure()
    c = quest.ensure_schema(store.connect(alt.db))
    quest.start(c, KIM)
    s = quest.stuecke(c, 2025, "werkstatt-1")[0]
    quest.sammeln(c, alt.root, s["id"], datei(tmp_path, "beleg.pdf", "inhalt"))
    c.close()
    shutil.copytree(alt.root, tmp_path / "neu")
    shutil.rmtree(alt.root)
    neu = Workspace(tmp_path / "neu")
    c = quest.ensure_schema(store.connect(neu.db))
    d = quest.dateien(c, 2025)[0]
    assert Path(d["pfad"]).is_file() and str(neu.root) in d["pfad"]
    ziel = truhe.packen(
        tmp_path / "t.zip", KIM, reich(KIM), quest.stuecke(c, 2025), quest.dateien(c, 2025), None
    )
    import zipfile

    with zipfile.ZipFile(ziel) as zf:
        assert any(n.endswith(".pdf") for n in zf.namelist())


def test_runde_17_kopie_neben_dem_original_fasst_das_original_nie_an(tmp_path):
    """Tester Marie-6 (Runde 16), Datensicherheit: Eine Kopie des FRITS-Ordners zeigte und
    verschob die Dateien des ORIGINALS, solange das noch da war."""
    import shutil

    alt = Workspace(tmp_path / "alt")
    alt.ensure()
    c = quest.ensure_schema(store.connect(alt.db))
    quest.start(c, KIM)
    s = quest.stuecke(c, 2025, "werkstatt-1")[0]
    quest.sammeln(c, alt.root, s["id"], datei(tmp_path, "beleg.pdf", "inhalt"))
    original = Path(quest.dateien(c, 2025)[0]["pfad"])
    c.close()
    shutil.copytree(alt.root, tmp_path / "kopie")
    kopie = Workspace(tmp_path / "kopie")
    c = quest.ensure_schema(store.connect(kopie.db))
    d = quest.dateien(c, 2025)[0]
    assert Path(d["pfad"]).is_relative_to(kopie.root) and Path(d["pfad"]).is_file()
    quest.entferne_datei(c, d["id"])
    assert original.is_file()  # das Original bleibt, wo es ist
    assert [p.name for p in quest.lager_dateien(kopie.root)] == ["beleg.pdf"]
    assert quest.lager_dateien(alt.root) == []


def test_runde_17_falscher_wunsch_laesst_sich_entfernen(conn, ws):
    quest.start(conn, KIM)
    fahrten = next(s for s in quest.stuecke(conn, 2025, "gehoeft-1") if s["key"] == "fahrten")
    quest.markiere_wunsch(conn, fahrten["id"])
    assert quest.wunsch_weg(conn, fahrten["id"]) == "markierung"
    assert not quest.stueck(conn, fahrten["id"])["wunsch"]
    neu = quest.neues_stueck(conn, 2025, "gehoeft-1", "Hausordnung", hinweis="Wunsch")
    assert quest.wunsch_weg(conn, neu) == "karte"
    with pytest.raises(quest.QuestError):
        quest.stueck(conn, neu)


def test_runde_17_drachenbaendigerin_erst_mit_echtem_beleg():
    karten = [
        {**_karte(status="gibts_nicht", id_=1), "wunsch": 0, "frage": ""},
        {**_karte(key="x", status="fragen", id_=2), "wunsch": 0, "frage": "Brauchst du das?"},
    ]
    orden = {e.key: e for e in spiel.erfolge(karten, [])}
    assert not orden["drachenbaendigerin"].erreicht


# ---------- Runde 18: Lenas Urteil ----------


def test_runde_18_urlaubsfoto_wird_nicht_einfach_gesichert(client, ws):
    # Lena: "Das Spiel glaubt dir alles." Ein Urlaubsfoto auf "Steuer-ID" gab Gold und
    # landete in der Truhe. Jetzt fragt Fritte erst, und Gold gibt es erst nach dem Ja.
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    sid = next(s for s in quest.stuecke(conn, 2025) if s["key"] == "steuer-id")
    gold = quest.gold(conn, 2025)
    seite = post(
        client,
        f"/steuer/2025/stueck/{sid['id']}/datei",
        files={"datei": ("urlaubsfoto.png", b"strand", "image/png")},
    )
    assert "Fritte stutzt" in seite.text and "klingt nach einem privaten Foto" in seite.text
    assert "Trotzdem ablegen" in seite.text and "Beiseite legen" in seite.text
    assert "+1 Beleg" not in seite.text
    assert quest.stueck(conn, sid["id"])["status"] == "offen" and quest.gold(conn, 2025) == gold
    assert (quest.lager(ws.root) / "urlaubsfoto.png").read_bytes() == b"strand"
    # "Trotzdem ablegen" geht immer
    seite = post(
        client,
        "/steuer/2025/lager/zuordnen",
        name="urlaubsfoto.png",
        stueck_id=str(sid["id"]),
        trotzdem="1",
        back="/steuer/2025/gebiet/rathaus",
    )
    assert "+1 Beleg!" in seite.text
    assert quest.stueck(conn, sid["id"])["status"] == "gesammelt"


def test_runde_18_auch_im_drachenkampf_und_aus_dem_korb(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    karte = spiel.drache(quest.stuecke(conn, 2025)).karte
    seite = post(
        client,
        "/steuer/2025/drache/schlag",
        files={"datei": ("Strand-Selfie.jpg", b"sonne", "image/jpeg")},
        stueck_id=str(karte["id"]),
        aktion="datei",
    )
    assert "Fritte stutzt" in seite.text and 'data-treffer="kritisch"' not in seite.text
    assert quest.stueck(conn, karte["id"])["status"] == "offen"
    seite = post(
        client,
        "/steuer/2025/drache/schlag",
        stueck_id=str(karte["id"]),
        aktion="zettel",
        zettel="Strand-Selfie.jpg",
    )
    assert "Fritte stutzt" in seite.text and quest.stueck(conn, karte["id"])["status"] == "offen"
    assert "Es bleibt erst mal im Eingangskorb" in seite.text  # lag ja schon dort
    seite = post(client, "/steuer/2025/lager/beiseite", name="Strand-Selfie.jpg", back="/")
    assert (quest.beiseite(ws.root) / "Strand-Selfie.jpg").exists()


def test_runde_18_pruefung_im_einzelnen(tmp_path):
    karte = {
        "id": 1,
        "gebiet": "rathaus",
        "key": "steuer-id",
        "name": "Steuer-ID",
        "status": "offen",
    }
    assert pruefung.privat_wort("urlaubsfoto.png") == "urlaub"
    assert pruefung.privat_wort("Hochzeit_2025.jpg") == "hochzeit"
    # Echte Unterlagen mit ähnlichen Wörtern bleiben unbehelligt
    for name in (
        "Urlaubsgeld_Abrechnung.pdf",
        "Familienkasse.pdf",
        "Hundesteuer.pdf",
        "Babysitter.pdf",
    ):
        assert pruefung.privat_wort(name) is None, name
    # Neutrale Namen fragen nicht nach
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert pruefung.pruefe(pdf, "Scan_0042.pdf", karte, [karte]) is None


@pytest.mark.skipif(not shutil.which("tesseract"), reason="Texterkennung nicht installiert")
def test_runde_18_foto_ohne_text_und_kassenbon(tmp_path):
    from PIL import Image

    karte = {"id": 1, "gebiet": "werkstatt-1", "key": "belege", "name": "Belege", "status": "offen"}
    bild = tmp_path / "IMG_1234.jpg"
    Image.new("RGB", (800, 600), (60, 120, 200)).save(bild)
    z = pruefung.pruefe(bild, "IMG_1234.jpg", karte, [karte])
    assert z is not None and z.grund == "kein_text"
    bon = Path(__file__).parent / "fixtures" / "docs" / "11_baumarkt.jpg"
    assert pruefung.pruefe(bon, "IMG_1235.jpg", karte, [karte]) is None


def test_runde_18_ehrliche_gold_serie_und_gewuerze_waermen_auf():
    assert "Doppelschlag!" not in spiel.RUFE
    # Eine Währung (2.11.): Die Serie heißt nur noch Serie, nirgends Gold
    assert not any("Gold" in r for r in spiel.RUFE) and "Gold" not in spiel.SERIE_WARTET
    assert spiel.start_serie(0) == 0
    assert spiel.start_serie(1) == 2 and spiel.multiplikator(spiel.start_serie(1)) == 2
    assert spiel.start_serie(7) == 4  # höchstens drei Gewürze zählen


def test_runde_18_drache_zeigt_den_echten_faktor(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    karte = spiel.drache(quest.stuecke(conn, 2025)).karte
    post(client, "/steuer/2025/drache/schlag", stueck_id=str(karte["id"]), aktion="fragen")
    zweite = spiel.drache(quest.stuecke(conn, 2025)).karte
    seite = post(client, "/steuer/2025/drache/schlag", stueck_id=str(zweite["id"]), aktion="fragen")
    # Runde 19: Fragen treffen, aber die Serie wartet auf Belege (Banner grau, ×1 + 2)
    assert "Die Serie wartet auf den nächsten Beleg" in seite.text
    assert "sq-combo wartet" in seite.text and "Serie ×2" in seite.text
    assert "1 × 1" not in seite.text


def test_runde_18_erwachen_bietet_den_hafen_an(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    quest.verdiene(conn, 2025, "test", 9, "Testgold")
    offen = [s for s in spiel.pflicht(quest.stuecke(conn, 2025)) if s["status"] == "offen"]
    for s in offen:
        stand = quest.stuecke(conn, 2025)
        if spiel.drache_wach(stand):
            break
        nachher = post(client, f"/steuer/2025/stueck/{s['id']}/status", status="gibts_nicht")
    assert "Der Drache erwacht!" in nachher.text
    assert "Erst Helfer anheuern" in nachher.text


def test_runde_18_freiberuflich_aus_der_einrichtung(client, ws):
    seite = client.get("/steuer/2025/fragebogen?teil=2")
    if "Welche Provinzen" not in seite.text:
        post(client, "/steuer/2025/fragebogen", teil="1", name="Kim")
        seite = client.get("/steuer/2025/fragebogen?teil=2")
    assert "Aus deiner Einrichtung übernommen" in seite.text


def test_runde_18_falscher_wunsch_laesst_sich_zurueckholen(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    neu = quest.neues_stueck(
        conn, 2025, "rathaus", "Hausordnung", hinweis="Wunsch deiner Beraterin"
    )
    seite = post(
        client, f"/steuer/2025/stueck/{neu}/wunsch-weg", back="/steuer/2025/gebiet/rathaus"
    )
    assert "Doch gewünscht" in seite.text
    seite = post(
        client,
        "/steuer/2025/wunsch-zurueck",
        gebiet="rathaus",
        name="Hausordnung",
        hinweis="Wunsch deiner Beraterin",
        status="offen",
        back="/steuer/2025/gebiet/rathaus",
    )
    assert "wieder ein Wunsch der Beraterin" in seite.text
    wieder = [s for s in quest.stuecke(conn, 2025) if s["name"] == "Hausordnung"]
    assert len(wieder) == 1 and wieder[0]["wunsch"] and wieder[0]["herkunft"] == "beraterin"


def test_runde_18_drachensieg_gibt_fritte_eine_truhe(client, ws, monkeypatch):
    # Brücke Quest -> Alltag: Der Sieg legt bei Fritte eine Truhe ab, einmal pro Jahr.
    # frits.spiel.truhen kommt aus dem Fritte-Thread; bis dahin hier ein Stellvertreter.
    import sys
    import types

    gegeben = []
    attrappe = types.ModuleType("frits.spiel.truhen")

    def truhe_geben(conn, grund, seltenheit=None, text="", heute=None):
        neu = grund not in [g[0] for g in gegeben]
        gegeben.append((grund, seltenheit, text))
        return neu

    attrappe.truhe_geben = truhe_geben
    monkeypatch.setitem(sys.modules, "frits.spiel.truhen", attrappe)
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    while spiel.drache(quest.stuecke(conn, 2025)).lp > 1:
        k = spiel.drache(quest.stuecke(conn, 2025)).karte
        post(client, "/steuer/2025/drache/schlag", stueck_id=str(k["id"]), aktion="fragen")
    k = spiel.drache(quest.stuecke(conn, 2025)).karte
    seite = post(client, "/steuer/2025/drache/schlag", stueck_id=str(k["id"]), aktion="fragen")
    assert gegeben == [("drachensieg:2025", "selten", "Der Drache 2025 ist besiegt")]
    # Im vollen Siegesdialog steht die Belohnung als Kachel: fette Zeile, kleiner Rest
    assert (
        "<b>Fritte hat eine Truhe für dich bekommen.</b> <small>Sie wartet bei Fritte.</small>"
        in seite.text
    )
    assert 'href="/fritte#truhen"' in seite.text


def test_runde_18_sieg_ohne_fritte_truhen_bricht_nicht(client, ws, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "frits.spiel.truhen", None)  # Import schlägt fehl
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    while spiel.drache(quest.stuecke(conn, 2025)).lp > 1:
        k = spiel.drache(quest.stuecke(conn, 2025)).karte
        post(client, "/steuer/2025/drache/schlag", stueck_id=str(k["id"]), aktion="fragen")
    k = spiel.drache(quest.stuecke(conn, 2025)).karte
    seite = post(client, "/steuer/2025/drache/schlag", stueck_id=str(k["id"]), aktion="fragen")
    assert seite.status_code == 200 and "Fritte hat eine Truhe" not in seite.text
    assert spiel.drache(quest.stuecke(conn, 2025)).besiegt


def test_runde_19_serie_waechst_nur_mit_belegen():
    combo, ruf = spiel.treffer(2, kritisch=False, zaehlt=False)
    assert combo == 2
    combo, ruf = spiel.treffer(2, kritisch=True)
    assert combo == 4 and ruf.startswith(spiel.VOLLTREFFER)
    # "Legendär" erst ganz oben, bei ×8
    assert len(spiel.RUFE) == spiel.MAX_MULT + 1
    assert all("Legendär" not in r for r in spiel.RUFE[: spiel.MAX_MULT])
    assert "Legendär" in spiel.treffer(6, kritisch=True)[1]
    assert "Legendär" not in spiel.treffer(5, kritisch=True)[1]


def test_runde_19_banner_zeigt_den_naechsten_beleg_vorab(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    from frits.spiel import kassensturz

    seite = client.get("/steuer/2025/drache")
    start = spiel.start_serie(len(kassensturz.regal(conn)))
    naechster = spiel.multiplikator(start + 2)
    assert f"Serie ×{naechster}" in seite.text
    assert (
        f"Nächster Beleg: {spiel.GOLD_FUND} × {naechster} = "
        f"{spiel.GOLD_FUND * naechster} Salzkörner" in seite.text
    )
    # Zwei Fragen: Die Vorschau bleibt gleich, die Serie wartet
    for _ in range(2):
        karte = spiel.drache(quest.stuecke(conn, 2025)).karte
        seite = post(
            client, "/steuer/2025/drache/schlag", stueck_id=str(karte["id"]), aktion="fragen"
        )
    assert f"Serie ×{naechster}" in seite.text and "sq-combo wartet" in seite.text


def test_runde_19_helfer_sagen_im_kampf_was_sie_tun(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    quest.verdiene(conn, 2025, "test-hafen", 40, "Testgold")
    post(client, "/steuer/2025/markt/kaufen", ware="spuernase")
    seite = client.get("/steuer/2025/drache")
    assert spiel.im_kampf("spuernase") in seite.text
    assert f"Nächster Beleg: {spiel.GOLD_FUND - 1} ×" in seite.text
    assert all(spiel.im_kampf(w.key) for w in spiel.MARKT if w.art == "helfer")


def test_runde_19_sanduhr_warnt_vor_dem_gewuerz_deckel(client, ws, monkeypatch):
    from frits.spiel import kassensturz

    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    seite = client.get("/steuer/2025/markt")
    assert "höher starten, als die Sanduhr erlaubt" not in seite.text  # ein Gewürz: kein Problem
    monkeypatch.setattr(kassensturz, "regal", lambda conn: ["a", "b", "c"])
    seite = client.get("/steuer/2025/markt")
    assert "höher starten, als die Sanduhr erlaubt" in seite.text
    assert "×4" not in seite.text  # nur eine Zahl: der Faktor steht im Kampf, nicht hier


def test_runde_19_toene_laufen_ueber_den_klangrahmen():
    js = (Path(__file__).parent.parent / "src/frits/web/static/steuer.js").read_text()
    klang = (Path(__file__).parent.parent / "src/frits/assets/sounds/frits-sound.js").read_text()
    assert "fs.tone(" in js
    assert "function tone(" in klang and "tone: tone" in klang and "host.tone(" in klang


def test_runde_31_eine_waehrung_quest_zahlt_in_frittes_beutel(client, ws):
    # Marie (2.11.): eine Währung statt Gold und Salzkörner. Die Quest zahlt direkt in
    # Frittes Beutel, der Sieg tauscht nichts mehr um.
    from frits.spiel import stand

    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    markt = client.get("/steuer/2025/markt").text
    assert "derselbe Beutel wie bei Fritte" in markt and "wird zu Salzkörnern" not in markt
    conn = quest.ensure_schema(store.connect(ws.db))
    vorher = stand.guthaben(conn)
    s = next(s for s in quest.stuecke(conn, 2025) if s["status"] == "offen")
    post(client, f"/steuer/2025/stueck/{s['id']}/datei", files=[("datei", ("a.pdf", b"%PDF a"))])
    assert stand.guthaben(conn) > vorher  # derselbe Beutel
    assert quest.gold(conn, 2025) == stand.guthaben(conn)
    assert any("Steuer-Quest 2025" in z["text"] for z in stand.kassenbuch(conn))
    leiste = client.get("/steuer/2025").text
    assert "Salzkörner</span></a></li>" in leiste and "> Gold</a>" not in leiste
    _bis_wach(conn)
    while not spiel.drache(quest.stuecke(conn, 2025)).besiegt:
        k = spiel.drache(quest.stuecke(conn, 2025)).karte
        seite = post(client, "/steuer/2025/drache/schlag", stueck_id=str(k["id"]), aktion="fragen")
    assert "übriges Gold" not in seite.text and quest.gold(conn) > 0


def test_runde_31_deckel_pro_steuerjahr(conn):
    assert quest.verdiene(conn, 2025, "a", 100, "viel") == 100
    assert quest.verdiene(conn, 2025, "b", 50, "zu viel") == spiel.SALZ_DECKEL - 100
    assert quest.verdiene(conn, 2025, "c", 5, "nichts mehr") == 0
    assert quest.hat_gold(conn, 2025, "c")  # steht mit 0 im Buch, klopft nicht wieder an
    assert quest.verdiene(conn, 2024, "a", 5, "anderes Jahr") == 5
    # Zurückbuchen macht wieder Platz, neu verdienen geht bis zum Deckel
    quest.storniere(conn, 2025, "a", "viel")
    assert quest.jahres_salz(conn, 2025) == spiel.SALZ_DECKEL - 100
    assert quest.verdiene(conn, 2025, "a", 10, "neu") == 10


def test_runde_31_altes_gold_zieht_einmal_um(conn):
    from frits.spiel import stand

    quest.ensure_schema(conn)
    stand.vorbereiten(conn)
    zeilen = [
        (2025, 30, "fund:x", "Belege"),
        (2025, -12, "kauf:spuernase", "Gekauft: Spürnase"),
        (2024, 60, "fund:y", "Belege"),  # schon gewonnen, Restgold wurde Salz (alter Weg)
        (2021, 90, "fund:z", "Belege"),  # zu alt: zahlt nicht in den Beutel
    ]
    for jahr, betrag, grund, text in zeilen:
        conn.execute(
            "INSERT INTO steuer_gold (jahr, zeit, betrag, grund, text) VALUES (?, 'x', ?, ?, ?)",
            (jahr, betrag, grund, text),
        )
    stand.setze(conn, "drachensieg_salz:2024", "2026-10-30")
    conn.commit()
    quest.umziehen(conn)
    assert stand.guthaben(conn) == 10 - 4  # ein Drittel, wie die neuen Preise
    assert quest.goldsummen(conn, 2025) == (10, 4)
    quest.umziehen(conn)
    assert stand.guthaben(conn) == 6  # nur einmal
    # Die alte Zeile brachte im Beutel nichts: Zurückbuchen nimmt dafür auch nichts
    assert quest.storniere(conn, 2025, "fund:x", "Belege") == 10
    assert stand.guthaben(conn) == 6


def test_runde_31_regal_fuer_die_insel_in_frittes_laden(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    quest.verdiene(conn, 2025, "test", 20, "Testgold")
    regal = client.app.state.helfer_regal(conn)
    assert regal["jahr"] == 2025 and len(regal["waren"]) == 5
    laden = client.get("/fritte/laden")
    if laden.status_code != 200:
        pytest.skip("Frittes Laden ist in diesem Aufbau nicht da")
    assert 'id="insel"' in laden.text and "Für die Insel" in laden.text
    seite = post(client, "/steuer/2025/markt/kaufen", ware="sanduhr", zurueck="laden")
    assert (
        str(seite.url).endswith("/fritte/laden#insel") and "Sanduhr spielt jetzt mit" in seite.text
    )
    assert "sanduhr" in quest.helfer(conn, 2025)
    assert 'href="/fritte/laden#insel"' in client.get("/steuer/2025").text


def test_laden_zeigt_wirkungen_als_kleine_formel(client, ws):
    """Marie (3.11.): „Salzstreuer ×2 für Fragen“ statt eines Satzes; der Satz steht hinter
    dem Info-i. Die Zahlen folgen den Konstanten."""
    assert spiel.ware("brieftaube").kurz == "{salz}×2 für Fragen"
    assert spiel.ware("goldwaage").kurz == "{salz}×2 für Schätze"
    assert spiel.ware("sanduhr").kurz == "Tagesziel {salz}×3"
    assert spiel.ware("brieftaube").haken_kurz == "„Gibt's nicht“ {salz}×0"
    assert all(w.kurz and w.haken_kurz for w in spiel.MARKT if w.art == "helfer")
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    quest.verdiene(conn, 2025, "test", 20, "Testgold")
    for seite in (client.get("/fritte/laden").text, client.get("/steuer/2025/markt").text):
        karte = seite.split(">Brieftaube ")[1].split("</form>")[0]
        info, rest = karte.split('<span class="info-i-text" role="note">')[1].split("</span>", 1)
        # der ganze Satz steht nur noch hinter dem Info-i, sichtbar ist die Formel
        assert "Fragen an die Beraterin bringen 2 Salzkörner statt 1." in info
        assert "Fragen an die Beraterin" not in rest
        assert "×2 für Fragen" in rest and "salz-zeichen" in rest.split("×2 für Fragen")[0]


def test_runde_20_ein_tagesziel_mit_fritte(client, ws, monkeypatch):
    # Ein Ziel unter einem Namen: Mit Fritte zeigt das Tagwerk Frittes gemeinsame Zählung.
    # frits.spiel.tagesziel kommt aus dem Fritte-Thread; bis dahin hier ein Stellvertreter.
    import sys
    import types

    stand = {"erledigt": 3}
    attrappe = types.ModuleType("frits.spiel.tagesziel")

    def zaehlen(conn, heute):
        n = stand["erledigt"]
        return {
            "erledigt": n,
            "ziel": 3,
            "geschafft": n >= 3,
            "text": "Tagesziel geschafft" if n >= 3 else f"Tagesziel: {n} von 3 geschafft",
            "erklaerung": "Es zählt, was heute echt erledigt wurde.",
        }

    attrappe.zaehlen = zaehlen
    monkeypatch.setitem(sys.modules, "frits.spiel.tagesziel", attrappe)
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    stand["erledigt"] = 2
    seite = client.get("/steuer/2025")
    # Runde 29: Mit Fritte steht das gemeinsame Tagesziel auf Heute, nicht noch einmal auf der Insel
    assert "Tagesziel: 2 von 3 geschafft" not in seite.text and "sq-tagwerk-ring" not in seite.text
    # Das Ziel wurde bei Fritte erreicht: Der nächste Quest-Schritt zahlt das Tagwerk-Gold
    stand["erledigt"] = 3
    conn = quest.ensure_schema(store.connect(ws.db))
    s = next(s for s in quest.stuecke(conn, 2025) if s["status"] == "offen")
    seite = post(client, f"/steuer/2025/stueck/{s['id']}/status", status="fragen")
    assert "Tagesziel geschafft: 3 echte Erledigungen heute" in seite.text
    assert quest.hat_gold(conn, 2025, f"tagwerk:{date.today()}")
    # Nur einmal am Tag
    s = next(s for s in quest.stuecke(conn, 2025) if s["status"] == "offen")
    seite = post(client, f"/steuer/2025/stueck/{s['id']}/status", status="fragen")
    assert "Tagesziel geschafft: 3" not in seite.text


def test_runde_20_ohne_fritte_bleibt_das_tagwerk(client, ws, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "frits.spiel.tagesziel", None)  # Import schlägt fehl
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    seite = client.get("/steuer/2025")
    assert "Tagesziel heute" in seite.text and "Tagwerk" not in seite.text


def test_runde_20_alte_jahre_versprechen_kein_gold(client, ws):
    # Lena (Runde 19): 2023 zeigte „Nächster Beleg: 3 × 6 = 18 Gold“, gezahlt wurde 0.
    fragebogen(client, jahr=2023, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn, 2023)
    seite = client.get("/steuer/2023/drache")
    assert "Keine Salzkörner in alten Jahren" in seite.text
    assert "Nächster Beleg" not in seite.text and "Serie ×" not in seite.text
    karte = spiel.drache(quest.stuecke(conn, 2023)).karte
    seite = post(client, "/steuer/2023/drache/schlag", stueck_id=str(karte["id"]), aktion="fragen")
    assert "Serie ×" not in seite.text.split("sq-combo")[1].split("</div>")[0]


def test_runde_20_banner_ohne_widerspruch(client, ws):
    # Lena (Runde 19): „jeder Beleg legt 2 drauf“ passte nicht zu ×1 → ×2
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    seite = client.get("/steuer/2025/drache")
    assert "legt 2 drauf" not in seite.text and "Die Serie steht bei" not in seite.text


def test_runde_21_orden_stellen_zierden_auf_die_insel(client, ws):
    # Eine Sammlung statt drei: Jeder Orden stellt seine Zierde auf die Insel
    assert set(spiel.ORDEN_ZIERDE) == {e.key for e in spiel.erfolge([], [])}
    assert len(set(spiel.ORDEN_ZIERDE.values())) == len(spiel.ORDEN_ZIERDE)
    assert all(spiel.ware(z) and spiel.ware(z).art == "zierde" for z in spiel.ORDEN_ZIERDE.values())
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    s = next(s for s in quest.stuecke(conn, 2025) if s["status"] == "offen")
    seite = post(client, f"/steuer/2025/stueck/{s['id']}/status", status="fragen")
    assert "Neuer Orden: Mutig gefragt." in seite.text
    assert "Neu auf deiner Insel: Kirschbaum." in seite.text
    assert "kirschbaum" in quest.besitz(conn, 2026)
    assert 'class="sq-zier kirschbaum' in client.get("/steuer/2025").text


def test_runde_21_orden_fuer_frittes_sammlung(conn):
    leer = quest.orden_fuer_sammlung(conn)
    assert len(leer) == 8 and not any(o["erreicht"] for o in leer)
    assert {"jahr", "key", "name", "zeichen", "beschreibung", "erreicht", "zierde"} <= set(leer[0])
    quest.notiere(conn, 2024, "erfolg", "Erster Fund")
    quest.notiere(conn, 2025, "erfolg", "Erster Fund")
    quest.notiere(conn, 2025, "erfolg", "Erster Fund")  # doppelt notiert zählt einmal
    alle = quest.orden_fuer_sammlung(conn)
    erreicht = [(o["jahr"], o["key"]) for o in alle if o["erreicht"]]
    assert erreicht == [(2024, "erster-fund"), (2025, "erster-fund")]
    assert len(alle) == 9 and next(o for o in alle if o["erreicht"])["zierde"] == "laternen"


def test_runde_21_insel_fuehrt_in_die_sammlung(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    insel = client.get("/steuer/2025").text
    assert 'href="/sammlung"><span class="sq-werkzeug-orden"' in insel
    assert '<ul class="sq-orden">' not in insel  # eine Vitrine statt zwei
    assert '<ul class="sq-orden">' not in client.get("/steuer/2025/belege").text
    vitrine = client.get("/sammlung")
    assert vitrine.status_code == 200
    assert "Gutes Gedächtnis" in vitrine.text and "Brunnen" in vitrine.text


def _fake_tagesziel(monkeypatch, n=3):
    import sys
    import types

    attrappe = types.ModuleType("frits.spiel.tagesziel")
    attrappe.zaehlen = lambda conn, heute: {
        "erledigt": n,
        "ziel": 3,
        "geschafft": n >= 3,
        "text": "Tagesziel geschafft",
        "erklaerung": "",
    }
    monkeypatch.setitem(sys.modules, "frits.spiel.tagesziel", attrappe)


def test_runde_21_tagwerk_gold_einmal_am_tag_ueber_alle_jahre(client, ws, monkeypatch):
    # Lena (Runde 20): „Tagwerk +5“ wurde pro Reich doppelt gezahlt
    _fake_tagesziel(monkeypatch)
    for jahr in (2025, 2024):
        fragebogen(client, jahr=jahr, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    for jahr in (2025, 2024):
        s = next(s for s in quest.stuecke(conn, jahr) if s["status"] == "offen")
        post(client, f"/steuer/{jahr}/stueck/{s['id']}/status", status="fragen")
    tag = f"tagwerk:{date.today()}"
    assert quest.hat_gold(conn, 2025, tag) and not quest.hat_gold(conn, 2024, tag)


def test_runde_22_zierde_nur_fuer_notierte_orden(client, ws):
    # Lena (Runde 21): Der Brunnen stand auf der Insel, die Sammlung kannte den Orden nicht.
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    quest.zierde_freischalten(conn, "brunnen")  # wie der alte Fehler: aufgestellt, nie notiert
    quest.zierde_freischalten(conn, "laternen")
    conn.execute(
        "INSERT INTO steuer_gold (jahr, zeit, betrag, grund, text) VALUES (2024, 'x', -10,"
        " 'kauf:laternen', 'Gekauft: Laternen')"
    )
    conn.commit()
    insel = client.get("/steuer/2025").text
    assert 'class="sq-zier brunnen' not in insel  # kein Orden, kein Brunnen
    assert 'class="sq-zier laternen' in insel  # früher mit Gold gekauft: bleibt
    quest.notiere(conn, 2024, "erfolg", "Gutes Gedächtnis")
    assert 'class="sq-zier brunnen' in client.get("/steuer/2025").text


def test_runde_22_erreichte_orden_werden_nachgetragen(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    s = next(s for s in quest.stuecke(conn, 2025) if s["status"] == "offen")
    conn.execute("UPDATE steuer_stuecke SET status = 'fragen' WHERE id = ?", (s["id"],))
    conn.commit()  # erreicht, aber nie notiert (alter Spielstand)
    seite = client.get("/steuer/2025").text
    assert "Orden nachgetragen: Mutig gefragt." in seite
    assert quest.hat_notiz(conn, 2025, "erfolg", "Mutig gefragt")
    assert 'class="sq-zier kirschbaum' in client.get("/steuer/2025").text
    assert "Orden nachgetragen" not in client.get("/steuer/2025").text  # nur einmal


def test_runde_22_eine_zahl_und_ein_name():
    trommel = spiel.ware("trommel")
    assert "×" not in trommel.text
    assert "Tagwerk" not in spiel.ware("sanduhr").text


def test_runde_22_gewuerz_ruf_ohne_zweite_zahl(client, ws, monkeypatch):
    from frits.spiel import kassensturz

    monkeypatch.setattr(kassensturz, "regal", lambda conn: ["a", "b", "c"])
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    _bis_wach(quest.ensure_schema(store.connect(ws.db)))
    seite = client.get("/steuer/2025/drache").text
    assert "Deine Serie startet schon warm" in seite
    assert "startet bei ×" not in seite


def test_runde_22_sammlung_als_marke_in_der_leiste(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    for pfad in ("/steuer/2025", "/steuer/2025/markt", "/steuer/2025/belege"):
        seite = client.get(pfad).text
        assert '<li class="sq-marke sammlung"><a href="/sammlung"' in seite, pfad


def test_runde_23_zierde_sagt_woher_sie_kommt(client, ws):
    # Lena (Runde 22): Der Brunnen steht auch in 2023, der Orden ist aber aus 2024
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    quest.notiere(conn, 2024, "erfolg", "Gutes Gedächtnis")
    insel = client.get("/steuer/2025").text
    assert "Brunnen: Belohnung für den Orden „Gutes Gedächtnis“ (2024)" in insel


def test_runde_23_markt_ohne_alte_saetze(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    markt = client.get("/steuer/2025/markt").text
    assert "Hier wird es zu Zierde" not in markt
    assert "×2, ×3" not in markt


def test_runde_23_fehlklick_im_kampf_zuruecknehmen(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    seite = client.get("/steuer/2025/drache").text
    m = re.search(r'name="stueck_id" value="(\d+)"', seite)
    sid = int(m.group(1))
    seite = post(client, "/steuer/2025/drache/schlag", stueck_id=sid, aktion="fragen").text
    assert "Verklickt? Zurücknehmen" in seite
    seite = post(client, "/steuer/2025/drache/zuruecknehmen", stueck_id=sid).text
    assert "ist wieder beim Drachen" in seite
    assert next(s for s in quest.stuecke(conn, 2025) if s["id"] == sid)["status"] == "offen"
    # Ein zweites Mal geht nicht: nichts mehr zurückzunehmen
    seite = post(client, "/steuer/2025/drache/zuruecknehmen", stueck_id=sid).text
    assert "nicht mehr zurücknehmen" in seite


def test_runde_24_hafen_auf_der_insel_ohne_zierde_fuer_gold(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    insel = client.get("/steuer/2025").text
    assert "Gold für Zierde" not in insel
    assert 'title="Hafenmarkt: Salzkörner für Helfer im Kampf"' in insel
    js = (Path(__file__).parents[1] / "src/frits/web/static/steuer.js").read_text(encoding="utf-8")
    assert "sq-zier-zettel" in js and "(hover: none)" in js  # Herkunft auf Touch per Antippen


def test_runde_26_fritte_stutzt_nicht_bei_der_quelle_der_karte(tmp_path):
    # Marie (Durchlauf 7): Die Steuer-ID sagt selbst "steht auf der Lohnsteuerbescheinigung",
    # trotzdem stutzte Fritte genau bei dieser Datei.
    from frits.steuer.katalog import quellen_text

    steuer_id = {"id": 1, "gebiet": "rathaus", "key": "steuer-id", "name": "Steuer-ID"}
    lstb = {"id": 2, "gebiet": "kaserne", "key": "lohnsteuerbescheinigung"}
    lstb["name"] = "Lohnsteuerbescheinigung"
    steuer_id["status"] = lstb["status"] = "offen"
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    alle = [steuer_id, lstb]
    name = "Lohnsteuerbescheinigung 2025.pdf"
    z = pruefung.pruefe(pdf, name, steuer_id, alle)
    assert z is not None and z.grund == "andere_karte"  # ohne Wissen um die Quelle: stutzt
    quellen = quellen_text("rathaus", "steuer-id")
    assert "Lohnsteuerbescheinigung" in quellen
    assert pruefung.pruefe(pdf, name, steuer_id, alle, quellen=quellen) is None
    # Ausdrücklich gewählt (Menü im Eingangskorb): kein Raten nach dem Dateinamen
    assert pruefung.pruefe(pdf, "LStB.pdf", steuer_id, alle, ausdruecklich=True) is None
    # Ein Urlaubsfoto bleibt aber verdächtig, auch ausdrücklich gewählt
    z = pruefung.pruefe(pdf, "Urlaub.pdf", steuer_id, alle, ausdruecklich=True)
    assert z is not None and z.grund == "privat"


def test_runde_26_rueckfrage_bleibt_am_zettel(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    lager = quest.lager(ws.root)
    lager.mkdir(parents=True, exist_ok=True)
    (lager / "Strandurlaub.pdf").write_bytes(b"%PDF-1.4 strand")
    s = next(s for s in quest.stuecke(conn, 2025) if s["key"] == "steuer-id")
    seite = post(
        client, "/steuer/2025/lager/zuordnen", name="Strandurlaub.pdf", stueck_id=s["id"]
    ).text
    assert "Fritte stutzt" in seite
    # Neu laden: Die Frage steht weiter am Zettel, mit dem Weg "Trotzdem"
    karte = client.get("/steuer/2025").text
    assert 'class="sq-ahnung sq-stutzt"' in karte and "Trotzdem zu „Steuer-ID“" in karte
    seite = post(
        client,
        "/steuer/2025/lager/zuordnen",
        name="Strandurlaub.pdf",
        stueck_id=s["id"],
        trotzdem="1",
    ).text
    assert "sq-stutzt" not in client.get("/steuer/2025").text


def test_runde_26_sieg_ohne_textwand():
    from frits.web.steuer import kopf_rest

    assert kopf_rest("Neuer Orden: Drachenbändigerin. Der Drache hat keine Karten mehr.") == (
        "Neuer Orden: Drachenbändigerin.",
        "Der Drache hat keine Karten mehr.",
    )
    assert kopf_rest("Gold-Siegel! Werkstatt ist komplett.") == (
        "Gold-Siegel!",
        "Werkstatt ist komplett.",
    )
    assert kopf_rest("Letzter Treffer: 3 × 4 = +12 Gold.") == (
        "Letzter Treffer: 3 × 4 = +12 Gold.",
        "",
    )


def test_runde_27_brief_vom_arbeitgeber_ist_kein_arbeitszimmer():
    alle = [
        {"id": 1, "gebiet": "kaserne", "key": "fahrten", "status": "offen"},
        {"id": 2, "gebiet": "kaserne", "key": "lohnsteuerbescheinigung", "status": "offen"},
        {"id": 3, "gebiet": "werkstatt-1", "key": "arbeitszimmer", "status": "offen"},
    ]
    alle[0]["name"] = "Fahrten zur Arbeit und Homeoffice-Tage"
    alle[1]["name"] = "Lohnsteuerbescheinigung"
    alle[2]["name"] = "Homeoffice oder Arbeitszimmer"
    a = spiel.ahnung("Brief_vom_Arbeitgeber.pdf", alle)
    assert a.stueck is None and a.gebiet == "kaserne"  # nur die Provinz, keine falsche Karte
    assert spiel.ahnung("Arbeitszimmer_Grundriss.pdf", alle).stueck["key"] == "arbeitszimmer"
    assert spiel.ahnung("Lohnsteuerbescheinigung_2025.pdf", alle).stueck["id"] == 2


def test_runde_28_hafenmarkt_ist_ein_stand_und_klappt_selten_gebrauchtes_ein(client, ws):
    """Marie (2.11.): Der Hafenmarkt soll schöner sein und sich abheben; weniger auf einmal."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    markt = client.get("/steuer/2025/markt").text
    assert 'class="sq-markise"' in markt and 'class="sq-theke"' in markt
    # Zierden, Goldregeln und Goldbuch liegen zugeklappt unter dem Stand
    assert markt.count('<details class="sq-papier sq-klapp') == 3
    assert "Zierden für deine Insel" in markt and "0 von 8" in markt
    insel = client.get("/steuer/2025").text
    assert 'class="sq-hafen-stand"' in insel and "sq-hafen-muenze" not in insel


def test_runde_29_insel_aufgeraeumt(client, ws):
    """Marie (2.11.): zu viele Menüs und Optionen. Die Insel hat einen Weg hinein, eine schlanke
    Leiste und eine Fritte-Blase; Seltenes liegt im Belegbuch."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    insel = client.get("/steuer/2025").text
    assert "Insel zu <b" in insel and '<details class="sq-mehr-marken">' in insel
    assert insel.count("data-einwurf") == 1 and "Alles hier ablegen" in insel
    assert 'id="sq-korb"' not in insel and "ohne-korb" in insel  # leer: kein Korb
    assert "Lager-Ordner" not in insel and "Was gehört wohin?" not in insel
    assert "Keine Sorge: Was fehlt" not in insel and insel.count('class="sq-sprechblase"') == 1
    buch = client.get("/steuer/2025/belege").text
    assert "Lager-Ordner" in buch and "/lager/oeffnen" in buch and "Chronik" in buch
    # Ein Zettel im Lager: Jetzt erscheint der Eingangskorb neben der Insel
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "Kontoauszug.pdf").write_bytes(b"%PDF-1.4")
    insel = client.get("/steuer/2025").text
    assert 'id="sq-korb"' in insel and "ohne-korb" not in insel


def test_runde_30_provinz_zettel_fuer_zettel(client, ws):
    """Marie (2.11.): weniger auf einmal. Eine Karte liegt vorn, „Später ›“ blättert weiter,
    die Liste bleibt als Umschalter und wird gemerkt."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    seite = client.get("/steuer/2025/gebiet/werkstatt-1").text
    assert '<ul class="sq-akte stapel"' in seite and seite.count(' vorn"') == 1
    assert " erledigt" in seite and "Später ›" in seite
    assert "Alle Zettel als Liste zeigen" in seite
    assert seite.count("Kein Dokument? Einfach eintippen") <= len(
        re.findall("sq-karte status-offen", seite)
    )
    conn = quest.ensure_schema(store.connect(ws.db))
    karten = [s for s in quest.stuecke(conn, 2025) if s["gebiet"] == "werkstatt-1"]
    zweite = karten[1]
    seite = client.get(f"/steuer/2025/gebiet/werkstatt-1?karte={zweite['id']}").text
    assert f'data-stueck="{zweite["id"]}"' in seite and 'aria-current="true"' in seite
    # Liste wählen: gemerkt, bis man zurückschaltet
    seite = client.get("/steuer/2025/gebiet/werkstatt-1?ansicht=liste").text
    assert "sq-akte stapel" not in seite and "Ein Zettel nach dem anderen" in seite
    assert "sq-akte stapel" not in client.get("/steuer/2025/gebiet/werkstatt-1").text
    client.get("/steuer/2025/gebiet/werkstatt-1?ansicht=stapel")
    assert "sq-akte stapel" in client.get("/steuer/2025/gebiet/werkstatt-1").text


def test_runde_30_frits_karten_stehen_als_erledigt_da(client, ws):
    rechnung(ws, "RE-2025-0001", "2025-03-01", "2025-03-02")
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    seite = client.get("/steuer/2025/gebiet/werkstatt-1").text
    assert "Hat FRITS schon dabei" in seite and "✓ Ausgangsrechnungen" in seite


def test_runde_32_stapel_zaehlt_wie_die_insel(client, ws):
    # Marie-Runde 8: „3 von 5“ auf der Insel und „Zettel 6 von 12“ in der Provinz
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    alle = quest.stuecke(conn, 2025)
    gid = next(
        g
        for g in {s["gebiet"] for s in alle}
        if any(s["art"] == "bonus" for s in alle if s["gebiet"] == g)
        and any(s["art"] != "bonus" for s in alle if s["gebiet"] == g)
    )
    eigene = [s for s in alle if s["gebiet"] == gid]
    pflicht = [s for s in eigene if s["art"] != "bonus"]
    frei = [s for s in eigene if s["art"] == "bonus"]
    seite = client.get(f"/steuer/2025/gebiet/{gid}?ansicht=stapel").text
    # Runde 33: Fortschritt statt Position
    assert f"0 von {len(pflicht)} erledigt" in seite and f"+ {len(frei)} freiwillig" in seite
    seite = client.get(f"/steuer/2025/gebiet/{gid}?karte={frei[0]['id']}").text
    assert f"Freiwillig · 0 von {len(frei)} gefunden" in seite
    assert "Bonus-Schatz · +4" in seite  # der Köder steht auf der Karte


def test_runde_32_nach_dem_sieg_lohnt_sich_noch_etwas(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    for s in spiel.pflicht(quest.stuecke(conn, 2025)):
        quest.setze_status(conn, s["id"], "fragen")
    erste = spiel.pflicht(quest.stuecke(conn, 2025))[0]
    (ws.root / "beleg.pdf").write_bytes(b"%PDF beleg")
    quest.sammeln(conn, ws.root, erste["id"], ws.root / "beleg.pdf")
    assert spiel.drache(quest.stuecke(conn, 2025)).besiegt
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "arbeitszimmer.txt").write_text("Arbeitszimmer 12 qm")
    sieg = client.get("/steuer/2025/sieg").text
    assert "Lohnt sich noch" in sieg and "arbeitszimmer.txt" in sieg
    assert "können Steuern sparen" in sieg or "kann Steuern sparen" in sieg
    insel = client.get("/steuer/2025").text
    assert "Im Korb wartet noch ein Zettel." in insel


def test_runde_32_frits_karten_nur_aus_der_ernte(client, ws):
    # Lena (Runde 31): Eine selbst hochgeladene Rechnung stand unter „Hat FRITS schon dabei“
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    karte = next(s for s in quest.stuecke(conn, 2025) if s["key"] == "ausgangsrechnungen")
    post(
        client,
        f"/steuer/2025/stueck/{karte['id']}/datei",
        files=[("datei", ("rechnung-word.pdf", b"%PDF selbst"))],
    )
    (ws.root / "aus-frits.pdf").write_bytes(b"%PDF ernte")
    quest.merke_aus_frits(conn, 2025, ws.root / "aus-frits.pdf")  # die Ernte lief schon
    assert karte["id"] not in quest.von_frits(conn, 2025)
    seite = client.get(f"/steuer/2025/gebiet/{karte['gebiet']}?ansicht=stapel").text
    assert "Hat FRITS schon dabei" not in seite and "Schon erledigt" in seite


def test_runde_33_trotzdem_ablegen_im_kampf_zaehlt_mit_serie(client, ws, monkeypatch):
    # Lena (Runde 32): Fritte stutzt bei einem Foto, „Trotzdem ablegen“ brachte nur +2
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    conn.execute("UPDATE steuer_stuecke SET geaendert = '2026-01-01 10:00:00'")
    conn.commit()
    karte = spiel.drache(quest.stuecke(conn, 2025)).karte
    zweifel = pruefung.Zweifel("andere_karte", "Das sieht nach etwas anderem aus.")
    monkeypatch.setattr(pruefung, "pruefe", lambda *a, **k: zweifel)
    seite = post(
        client,
        "/steuer/2025/drache/schlag",
        stueck_id=str(karte["id"]),
        aktion="datei",
        files=[("datei", ("foto.png", b"\x89PNG foto"))],
    )
    assert "Fritte stutzt" in seite.text and "/steuer/2025/drache/schlag" in seite.text
    # Noch einmal dieselbe Datei: keine zweite Kopie im Korb
    post(
        client,
        "/steuer/2025/drache/schlag",
        stueck_id=str(karte["id"]),
        aktion="datei",
        files=[("datei", ("foto.png", b"\x89PNG foto"))],
    )
    assert [p.name for p in quest.lager_dateien(ws.root)] == ["foto.png"]
    vorher = quest.gold(conn)
    seite = post(
        client,
        "/steuer/2025/drache/schlag",
        stueck_id=str(karte["id"]),
        aktion="zettel",
        zettel="foto.png",
        trotzdem="1",
    )
    assert quest.gold(conn) - vorher > spiel.GOLD_FUND  # mit Serie, nicht nur der Grundwert
    assert 'class="sq-rechnung"' in seite.text and not quest.lager_dateien(ws.root)


def test_runde_33_nach_dem_sieg_keine_trommel_und_kontostand_im_regal(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    for s in spiel.pflicht(quest.stuecke(conn, 2025)):
        quest.setze_status(conn, s["id"], "fragen")
    erste = spiel.pflicht(quest.stuecke(conn, 2025))[0]
    (ws.root / "beleg.pdf").write_bytes(b"%PDF beleg")
    quest.sammeln(conn, ws.root, erste["id"], ws.root / "beleg.pdf")
    regal = client.app.state.helfer_regal(conn)
    assert regal["besiegt"]
    laden = client.get("/fritte/laden")
    if laden.status_code == 200:
        assert "Der Drache schläft bis nächstes Jahr" in laden.text
        assert "Du hast" in laden.text and "Passt jetzt" in laden.text
    sieg = client.get("/steuer/2025/sieg").text
    assert "Jeder Fund bringt +4" in sieg and "Zum ersten Schatz" in sieg


def test_runde_33_ohne_ernte_nichts_von_frits(conn):
    assert quest.von_frits(conn, 2025) == set()


def test_heute_nennt_den_naechsten_quest_schritt(client, ws):
    """Lena (11-02): „Als Nächstes“ schwieg, obwohl die Quest etwas zu tun hatte."""
    Settings(
        name="Kim Beispiel",
        address="Weg 1, 12345 Ort",
        iban="DE02120300000000202051",
        hourly_rate=Decimal(1),
        setup_done=True,
    ).save(ws.settings_file)
    # Noch keine Quest: Heute zeigt den Einstieg (Lena, Runde 41)
    start = client.get("/").text
    assert "Leg los" in start and 'class="button primary" href="/steuer"' in start
    fragebogen(client)
    page = client.get("/").text
    nxt = page[page.index('id="als-naechstes"') :]
    nxt = nxt[: nxt.index("</section>")]
    assert "Steuer 2025:" in nxt and 'class="button primary" href="/steuer/2025' in nxt
    # Ist die Fundkiste gepackt, ist das Jahr fertig und Heute wieder ruhig
    quest.notiere(store.connect(ws.db), 2025, "truhe", "fundkiste.zip")
    assert "Nichts, was eilt" in client.get("/").text


def _besiegt_mit_bonus(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    for s in spiel.pflicht(quest.stuecke(conn, 2025)):
        quest.setze_status(conn, s["id"], "fragen")
    erste = spiel.pflicht(quest.stuecke(conn, 2025))[0]
    (ws.root / "beleg.pdf").write_bytes(b"%PDF beleg")
    quest.sammeln(conn, ws.root, erste["id"], ws.root / "beleg.pdf")
    bonus = next(s for s in quest.stuecke(conn, 2025) if spiel.ist_bonus(s))
    return conn, bonus


def test_runde_34_fundkiste_neu_packen_nach_neuen_belegen(client, ws):
    """Lena (Runde 33): Belege nach dem Packen machten die Zip alt, niemand sagte es."""
    conn, bonus = _besiegt_mit_bonus(client, ws)
    assert quest.kiste_stand(conn, 2025) is None
    assert "Pack die Fundkiste" in client.app.state.quest_auftrag(conn)["text"]
    post(client, "/steuer/2025/truhe", schloss="ohne")
    assert quest.kiste_stand(conn, 2025) == (0, 0)
    assert client.app.state.quest_auftrag(conn) is None  # Heute ist ruhig
    insel = client.get("/steuer/2025").text
    assert "Die Fundkiste ist gepackt" in insel and "Pack die Fundkiste für" not in insel
    (ws.root / "spende.pdf").write_bytes(b"%PDF spende")
    quest.sammeln(conn, ws.root, bonus["id"], ws.root / "spende.pdf")
    assert quest.kiste_stand(conn, 2025) == (1, 0)
    heute = client.app.state.quest_auftrag(conn)
    assert heute and "1 neuer Beleg seit dem Packen" in heute["text"]
    assert heute["ziel"].endswith("/sieg#kiste")
    sieg = client.get("/steuer/2025/sieg").text
    assert 'class="sq-kiste-alt"' in sieg and "1 neuer Beleg seit dem Packen" in sieg
    post(client, "/steuer/2025/truhe", schloss="ohne")
    assert quest.kiste_stand(conn, 2025) == (0, 0)
    assert 'class="sq-kiste-alt"' not in client.get("/steuer/2025/sieg").text
    spende = next(d for d in quest.dateien(conn, 2025) if d["original"] == "spende.pdf")
    quest.entferne_datei(conn, spende["id"])
    assert quest.kiste_stand(conn, 2025) == (0, 1)


def test_runde_34_kiste_aenderung_text():
    assert spiel.kiste_aenderung(0, 0) == ""
    assert spiel.kiste_aenderung(2, 0) == "2 neue Belege seit dem Packen."
    assert spiel.kiste_aenderung(1, 1) == "1 Beleg getauscht seit dem Packen."


def test_runde_34_fund_banner_nennt_die_salzkoerner(client, ws):
    """Lena (Runde 33): Ein Schatz brachte +4, das Banner sagte es nicht."""
    conn, bonus = _besiegt_mit_bonus(client, ws)
    vorher = quest.gold(conn)
    seite = post(
        client,
        f"/steuer/2025/stueck/{bonus['id']}/datei",
        files=[("datei", ("spende.pdf", b"%PDF spende"))],
    ).text
    assert quest.gold(conn) > vorher
    salz = spiel.gold_fuer(bonus, "gesammelt", frozenset())
    assert f"Gesichert: {bonus['name']} · +{spiel.koerner(salz)}" in seite


def test_runde_35_nach_dem_packen_ist_alles_ruhig(client, ws):
    """Lena (Runde 34): Fritte, Siegel und Hort sagten nach dem Packen noch „Beute wartet“."""
    conn, bonus = _besiegt_mit_bonus(client, ws)
    post(client, "/steuer/2025/truhe", schloss="passwort")
    insel = client.get("/steuer/2025").text
    assert "Die Fundkiste wartet auf dich" not in insel and "Besiegt! Zur Beute" not in insel
    assert "Fundkiste gepackt" in insel and "sq-auftrag art-sieg ruhig" in insel
    (ws.root / "spende.pdf").write_bytes(b"%PDF spende")
    quest.sammeln(conn, ws.root, bonus["id"], ws.root / "spende.pdf")
    insel = client.get("/steuer/2025").text
    assert "Pack die Fundkiste neu" in insel and "Kiste neu packen" in insel
    sieg = client.get("/steuer/2025/sieg").text
    box = sieg[sieg.index('class="sq-kiste-alt"') :]
    box = box[: box.index("</form>")]
    assert "Neu packen" in box and "neues Passwort" in box and 'value="passwort"' in box
    assert "Ich zeige es nur jetzt" not in sieg


def test_runde_36_handyticket_ist_eine_reise(client, ws):
    """Lena (Runde 35): „handy“ in „Handyticket“ schickte das Bahnticket zu den Anschaffungen."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    alle = quest.stuecke(conn, 2025)
    assert spiel.ahnung("DB_Handyticket_Scan.pdf", alle).stueck["key"] == "reisen"
    assert spiel.ahnung("Hotel Leipzig.pdf", alle).stueck["key"] == "reisen"
    assert spiel.ahnung("Rechnung neues Handy.pdf", alle).stueck["key"] == "anschaffungen"


def test_runde_36_neu_packen_nennt_das_neue_passwort(client, ws):
    _conn, _bonus = _besiegt_mit_bonus(client, ws)
    r = client.post("/steuer/2025/truhe", data={"token": client.token, "schloss": "passwort"},
                    follow_redirects=False)  # fmt: skip
    assert r.headers["location"].endswith("/sieg#kiste")
    seite = client.get("/steuer/2025/sieg").text
    assert "Neu gepackt" not in seite and "data-konfetti" not in seite
    # Im Finale führt „Weiter zum Brief“, der zweite Brief-Knopf bleibt weg (Lena, Runde 47)
    assert (
        "Erst den Brief lesen" in seite
        and "Weiter zu 2. Brief" not in seite
        and 'id="brief"' in seite
    )
    seite = post(client, "/steuer/2025/truhe", schloss="passwort").text
    assert "Neu gepackt, mit neuem Passwort" in seite and "Weiter zu 2. Brief" in seite


def test_runde_36_nur_was_spart_ist_ein_koeder(client, ws):
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    alle = quest.stuecke(conn, 2025)
    frei = [s for s in alle if s["status"] == "offen" and spiel.ist_bonus(s)]
    sparen = [s for s in frei if spiel.spart_steuern(s)]
    assert len(sparen) < len(frei)  # Buchungsliste und GoBD sparen keine Steuern
    sieg = client.get("/steuer/2025/sieg").text
    assert f"{len(sparen)} Zettel können Steuern sparen" in sieg
    erster = sieg[sieg.index("Zum ersten Schatz") - 200 : sieg.index("Zum ersten Schatz")]
    assert f"karte={sparen[0]['id']}" in erster


def test_runde_36_rueckfrage_verschwindet_wenn_die_karte_erledigt_ist(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    karte = spiel.pflicht(quest.stuecke(conn, 2025))[0]
    client.app.state.steuer_stutzen["brief.png"] = {
        "jahr": 2025, "stueck_id": karte["id"], "karte": karte["name"], "text": "Hm."
    }  # fmt: skip
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "brief.png").write_bytes(b"\x89PNG")
    assert "Trotzdem zu" in client.get("/steuer/2025").text
    (ws.root / "beleg.pdf").write_bytes(b"%PDF beleg")
    quest.sammeln(conn, ws.root, karte["id"], ws.root / "beleg.pdf")
    assert "Trotzdem zu" not in client.get("/steuer/2025").text


def test_runde_37_erster_schatz_ist_die_erste_zeile(client, ws):
    """Lena (Runde 36): Die Liste begann mit der Burg, der Knopf führte in die Werkstatt."""
    _besiegt_mit_bonus(client, ws)
    sieg = client.get("/steuer/2025/sieg").text
    box = sieg[sieg.index('class="sq-papier sq-noch-frei"') :]
    erste_zeile = re.search(r'<li><a href="([^"]+)"', box).group(1)
    knopf = re.search(r'href="([^"]+)">Zum ersten Schatz', box).group(1)
    assert knopf == erste_zeile
    assert "und 1 weitere<" not in box and "und 1 weitere " not in box


def test_runde_37_belegbuch_klappt_nach_provinz(client, ws):
    _besiegt = _besiegt_mit_bonus(client, ws)
    seite = client.get("/steuer/2025/belege").text
    assert seite.count('<details class="sq-buch-klappe" open>') >= 1
    assert "1 Datei" in seite and "Zur Provinz" in seite


def test_runde_38_rueckbuchung_rechnet_ehrlich(client, ws):
    """Lena (Runde 37): Nach −20 aus dem Kampf versprach der Satz alles zurück, es kamen 6."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    karte = spiel.pflicht(quest.stuecke(conn, 2025))[0]
    seite = post(
        client,
        f"/steuer/2025/stueck/{karte['id']}/datei",
        files=[("datei", ("beleg.pdf", b"%PDF beleg"))],
    )
    assert seite.status_code == 200
    # So als wäre der Treffer im Kampf mit Serie gefallen
    conn.execute(
        "UPDATE spiel_beutel SET betrag = 16 WHERE grund = (SELECT 'quest:2025:gold:' || id"
        " FROM steuer_gold WHERE grund = ?)",
        (spiel.karten_grund(karte, "fund"),),
    )
    conn.execute("UPDATE steuer_gold SET betrag = 16 WHERE grund = ?",
                 (spiel.karten_grund(karte, "fund"),))  # fmt: skip
    conn.commit()
    d = quest.dateien(conn, 2025, karte["id"])[0]
    seite = post(client, f"/steuer/2025/datei/{d['id']}/entfernen").text
    assert "−16 Salzkörner zurückgebucht" in seite
    assert "etwa 2 Salzkörner zurück" in seite and "Serie aus dem Kampf" in seite
    assert "bekommst du sie zurück" not in seite


def test_runde_38_doppelte_datei_ohne_fehlermeldung(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    post(client, "/steuer/2025/lager/hochladen", files=[("datei", ("a.pdf", b"%PDF a"))])
    seite = post(
        client, "/steuer/2025/lager/hochladen", files=[("datei", ("a.pdf", b"%PDF a"))]
    ).text
    assert "liegt schon im Eingangskorb" in seite
    assert "keine Datei mitgeschickt" not in seite


def test_runde_38_wieder_besiegt_ohne_zweites_fest(client, ws):
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    quest.notiere(conn, 2025, "sieg", "Der Papierdrache ist besiegt")
    erste = spiel.pflicht(quest.stuecke(conn, 2025))[0]
    d = quest.dateien(conn, 2025, erste["id"])[0]
    post(client, f"/steuer/2025/datei/{d['id']}/entfernen")
    assert not spiel.drache(quest.stuecke(conn, 2025)).besiegt
    seite = post(
        client,
        f"/steuer/2025/stueck/{erste['id']}/datei",
        files=[("datei", ("neu.pdf", b"%PDF neu"))],
    ).text
    assert "Der Drache ist wieder besiegt" in seite
    assert "faltet sich" not in seite


def test_runde_38_zurueckgelegt_bleibt_in_der_provinz_und_fremdes_jahr(client, ws):
    _conn, _bonus = _besiegt_mit_bonus(client, ws)
    belege = client.get("/steuer/2025/belege").text
    assert re.search(r'name="back" value="/steuer/2025/belege#provinz-[\w-]+"', belege)
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "krankenversicherung-2024.pdf").write_bytes(b"%PDF kv")
    assert "aus 2024?" in client.get("/steuer/2025").text


def test_runde_39_zurueckgelegt_und_neu_geworfen_ohne_serie(client, ws):
    """Lena (Runde 38): Zurücklegen und auf der Drachenseite neu werfen brachte die neue Serie,
    jede Karte also mehr als vorher (Steuer-ID 44 → 38 → 46)."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    lager = quest.lager(ws.root)
    lager.mkdir(parents=True, exist_ok=True)
    for name in ("a.pdf", "b.pdf"):
        (lager / name).write_bytes(b"%PDF " + name.encode())
    erste = spiel.drache(quest.stuecke(conn, 2025)).karte
    post(client, "/steuer/2025/drache/schlag", stueck_id=str(erste["id"]), aktion="zettel",
         zettel="a.pdf")  # fmt: skip
    zweite = spiel.drache(quest.stuecke(conn, 2025)).karte
    post(client, "/steuer/2025/drache/schlag", stueck_id=str(zweite["id"]), aktion="zettel",
         zettel="b.pdf")  # fmt: skip
    grund = spiel.karten_grund(zweite, "fund")
    basis = spiel.gold_fuer(zweite, "gesammelt")
    sql = "SELECT betrag FROM steuer_gold WHERE jahr = 2025 AND grund = ?"
    assert conn.execute(sql, (grund,)).fetchone()[0] > basis  # mit Serie
    d = quest.dateien(conn, 2025, zweite["id"])[0]
    post(client, f"/steuer/2025/datei/{d['id']}/entfernen")
    assert grund in quest.stornierte_gruende(conn, 2025)
    serie = client.app.state.steuer_combo.get(2025, {}).get("combo", 0)
    name = quest.lager_dateien(ws.root)[0].name
    seite = post(client, "/steuer/2025/drache/schlag", stueck_id=str(zweite["id"]),
                 aktion="zettel", zettel=name).text  # fmt: skip
    assert conn.execute(sql, (grund,)).fetchone()[0] == basis  # nur der Grundwert
    assert client.app.state.steuer_combo[2025]["combo"] == serie  # die Serie wächst nicht
    assert "schon gewertet" in seite and "Volltreffer mit Beleg" not in seite


def test_runde_39_letzte_bonus_datei_fuehrt_zum_korb(client, ws):
    """Lena (Runde 38): Die Burg verschwand aus dem Belegbuch, die Seite sprang nach oben."""
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    fragebogen(
        client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"}, teil3={"haken": "vorsorge"}
    )
    bonus = quest.stuecke(conn, 2025, "burg")[0]  # die Burg hat nur Bonus-Schätze
    (ws.root / "riester.pdf").write_bytes(b"%PDF riester")
    quest.sammeln(conn, ws.root, bonus["id"], ws.root / "riester.pdf")
    d = quest.dateien(conn, 2025, bonus["id"])[0]
    ziel = f"/steuer/2025/belege#provinz-{bonus['gebiet']}"
    antwort = client.post(
        f"/steuer/2025/datei/{d['id']}/entfernen",
        data={"token": client.token, "back": ziel},
        follow_redirects=False,
    )
    assert antwort.headers["location"].endswith("/steuer/2025#sq-korb")
    # Eine Pflicht-Provinz bleibt im Belegbuch: dorthin geht es zurück
    erste = spiel.pflicht(quest.stuecke(conn, 2025))[0]
    d = quest.dateien(conn, 2025, erste["id"])[0]
    ziel = f"/steuer/2025/belege#provinz-{erste['gebiet']}"
    antwort = client.post(
        f"/steuer/2025/datei/{d['id']}/entfernen",
        data={"token": client.token, "back": ziel},
        follow_redirects=False,
    )
    assert antwort.headers["location"].endswith(ziel)


def test_runde_39_vorjahres_briefe_und_kleine_worte(client, ws):
    from frits.web.steuer import fremdes_jahr

    assert fremdes_jahr("Steuerbescheid_2024.pdf", 2025, "bescheid") is None
    assert fremdes_jahr("vorauszahlung-2024.png", 2025, "vorauszahlungen") is None
    assert fremdes_jahr("Lohnsteuerbescheinigung_2024.png", 2025, "lohnsteuer") == 2024
    assert fremdes_jahr("Steuerbescheid_2022.pdf", 2025, "bescheid") == 2022
    _conn, _bonus = _besiegt_mit_bonus(client, ws)
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "krankenversicherung-2024.pdf").write_bytes(b"%PDF kv")
    insel = client.get("/steuer/2025").text
    # Das Schild steht in der zweiten Zeile, nicht als drittes Kind im Raster des Zettels
    assert re.search(r'<small[^>]*><span class="sq-anderes-jahr"[^>]*>aus 2024\?</span>', insel)
    post(client, "/steuer/2025/lager/hochladen", files=[("datei", ("x.pdf", b"%PDF x"))])
    seite = post(
        client, "/steuer/2025/lager/hochladen", files=[("datei", ("x.pdf", b"%PDF x"))]
    ).text
    assert 'href="/steuer/2025#sq-korb">Zum Korb' in seite


def test_runde_40_kein_umweg_ueber_fragen_und_helfer(client, ws):
    """Lena (Runde 39): Zurück, „Lieber fragen“, Datei wieder dran brachte +1; Goldwaage kaufen
    und einen gewerteten Schatz umbuchen brachte +4."""
    fragebogen(
        client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"}, teil3={"haken": "vorsorge"}
    )
    conn = quest.ensure_schema(store.connect(ws.db))
    karte = spiel.pflicht(quest.stuecke(conn, 2025))[0]
    datei = [("datei", ("id.pdf", b"%PDF id"))]
    post(client, f"/steuer/2025/stueck/{karte['id']}/datei", files=datei)
    start = quest.gold(conn, 2025)
    d = quest.dateien(conn, 2025, karte["id"])[0]
    post(client, f"/steuer/2025/datei/{d['id']}/entfernen")
    post(client, f"/steuer/2025/stueck/{karte['id']}/status", status="fragen")
    post(client, f"/steuer/2025/stueck/{karte['id']}/datei", files=datei)
    assert quest.stueck(conn, karte["id"])["status"] == "gesammelt"
    assert quest.gold(conn, 2025) == start  # netto 0

    # Seit Runde 57 erkennt Fritte Riester: Die Datei kommt auf die passende Karte
    schatz = next(s for s in quest.stuecke(conn, 2025, "burg") if s["key"] == "altersvorsorge")
    riester = [("datei", ("riester.pdf", b"%PDF riester"))]
    post(client, f"/steuer/2025/stueck/{schatz['id']}/datei", files=riester)
    quest.verdiene(conn, 2025, "test", 100, "Testgold")
    post(client, "/steuer/2025/markt/kaufen", ware="goldwaage")
    assert "goldwaage" in quest.helfer(conn, 2025)
    vorher = quest.gold(conn, 2025)
    d = quest.dateien(conn, 2025, schatz["id"])[0]
    seite = post(client, f"/steuer/2025/datei/{d['id']}/entfernen").text
    assert "bekommst du sie zurück" in seite  # nicht mehr als vorher versprochen
    name = next(f.name for f in quest.lager_dateien(ws.root) if "riester" in f.name.lower())
    post(
        client, "/steuer/2025/lager/zuordnen", name=name, stueck_id=str(schatz["id"]), trotzdem="1"
    )
    assert quest.stueck(conn, schatz["id"])["status"] == "gesammelt"
    assert quest.gold(conn, 2025) == vorher  # netto 0, auch mit der Goldwaage


def test_runde_40_serie_bleibt_und_drache_verspricht_ehrlich(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    lager = quest.lager(ws.root)
    lager.mkdir(parents=True, exist_ok=True)
    (lager / "a.pdf").write_bytes(b"%PDF a")
    erste = spiel.drache(quest.stuecke(conn, 2025)).karte
    post(client, "/steuer/2025/drache/schlag", stueck_id=str(erste["id"]), aktion="zettel",
         zettel="a.pdf")  # fmt: skip
    serie = client.app.state.steuer_combo[2025]["combo"]
    assert serie > 0
    d = quest.dateien(conn, 2025, erste["id"])[0]
    seite = post(client, f"/steuer/2025/datei/{d['id']}/entfernen").text
    assert "nicht noch einmal" in seite
    assert client.app.state.steuer_combo[2025]["combo"] == serie  # die Serie bleibt stehen
    drache = client.get("/steuer/2025/drache").text
    assert spiel.drache(quest.stuecke(conn, 2025)).karte["id"] == erste["id"]
    assert "Die Karte vorn war schon gewertet: +2" in drache
    assert "schon gewertet, +2" in drache and "Nächster Beleg:" not in drache
    name = quest.lager_dateien(ws.root)[0].name
    seite = post(client, "/steuer/2025/drache/schlag", stueck_id=str(erste["id"]),
                 aktion="zettel", zettel=name).text  # fmt: skip
    assert "ohne Serie, die Karte war schon gewertet" in seite


def test_runde_40_dateinamen_brechen_an_trennzeichen():
    from frits.web.steuer import umbruch

    assert str(umbruch("Steuerbescheid_2024_Finanzamt.pdf")) == (
        "Steuerbescheid_<wbr>2024_<wbr>Finanzamt.pdf"
    )
    assert "&lt;b&gt;" in str(umbruch("<b>_x"))


def test_runde_41_letzte_kanten_beim_umbuchen(client, ws):
    """Lena (Runde 40): Gibt's nicht auf Schätzen zahlte, Gibt's nicht und dann Datei +1,
    und ein neu verdientes Siegel zahlte einen Nachschlag."""
    fragebogen(
        client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"}, teil3={"haken": "vorsorge"}
    )
    conn = quest.ensure_schema(store.connect(ws.db))
    schatz = quest.stuecke(conn, 2025, "burg")[0]
    start = quest.gold(conn, 2025)
    seite = post(client, f"/steuer/2025/stueck/{schatz['id']}/status", status="gibts_nicht").text
    assert quest.gold(conn, 2025) == start and "Abgehakt" in seite and "Zählt trotzdem" not in seite

    # Gibt's nicht, dann doch eine Datei: wie ein direkter Fund
    karte = next(
        s
        for s in spiel.pflicht(quest.stuecke(conn, 2025))
        if not ist_kern(s["gebiet"], s["key"]) and not fehlt_nie(s["gebiet"], s["key"])
    )
    vorher = quest.gold(conn, 2025)
    post(client, f"/steuer/2025/stueck/{karte['id']}/status", status="gibts_nicht")
    assert quest.gold(conn, 2025) == vorher + spiel.GOLD_GIBTS_NICHT
    post(
        client,
        f"/steuer/2025/stueck/{karte['id']}/datei",
        files=[("datei", ("doku.pdf", b"%PDF doku"))],
    )
    assert quest.stueck(conn, karte["id"])["status"] == "gesammelt"
    assert quest.gold(conn, 2025) == vorher + spiel.gold_fuer(karte, "gesammelt")


def test_runde_41_siegel_zahlt_nach_dem_zuruecklegen_nicht_mehr(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    rathaus = [s for s in spiel.pflicht(quest.stuecke(conn, 2025)) if s["gebiet"] == "rathaus"]
    for s in rathaus:  # alles als Frage: Das Siegel zahlt wenig (keine Funde)
        post(client, f"/steuer/2025/stueck/{s['id']}/status", status="fragen")
    sql = "SELECT betrag FROM steuer_gold WHERE jahr = 2025 AND grund = 'siegel:rathaus'"
    assert conn.execute(sql).fetchone()[0] == spiel.siegel_gold(0)
    erste = rathaus[0]
    post(client, f"/steuer/2025/stueck/{erste['id']}/datei",
         files=[("datei", ("id.pdf", b"%PDF id"))])  # fmt: skip
    vorher = quest.gold(conn, 2025)
    d = quest.dateien(conn, 2025, erste["id"])[0]
    post(client, f"/steuer/2025/datei/{d['id']}/entfernen")
    name = next(f.name for f in quest.lager_dateien(ws.root) if f.name.startswith("id"))
    post(client, "/steuer/2025/lager/zuordnen", name=name, stueck_id=str(erste["id"]),
         trotzdem="1")  # fmt: skip
    assert quest.stueck(conn, erste["id"])["status"] == "gesammelt"
    assert conn.execute(sql).fetchone()[0] == spiel.siegel_gold(0)  # kein Nachschlag
    # Das Korn für die Frage von vorhin bleibt weg (der Satz sagte „etwa 2“), nie mehr als vorher
    assert quest.gold(conn, 2025) == vorher - spiel.GOLD_FRAGE


def test_runde_42_finale_beim_packen_und_ruhige_meldungen(client, ws):
    """Lena (Runde 41): Das Packen war nur ein Formular; nach dem Zurücklegen kamen vier
    Meldungen auf einmal; nach dem Sieg stand „×4, bleibt stehen“."""
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    seite = post(client, "/steuer/2025/truhe", schloss="ohne").text
    assert 'class="sq-kiste-finale"' in seite and "Die Fundkiste 2025 ist zu!" in seite
    assert 'class="sq-kiste-finale"' not in client.get("/steuer/2025/sieg").text  # nur einmal
    seite = post(client, "/steuer/2025/truhe", schloss="ohne").text
    assert 'class="sq-kiste-finale"' not in seite  # Neu packen ist kein zweites Finale

    erste = spiel.pflicht(quest.stuecke(conn, 2025))[0]
    d = quest.dateien(conn, 2025, erste["id"])[0]
    seite = post(client, f"/steuer/2025/datei/{d['id']}/entfernen").text
    assert seite.count('class="sq-meldung hinweis"') == 1
    assert "Die Datei liegt wieder im Eingangskorb." in seite
    assert "neue Serie" in client.app.state.steuer_combo[2025]["ruf"]


def test_runde_42_vorschau_nennt_das_siegel_und_erster_fund_nur_einmal(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    _bis_wach(conn)
    karte = spiel.drache(quest.stuecke(conn, 2025)).karte
    andere = [
        s
        for s in spiel.pflicht(quest.stuecke(conn, 2025))
        if s["gebiet"] == karte["gebiet"] and s["id"] != karte["id"] and s["status"] == "offen"
    ]
    for s in andere:
        quest.setze_status(conn, s["id"], "fragen")
    drache = client.get("/steuer/2025/drache").text
    assert "dazu das Siegel +" in drache


def test_runde_42_erster_fund_kommt_nur_einmal(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    karte = spiel.pflicht(quest.stuecke(conn, 2025))[0]
    seite = post(client, f"/steuer/2025/stueck/{karte['id']}/datei",
                 files=[("datei", ("a.pdf", b"%PDF a"))]).text  # fmt: skip
    assert "Neuer Orden: Erster Fund" in seite
    fragebogen(client, jahr=2024, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    karte = spiel.pflicht(quest.stuecke(conn, 2024))[0]
    seite = post(client, f"/steuer/2024/stueck/{karte['id']}/datei",
                 files=[("datei", ("b.pdf", b"%PDF b"))]).text  # fmt: skip
    assert quest.stueck(conn, karte["id"])["status"] == "gesammelt"
    assert "Neuer Orden: Erster Fund" not in seite


def test_runde_43_kiste_vergleicht_nach_inhalt(client, ws):
    """Lena (Runde 44): Zurücklegen und dieselbe Datei wieder ablegen hieß „2 neue und 2
    herausgenommen“ und verlangte ein neues Passwort."""
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    seite = post(client, "/steuer/2025/truhe", schloss="passwort").text
    assert "sq-kiste-finale" in seite and "truhe-zu" in seite
    assert "Die Fundkiste ist gepackt und mit Passwort" not in seite  # das sagt das Finale
    assert quest.kiste_stand(conn, 2025) == (0, 0)
    erste = spiel.pflicht(quest.stuecke(conn, 2025))[0]
    d = quest.dateien(conn, 2025, erste["id"])[0]
    post(client, f"/steuer/2025/datei/{d['id']}/entfernen")
    assert quest.kiste_stand(conn, 2025) == (0, 1)
    name = quest.lager_dateien(ws.root)[0].name
    post(client, "/steuer/2025/lager/zuordnen", name=name, stueck_id=str(erste["id"]),
         trotzdem="1")  # fmt: skip
    assert quest.kiste_stand(conn, 2025) == (0, 0)  # gleicher Inhalt: nichts neu zu packen
    assert "Pack die Fundkiste neu" not in client.get("/steuer/2025/sieg").text


def test_runde_44_kiste_aenderung_sagt_getauscht():
    """Lena (Runde 45): „1 neuer Beleg und 1 Beleg herausgenommen“ heißt einfach getauscht."""
    assert spiel.kiste_aenderung(2, 1) == "1 Beleg getauscht und 1 neuer Beleg seit dem Packen."
    assert (
        spiel.kiste_aenderung(1, 3)
        == "1 Beleg getauscht und 2 Belege herausgenommen seit dem Packen."
    )


def test_runde_44_leere_kiste_wird_nicht_gefeiert(client, ws):
    """Lena (Runde 45): „Heute nur senden“, 0 Dokumente, und das Finale sagte „Alles drin“."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    post(client, "/steuer/2025/drache/notausgang")
    seite = post(client, "/steuer/2025/truhe", schloss="passwort").text
    assert 'class="sq-kiste-finale"' not in seite and "sq-kiste-siegel" not in seite
    assert "ist gepackt, aber noch leer" in seite and "noch offen" in seite
    assert "Alles, was deine Beraterin braucht" not in seite


def test_runde_44_fritte_stutzt_mit_weg_zur_passenden_karte(client, ws):
    """Lena (Runde 45): Fritte nannte die richtige Karte, aber ohne Knopf dorthin."""
    from frits.steuer.katalog import quellen_text
    from frits.steuer.pruefung import nennt_die_karte

    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    alle = quest.stuecke(conn, 2025)
    paar = next(
        (s, b)
        for s in alle
        for b in alle
        if b["id"] != s["id"]
        and spiel.ahnung(f"{b['name']}.pdf", [s]).stueck is None
        and (spiel.ahnung(f"{b['name']}.pdf", alle).stueck or {}).get("id") == b["id"]
        and not nennt_die_karte(f"{b['name']}.pdf", quellen_text(s["gebiet"], s["key"]))
    )
    s, besser = paar
    name = f"{besser['name']}.pdf"
    lager = quest.lager(ws.root)
    lager.mkdir(parents=True, exist_ok=True)
    (lager / name).write_bytes(b"%PDF-1.4 brief")
    seite = post(client, "/steuer/2025/lager/zuordnen", name=name, stueck_id=s["id"]).text
    assert "Fritte stutzt" in seite and f"Zu „{besser['name']}“" in seite
    assert "Trotzdem ablegen" in seite and "Beiseite legen" in seite
    korb = client.get("/steuer/2025").text
    assert 'class="sq-besser"' in korb
    post(client, "/steuer/2025/lager/zuordnen", name=name, stueck_id=besser["id"], trotzdem="1")
    assert quest.stueck(conn, besser["id"])["status"] == "gesammelt"
    assert quest.stueck(conn, s["id"])["status"] == "offen"


def test_runde_44_frisches_jahr_sagt_der_korb_gilt_fuer_alle_jahre(client, ws):
    """Lena (Runde 45): 2024 begann mit „Ein Zettel wartet“, dem DB-Ticket aus 2025."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    alle = [s for s in quest.stuecke(conn, 2025) if s["key"] != "steuer-id"]
    a = spiel.auftrag(2025, [], spiel.drache(alle), 1, alle)
    assert a.art == "korb" and "Der Korb gilt für alle Jahre" in a.text and "2025" in a.text
    # Lena (Runde 57): Mit offener Steuer-ID beginnt ein frisches Jahr leicht, trotz Korb
    p = Profil.load(quest.profil_datei(ws.root, 2025))
    alle = quest.stuecke(conn, 2025)
    a = spiel.auftrag(2025, spiel.provinzen(reich(p), alle), spiel.drache(alle), 6, alle)
    assert a.art == "provinz" and "Steuer-ID" in a.text and "6 Zettel" in a.text


def test_runde_45_finale_nach_dem_notausgang(client, ws):
    """Lena (Runde 46): Nach dem Notausgang gab es nie ein Finale, obwohl FRITS versprach:
    „Das Siegel gibt es, wenn der Drache besiegt ist“."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    post(client, "/steuer/2025/drache/notausgang")
    seite = post(client, "/steuer/2025/truhe", schloss="ohne").text
    assert "aber noch leer" in seite and 'class="sq-kiste-finale"' not in seite
    # Jetzt den Drachen besiegen, wie im Spiel (mit Notiz in der Chronik)
    for s in spiel.pflicht(quest.stuecke(conn, 2025)):
        quest.setze_status(conn, s["id"], "fragen")
    erste = spiel.pflicht(quest.stuecke(conn, 2025))[0]
    (ws.root / "beleg.pdf").write_bytes(b"%PDF beleg")
    quest.sammeln(conn, ws.root, erste["id"], ws.root / "beleg.pdf")
    quest.notiere(conn, 2025, "sieg", "Der Papierdrache ist besiegt")
    seite = post(client, "/steuer/2025/truhe", schloss="ohne").text
    assert 'class="sq-kiste-finale"' in seite and "Erst den Brief lesen" in seite
    assert "Die Fundkiste ist gepackt, ohne Passwort." not in seite  # das sagt das Finale
    seite = post(client, "/steuer/2025/truhe", schloss="ohne").text
    assert 'class="sq-kiste-finale"' not in seite  # nur einmal


def test_runde_45_kein_zweites_finale_fuer_alte_kisten(client, ws):
    """Wer vor dieser Runde nach dem Sieg gepackt hat, bekommt beim Neu-Packen kein Fest."""
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    quest.notiere(conn, 2025, "sieg", "Der Papierdrache ist besiegt")
    quest.notiere(conn, 2025, "truhe", "Steuer-2025-Kim.zip")
    quest.kiste_gepackt(conn, 2025)
    seite = post(client, "/steuer/2025/truhe", schloss="ohne").text
    assert 'class="sq-kiste-finale"' not in seite


def test_runde_45_steuer_id_im_dateinamen():
    """Lena (Runde 46): „steuer-id-brief.png“ landete bei den Briefen, „Steuer-ID.pdf“ nirgends."""
    alle = [
        {"id": 1, "gebiet": "rathaus", "key": "steuer-id", "name": "Steuer-ID", "status": "offen"},
        {"id": 2, "gebiet": "rathaus", "key": "briefe", "status": "offen",
         "name": "Weitere Briefe vom Finanzamt"},
    ]  # fmt: skip
    for name in ("steuer-id-brief.png", "Steuer-ID.pdf", "Steuer_ID_2025.pdf", "SteuerID.jpg"):
        assert spiel.ahnung(name, alle).stueck["id"] == 1, name
    assert spiel.ahnung("finanzamt-brief.png", alle).stueck["id"] == 2


def test_runde_46_nachtrag_mail_nach_dem_notausgang(client, ws):
    """Lena (Runde 47): Die Mail nach dem Sieg war wortgleich mit der Notausgang-Mail. Die
    Beraterin wusste nicht, welche Kiste die vollständige ist."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    post(client, "/steuer/2025/drache/notausgang")
    post(client, "/steuer/2025/truhe", schloss="ohne")
    erste_zip = quest.ordner(ws.root, 2025) / "Steuer-2025-Kim.zip"
    assert erste_zip.exists()
    seite = client.get("/steuer/2025/sieg").text
    assert "vollständige Stand" not in seite
    for s in spiel.pflicht(quest.stuecke(conn, 2025)):
        quest.setze_status(conn, s["id"], "fragen")
    erste = spiel.pflicht(quest.stuecke(conn, 2025))[0]
    (ws.root / "beleg.pdf").write_bytes(b"%PDF beleg")
    quest.sammeln(conn, ws.root, erste["id"], ws.root / "beleg.pdf")
    quest.notiere(conn, 2025, "sieg", "Der Papierdrache ist besiegt")
    seite = post(client, "/steuer/2025/truhe", schloss="passwort").text
    assert 'class="sq-kiste-finale"' in seite
    assert "Steuer-2025-Kim-vollstaendig.zip" in seite
    assert (quest.ordner(ws.root, 2025) / "Steuer-2025-Kim-vollstaendig.zip").exists()
    assert "vollständige Stand meiner Unterlagen" in seite and "ersetzt meine Sendung vom" in seite
    assert "– vollständig (ersetzt die erste Sendung)" in seite  # im Betreff
    # Neu packen bleibt beim neuen Namen, ohne zweites Finale
    seite = post(client, "/steuer/2025/truhe", schloss="ohne").text
    assert "Steuer-2025-Kim-vollstaendig.zip" in seite and 'class="sq-kiste-finale"' not in seite


def test_runde_46_normales_jahr_ist_kein_nachtrag(client, ws):
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    quest.notiere(conn, 2025, "sieg", "Der Papierdrache ist besiegt")
    seite = post(client, "/steuer/2025/truhe", schloss="ohne").text
    assert 'class="sq-kiste-finale"' in seite
    assert "vollstaendig" not in seite and "vollständige Stand" not in seite


def test_runde_46_steuer_id_mehr_schreibweisen():
    alle = [
        {"id": 1, "gebiet": "rathaus", "key": "steuer-id", "name": "Steuer-ID", "status": "offen"},
        {"id": 2, "gebiet": "rathaus", "key": "briefe", "status": "offen",
         "name": "Weitere Briefe vom Finanzamt"},
    ]  # fmt: skip
    for name in ("Steuer-IDs_Familie.pdf", "BZSt-Brief.pdf"):
        assert spiel.ahnung(name, alle).stueck["id"] == 1, name


def _notausgang_dann_sieg(client, ws, schloss="ohne"):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    post(client, "/steuer/2025/drache/notausgang")
    post(client, "/steuer/2025/truhe", schloss="ohne")
    for s in spiel.pflicht(quest.stuecke(conn, 2025)):
        quest.setze_status(conn, s["id"], "fragen")
    erste = spiel.pflicht(quest.stuecke(conn, 2025))[0]
    (ws.root / "beleg.pdf").write_bytes(b"%PDF beleg")
    quest.sammeln(conn, ws.root, erste["id"], ws.root / "beleg.pdf")
    # Der Beleg kam nach der halben Kiste
    conn.execute("UPDATE steuer_dateien SET hinzugefuegt = '2999-01-01T00:00:00'")
    conn.commit()
    quest.notiere(conn, 2025, "sieg", "Der Papierdrache ist besiegt")
    return conn, erste, post(client, "/steuer/2025/truhe", schloss=schloss).text


def test_runde_47_nachtrag_innen_und_was_neu_ist(client, ws):
    """Lena (Runde 48): Innen sah die vollständige Kiste aus wie die erste, die Mail nannte
    nicht, was neu ist, und die halbe Zip lag gleich daneben."""
    import zipfile

    _conn, erste, seite = _notausgang_dann_sieg(client, ws)
    assert f"Neu dabei seit der letzten Sendung: {erste['name']}." in seite
    ordner = quest.ordner(ws.root, 2025)
    assert (ordner / "Steuer-2025-Kim-erste-Sendung.zip").exists()
    assert not (ordner / "Steuer-2025-Kim.zip").exists()
    with zipfile.ZipFile(ordner / "Steuer-2025-Kim-vollstaendig.zip") as zf:
        inhalt = zf.read("00 Inhaltsverzeichnis.txt").decode()
    assert "Vollständiger Stand. Ersetzt meine Sendung vom" in inhalt
    assert "(neu)" in inhalt


def test_runde_47_innen_name_beim_nachtrag(client, ws):
    import zipfile

    _notausgang_dann_sieg(client, ws, schloss="passwort")
    zip_pfad = quest.ordner(ws.root, 2025) / "Steuer-2025-Kim-vollstaendig.zip"
    with zipfile.ZipFile(zip_pfad) as zf:
        assert zf.namelist() == ["Unterlagen-2025-vollstaendig.zip"]


def test_runde_47_zwei_halbe_sendungen(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    quest.notiere(conn, 2025, "truhe", "a.zip")
    quest.notiere(conn, 2025, "truhe", "b.zip")
    conn.execute("UPDATE steuer_chronik SET zeit = '2026-10-20T10:00:00' WHERE text = 'a.zip'")
    conn.execute("UPDATE steuer_chronik SET zeit = '2026-10-28T10:00:00' WHERE text = 'b.zip'")
    conn.commit()
    quest.notiere(conn, 2025, "sieg", "Der Papierdrache ist besiegt")
    assert quest.halbe_sendungen(conn, 2025) == ("2026-10-28T10:00:00", 2)
    n = truhe.Nachtrag(datum="28. Oktober 2026", mehrere=True)
    assert n.ersetzt() == "meine bisherigen Sendungen (zuletzt vom 28. Oktober 2026)"


def test_runde_47_mail_ausweg_bleibt_an_der_fundkiste(client, ws, monkeypatch):
    """Lena (Runde 48): Ließ sich die Mail nicht öffnen, landete man auf der Rechnungsseite."""
    _conn, _bonus = _besiegt_mit_bonus(client, ws)
    post(client, "/steuer/2025/truhe", schloss="passwort")
    monkeypatch.setattr(
        system,
        "compose_email",
        lambda *a, **k: system.Composed(False, "mailto:x@example.org?subject=S"),
    )
    r = client.post("/steuer/2025/mail", data={"token": client.token, "text": "Hallo", "oeffnen": "1"},
                    follow_redirects=False)  # fmt: skip
    assert r.headers["location"].endswith("/steuer/2025/sieg#brief")
    seite = client.get("/steuer/2025/sieg").text
    assert "sq-mail-ausweg" in seite and 'href="mailto:' in seite
    assert "Text kopieren" in seite and "Steuer-2025-Kim.zip" in seite
    assert "Das PDF" not in seite and "Rechnungsordner" not in seite


def test_runde_47_inhaltsverzeichnis_ohne_leere_bereiche(client, ws):
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    p = Profil.load(quest.profil_datei(ws.root, 2025))
    inhalt = truhe.inhaltsverzeichnis(
        p, reich(p), quest.stuecke(conn, 2025), quest.dateien(conn, 2025)
    )
    zeilen = inhalt.splitlines()
    for i, z in enumerate(zeilen):
        if z[:2].isdigit() and z[2:3] == " ":
            assert zeilen[i + 1].startswith("   "), z


def test_runde_47_beraterin_gilt_fuer_alle_jahre(client, ws):
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    fragebogen(client, jahr=2024, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    post(client, "/steuer/2024/beraterin/daten", beraterin_name="Frau Weber", anrede="sie",
         beraterin_email="weber@example.org")  # fmt: skip
    p25 = Profil.load(quest.profil_datei(ws.root, 2025))
    assert (p25.beraterin_name, p25.anrede) == ("Frau Weber", "sie")


def test_runde_48_ausweg_link_ist_immer_frisch(client, ws, monkeypatch):
    """Lena (Runde 49): Nach dem Finale öffnete der Ausweg noch die alte, halbe Mail."""
    from urllib.parse import unquote

    monkeypatch.setattr(
        system, "compose_email", lambda *a, **k: system.Composed(False, "mailto:alt")
    )
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    post(client, "/steuer/2025/drache/notausgang")
    post(client, "/steuer/2025/truhe", schloss="ohne")
    post(client, "/steuer/2025/mail", oeffnen="1", text="Hallo")
    seite = client.get("/steuer/2025/sieg").text
    assert "sq-mail-ausweg" in seite and "mailto:alt" not in seite
    for s in spiel.pflicht(quest.stuecke(conn, 2025)):
        quest.setze_status(conn, s["id"], "fragen")
    erste = spiel.pflicht(quest.stuecke(conn, 2025))[0]
    (ws.root / "beleg.pdf").write_bytes(b"%PDF beleg")
    quest.sammeln(conn, ws.root, erste["id"], ws.root / "beleg.pdf")
    quest.notiere(conn, 2025, "sieg", "Der Papierdrache ist besiegt")
    seite = post(client, "/steuer/2025/truhe", schloss="ohne").text
    # Lena (Runde 51): Nach dem Finale gilt der alte Ausweg gar nicht mehr; der Kasten geht weg
    assert "sq-mail-ausweg" not in seite and "mailto:" not in seite
    assert "vollständig" in seite.split('id="sq-mailtext"')[1]
    post(client, "/steuer/2025/mail", oeffnen="1", text="Hallo, alles da")
    seite = client.get("/steuer/2025/sieg").text
    link = re.search(r'href="(mailto:[^"]+)"', seite).group(1)
    assert "body=Hallo, alles da" in unquote(link.replace("&amp;", "&"))


def test_runde_48_finder_ohne_neuladen(client, ws):
    _conn, _bonus = _besiegt_mit_bonus(client, ws)
    post(client, "/steuer/2025/truhe", schloss="ohne")
    r = client.post("/steuer/2025/truhe/zeigen", data={"token": client.token},
                    headers={"x-frits-leise": "1"})  # fmt: skip
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["pfad"].endswith("Steuer-2025-Kim.zip")


def test_runde_48_beraterin_feld_fuer_feld(client, ws):
    """Lena (Runde 49): Das stille Speichern kommt Feld für Feld, die Mail kam nicht mit."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    fragebogen(client, jahr=2024, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    leise = {"x-frits-leise": "1", "x-frits-teilen": "1"}  # wie beim Verlassen des Felds
    client.post("/steuer/2024/beraterin/daten", headers=leise,
                data={"token": client.token, "beraterin_name": "Frau Weber"})  # fmt: skip
    client.post("/steuer/2024/beraterin/daten", headers=leise,
                data={"token": client.token, "anrede": "sie"})  # fmt: skip
    client.post("/steuer/2024/beraterin/daten", headers=leise,
                data={"token": client.token, "beraterin_email": "weber@example.org"})  # fmt: skip
    p25 = Profil.load(quest.profil_datei(ws.root, 2025))
    assert (p25.beraterin_name, p25.beraterin_email, p25.anrede) == (
        "Frau Weber",
        "weber@example.org",
        "sie",
    )
    # Eine andere Beraterin in einem anderen Jahr bleibt, wie sie ist
    client.post("/steuer/2025/beraterin/daten", headers=leise,
                data={"token": client.token, "beraterin_name": "Herr Kurz",
                      "beraterin_email": "kurz@example.org"})  # fmt: skip
    p24 = Profil.load(quest.profil_datei(ws.root, 2024))
    assert p24.beraterin_name == "Frau Weber"


def test_runde_49_halbe_werte_bleiben_im_jahr(client, ws):
    """Lena (Runde 50): Beim Tippen landete „Frau“ in 2025 und „weber@kanzlei“ in 2023."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    fragebogen(client, jahr=2024, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    tippen = {"x-frits-leise": "1"}
    client.post("/steuer/2024/beraterin/daten", headers=tippen,
                data={"token": client.token, "beraterin_name": "Frau"})  # fmt: skip
    p25 = Profil.load(quest.profil_datei(ws.root, 2025))
    assert p25.beraterin_name == ""  # beim Tippen wird nichts geteilt
    # Auch beim Verlassen nicht, solange der Wert halb ist
    raus = {"x-frits-leise": "1", "x-frits-teilen": "1"}
    client.post("/steuer/2024/beraterin/daten", headers=raus,
                data={"token": client.token, "beraterin_email": "weber@kanzlei"})  # fmt: skip
    p25 = Profil.load(quest.profil_datei(ws.root, 2025))
    assert (p25.beraterin_name, p25.beraterin_email) == ("", "")
    # Ein halber Rest von früher wird vervollständigt
    p25.beraterin_name = "Frau"
    p25.save(quest.profil_datei(ws.root, 2025))
    client.post("/steuer/2024/beraterin/daten", headers=raus,
                data={"token": client.token, "beraterin_name": "Frau Weber",
                      "beraterin_email": "weber@kanzlei.de"})  # fmt: skip
    p25 = Profil.load(quest.profil_datei(ws.root, 2025))
    assert (p25.beraterin_name, p25.beraterin_email) == ("Frau Weber", "weber@kanzlei.de")


def test_runde_49_eigener_mailtext_bleibt_nach_dem_ausweg(client, ws, monkeypatch):
    """Lena (Runde 50): Der eigene Zusatz im Mailtext ging nach dem Ausweg verloren."""
    monkeypatch.setattr(system, "compose_email", lambda *a, **k: system.Composed(False, "x"))
    _conn, _bonus = _besiegt_mit_bonus(client, ws)
    post(client, "/steuer/2025/truhe", schloss="ohne")
    seite = client.get("/steuer/2025/sieg").text
    text = re.search(
        r'<textarea id="sq-mailtext" name="text" rows="16">(.*?)</textarea>', seite, re.DOTALL
    )
    eigener = text.group(1).replace("&#34;", '"') + "\nPS: Bis Freitag?"
    post(client, "/steuer/2025/mail", oeffnen="1", text=eigener, betreff="Meine Unterlagen")
    seite = client.get("/steuer/2025/sieg").text
    assert "PS: Bis Freitag?" in seite and 'value="Meine Unterlagen"' in seite
    assert "Noch einmal in Mail öffnen" in seite


def test_runde_51_beraterin_wechselt_als_paar(client, ws):
    """Lena (Runde 51): Feld für Feld gewechselt, bekam 2020 „Frau Lange“ mit Brauns Mail."""
    for jahr in (2020, 2024, 2025):
        fragebogen(client, jahr=jahr, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    raus = {"x-frits-leise": "1", "x-frits-teilen": "1"}

    def tippen(jahr, **felder):
        client.post(f"/steuer/{jahr}/beraterin/daten", headers=raus,
                    data={"token": client.token, **felder})  # fmt: skip

    def paar(jahr):
        p = Profil.load(quest.profil_datei(ws.root, jahr))
        return (p.beraterin_name, p.beraterin_email)

    tippen(2025, beraterin_name="Herr Braun", beraterin_email="braun@example.org")
    assert paar(2020) == paar(2024) == ("Herr Braun", "braun@example.org")
    tippen(2024, beraterin_name="Frau Weber", beraterin_email="weber@example.org")
    assert paar(2020) == ("Herr Braun", "braun@example.org")  # 2020 hat Braun von 2025
    # Wechsel Feld für Feld: erst der Name, dann die Mail
    tippen(2025, beraterin_name="Frau Lange")
    tippen(2025, beraterin_email="lange@example.org")
    assert paar(2020) == ("Frau Lange", "lange@example.org")
    assert paar(2024) == ("Frau Weber", "weber@example.org")  # selbst getippt, bleibt
    # Leeren und neu tippen zieht ebenso als Paar mit
    tippen(2025, beraterin_email="")
    tippen(2025, beraterin_email="lange@kanzlei.de")
    assert paar(2020) == ("Frau Lange", "lange@kanzlei.de")
    # Halb getippt zieht nicht mit
    tippen(2025, beraterin_name="Herr")
    assert paar(2020) == ("Frau Lange", "lange@kanzlei.de")


def test_runde_51_finder_in_abschnitt_1_ohne_neuladen(client, ws):
    _conn, _bonus = _besiegt_mit_bonus(client, ws)
    seite = post(client, "/steuer/2025/truhe", schloss="ohne").text
    abschnitt = seite.split("<h2>1. Fundkiste packen</h2>")[1].split('id="brief"')[0]
    assert '<button class="sq-stempelknopf" data-leise>Im Finder zeigen</button>' in abschnitt
    assert "<span>Liegt hier:</span> <code></code>" in abschnitt


def test_runde_51_mailtext_still_gemerkt(client, ws, monkeypatch):
    """Lena (Runde 51): Geänderter Text war nach dem Neuladen weg, auch nach dem Ausweg."""
    monkeypatch.setattr(system, "compose_email", lambda *a, **k: system.Composed(False, "x"))
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    post(client, "/steuer/2025/truhe", schloss="ohne")
    seite = client.get("/steuer/2025/sieg").text
    assert 'data-brief-merken="/steuer/2025/mail/merken"' in seite
    post(client, "/steuer/2025/mail", oeffnen="1", text="Hallo")  # Ausweg
    r = client.post("/steuer/2025/mail/merken", headers={"x-frits-leise": "1"},
                    data={"token": client.token, "betreff": "Belege",
                          "text": "Hallo, PS: bis Freitag"})  # fmt: skip
    assert r.json() == {"ok": True}
    seite = client.get("/steuer/2025/sieg").text
    assert "PS: bis Freitag" in seite and "sq-mail-ausweg" in seite
    assert "sq-alter-text" not in seite
    # Nach dem Finale gilt die neue Mail: der Ausweg-Kasten geht, der alte Text bleibt greifbar
    quest.notiere(conn, 2025, "sieg", "Der Papierdrache ist besiegt")
    seite = post(client, "/steuer/2025/truhe", schloss="ohne").text
    assert "sq-mail-ausweg" not in seite
    assert "sq-alter-text" in seite and "PS: bis Freitag" in seite
    assert "vollständig" in seite.split('id="sq-mailtext"')[1]


def test_runde_52_brief_oeffnet_mail_nur_mit_knopf(client, ws, monkeypatch):
    """Lena (Runde 52): Das Fragebogen-Skript schickte den Brief ab, Mail ging ohne Klick auf."""
    geoeffnet = []
    monkeypatch.setattr(
        system, "compose_email", lambda *a, **k: geoeffnet.append(a) or system.Composed(True, "")
    )
    _conn, _bonus = _besiegt_mit_bonus(client, ws)
    post(client, "/steuer/2025/truhe", schloss="ohne")
    seite = client.get("/steuer/2025/sieg").text
    assert "form[data-merken]" not in seite and "data-merken=" not in seite.split('id="brief"')[1]
    assert 'data-brief-merken="/steuer/2025/mail/merken"' in seite
    assert '<button name="oeffnen" value="1"' in seite
    post(client, "/steuer/2025/mail", text="Hallo, nur gemerkt")  # ohne den Knopf
    assert geoeffnet == []
    seite = client.get("/steuer/2025/sieg").text
    assert "Hallo, nur gemerkt" in seite and "sq-mail-ausweg" not in seite
    post(client, "/steuer/2025/mail", oeffnen="1", text="Hallo")
    assert len(geoeffnet) == 1


def test_runde_52_kopie_des_vorjahrs_zieht_mit(client, ws):
    """Lena (Runde 52): Ein als Kopie des Vorjahrs angelegtes Jahr zog nie mit; Leeren in einem
    Jahr schrieb in allen anderen Anrede und Herkunft um."""
    raus = {"x-frits-leise": "1", "x-frits-teilen": "1"}

    def tippen(jahr, **felder):
        client.post(f"/steuer/{jahr}/beraterin/daten", headers=raus,
                    data={"token": client.token, **felder})  # fmt: skip

    def lies(jahr):
        return Profil.load(quest.profil_datei(ws.root, jahr))

    fragebogen(client, jahr=2023, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    tippen(2023, beraterin_name="Frau Weber", beraterin_email="weber@example.org", anrede="sie")
    fragebogen(client, jahr=2024, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    assert lies(2024).beraterin_von == 2023  # Kopie des Vorjahrs
    fragebogen(client, jahr=2020, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    tippen(2020, beraterin_name="Herr Kurz", beraterin_email="kurz@example.org", anrede="du")
    tippen(2023, beraterin_name="Frau Lange")
    tippen(2023, beraterin_email="lange@example.org")
    assert (lies(2024).beraterin_name, lies(2024).beraterin_email) == (
        "Frau Lange",
        "lange@example.org",
    )
    # Leeren in 2023 lässt das selbst getippte Jahr 2020 ganz in Ruhe
    tippen(2023, beraterin_name="", beraterin_email="")
    p20 = lies(2020)
    assert (p20.beraterin_name, p20.anrede, p20.beraterin_von) == ("Herr Kurz", "du", 0)


def test_runde_52_mail_nicht_rot_solange_neu_packen(client, ws):
    conn, bonus = _besiegt_mit_bonus(client, ws)
    post(client, "/steuer/2025/truhe", schloss="ohne")
    (ws.root / "spende.pdf").write_bytes(b"%PDF spende")
    quest.sammeln(conn, ws.root, bonus["id"], ws.root / "spende.pdf")
    seite = client.get("/steuer/2025/sieg").text
    brief = seite.split('id="brief"')[1]
    assert "Erst oben neu packen" in brief
    assert '<button name="oeffnen" value="1" class="sq-stempelknopf"' in brief


def test_runde_52_alter_text_bleibt_beim_neuen_brief(client, ws):
    """Lena (Runde 52): Wer nach dem Finale den neuen Brief änderte, verlor den alten Text."""
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    post(client, "/steuer/2025/truhe", schloss="ohne")
    merken = {"x-frits-leise": "1"}
    client.post("/steuer/2025/mail/merken", headers=merken,
                data={"token": client.token, "betreff": "B", "text": "Alter Text mit PS"})  # fmt: skip
    quest.notiere(conn, 2025, "sieg", "Der Papierdrache ist besiegt")
    post(client, "/steuer/2025/truhe", schloss="ohne")
    client.post("/steuer/2025/mail/merken", headers=merken,
                data={"token": client.token, "betreff": "B", "text": "Neuer Text"})  # fmt: skip
    seite = client.get("/steuer/2025/sieg").text
    assert "Neuer Text" in seite and "Alter Text mit PS" in seite and "sq-alter-text" in seite


def test_runde_53_enter_und_alte_kiste(client, ws, monkeypatch):
    """Lena (Runde 53): Enter im Betreff öffnete die Mail, auch mit der alten Kiste."""
    geoeffnet = []
    monkeypatch.setattr(
        system, "compose_email", lambda *a, **k: geoeffnet.append(a) or system.Composed(True, "")
    )
    conn, bonus = _besiegt_mit_bonus(client, ws)
    post(client, "/steuer/2025/truhe", schloss="ohne")
    seite = client.get("/steuer/2025/sieg").text
    # Der erste Knopf des Briefs ist gesperrt: Enter schickt nichts ab
    erster = re.search(r'<button[^>]*form="brief"[^>]*>|<form[^>]*id="brief"', seite).group(0)
    assert "disabled" in erster
    (ws.root / "spende.pdf").write_bytes(b"%PDF spende")
    quest.sammeln(conn, ws.root, bonus["id"], ws.root / "spende.pdf")
    seite = client.get("/steuer/2025/sieg").text
    # Lena (Runde 54): Mit der alten Kiste passte der Text nicht; der Knopf ist gesperrt
    assert 'href="#kiste">↑ Zur Fundkiste' in seite
    assert 'disabled title="Erst die Fundkiste neu packen"' in seite
    r = post(client, "/steuer/2025/mail", oeffnen="1", text="Hallo")
    assert geoeffnet == [] and "Pack die Fundkiste erst neu" in r.text
    post(client, "/steuer/2025/mail", oeffnen="1", alte_kiste="1", text="Hallo")
    assert geoeffnet == []
    post(client, "/steuer/2025/truhe", schloss="ohne")
    post(client, "/steuer/2025/mail", oeffnen="1", text="Hallo")
    assert len(geoeffnet) == 1


def test_runde_53_alle_frueheren_fassungen(client, ws):
    """Lena (Runde 53): Über zwei Wechsel blieb nur die letzte eigene Fassung."""
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    post(client, "/steuer/2025/truhe", schloss="ohne")
    leise = {"x-frits-leise": "1"}

    def merken(text, kennung=""):
        client.post("/steuer/2025/mail/merken", headers=leise,
                    data={"token": client.token, "betreff": "B", "text": text,
                          "kennung": kennung})  # fmt: skip

    def kennung():
        seite = client.get("/steuer/2025/sieg").text
        return re.search(r'name="kennung" value="([0-9a-f]+)"', seite).group(1)

    alt = kennung()
    merken("Erster Brief mit P1", alt)
    quest.notiere(conn, 2025, "sieg", "Der Papierdrache ist besiegt")
    post(client, "/steuer/2025/truhe", schloss="ohne")  # Brief neu
    # Der alte Tab merkt beim Wegklicken noch den alten Text: der neue Brief bleibt unberührt
    merken("Erster Brief mit P1, spaeter", alt)
    seite = client.get("/steuer/2025/sieg").text
    brief = re.search(r'id="sq-mailtext" name="text" rows="16">(.*?)</textarea>', seite, re.DOTALL)
    assert "spaeter" not in brief.group(1) and "Erster Brief mit P1, spaeter" in seite
    merken("Zweiter Brief EDIT0", kennung())
    # Noch ein Wechsel: Mit Namen der Beraterin schreibt FRITS eine andere Anrede
    post(client, "/steuer/2025/beraterin/daten", beraterin_name="Frau Weber")
    merken("Dritter Brief", kennung())
    seite = client.get("/steuer/2025/sieg").text
    assert "Zweiter Brief EDIT0" in seite and "Erster Brief mit P1, spaeter" in seite


def test_runde_53_leeren_gibt_kein_halbes_paar(client, ws):
    raus = {"x-frits-leise": "1", "x-frits-teilen": "1"}

    def tippen(jahr, **felder):
        client.post(f"/steuer/{jahr}/beraterin/daten", headers=raus,
                    data={"token": client.token, **felder})  # fmt: skip

    fragebogen(client, jahr=2024, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    tippen(2024, beraterin_name="Frau Weber", beraterin_email="weber@example.org")
    fragebogen(client, jahr=2020, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    p20 = Profil.load(quest.profil_datei(ws.root, 2020))
    p20.beraterin_name = p20.beraterin_email = ""
    p20.beraterin_von = 0
    p20.save(quest.profil_datei(ws.root, 2020))
    tippen(2024, beraterin_email="")
    p20 = Profil.load(quest.profil_datei(ws.root, 2020))
    assert (p20.beraterin_name, p20.beraterin_email) == ("", "")


def test_runde_54_wieder_abgelegt_ist_nicht_neu(client, ws):
    """Lena (Runde 54): Zurückgelegt und wieder abgelegt hieß in der Mail „neu dabei“."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    pflicht = spiel.pflicht(quest.stuecke(conn, 2025))
    erste, zweite = pflicht[0], pflicht[1]
    (ws.root / "a.pdf").write_bytes(b"%PDF a")
    quest.sammeln(conn, ws.root, erste["id"], ws.root / "a.pdf")
    post(client, "/steuer/2025/drache/notausgang")
    post(client, "/steuer/2025/truhe", schloss="ohne")  # halbe Kiste
    conn.execute("UPDATE steuer_chronik SET zeit = '2000-01-01T00:00:00' WHERE art = 'truhe'")
    conn.commit()
    d = quest.dateien(conn, 2025, erste["id"])[0]
    quest.entferne_datei(conn, d["id"])
    quest.sammeln(conn, ws.root, erste["id"], quest.lager(ws.root) / "a.pdf")  # dieselbe Datei
    (ws.root / "b.pdf").write_bytes(b"%PDF b")
    quest.sammeln(conn, ws.root, zweite["id"], ws.root / "b.pdf")  # wirklich neu
    for s in spiel.pflicht(quest.stuecke(conn, 2025))[2:]:
        quest.setze_status(conn, s["id"], "fragen")
    quest.notiere(conn, 2025, "sieg", "Der Papierdrache ist besiegt")
    seite = post(client, "/steuer/2025/truhe", schloss="ohne").text
    neu = re.search(r"Neu dabei seit der letzten Sendung: ([^\n]*)", seite).group(1)
    assert zweite["name"] in neu and erste["name"] not in neu


def test_runde_54_alter_tab_und_eigener_satz(client, ws):
    """Lena (Runde 54): Im alten Tab Nachgetipptes ging still verloren; Fassungen sahen gleich aus."""
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    post(client, "/steuer/2025/truhe", schloss="ohne")
    leise = {"x-frits-leise": "1"}

    def kennung():
        seite = client.get("/steuer/2025/sieg").text
        return re.search(r'name="kennung" value="([0-9a-f]+)"', seite).group(1)

    def merken(text, k):
        client.post("/steuer/2025/mail/merken", headers=leise,
                    data={"token": client.token, "betreff": "B", "text": text,
                          "kennung": k})  # fmt: skip

    def brief():
        seite = client.get("/steuer/2025/sieg").text
        t = re.search(r'id="sq-mailtext" name="text" rows="16">(.*?)</textarea>', seite, re.DOTALL)
        return html.unescape(t.group(1))

    alt, vorlage = kennung(), brief()
    merken(vorlage + "\nPS: Bis Freitag?", alt)
    quest.notiere(conn, 2025, "sieg", "Der Papierdrache ist besiegt")
    post(client, "/steuer/2025/truhe", schloss="ohne")
    merken(brief() + "\nNeuer Gruß", kennung())  # der neue Tab merkt zuerst
    merken(vorlage + "\nPS: Bis Freitag? Und danke!", alt)  # der alte Tab tippt nach
    seite = client.get("/steuer/2025/sieg").text
    assert "PS: Bis Freitag? Und danke!" in seite
    assert "mit: „PS: Bis Freitag? Und danke!“" in seite
    assert seite.count('class="sq-alter-satz"') == 1  # ähnliche Fassung ersetzt, nicht doppelt


def test_runde_55_erster_auftrag_ist_leicht(client, ws):
    """Lena (Runde 55): Der erste Auftrag war die Jahressteuerbescheinigung; danach die Burg."""
    fragebogen(client, teil1={"name": "Kim"},
               teil2={"m_freiberuflich_0": "1", "m_depots_inland": "1"})  # fmt: skip
    conn = quest.ensure_schema(store.connect(ws.db))
    a = client.app.state.quest_auftrag(conn)
    assert "Rathaus" in a["titel"] and "Steuer-ID" in a["text"] and "?karte=" in a["ziel"]
    # Alle Pflichtkarten erledigt außer der Burg: kein Auftrag in die freiwillige Burg
    for s in spiel.pflicht(quest.stuecke(conn, 2025)):
        quest.setze_status(conn, s["id"], "fragen")
    seite = client.get("/steuer/2025").text
    assert "Versiegle Burg" not in seite


def test_runde_55_crlf_ist_keine_eigene_fassung(client, ws):
    _conn, _bonus = _besiegt_mit_bonus(client, ws)
    post(client, "/steuer/2025/truhe", schloss="ohne")
    seite = client.get("/steuer/2025/sieg").text
    t = re.search(r'id="sq-mailtext" name="text" rows="16">(.*?)</textarea>', seite, re.DOTALL)
    k = re.search(r'name="kennung" value="([0-9a-f]+)"', seite).group(1)
    client.post("/steuer/2025/mail/merken", headers={"x-frits-leise": "1"},
                data={"token": client.token, "kennung": k,
                      "betreff": re.search(r'name="betreff" enterkeyhint="next" value="([^"]*)"',
                                           seite).group(1),
                      "text": html.unescape(t.group(1)).replace("\n", "\r\n")})  # fmt: skip
    assert 2025 not in client.app.state.steuer_mailtext
    assert "Noch keine Mail-Adresse deiner Beraterin" in seite


def test_runde_56_anders_gestempelt_heisst_neu_packen(client, ws):
    """Lena (Runde 56): Nach dem Packen „Gibt's nicht“ gestempelt: Brief und Zip widersprachen sich."""
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    post(client, "/steuer/2025/truhe", schloss="ohne")
    assert quest.kiste_karten(conn, 2025) == 0
    frage = next(s for s in spiel.pflicht(quest.stuecke(conn, 2025)) if s["status"] == "fragen")
    quest.setze_status(conn, frage["id"], "gibts_nicht")
    assert quest.kiste_karten(conn, 2025) == 1
    seite = client.get("/steuer/2025/sieg").text
    assert "1 Karte anders gestempelt seit dem Packen" in seite
    assert 'disabled title="Erst die Fundkiste neu packen"' in seite
    post(client, "/steuer/2025/truhe", schloss="ohne")
    assert quest.kiste_karten(conn, 2025) == 0


def test_runde_56_krankenversicherung_nicht_presseausweis():
    st = [
        {"id": 1, "gebiet": "werkstatt", "key": "beitraege", "status": "offen", "art": "bonus",
         "name": "Verbände, Presseausweis, Berufshaftpflicht"},
        {"id": 2, "gebiet": "burg", "key": "spenden", "status": "offen", "art": "bonus",
         "name": "Spenden und Mitgliedsbeiträge"},
    ]  # fmt: skip
    a = spiel.ahnung("Beitragsbescheinigung Krankenversicherung 2025.pdf", st)
    # Seit Runde 58: Fritte bietet die fehlende Karte an, statt nur die Burg zu nennen
    assert a.stueck is None and a.dazu[:2] == ("vorsorge", "Kranken- und Pflegeversicherung")


def test_runde_56_tagesziel_wartet_bis_nach_dem_kampf(client, ws):
    app = client.app
    app.state.steuer_tagwerk_spaeter[2025] = "Tagesziel geschafft: 3 Karten heute."
    _conn, _bonus = _besiegt_mit_bonus(client, ws)
    # Lena (Runde 57): Nach dem Sieg wartet es weiter, bis die Mail offen ist
    seite = client.get("/steuer/2025/sieg").text
    assert "Tagesziel geschafft: 3 Karten heute." not in seite
    assert 'class="sq-fast-fertig"' in seite and "Fundkiste packen und Mail öffnen" in seite
    post(client, "/steuer/2025/truhe", schloss="ohne")
    seite = client.get("/steuer/2025/sieg").text
    assert "Tagesziel geschafft: 3 Karten heute." not in seite
    assert "nur noch die Mail öffnen" in seite
    post(client, "/steuer/2025/mail", text="Hallo", oeffnen="1")
    seite = client.get("/steuer/2025/sieg").text
    assert 2025 not in app.state.steuer_tagwerk_spaeter
    assert 'class="sq-fast-fertig"' not in seite
    assert quest.hat_notiz(_conn, 2025, "mail")


def test_runde_57_fritte_raet_kv_spenden_zahnarzt():
    p = Profil(jahr=2025, mengen={"freiberuflich": [1, 0]}, haken=["vorsorge"])
    st = []
    for i, g in enumerate(reich(p)):
        for j, k in enumerate(g.stuecke):
            st.append({"id": i * 100 + j, "gebiet": g.id, "key": k.key, "name": k.name,
                       "status": "offen"})  # fmt: skip
    for name in ("KV-Beitragsbescheinigung_2025.pdf", "TK_Beitragsbescheinigung.pdf"):
        assert spiel.ahnung(name, st).stueck["key"] == "krankenversicherung", name
    assert spiel.ahnung("Riester_Bescheinigung_2025.pdf", st).stueck["key"] == "altersvorsorge"
    # Ohne Spenden- und Krankheits-Karte: nie die falsche Werkstatt-Karte, sondern die fehlende
    # Burg-Karte zum Dazunehmen (Runde 58)
    for name, haken in (
        ("Spendenquittung_2025.pdf", "spenden"),
        ("Zahnarzt_Rechnung_2025.pdf", "krankheit"),
    ):
        a = spiel.ahnung(name, st)
        assert a.stueck is None and a.dazu[0] == haken, name


def test_runde_57_lieber_fragen_bei_kernkarte_heisst_noch_nicht_da():
    p = Profil(jahr=2025, mengen={"depots_inland": [1, 0]})
    gebiete = reich(p)
    st = []
    for i, g in enumerate(gebiete):
        for j, k in enumerate(g.stuecke):
            st.append({"id": i * 100 + j, "gebiet": g.id, "key": k.key, "name": k.name,
                       "status": "fragen" if k.key == "steuerbescheinigung" else "gesammelt",
                       "frage": "", "wunsch": "", "dateien": 1})  # fmt: skip
    _betreff, text = truhe.mail(p, gebiete, st, mit_passwort=False)
    assert (
        "Jahressteuerbescheinigung: Habe ich noch nicht. Reicht es, wenn ich das nachreiche?"
        in text
    )
    assert "Ich bin unsicher" not in text


def test_runde_57_umstempeln_nach_dem_packen_nennt_auch_den_stempel(client, ws):
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    zweite = next(s for s in spiel.pflicht(quest.stuecke(conn, 2025)) if s["status"] == "fragen")
    (ws.root / "zwei.pdf").write_bytes(b"%PDF zwei")
    quest.sammeln(conn, ws.root, zweite["id"], ws.root / "zwei.pdf")
    post(client, "/steuer/2025/truhe", schloss="ohne")
    for d in quest.dateien(conn, 2025):
        if d["stueck_id"] == zweite["id"]:
            quest.entferne_datei(conn, d["id"])
    quest.setze_status(conn, zweite["id"], "gibts_nicht")
    seite = client.get("/steuer/2025/sieg").text
    assert "1 Beleg herausgenommen und 1 Karte anders gestempelt seit dem Packen" in seite


def test_runde_58_ohne_haken_karte_dazunehmen(client, ws):
    """Lena (Runde 58): Ohne Haken riet Fritte 0 von 5, die Krankenkasse ging zum Presseausweis."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    alle = quest.stuecke(conn, 2025)
    assert not any(s["gebiet"] == "burg" for s in alle)
    for name, haken in (
        ("KV-Beitragsbescheinigung_2022.pdf", "vorsorge"),
        ("TK_Jahresmeldung_2022.pdf", "vorsorge"),
        ("Riester_Bescheinigung_2022.pdf", "vorsorge"),
        ("Spendenquittung_2022.png", "spenden"),
        ("Zahnarzt_Rechnung_2022.pdf", "krankheit"),
    ):
        a = spiel.ahnung(name, alle)
        assert a.stueck is None and a.dazu and a.dazu[0] == haken, name
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "KV-Beitragsbescheinigung_2025.pdf").write_bytes(b"%PDF tk")
    insel = client.get("/steuer/2025").text
    assert "Karte dazunehmen" in insel and "Kranken- und Pflegeversicherung" in insel
    post(client, "/steuer/2025/lager/karte-dazu", name="KV-Beitragsbescheinigung_2025.pdf",
         haken="vorsorge")  # fmt: skip
    p = Profil.load(quest.profil_datei(ws.root, 2025))
    assert "vorsorge" in p.haken
    kv = next(s for s in quest.stuecke(conn, 2025) if s["key"] == "krankenversicherung")
    assert kv["status"] == "gesammelt"


def test_runde_58_tagesziel_wartet_auch_bei_anderen_karten(client, ws):
    """Lena (Runde 58): Nach dem Sieg, vor der Mail, kam „Für heute Schluss“ beim Zurücklegen."""
    app = client.app
    conn, bonus = _besiegt_mit_bonus(client, ws)
    app.state.steuer_tagwerk_spaeter[2025] = "Tagesziel geschafft: 3 Karten heute."
    post(client, f"/steuer/2025/stueck/{bonus['id']}/datei",
         files=[("datei", ("spende.pdf", b"%PDF spende"))])  # fmt: skip
    assert 2025 in app.state.steuer_tagwerk_spaeter
    post(client, "/steuer/2025/truhe", schloss="ohne")
    client.post("/steuer/2025/mail/geoeffnet", data={"token": client.token})
    assert quest.hat_notiz(conn, 2025, "mail")
    seite = client.get("/steuer/2025/sieg").text
    assert 'class="sq-meldung tagwerk"' in seite and "Tagesziel geschafft" in seite
    assert 2025 not in app.state.steuer_tagwerk_spaeter


def test_runde_59_karte_dazu_landet_auch_im_fragebogen_entwurf(client, ws):
    """Lena (Runde 59, P1): Fragebogen offen, dann „Karte dazunehmen“, dann Fragebogen
    speichern: Der neue Haken war weg und die leere Karte mit ihm."""
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    conn = quest.ensure_schema(store.connect(ws.db))
    client.get("/steuer/2025/fragebogen?teil=3")  # Entwurf liegt jetzt im Speicher
    client.post("/steuer/2025/fragebogen", data={"token": client.token, "teil": "3", "merken": "1"})
    quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
    (quest.lager(ws.root) / "Spendenquittung_2025.pdf").write_bytes(b"%PDF spende")
    post(client, "/steuer/2025/lager/karte-dazu", name="Spendenquittung_2025.pdf", haken="spenden")
    seite = client.get("/steuer/2025/fragebogen?teil=3").text
    assert re.search(r'name="haken" value="spenden"[^>]*checked', seite)
    post(client, "/steuer/2025/fragebogen", teil="3", haken="spenden")
    assert "spenden" in Profil.load(quest.profil_datei(ws.root, 2025)).haken
    assert any(s["key"] == "spenden" for s in quest.stuecke(conn, 2025))


def test_runde_59_tagesziel_wartet_auch_beim_pflicht_beleg(client, ws):
    """Lena (Runde 59): Pflicht-Beleg nach dem Sieg zurückgelegt: „Für heute Schluss“ kam."""
    app = client.app
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    quest.notiere(conn, 2025, "sieg", "Der Papierdrache ist besiegt")
    app.state.steuer_tagwerk_spaeter[2025] = (date.today(), "Tagesziel geschafft: 3 Karten heute.")
    d = quest.dateien(conn, 2025)[0]
    seite = post(client, f"/steuer/2025/datei/{d['id']}/entfernen").text
    assert "Tagesziel geschafft" not in seite and 2025 in app.state.steuer_tagwerk_spaeter
    # Ein Zettel von gestern kommt nicht mehr
    app.state.steuer_tagwerk_spaeter[2025] = (date(2020, 1, 1), "Tagesziel geschafft: alt.")
    quest.notiere(conn, 2025, "mail", "Mail an die Beraterin geöffnet")
    assert "Tagesziel geschafft: alt." not in client.get("/steuer/2025/sieg").text


def test_steuer_quest_neu_beginnen_sichert_und_verliert_nichts(client, ws):
    """Marie (3.11.): „Kann ich irgendwo komplett neu starten?“ Pro Jahr, ohne Datenverlust."""
    conn, _bonus = _besiegt_mit_bonus(client, ws)
    post(client, "/steuer/2025/truhe", schloss="ohne")
    gold = quest.gold(conn, 2025)
    beleg = quest.dateien(conn, 2025)[0]
    karte_id = beleg["stueck_id"]
    seite = client.get("/steuer/neu-beginnen?jahr=2025").text
    assert "Nichts geht verloren" in seite and 'value="2025" selected' in seite
    post(client, "/steuer/neu-beginnen", jahr="2025")
    # Sicherung mit Belegen, Fragebogen und Datenbank
    sich = next(quest.sicherungen(ws.root).iterdir())
    assert (sich / "2025" / "fragebogen.toml").exists() and (sich / "frits.db").exists()
    assert list((sich / "2025").rglob(Path(beleg["pfad"]).name))
    # Beleg im Korb, Jahr ohne Fragebogen, Karten offen, Salzkörner bleiben
    assert (quest.lager(ws.root) / beleg["original"]).exists()
    assert not quest.profil_datei(ws.root, 2025).exists()
    assert quest.dateien(conn, 2025) == []
    assert all(s["status"] == "offen" for s in quest.stuecke(conn, 2025))
    assert quest.gold(conn, 2025) == gold
    assert (
        client.get("/steuer/2025", follow_redirects=False)
        .headers["location"]
        .endswith("/steuer/2025/intro")
    )
    # Neu anfangen: dieselbe Karte behält ihre Nummer, kein zweites Salz
    fragebogen(client, teil1={"name": "Kim"}, teil2={"m_freiberuflich_0": "1"})
    assert quest.stueck(conn, karte_id) is not None
    assert "Neu beginnen" in client.get("/steuer/2025").text

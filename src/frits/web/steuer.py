"""Die Seiten der Steuer-Quest in der FRITS-App.

Die Spielregeln stehen in frits.steuer; hier wird nur angezeigt, gespeichert und weitergeleitet.
Wie überall in FRITS: Jeder Knopf, der etwas ändert, ist ein POST mit dem geheimen Token.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from frits import store, system
from frits.settings import Workspace
from frits.steuer import beraterin, quest, spiel, truhe
from frits.steuer.katalog import Profil, gebiet, reich

AKTIONEN = {"gibts_nicht", "fragen", "offen"}


def register_steuer(
    app: FastAPI,
    *,
    ws: Workspace,
    page: Callable,
    back: Callable,
    flash: Callable,
    settings: Callable,
    guarded: list,
    today: Callable[[], date],
) -> None:
    app.state.steuer_combo = {}
    app.state.steuer_notausgang = set()
    app.state.steuer_passwort = {}

    def conn():
        return quest.ensure_schema(store.connect(ws.db))

    def profil(jahr: int) -> Profil | None:
        return Profil.load(quest.profil_datei(ws.root, jahr))

    def profil_oder_404(jahr: int) -> Profil:
        p = profil(jahr)
        if p is None:
            raise HTTPException(404, "Für dieses Jahr gibt es noch keinen Fragebogen.")
        return p

    def lage(jahr: int):
        """Alles, was die Seiten über den Spielstand wissen müssen."""
        p = profil_oder_404(jahr)
        c = conn()
        gebiete = reich(p)
        alle = quest.stuecke(c, jahr)
        staende = spiel.provinzen(gebiete, alle)
        gesamt = spiel.stand(alle)
        vergleich = quest.vergleich(c, jahr)
        erfolge = spiel.erfolge(alle, staende, vergleich, quest.hat_notiz(c, jahr, "brief"))
        return {
            "jahr": jahr,
            "profil": p,
            "gebiete": gebiete,
            "stuecke": alle,
            "provinzen": staende,
            "gesamt": gesamt,
            "ressourcen": spiel.ressourcen(alle, staende),
            "zeitalter": spiel.zeitalter(gesamt.anteil),
            "naechstes": spiel.naechstes_zeitalter(gesamt.anteil),
            "drache": spiel.drache(alle),
            "vergleich": vergleich,
            "erfolge": erfolge,
            "chronik": quest.chronik(c, jahr),
        }

    def belohnen(jahr: int, vorher: dict) -> None:
        """Nach jeder Änderung: neue Siegel, neues Zeitalter und neue Erfolge feiern."""
        nachher = lage(jahr)
        c = conn()
        alt_siegel = {p.gebiet.id for p in vorher["provinzen"] if p.stand.siegel}
        for p in nachher["provinzen"]:
            if p.stand.siegel and p.gebiet.id not in alt_siegel:
                text = f"Gold-Siegel! {p.gebiet.titel} ist komplett."
                quest.notiere(c, jahr, "siegel", text)
                flash(text, "siegel", cue="done")
        if nachher["zeitalter"][0] > vorher["zeitalter"][0]:
            text = f"Aufstieg ins {nachher['zeitalter'][1]}!"
            quest.notiere(c, jahr, "zeitalter", text)
            flash(text, "zeitalter", cue="done")
        for e in spiel.neue_erfolge(vorher["erfolge"], nachher["erfolge"]):
            quest.notiere(c, jahr, "erfolg", e.name)
            flash(f"Erfolg freigeschaltet: {e.name}. {e.beschreibung}", "erfolg")

    def stueck_im_jahr(jahr: int, stueck_id: int) -> dict:
        try:
            s = quest.stueck(conn(), stueck_id)
        except quest.QuestError as exc:
            raise HTTPException(404, str(exc)) from exc
        if s["jahr"] != jahr:
            raise HTTPException(404)
        return s

    def standard_jahr() -> int:
        # Im Herbst 2026 sammelt man meistens für 2025.
        return today().year - 1

    async def hochladen(jahr: int, stueck_id: int, uploads: list[UploadFile]) -> int:
        """Speichert hochgeladene Dateien und sammelt sie ein. Gibt die Zahl neuer Funde zurück."""
        neu = 0
        c = conn()
        ablage = quest.lager(ws.root)
        ablage.mkdir(parents=True, exist_ok=True)
        for upload in uploads:
            name = Path(upload.filename or "").name
            if not name:
                continue
            tmp = ablage / f".upload-{name}"
            with tmp.open("wb") as fh:
                shutil.copyfileobj(upload.file, fh)
            fund = quest.sammeln(c, ws.root, stueck_id, tmp, original=name, verschieben=True)
            if fund.neu:
                neu += 1
                quest.notiere(c, jahr, "fund", f"{fund.stueck['name']}: {name}")
        return neu

    # ----- Einstieg und Fragebogen -----

    @app.get("/steuer")
    def steuer_start():
        return back(f"/steuer/{standard_jahr()}")

    @app.get("/steuer/{jahr}", response_class=HTMLResponse)
    def karte(request: Request, jahr: int):
        if profil(jahr) is None:
            return back(f"/steuer/{jahr}/fragebogen")
        return page(
            request,
            "steuer_karte.html",
            lager=quest.lager_dateien(ws.root),
            **lage(jahr),
        )

    @app.get("/steuer/{jahr}/fragebogen", response_class=HTMLResponse)
    def fragebogen(request: Request, jahr: int):
        p = profil(jahr) or profil(jahr - 1) or Profil(jahr=jahr, name=settings().name)
        p.jahr = jahr
        return page(request, "steuer_fragebogen.html", jahr=jahr, profil=p)

    @app.post("/steuer/{jahr}/fragebogen", dependencies=guarded)
    async def fragebogen_speichern(request: Request, jahr: int):
        form = await request.form()
        ja = set(form.getlist("ja"))
        try:
            wohnungen = max(0, min(20, int(str(form.get("wohnungen") or 0))))
        except ValueError:
            wohnungen = 0
        if "vermietung" in ja and wohnungen == 0:
            wohnungen = 1
        if "vermietung" not in ja:
            wohnungen = 0
        p = Profil(
            jahr=jahr,
            selbststaendig="selbststaendig" in ja,
            gesellschaft="gesellschaft" in ja,
            angestellt="angestellt" in ja,
            wohnungen=wohnungen,
            depot_inland="depot_inland" in ja,
            depot_ausland="depot_ausland" in ja,
            familie="familie" in ja,
            umsatzsteuer=str(form.get("umsatzsteuer") or "keine"),
            beraterin_email=str(form.get("beraterin_email") or "").strip(),
            name=str(form.get("name") or "").strip(),
        )
        neu = profil(jahr) is None
        p.save(quest.profil_datei(ws.root, jahr))
        gebiete = quest.start(conn(), p)
        quest.lager(ws.root).mkdir(parents=True, exist_ok=True)
        if neu:
            quest.notiere(conn(), jahr, "start", f"Reich gegründet mit {len(gebiete)} Provinzen")
            flash(f"Dein Reich steht: {len(gebiete)} Provinzen warten auf dich.", "erfolg")
        else:
            flash("Fragebogen gespeichert. Die Karte ist aktualisiert.")
        return back(f"/steuer/{jahr}")

    # ----- Provinzen und Sammeln -----

    @app.get("/steuer/{jahr}/gebiet/{gebiet_id}", response_class=HTMLResponse)
    def provinz(request: Request, jahr: int, gebiet_id: str):
        g = gebiet(profil_oder_404(jahr), gebiet_id)
        if g is None:
            raise HTTPException(404)
        daten = lage(jahr)
        c = conn()
        eigene = [s for s in daten["stuecke"] if s["gebiet"] == g.id]
        for s in eigene:
            s["datei_liste"] = quest.dateien(c, jahr, s["id"])
        vorjahr = [v for v in daten["vergleich"] if v["gebiet"] == g.id]
        stand = next(p for p in daten["provinzen"] if p.gebiet.id == g.id)
        return page(
            request,
            "steuer_gebiet.html",
            g=g,
            eigene=eigene,
            vorjahr=vorjahr,
            provinzstand=stand,
            lager=quest.lager_dateien(ws.root),
            **daten,
        )

    @app.post("/steuer/{jahr}/stueck/{stueck_id}/status", dependencies=guarded)
    async def status(request: Request, jahr: int, stueck_id: int):
        form = await request.form()
        s = stueck_im_jahr(jahr, stueck_id)
        aktion = str(form.get("status", ""))
        if aktion not in AKTIONEN:
            raise HTTPException(400)
        vorher = lage(jahr)
        frage = str(form.get("frage") or "").strip() if aktion == "fragen" else ""
        quest.setze_status(conn(), stueck_id, aktion, frage)
        if aktion == "gibts_nicht":
            flash(f"Notiert: {s['name']} gibt es dieses Jahr nicht. Zählt trotzdem!", "beute")
        elif aktion == "fragen":
            flash("Kommt auf die Fragenliste für deine Beraterin. Fragen ist klug.", "beute")
        belohnen(jahr, vorher)
        return back(str(form.get("back") or f"/steuer/{jahr}/gebiet/{s['gebiet']}"))

    @app.post("/steuer/{jahr}/stueck/{stueck_id}/datei", dependencies=guarded)
    async def datei_hochladen(request: Request, jahr: int, stueck_id: int):
        form = await request.form()
        s = stueck_im_jahr(jahr, stueck_id)
        uploads = [f for f in form.getlist("datei") if hasattr(f, "filename") and f.filename]
        if not uploads:
            flash("Da war keine Datei dabei. Zieh eine Datei in das Feld oder wähle eine aus.")
            return back(f"/steuer/{jahr}/gebiet/{s['gebiet']}")
        vorher = lage(jahr)
        neu = await hochladen(jahr, stueck_id, uploads)
        if neu:
            flash(
                f"+{neu} Beleg{'e' if neu > 1 else ''}! {s['name']} ist gesichert.", "beute", "done"
            )
        else:
            flash("Diese Datei hattest du schon gesammelt. Doppelt zählt nicht, aber gut gemerkt!")
        belohnen(jahr, vorher)
        return back(str(form.get("back") or f"/steuer/{jahr}/gebiet/{s['gebiet']}"))

    @app.post("/steuer/{jahr}/lager/zuordnen", dependencies=guarded)
    async def lager_zuordnen(request: Request, jahr: int):
        form = await request.form()
        name = Path(str(form.get("name", ""))).name
        quelle = quest.lager(ws.root) / name
        if not name or not quelle.is_file():
            raise HTTPException(404)
        try:
            stueck_id = int(str(form.get("stueck_id", "")))
        except ValueError:
            flash("Wähle aus, wohin die Datei gehört.")
            return back(f"/steuer/{jahr}")
        s = stueck_im_jahr(jahr, stueck_id)
        vorher = lage(jahr)
        c = conn()
        fund = quest.sammeln(c, ws.root, stueck_id, quelle, verschieben=True)
        if fund.neu:
            quest.notiere(c, jahr, "fund", f"{s['name']}: {name}")
            flash(f"+1 Beleg! {name} → {s['name']}", "beute", "done")
        else:
            flash(f"{name} war schon gesammelt, ich habe die doppelte Kopie weggeräumt.")
        belohnen(jahr, vorher)
        return back(str(form.get("back") or f"/steuer/{jahr}"))

    @app.post("/steuer/{jahr}/datei/{datei_id}/entfernen", dependencies=guarded)
    async def datei_entfernen(request: Request, jahr: int, datei_id: int):
        form = await request.form()
        quest.entferne_datei(conn(), datei_id)
        flash("Die Datei liegt wieder im Lager. Nichts ist verloren.")
        return back(str(form.get("back") or f"/steuer/{jahr}"))

    @app.post("/steuer/{jahr}/lager/oeffnen", dependencies=guarded)
    def lager_oeffnen(jahr: int):
        ordner = quest.lager(ws.root)
        ordner.mkdir(parents=True, exist_ok=True)
        if not system.open_path(ordner):
            flash(f"Dein Lager findest du hier: {ordner}")
        return back(f"/steuer/{jahr}")

    # ----- Mail der Beraterin -----

    @app.get("/steuer/{jahr}/beraterin", response_class=HTMLResponse)
    def beraterin_seite(request: Request, jahr: int):
        return page(request, "steuer_beraterin.html", ergebnis=None, text="", **lage(jahr))

    @app.post("/steuer/{jahr}/beraterin", dependencies=guarded)
    async def beraterin_lesen(request: Request, jahr: int):
        form = await request.form()
        text = str(form.get("text") or "")
        daten = lage(jahr)
        wuensche = beraterin.zuordnen(text, daten["gebiete"], daten["stuecke"])
        if not wuensche:
            flash("Ich habe in der Mail keine Wünsche gefunden. Aufzählungen klappen am besten.")
            return page(request, "steuer_beraterin.html", ergebnis=None, text=text, **daten)
        c = conn()
        titel = {g.id: g.titel for g in daten["gebiete"]}
        ergebnis = []
        for w in wuensche:
            if w.stueck_id:
                quest.markiere_wunsch(c, w.stueck_id)
                ergebnis.append({"text": w.text, "wo": titel[w.gebiete[0]], "neu": False})
            else:
                for gid in w.gebiete:
                    quest.neues_stueck(
                        c, jahr, gid, w.text[:120], hinweis="Wunsch deiner Beraterin"
                    )
                ergebnis.append(
                    {"text": w.text, "wo": ", ".join(titel[g] for g in w.gebiete), "neu": True}
                )
        vorher = daten
        quest.notiere(c, jahr, "brief", f"{len(wuensche)} Aufträge der Beraterin")
        flash(f"Botschaft entschlüsselt: {len(wuensche)} Aufträge auf deiner Karte.", "erfolg")
        belohnen(jahr, vorher)
        return page(request, "steuer_beraterin.html", ergebnis=ergebnis, text="", **lage(jahr))

    # ----- Der Papierdrache -----

    @app.get("/steuer/{jahr}/drache", response_class=HTMLResponse)
    def drache_seite(request: Request, jahr: int):
        daten = lage(jahr)
        combo = app.state.steuer_combo.get(jahr, {"combo": 0, "ruf": spiel.RUFE[0]})
        return page(request, "steuer_drache.html", combo=combo, **daten)

    @app.post("/steuer/{jahr}/drache/schlag", dependencies=guarded)
    async def drache_schlag(request: Request, jahr: int):
        form = await request.form()
        s = stueck_im_jahr(jahr, int(str(form.get("stueck_id") or 0)))
        aktion = str(form.get("aktion") or "")
        vorher = lage(jahr)
        kritisch = False
        if aktion == "datei":
            uploads = [f for f in form.getlist("datei") if hasattr(f, "filename") and f.filename]
            if not uploads:
                flash("Wähle eine Datei aus, dann trifft der Schlag.")
                return back(f"/steuer/{jahr}/drache")
            kritisch = await hochladen(jahr, s["id"], uploads) > 0
            if not kritisch:
                quest.setze_status(conn(), s["id"], "gesammelt")
        elif aktion in ("gibts_nicht", "fragen"):
            frage = str(form.get("frage") or "").strip() if aktion == "fragen" else ""
            quest.setze_status(conn(), s["id"], aktion, frage)
        else:
            raise HTTPException(400)
        alt = app.state.steuer_combo.get(jahr, {"combo": 0})
        combo, ruf = spiel.treffer(alt["combo"], kritisch)
        app.state.steuer_combo[jahr] = {"combo": combo, "ruf": ruf}
        quest.notiere(conn(), jahr, "treffer", f"{s['name']} ({ruf})")
        belohnen(jahr, vorher)
        if spiel.drache(quest.stuecke(conn(), jahr)).besiegt:
            quest.notiere(conn(), jahr, "sieg", "Der Papierdrache ist besiegt")
            app.state.steuer_combo.pop(jahr, None)
            flash("SIEG! Der Drache faltet sich zu einer Schatztruhe zusammen.", "sieg", "sent")
            return back(f"/steuer/{jahr}/sieg")
        return back(f"/steuer/{jahr}/drache")

    @app.post("/steuer/{jahr}/drache/notausgang", dependencies=guarded)
    def notausgang(jahr: int):
        app.state.steuer_notausgang.add(jahr)
        flash("Kein Problem. Was fehlt, steht in der Mail. Der Drache wartet geduldig.")
        return back(f"/steuer/{jahr}/sieg")

    # ----- Sieg, Schatztruhe und Mail -----

    def truhe_pfad(jahr: int, p: Profil) -> Path:
        name = f"-{quest._slug(p.name)}" if p.name else ""
        return quest.ordner(ws.root, jahr) / f"Steuer-{jahr}{name}.zip"

    @app.get("/steuer/{jahr}/sieg", response_class=HTMLResponse)
    def sieg(request: Request, jahr: int):
        daten = lage(jahr)
        if not daten["drache"].besiegt and jahr not in app.state.steuer_notausgang:
            flash("Erst der Drache, dann die Beute. Oder nimm den Notausgang, wenn es eilt.")
            return back(f"/steuer/{jahr}/drache")
        betreff, text = truhe.mail(daten["profil"], daten["gebiete"], daten["stuecke"])
        zip_pfad = truhe_pfad(jahr, daten["profil"])
        return page(
            request,
            "steuer_sieg.html",
            betreff=betreff,
            mailtext=text,
            zip_pfad=zip_pfad if zip_pfad.exists() else None,
            passwort=app.state.steuer_passwort.get(jahr),
            dateien=quest.dateien(conn(), jahr),
            **daten,
        )

    @app.post("/steuer/{jahr}/truhe", dependencies=guarded)
    def truhe_packen(jahr: int):
        daten = lage(jahr)
        if not daten["drache"].besiegt and jahr not in app.state.steuer_notausgang:
            return back(f"/steuer/{jahr}/drache")
        kennwort = truhe.passwort()
        ziel = truhe.packen(
            truhe_pfad(jahr, daten["profil"]),
            daten["profil"],
            daten["gebiete"],
            daten["stuecke"],
            quest.dateien(conn(), jahr),
            kennwort,
        )
        app.state.steuer_passwort[jahr] = kennwort
        quest.notiere(conn(), jahr, "truhe", ziel.name)
        flash("Die Schatztruhe ist gepackt und verschlossen.", "erfolg", "done")
        return back(f"/steuer/{jahr}/sieg")

    @app.post("/steuer/{jahr}/mail", dependencies=guarded)
    async def mail_oeffnen(request: Request, jahr: int):
        form = await request.form()
        p = profil_oder_404(jahr)
        zip_pfad = truhe_pfad(jahr, p)
        if not zip_pfad.exists():
            flash("Pack zuerst die Schatztruhe, dann hänge ich sie an.")
            return back(f"/steuer/{jahr}/sieg")
        betreff = str(form.get("betreff") or f"Steuerunterlagen {jahr}")
        text = str(form.get("text") or "")
        result = system.compose_email(p.beraterin_email, betreff, text, zip_pfad)
        if result.opened:
            flash("Die Mail ist in deinem Mail-Programm. Du drückst dort auf Senden.", cue="sent")
            return back(f"/steuer/{jahr}/sieg")
        app.state.mailto = result.mailto
        flash(f"Hänge die Truhe bitte selbst an: {zip_pfad}")
        return back("/mailto")

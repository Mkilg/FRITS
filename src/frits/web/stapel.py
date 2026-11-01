"""Die Stapel-Seiten: viele Dokumente auf einmal hineingeben, sortieren lassen, bestätigen.

Ablauf im Browser:
  /stapel                     Einwurf für Ordner, ZIP, viele Dateien oder Text
  /stapel/{id}                liest Datei für Datei hier (Fortschritt), dann die Übersicht
  /stapel/{id}/ki             zeigt genau, was an die KI ginge; erst der Knopf schickt
  /stapel/{id}/uebernehmen    legt jede bestätigte Datei an ihren Platz
  /stapel/{id}/dok/{nr}       ein Dokument im Detail: Vorschau, was die KI sieht, Vermutung

Ohne JavaScript geht alles auch, nur in größeren Schritten.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from frits import expenses, notfall, stapel, store
from frits.customers import Customer, note_id, save_note
from frits.ingest import IngestError, extract_text
from frits.providers import ProviderError
from frits.redact import LeakError, Mapping
from frits.settings import Workspace
from frits.stapel import Dok, Stapel, StapelError

LESEN_JE_SCHRITT = 2  # Dateien je Anfrage, wenn die Seite selbst weiterliest
LESEN_SEKUNDEN = 20  # ohne JavaScript: so lange liest eine Anfrage, dann geht es weiter


def register_stapel(
    app: FastAPI,
    *,
    ws: Workspace,
    page: Callable,
    back: Callable,
    flash: Callable,
    settings: Callable,
    customers: Callable[[], list[Customer]],
    nlp: Callable,
    guarded: list,
    provider_for: Callable,
    ai_ready: Callable,
    db: Callable,
    today: Callable[[], date],
) -> None:
    root = ws.root

    # ----- Hilfen -----

    def leise(request: Request) -> bool:
        return bool(request.headers.get("x-frits-leise"))

    def holen(stapel_id: str) -> Stapel:
        try:
            return stapel.laden(root, stapel_id)
        except StapelError:
            raise HTTPException(404) from None

    def steuerjahr() -> int:
        return today().year - 1  # im Herbst 2026 sammelt man für 2025, wie in der Steuer-Quest

    def quest_lage() -> dict:
        """Was die Steuer-Quest gerade weiß: Profil, Karten, Gebiete. Leer, wenn sie nicht läuft."""
        from frits.steuer import quest
        from frits.steuer.katalog import Profil, katalog, reich

        jahr = steuerjahr()
        profil = Profil.load(quest.profil_datei(root, jahr))
        lage = {"jahr": jahr, "profil": profil, "stuecke": [], "gebiete": {}, "aktiv": set()}
        lage["provinzen"] = {k: p.bereich for k, p in katalog().items()}
        if profil is None:
            return lage
        gebiete = reich(profil)
        lage["gebiete"] = {g.id: g for g in gebiete}
        lage["aktiv"] = {g.provinz.key for g in gebiete}
        lage["stuecke"] = quest.stuecke(quest.ensure_schema(db()), jahr)
        return lage

    def karten_fuer_ki(lage: dict) -> dict[str, dict]:
        out = {}
        for st in lage["stuecke"]:
            g = lage["gebiete"].get(st["gebiet"])
            bereich = g.bereich if g else st["gebiet"]
            out[f"K{st['id']}"] = {**st, "bereich": bereich}
        return out

    def gespraech(s: Stapel) -> notfall.Gespraech:
        """Eine Platzhalter-Liste für den ganzen Stapel: [PERSON_1] ist überall dieselbe Person."""
        g = notfall.neues_gespraech()
        g.mapping = Mapping(s.mapping)
        g.extra, g.auto_hidden = list(s.extra), list(s.auto_hidden)
        return g

    def bekannt(sha: str) -> str:
        conn = db()
        row = conn.execute("SELECT filename, status FROM jobs WHERE sha256 = ?", (sha,)).fetchone()
        if row:
            return f"liegt schon in FRITS als {row['filename']}"
        try:
            row = conn.execute(
                "SELECT original FROM steuer_dateien WHERE sha256 = ? LIMIT 1", (sha,)
            ).fetchone()
        except Exception:  # noqa: BLE001  (die Steuer-Tabellen gibt es erst nach dem ersten Besuch)
            row = None
        if row:
            return f"liegt schon in der Steuer-Quest ({row['original']})"
        return ""

    def lies(s: Stapel, d: Dok, lage: dict, people: list[Customer], model) -> None:
        """Eine Datei hier lesen, raten, schwärzen. Nichts davon verlässt den Computer."""
        from frits import beleg, kontakt

        path = stapel.pfad(root, s, d)
        d.art = stapel.art_von(d.datei)
        text = ""
        try:
            if d.art == "kontakt":
                text = path.read_bytes().decode("utf-8", errors="replace")
            else:
                text = extract_text(path).text
        except IngestError as exc:
            d.fehler = str(exc)
        except OSError:
            d.fehler = "Die Datei ließ sich nicht öffnen."
        text = text.replace("\r\n", "\n").strip()
        d.text = text[: stapel.MAX_TEXT]
        d.doppelt = bekannt(d.sha)
        felder = beleg.guess(text, today()) if text else {}
        gefunden: dict = {}
        if text and (d.art == "kontakt" or len(text) < 1500):
            try:
                gefunden = kontakt.details(text, model)
            except Exception:  # noqa: BLE001  (eine Vermutung darf nie das Lesen aufhalten)
                gefunden = {}
        v = stapel.vermuten(
            d,
            text,
            stuecke=lage["stuecke"],
            beleg=felder,
            kontakt=gefunden,
            eigene_iban=settings().iban,
            kunden=customers(),
        )
        d.ziel, d.grund, d.karte, d.provinz = v.ziel, v.grund, v.karte, v.provinz
        d.felder, d.kontakt = felder, gefunden
        if d.doppelt:
            d.ziel, d.grund = "beiseite", f"Kenne ich schon: {d.doppelt}."
        if text and model is not None and d.art != "kontakt":
            g = gespraech(s)
            try:
                d.geschwaerzt = g.schwaerzen(text[: stapel.AUSZUG], people, model)
            except LeakError:
                d.schwaerzen_fehler = (
                    "Ließ sich nicht sicher schwärzen. Darum geht davon nichts an die KI."
                )
            s.mapping, s.extra, s.auto_hidden = g.mapping.entries, g.extra, g.auto_hidden
        d.status = "gelesen" if text else "fehler"

    def lies_weiter(s: Stapel, anzahl: int | None = None, sekunden: float | None = None) -> int:
        lage = quest_lage()
        people = stapel.leute(customers(), settings().owner if settings().name else None)
        try:
            model = nlp()
        except RuntimeError:
            model = None  # ohne Sprachmodell: lokal raten geht, an die KI geht dann nichts
        start, n = time.monotonic(), 0
        for d in s.ungelesen:
            lies(s, d, lage, people, model)
            n += 1
            stapel.speichern(root, s)
            if anzahl and n >= anzahl:
                break
            if sekunden and time.monotonic() - start > sekunden:
                break
        if model is None and n:
            for d in s.docs:
                if d.status == "gelesen" and not d.geschwaerzt and not d.schwaerzen_fehler:
                    d.schwaerzen_fehler = "Das deutsche Sprachmodell zum Schwärzen fehlt. Darum geht nichts an die KI."
            stapel.speichern(root, s)
        return n

    async def nimm_dateien(s: Stapel, form) -> list[str]:
        hinweise: list[str] = []
        uploads = [u for u in form.getlist("files") if getattr(u, "filename", None)]
        pfade = [str(p) for p in form.getlist("pfade")]
        for i, upload in enumerate(uploads):
            name = pfade[i] if len(pfade) == len(uploads) and pfade[i] else str(upload.filename)
            data = await upload.read(stapel.MAX_DATEI + 1)
            if Path(name).suffix.lower() == ".zip" and len(data) > stapel.MAX_DATEI:
                await upload.seek(0)
                data = await upload.read(stapel.MAX_ZIP_INHALT)  # ein ZIP darf größer sein
            hinweise += stapel.hinzufuegen(root, s, name, data)
        text = str(form.get("text") or "").strip()
        if text:
            from frits.web.app import pasted_name

            hinweise += stapel.hinzufuegen(
                root, s, pasted_name("eingefuegt"), text.encode("utf-8")[:200_000]
            )
        stapel.speichern(root, s)
        return hinweise

    def zeige_hinweise(s: Stapel, hinweise: list[str]) -> None:
        for h in hinweise[:6]:
            flash(h, "error")
        if len(hinweise) > 6:
            flash(f"Und {len(hinweise) - 6} weitere Hinweise.", "error")

    def offene_belege() -> int:
        expenses.init_db(ws.db)
        return (
            db()
            .execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'receipt' AND filename NOT IN"
                " (SELECT source_file FROM expenses WHERE source_file IS NOT NULL)"
            )
            .fetchone()[0]
        )

    def im_eingang() -> int:
        if not ws.inbox.is_dir():
            return 0
        return sum(1 for p in ws.inbox.iterdir() if p.is_file() and not p.name.startswith("."))

    def luecken(s: Stapel, lage: dict) -> dict:
        """Was noch fehlt: offene Steuer-Karten, Provinzen zum Einschalten, Alltag, KI-Hinweise."""
        jahr = lage["jahr"]
        out = {"steuer": [], "provinzen": [], "alltag": [], "ki": [], "quest_fehlt": False}
        steuer_docs = [d for d in s.docs if d.ziel == "steuer"]
        if lage["profil"] is not None:
            gedeckt = {d.karte for d in steuer_docs if d.karte}
            titel = {gid: g.titel for gid, g in lage["gebiete"].items()}
            out["steuer"] = stapel.steuer_luecken(lage["stuecke"], titel, gedeckt, jahr)
            for key, n in stapel.neue_provinzen(s, lage["aktiv"]).items():
                out["provinzen"].append((lage["provinzen"].get(key, key), n))
        elif steuer_docs:
            out["quest_fehlt"] = True
        belege = offene_belege()
        if belege:
            out["alltag"].append(
                stapel.Luecke(
                    "Alltag",
                    f"{belege} Beleg{'e warten' if belege > 1 else ' wartet'} noch auf Betrag, "
                    "Datum oder Kategorie.",
                    "/expenses",
                    "Eintragen",
                )
            )
        eingang = im_eingang()
        if eingang:
            out["alltag"].append(
                stapel.Luecke(
                    "Alltag",
                    f"{eingang} Datei{'en liegen' if eingang > 1 else ' liegt'} im Eingang. Daraus "
                    "werden Rechnungsentwürfe, sobald die KI sie liest.",
                    "/",
                    "Zum Eingang",
                )
            )
        for d in s.docs:
            neu_ohne_adresse = (
                d.ziel == "kunde"
                and d.status == "erledigt"
                and d.ergebnis.startswith("Neu bei Kunden")
                and not d.kontakt.get("address")
            )
            if neu_ohne_adresse:
                out["alltag"].append(
                    stapel.Luecke(
                        "Alltag",
                        f"Bei {d.kontakt.get('company') or d.kontakt.get('name')} fehlt noch die "
                        "Adresse. Ohne sie geht keine Rechnung.",
                        d.link,
                        "Ergänzen",
                    )
                )
        out["ki"] = [s.reidentify(h) for h in s.ki_hinweise]
        return out

    def zeigen(request: Request, s: Stapel | None, **extra) -> HTMLResponse:
        current = settings()
        lage = quest_lage()
        ctx: dict = {"s": s, "alle": [], "ziele": stapel.ZIELE, "zeichen": stapel.ZIEL_ZEICHEN}
        ctx["ki_bereit"] = ai_ready(current)
        ctx["kategorien"] = expenses.CATEGORIES
        ctx["lage"] = lage
        if s is None:
            ctx["alle"] = [x for x in stapel.alle(root) if not x.abgeschlossen]
        else:
            ctx["luecken"] = luecken(s, lage)
            karten = []
            for st in lage["stuecke"]:
                g = lage["gebiete"].get(st["gebiet"])
                karten.append((st["id"], g.titel if g else st["gebiet"], st["name"], st["status"]))
            ctx["karten"] = karten
            ctx["karten_name"] = {k[0]: f"{k[2]} ({k[1]})" for k in karten}
        return page(request, "stapel.html", **ctx, **extra)

    # ----- Seiten -----

    @app.get("/stapel", response_class=HTMLResponse)
    def stapel_start(request: Request):
        return zeigen(request, None)

    @app.post("/stapel/neu", dependencies=guarded)
    async def stapel_neu(request: Request):
        form = await request.form()
        s = stapel.neu(root)
        hinweise = await nimm_dateien(s, form)
        if leise(request):
            return JSONResponse(
                {"id": s.id, "url": f"/stapel/{s.id}", "anzahl": len(s.docs), "hinweise": hinweise}
            )
        if not s.docs:
            stapel.schliessen(root, s)
            zeige_hinweise(s, hinweise)
            if not hinweise:
                flash(
                    "Es kam keine Datei an, die ich lesen kann. Zieh Dateien, einen Ordner "
                    "oder ein ZIP auf das Feld.",
                    "error",
                )
            return back("/stapel")
        zeige_hinweise(s, hinweise)
        return back(f"/stapel/{s.id}")

    @app.post("/stapel/{stapel_id}/dazu", dependencies=guarded)
    async def stapel_dazu(request: Request, stapel_id: str):
        s = holen(stapel_id)
        hinweise = await nimm_dateien(s, await request.form())
        if leise(request):
            return JSONResponse({"id": s.id, "anzahl": len(s.docs), "hinweise": hinweise})
        zeige_hinweise(s, hinweise)
        return back(f"/stapel/{s.id}")

    @app.get("/stapel/{stapel_id}", response_class=HTMLResponse)
    def stapel_ansicht(request: Request, stapel_id: str):
        s = holen(stapel_id)
        return zeigen(request, s)

    @app.post("/stapel/{stapel_id}/lesen", dependencies=guarded)
    async def stapel_lesen(request: Request, stapel_id: str):
        s = holen(stapel_id)
        if leise(request):
            await run_in_threadpool(lies_weiter, s, LESEN_JE_SCHRITT)
            gelesen = len(s.docs) - len(s.ungelesen)
            return JSONResponse(
                {"gelesen": gelesen, "gesamt": len(s.docs), "fertig": not s.ungelesen}
            )
        await run_in_threadpool(lies_weiter, s, None, LESEN_SEKUNDEN)
        if not s.ungelesen:
            flash(
                f"Fertig gelesen: {len(s.docs)} Dokumente. Schau dir meine Vorschläge an.",
                cue="done",
            )
        return back(f"/stapel/{s.id}")

    @app.get("/stapel/{stapel_id}/datei/{nr}")
    def stapel_datei(stapel_id: str, nr: int):
        s = holen(stapel_id)
        d = s.dok(nr)
        if d is None:
            raise HTTPException(404)
        path = stapel.pfad(root, s, d).resolve()
        if not path.is_file() or stapel.ordner(root, s.id).resolve() not in path.parents:
            raise HTTPException(404)
        return FileResponse(path, content_disposition_type="inline")

    @app.get("/stapel/{stapel_id}/dok/{nr}", response_class=HTMLResponse)
    def stapel_dok(request: Request, stapel_id: str, nr: int):
        s = holen(stapel_id)
        d = s.dok(nr)
        if d is None:
            raise HTTPException(404)
        return zeigen(request, s, d=d)

    @app.get("/stapel/{stapel_id}/ki", response_class=HTMLResponse)
    def stapel_ki_vorschau(request: Request, stapel_id: str):
        s = holen(stapel_id)
        docs = s.ki_wartet
        lage = quest_lage()
        nachricht = (
            stapel.ki_nachricht(
                docs,
                karten_fuer_ki(lage),
                lage["provinzen"],
                lage["aktiv"],
                {k: c.label for k, c in expenses.CATEGORIES.items()},
            )
            if docs
            else ""
        )
        return zeigen(
            request,
            s,
            ki_vorschau=True,
            ki_docs=docs,
            ki_text=nachricht,
            ki_system=stapel.SYSTEM,
            ki_pakete=-(-len(docs) // stapel.KI_PAKET),
        )

    def ki_paket(s: Stapel) -> tuple[int, str]:
        """Schickt das nächste Paket an die KI. Gibt (geänderte Dokumente, Fehlertext) zurück."""
        docs = s.ki_wartet[: stapel.KI_PAKET]
        if not docs:
            return 0, ""
        lage = quest_lage()
        karten = karten_fuer_ki(lage)
        kategorien = {k: c.label for k, c in expenses.CATEGORIES.items()}
        nachricht = stapel.ki_nachricht(docs, karten, lage["provinzen"], lage["aktiv"], kategorien)
        try:
            provider = provider_for(settings())
            if not hasattr(provider, "chat"):
                raise ProviderError(
                    "Diese KI kann keine Stapel sortieren. Meine Vermutungen bleiben."
                )
            antwort, usage = provider.chat(stapel.SYSTEM, [{"role": "user", "content": nachricht}])
        except ProviderError as exc:
            s.ki_fehler = str(exc)
            stapel.speichern(root, s)
            return 0, str(exc)
        store.log_ai_call(db(), None, usage, stapel.fingerabdruck(nachricht))
        s.ki_kosten += usage.cost_usd or 0.0
        s.ki_modell = usage.model
        s.ki_gesendet += [d.nr for d in docs]
        try:
            n = stapel.ki_antwort_uebernehmen(s, antwort, karten, set(kategorien))
            s.ki_fehler = ""
        except StapelError as exc:
            n, s.ki_fehler = 0, str(exc)
        stapel.speichern(root, s)
        return n, s.ki_fehler

    @app.post("/stapel/{stapel_id}/ki", dependencies=guarded)
    async def stapel_ki(request: Request, stapel_id: str):
        s = holen(stapel_id)
        if leise(request):
            n, fehler = await run_in_threadpool(ki_paket, s)
            return JSONResponse(
                {
                    "geaendert": n,
                    "fehler": fehler,
                    "fertig": bool(fehler) or not s.ki_wartet,
                    "gesendet": len(s.ki_gesendet),
                    "gesamt": len(s.ki_gesendet) + len(s.ki_wartet),
                }
            )
        gesamt, fehler = 0, ""
        while s.ki_wartet and not fehler:
            n, fehler = await run_in_threadpool(ki_paket, s)
            gesamt += n
        if fehler:
            flash(fehler, "error")
        else:
            flash(f"Die KI hat {gesamt} Dokumente einsortiert. Prüf die Vorschläge.", cue="done")
        return back(f"/stapel/{s.id}#uebersicht")

    # ----- Übernehmen -----

    def ziel_steuer(s: Stapel, d: Dok, karte_id: int | None, lage: dict) -> None:
        from frits.steuer import quest

        name = PurePosixPath(d.name).name
        karte = next((st for st in lage["stuecke"] if st["id"] == karte_id), None)
        if karte:
            # Der Kartenname vorn im Dateinamen: so schlägt der Eingangskorb der Quest
            # genau diese Karte vor, und "✓ Ja, passt" legt sie mit einem Klick ab.
            name = f"{karte['name']} – {name}"
        ziel = stapel.frei(quest.lager(root), name, move_from=stapel.pfad(root, s, d))
        d.karte = karte["id"] if karte else None
        jahr = lage["jahr"]
        if lage["profil"] is None:
            d.ergebnis = f"Liegt im Lager der Steuer-Quest ({ziel.name})."
            d.link = f"/steuer/{jahr}"
        elif karte:
            d.ergebnis = f"Im Eingangskorb der Steuer-Quest, Vorschlag: {karte['name']}."
            d.link = f"/steuer/{jahr}"
        else:
            d.ergebnis = "Im Eingangskorb der Steuer-Quest. Zieh es dort auf eine Provinz."
            d.link = f"/steuer/{jahr}"

    def ziel_ausgabe(s: Stapel, d: Dok, f: dict) -> None:
        from frits.billing import BillingError, parse_amount

        conn = db()
        if conn.execute("SELECT 1 FROM jobs WHERE sha256 = ?", (d.sha,)).fetchone():
            d.ergebnis, d.link = (
                "Diesen Beleg kenne ich schon. Nichts doppelt gebucht.",
                "/expenses",
            )
            stapel.frei(
                stapel.wurzel(root) / "beiseite gelegt", d.datei, move_from=stapel.pfad(root, s, d)
            )
            return
        datei = stapel.frei(ws.done, PurePosixPath(d.name).name, move_from=stapel.pfad(root, s, d))
        job_id = store.add_job(conn, d.sha, datei.name, "receipt")
        try:
            gross = parse_amount(f.get("gross"))
            tag = date.fromisoformat(str(f.get("date") or ""))
            kategorie = str(f.get("category") or "")
            rate = str(f.get("vat_rate") or "")
            if gross is None or gross <= 0 or kategorie not in expenses.CATEGORIES:
                raise ValueError
            expense = expenses.Expense(
                date=tag,
                vendor=str(f.get("vendor") or "").strip()[:80] or "unbekannt",
                description=s.reidentify(d.titel)[:120],
                gross=gross,
                vat_rate=Decimal(rate) if rate in ("0", "0.07", "0.19") else None,
                category=kategorie,
                source_file=datei.name,
            )
        except (BillingError, ValueError, TypeError, ArithmeticError):
            d.ergebnis = "Wartet bei Ausgaben: Betrag, Datum oder Kategorie fehlen noch."
            d.link = f"/expenses?receipt={job_id}#neu"
            return
        expenses.add_expense(ws.db, expense)
        d.felder = {**d.felder, **f}
        betrag = f"{expense.gross:.2f}".replace(".", ",")
        d.ergebnis = f"Gebucht: {betrag} € bei {expense.vendor}."
        d.link = f"/expenses?year={expense.date.year}"

    def ziel_kunde(s: Stapel, d: Dok) -> bool:
        """Legt den Kunden an. False: kein Name gefunden, das Dokument bleibt offen."""
        k = d.kontakt
        name = (k.get("name") or k.get("company") or "").strip()
        if not name:
            d.grund = "Darin war kein Name. Leg den Kunden lieber von Hand an."
            return False
        vorhanden = stapel.kunde_bekannt(k, customers())
        if vorhanden:
            d.ergebnis = f"{vorhanden.display_name} kenne ich schon."
            d.link = f"/customers/{vorhanden.id}"
        else:
            cid = note_id(k.get("company") or name)
            if (ws.customers / f"{cid}.md").exists():
                import secrets

                cid = f"{cid}-{secrets.token_hex(2)}"
            c = Customer(
                id=cid,
                name=name,
                company=(k.get("company") or None) if k.get("company") != name else None,
                address=" ".join(str(k.get("address") or "").split()) or None,
                email=(k.get("email") or None),
                phone=(k.get("phone") or None),
            )
            save_note(ws.customers, c)
            d.ergebnis, d.link = f"Neu bei Kunden: {c.display_name}.", f"/customers/{c.id}"
        stapel.frei(ws.done, PurePosixPath(d.name).name, move_from=stapel.pfad(root, s, d))
        return True

    @app.post("/stapel/{stapel_id}/uebernehmen", dependencies=guarded)
    async def stapel_uebernehmen(request: Request, stapel_id: str):
        s = holen(stapel_id)
        form = await request.form()
        lage = quest_lage()
        zahl = dict.fromkeys(stapel.ZIELE, 0)
        for d in s.offen:
            if f"nimm_{d.nr}" not in form:
                continue
            ziel = str(form.get(f"ziel_{d.nr}") or d.ziel)
            if ziel not in stapel.ZIELE:
                continue
            if ziel != d.ziel:
                d.quelle = "du"
            d.ziel = ziel
            if not stapel.pfad(root, s, d).is_file():
                d.status, d.ergebnis = "erledigt", "Die Datei war nicht mehr da."
                continue
            if ziel == "rechnung":
                ziel_datei = stapel.frei(
                    ws.inbox, PurePosixPath(d.name).name, move_from=stapel.pfad(root, s, d)
                )
                d.ergebnis, d.link = f"Liegt im Eingang ({ziel_datei.name}).", "/"
            elif ziel == "ausgabe":
                f = {
                    "gross": str(form.get(f"gross_{d.nr}") or d.felder.get("gross") or ""),
                    "date": str(form.get(f"date_{d.nr}") or d.felder.get("date") or ""),
                    "vendor": str(form.get(f"vendor_{d.nr}") or d.felder.get("vendor") or ""),
                    "category": str(form.get(f"category_{d.nr}") or d.felder.get("category") or ""),
                    "vat_rate": str(d.felder.get("vat_rate") or ""),
                }
                ziel_ausgabe(s, d, f)
            elif ziel == "kunde":
                if not ziel_kunde(s, d):
                    continue
            elif ziel == "steuer":
                try:
                    karte = int(str(form.get(f"karte_{d.nr}") or d.karte or ""))
                except ValueError:
                    karte = None
                ziel_steuer(s, d, karte, lage)
            else:
                stapel.frei(
                    stapel.wurzel(root) / "beiseite gelegt",
                    d.datei,
                    move_from=stapel.pfad(root, s, d),
                )
                d.ergebnis = "Beiseite gelegt. Nichts gelöscht."
            d.status = "erledigt"
            zahl[ziel] += 1
        stapel.speichern(root, s)
        teile = []
        for key, eins, viele in (
            ("rechnung", "für eine Rechnung im Eingang", "für Rechnungen im Eingang"),
            ("ausgabe", "Ausgabe", "Ausgaben"),
            ("kunde", "Kunde", "Kunden"),
            ("steuer", "für die Steuer-Quest", "für die Steuer-Quest"),
            ("beiseite", "beiseite gelegt", "beiseite gelegt"),
        ):
            if zahl[key]:
                teile.append(f"{zahl[key]} {eins if zahl[key] == 1 else viele}")
        if teile:
            flash("Erledigt: " + ", ".join(teile) + ".", cue="done", stamp="Fertig")
        else:
            flash("Du hast nichts angehakt. Hak an, was ich übernehmen soll.", "error")
        return back(f"/stapel/{s.id}#fehlt")

    @app.post("/stapel/{stapel_id}/schliessen", dependencies=guarded)
    def stapel_schliessen(stapel_id: str):
        s = holen(stapel_id)
        ziel = stapel.schliessen(root, s)
        if ziel:
            flash(
                f"Der Stapel ist zu. Was übrig war, liegt im Ordner „{ziel.parent.name}/{ziel.name}“. "
                "Nichts ist gelöscht."
            )
        else:
            flash("Der Stapel ist zu. Alles hat seinen Platz.", cue="done")
        return back("/stapel")

    @app.post("/stapel/{stapel_id}/schwaerzen", dependencies=guarded)
    async def stapel_schwaerzen(request: Request, stapel_id: str):
        """Weitere Wörter schwärzen ("Auch schwärzen"), für alle Auszüge des Stapels."""
        from frits.lernen import extra_terms

        s = holen(stapel_id)
        form = await request.form()
        try:
            model = nlp()
        except RuntimeError as exc:
            flash(str(exc), "error")
            return back(f"/stapel/{s.id}/ki")
        g = gespraech(s)
        g.extra = extra_terms(str(form.get("extra") or ""))
        people = stapel.leute(customers(), settings().owner if settings().name else None)
        for d in s.docs:
            if (
                d.status == "gelesen"
                and d.text
                and d.art != "kontakt"
                and d.nr not in s.ki_gesendet
            ):
                try:
                    d.geschwaerzt = g.schwaerzen(d.text[: stapel.AUSZUG], people, model)
                    d.schwaerzen_fehler = ""
                except LeakError:
                    d.geschwaerzt = ""
                    d.schwaerzen_fehler = (
                        "Ließ sich nicht sicher schwärzen. Darum geht davon nichts an die KI."
                    )
        s.mapping, s.extra, s.auto_hidden = g.mapping.entries, g.extra, g.auto_hidden
        stapel.speichern(root, s)
        flash("Gemerkt. Die Auszüge sind neu geschwärzt.")
        return back(f"/stapel/{s.id}/ki")

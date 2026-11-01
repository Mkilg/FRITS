"""Files for the tax adviser: the Buchungsliste and the Verfahrensdokumentation of one year.

Both only write files into a folder; packing and sending is someone else's job (the
Steuer-Quest puts them into its Schatztruhe, see frits.steuer.ernte).

* ``buchungsliste``: every payment that counts for the EÜR of the year, one row per VAT
  rate, as a CSV that German Excel and the usual import dialogs (DATEV "Buchungsstapel"
  by hand, Lexware, a spreadsheet) understand. It lists the same money as the EÜR, so the
  sums match it to the cent.
* ``verfahrensdoku``: the short Verfahrensdokumentation the GoBD ask for (BMF-Schreiben
  vom 28.11.2019, Rz. 151 ff.): how receipts come in, where they are kept, why nothing can
  be changed unnoticed, and how long things are kept.

FRITS doesn't pick accounts (SKR03/SKR04): that is the adviser's call, so there is no
account column; the EÜR line says what the money is.
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from frits import __version__, gutschriften
from frits.euer import LINE_KLEINUNTERNEHMER, LINE_NET_INCOME, LINE_TAX_FREE, expenses_of_year
from frits.expenses import CATEGORIES, VAT_PAYMENT
from frits.render import FONTS
from frits.settings import Settings, Workspace
from frits.ustva import invoice_groups

BUCHUNGSLISTE = "Buchungsliste_{jahr}.csv"
VERFAHRENSDOKU = "Verfahrensdokumentation_{jahr}.pdf"

COLUMNS = (
    "Datum",
    "Belegnummer",
    "Buchungstext",
    "Einnahme/Ausgabe",
    "Brutto (EUR)",
    "Netto (EUR)",
    "USt-Satz (%)",
    "USt/Vorsteuer (EUR)",
    "Zeile Anlage EÜR",
    "Belegdatum",
    "Belegdatei",
)


@dataclass(frozen=True)
class Buchung:
    day: date
    beleg: str
    text: str
    income: bool
    gross: Decimal
    net: Decimal
    rate: Decimal | None
    vat: Decimal
    euer_line: str
    beleg_day: date
    file: str


def _num(amount: Decimal) -> str:
    return f"{amount:.2f}".replace(".", ",")


def _rate(rate: Decimal | None) -> str:
    if rate is None:
        return ""
    return f"{rate.normalize():f}".replace(".", ",")


def _income_line(regel: bool, kind: str, net: Decimal, vat: Decimal) -> str:
    """The Anlage-EÜR line, chosen exactly as frits.euer.summarize chooses it."""
    if not regel:
        return LINE_KLEINUNTERNEHMER
    if kind != "inland" or (vat == 0 and net != 0):
        return LINE_TAX_FREE
    return LINE_NET_INCOME


def buchungen(ws: Workspace, jahr: int, settings: Settings | None = None) -> list[Buchung]:
    """All payments of ``jahr`` in the order they happened."""
    settings = settings or Settings.load(ws.settings_file)
    regel = settings.tax_mode == "regelbesteuerung"
    rows: list[Buchung] = []
    conn = sqlite3.connect(ws.db)
    conn.row_factory = sqlite3.Row
    try:
        invoices = conn.execute(
            "SELECT * FROM invoices WHERE paid_date >= ? AND paid_date < ? ORDER BY paid_date",
            (f"{jahr:04d}-01-01", f"{jahr + 1:04d}-01-01"),
        ).fetchall()
        for row in invoices:
            paid = date.fromisoformat(row["paid_date"])
            issued = date.fromisoformat(row["issue_date"])
            name = Path(row["pdf_path"]).name if row["pdf_path"] else ""
            text = (
                f"Storno {row['number']} zu {row['cancels']} {row['customer']}"
                if row["cancels"]
                else f"Rechnung {row['number']} {row['customer']}"
            )
            groups = list(invoice_groups(conn, row, settings))
            if not groups:  # written as Kleinunternehmer: one row with the whole amount
                gross = Decimal(row["gross"])
                groups = [("inland", None, gross, Decimal(0))]
            for kind, rate, net, vat in groups:
                line = _income_line(regel, kind, net, vat)
                rows.append(
                    Buchung(
                        paid,
                        row["number"],
                        text,
                        True,
                        net + vat,
                        net,
                        rate,
                        vat,
                        line,
                        issued,
                        name,
                    )
                )
        credits = gutschriften.listing(
            conn, column="paid_date", start=date(jahr, 1, 1), end=date(jahr, 12, 31)
        )
    finally:
        conn.close()
    for c in credits:
        rows.append(
            Buchung(
                c.paid_date,
                f"GS {c.their_number}",
                c.label,
                True,
                c.gross,
                c.net,
                c.vat_rate,
                c.vat,
                _income_line(regel, c.kind, c.net, c.vat),
                c.issue_date,
                "fehlt",  # FRITS keeps the amounts of a customer's Gutschrift, not the paper
            )
        )
    for e in expenses_of_year(ws.db, jahr):
        cat = CATEGORIES[e.category]
        rows.append(
            Buchung(
                e.date,
                f"A-{e.id}",
                f"{e.vendor}: {e.description}",
                False,
                e.gross,
                e.net if regel else e.gross,
                None if not regel or e.category == VAT_PAYMENT else (e.vat_rate or 0) * 100,
                e.vat if regel else Decimal(0),
                cat.euer_line,
                e.date,
                Path(e.source_file).name if e.source_file else "fehlt",
            )
        )
    rows.sort(key=lambda b: (b.day, not b.income, b.beleg))
    return rows


def buchungsliste(ws: Workspace, jahr: int, ordner: Path) -> Path:
    """Write ``Buchungsliste_<jahr>.csv`` into ``ordner`` and return its path."""
    ordner.mkdir(parents=True, exist_ok=True)
    path = ordner / BUCHUNGSLISTE.format(jahr=jahr)
    # utf-8-sig and ";" are what German Excel expects, so umlauts and columns come out right.
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(COLUMNS)
        for b in buchungen(ws, jahr):
            writer.writerow(
                [
                    b.day.strftime("%d.%m.%Y"),
                    b.beleg,
                    b.text,
                    "Einnahme" if b.income else "Ausgabe",
                    _num(b.gross),
                    _num(b.net),
                    _rate(b.rate),
                    _num(b.vat),
                    b.euer_line,
                    b.beleg_day.strftime("%d.%m.%Y"),
                    b.file,
                ]
            )
    return path


def _doku_text(settings: Settings, jahr: int, heute: date) -> list[tuple[str, str]]:
    klein = settings.tax_mode != "regelbesteuerung"
    steuer = (
        "Kleinunternehmer nach § 19 UStG: Rechnungen ohne Umsatzsteuer."
        if klein
        else "Regelbesteuerung: Umsatzsteuer-Voranmeldung "
        + ("monatlich" if settings.vat_period == "monat" else "vierteljährlich")
        + (
            ", Ist-Versteuerung nach § 20 UStG (laut Einstellung; setzt die Genehmigung des "
            "Finanzamts voraus)."
            if settings.vat_basis == "ist"
            else ", Soll-Versteuerung (nach Leistung, der gesetzliche Normalfall)."
        )
    )
    sicherung = (
        "Die Datensicherung von FRITS packt den ganzen Ordner verschlüsselt (AES) in eine "
        f"Datei auf einem zweiten Datenträger ({settings.backup_dir}) und erinnert, wenn die "
        "letzte Sicherung älter als eine Woche ist. Alte Sicherungen werden nicht gelöscht."
        if settings.backup_dir
        else "Der Ordner FRITS wird regelmäßig auf einen zweiten Datenträger gesichert "
        "(Datensicherung in FRITS oder Time Machine)."
    )
    return [
        (
            "1. Allgemeines",
            (
                f"Unternehmen: {settings.name}, {settings.address}. Gewinnermittlung durch "
                f"Einnahmen-Überschuss-Rechnung (§ 4 Abs. 3 EStG). {steuer} Diese Beschreibung "
                f"gilt für das Jahr {jahr} und wurde am {heute:%d.%m.%Y} mit FRITS {__version__} "
                "erstellt. FRITS ist ein Programm, das auf dem eigenen Computer läuft. Alle Daten "
                "liegen im Ordner FRITS (in der Regel Dokumente/FRITS)."
            ),
        ),
        (
            "2. Wie Belege hereinkommen",
            (
                "Notizen, E-Mails, PDFs und Fotos von Belegen werden in FRITS hineingezogen oder "
                "eingefügt. Das Original wird unverändert im Ordner done abgelegt und bleibt dort. "
                "Ausgaben werden mit Datum, Empfänger, Betrag, Steuersatz und Kategorie erfasst "
                "und verweisen auf ihre Belegdatei, wenn eine hereingegeben wurde. In der "
                "Buchungsliste steht sonst „fehlt“. Eine KI kann beim Lesen helfen. Sie sieht "
                "nur geschwärzten Text (Namen, Adressen, Bankdaten werden vorher auf dem Computer "
                "ersetzt), schlägt nur vor und bucht nie selbst. Jede Rechnung und jede Ausgabe "
                "wird vom Unternehmer geprüft und bestätigt."
            ),
        ),
        (
            "3. Ausgangsrechnungen",
            (
                "Rechnungen entstehen aus einem Entwurf. Erst beim Bestätigen bekommen sie eine "
                "fortlaufende, lückenlose Nummer. Die Rechnung wird als PDF/A-3 mit eingebetteter "
                "E-Rechnung (ZUGFeRD/Factur-X, EN 16931) im Ordner invoices gespeichert und nie "
                "überschrieben. Die Pflichtangaben nach § 14 Abs. 4 UStG prüft FRITS vor der "
                "Nummernvergabe. Korrekturen laufen nur über eine Stornorechnung mit eigener "
                "Nummer, die auf das Original verweist. Gutschriften von Kunden werden als "
                "eigene Belege mit der Nummer des Kunden erfasst."
            ),
        ),
        (
            "4. Unveränderbarkeit (§ 146 Abs. 4 AO)",
            (
                "Die Daten liegen in einer SQLite-Datenbank (frits.db). Die Datenbank selbst "
                "verbietet, festgeschriebene Rechnungen und Ausgaben zu löschen oder ihren Inhalt "
                "zu ändern (Trigger). Eine gelöschte Ausgabe wird nur als gestrichen markiert und "
                "bleibt mit Zeitpunkt im Protokoll (Tabelle expense_log) erhalten. Sie kann "
                "zurückgeholt werden. Zahlungseingänge werden mit Datum vermerkt."
            ),
        ),
        (
            "5. Zeitgerechte Erfassung",
            (
                "FRITS zeigt offene Belege und Entwürfe auf der Startseite und erinnert daran, "
                "Belege zeitnah zu erfassen (Ziel: innerhalb von 10 Tagen)."
            ),
        ),
        (
            "6. Auswertungen",
            (
                "Aus denselben Daten entstehen die EÜR nach den Zeilen der Anlage EÜR, die "
                "Kennzahlen der Umsatzsteuer-Voranmeldung und die Buchungsliste. Die Buchungsliste "
                "enthält jede Zahlung, die für die EÜR des Jahres zählt, und ergibt dieselben "
                "Summen."
            ),
        ),
        (
            "7. Aufbewahrung und Datensicherung",
            (
                "Rechnungen und Buchungsbelege werden 8 Jahre aufbewahrt, Bücher und "
                "Jahresabschlüsse (hier die EÜR) 10 Jahre, sonstige Geschäftsbriefe 6 Jahre "
                "(§ 147 Abs. 3 AO, § 14b UStG). Die Frist beginnt mit Ende des Kalenderjahres. "
                f"FRITS löscht nichts von selbst. {sicherung} Für eine Außenprüfung können "
                "die Daten als CSV und PDF ausgegeben werden (Datenzugriff nach § 147 Abs. 6 AO)."
            ),
        ),
        (
            "Hinweis",
            (
                "Erzeugt von FRITS aus den Einstellungen des Unternehmens. Diese Beschreibung "
                "ist keine Steuerberatung."
            ),
        ),
    ]


def verfahrensdoku(ws: Workspace, jahr: int, ordner: Path, heute: date | None = None) -> Path:
    """Write ``Verfahrensdokumentation_<jahr>.pdf`` into ``ordner`` and return its path."""
    from fpdf import FPDF

    settings = Settings.load(ws.settings_file)
    ordner.mkdir(parents=True, exist_ok=True)
    path = ordner / VERFAHRENSDOKU.format(jahr=jahr)
    pdf = FPDF(format="A4")
    pdf.add_font("DejaVu", fname=str(FONTS / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", style="B", fname=str(FONTS / "DejaVuSans-Bold.ttf"))
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("DejaVu", "B", 15)
    pdf.multi_cell(0, 8, f"Verfahrensdokumentation {jahr}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", size=9)
    pdf.multi_cell(
        0, 5, "nach den GoBD (BMF-Schreiben vom 28.11.2019)", new_x="LMARGIN", new_y="NEXT"
    )
    pdf.ln(3)
    for title, body in _doku_text(settings, jahr, heute or date.today()):
        pdf.set_font("DejaVu", "B", 11)
        pdf.multi_cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", size=10)
        pdf.multi_cell(0, 5.5, body, align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
    pdf.output(str(path))
    return path

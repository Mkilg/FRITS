"""Umsatzsteuer-Voranmeldung (UStVA): the numbers Fritz types into ELSTER each month or quarter.

Only for Regelbesteuerung. A Kleinunternehmer (§ 19 UStG) files none.

What goes where (form "Umsatzsteuer-Voranmeldung", Kennzahlen as ELSTER shows them):

* Kz 81: net amounts taxed at 19 %, in whole euros (cents are dropped, as the form asks)
* Kz 86: net amounts taxed at 7 %, whole euros
* Kz 21: services to businesses in other EU countries (Reverse Charge, § 3a Abs. 2 UStG).
  These also belong in the Zusammenfassende Meldung to the BZSt.
* Kz 45: other turnover not taxable in Germany, e.g. customers outside the EU
* Kz 66: Vorsteuer from receipts, with cents
* Kz 83: what is left to pay (or, if negative, what comes back)

When an invoice counts:

* ``ist`` (Istversteuerung, § 20 UStG, usual for freelancers and small firms on request):
  in the period the customer paid. A Storno counts when the money went back.
* ``soll`` (Sollversteuerung, the legal default): in the period the service was done
  (§ 13 Abs. 1 Nr. 1a UStG), read from the line dates; without dates the invoice date.
  A Storno counts on its own date (§ 17 UStG).

Vorsteuer counts on the day of the receipt, as entered in Ausgaben.

This is a helper, not tax advice: Fritz checks the numbers before he sends them.
"""

from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path

from frits import gutschriften
from frits.billing import Draft, _tax_from_invoice, service_dates, totals
from frits.expenses import CENT, list_expenses
from frits.settings import Settings

PERIODS = {"monat": "monatlich", "quartal": "vierteljährlich"}
BASES = {"ist": "nach bezahlten Rechnungen (Ist)", "soll": "nach Leistungsdatum (Soll)"}
QUARTER_CODES = {1: 41, 2: 42, 3: 43, 4: 44}  # how ELSTER numbers the quarters


@dataclass(frozen=True)
class Feld:
    kz: str
    label: str
    amount: Decimal
    tax: Decimal | None = None
    """For Kz 81/86: the tax ELSTER works out from the base (shown, not typed)."""
    whole_euros: bool = True


@dataclass
class Voranmeldung:
    year: int
    period: str  # "monat" or "quartal"
    number: int  # month 1-12 or quarter 1-4
    basis: str
    start: date
    end: date
    felder: list[Feld] = field(default_factory=list)
    invoices: int = 0
    receipts: int = 0
    eu_customers: bool = False

    @property
    def title(self) -> str:
        if self.period == "quartal":
            return f"{self.number}. Quartal {self.year}"
        return f"{_MONTHS[self.number - 1]} {self.year}"

    @property
    def elster_period(self) -> str:
        """The Voranmeldungszeitraum as ELSTER's drop-down shows it."""
        if self.period == "quartal":
            return f"{QUARTER_CODES[self.number]} – {self.number}. Kalendervierteljahr"
        return f"{self.number:02d} – {_MONTHS[self.number - 1]}"

    @property
    def due(self) -> date:
        """10th of the following month (§ 18 Abs. 1 UStG), moved past a weekend.

        Holidays move it too; ELSTER knows those, FRITS doesn't."""
        return due_date(self.year, self.period, self.number)

    def feld(self, kz: str) -> Feld | None:
        return next((f for f in self.felder if f.kz == kz), None)

    @property
    def zahllast(self) -> Decimal:
        """Kz 83: tax on 81 and 86 minus Vorsteuer. Negative means money back."""
        tax = sum((f.tax for f in self.felder if f.tax is not None), Decimal(0))
        vorsteuer = self.feld("66")
        return tax - (vorsteuer.amount if vorsteuer else Decimal(0))


_MONTHS = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)


def bounds(year: int, period: str, number: int) -> tuple[date, date]:
    if period == "quartal":
        if not 1 <= number <= 4:
            raise ValueError("Quartal 1 bis 4")
        first = 3 * number - 2
        return date(year, first, 1), date(year, first + 2, calendar.monthrange(year, first + 2)[1])
    if not 1 <= number <= 12:
        raise ValueError("Monat 1 bis 12")
    return date(year, number, 1), date(year, number, calendar.monthrange(year, number)[1])


def current(today: date, period: str) -> tuple[int, int]:
    """The period Fritz most likely files now: the one that ended last."""
    if period == "quartal":
        quarter = (today.month - 1) // 3  # the one before the running quarter
        return (today.year, quarter) if quarter else (today.year - 1, 4)
    return (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)


def due_date(year: int, period: str, number: int) -> date:
    """10th of the month after the period (§ 18 Abs. 1 UStG), moved past a weekend."""
    day = bounds(year, period, number)[1] + timedelta(days=10)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def next_due(today: date, period: str) -> tuple[int, int]:
    """The period whose deadline comes next: the one that just ended, unless its deadline
    has passed already; then the running one."""
    year, number = current(today, period)
    if due_date(year, period, number) >= today:
        return year, number
    if period == "quartal":
        return today.year, (today.month - 1) // 3 + 1
    return today.year, today.month


def invoice_groups(conn: sqlite3.Connection, row, settings: Settings):
    """(kind, rate, net, vat) for one issued invoice, from the snapshot frozen at approval."""
    data = None
    if row["draft_id"] is not None:
        found = conn.execute("SELECT data FROM drafts WHERE id = ?", (row["draft_id"],)).fetchone()
        data = found["data"] if found else None
    if data is None:  # very old or imported: only the stored totals are known
        tax = _tax_from_invoice(row)
        rate = Decimal(tax["rate"])
        yield "inland", rate, Decimal(row["net"]), Decimal(row["vat"])
        return
    draft = Draft.from_json(data)
    if row["cancels"]:
        for line in draft.lines:
            line.quantity = -line.quantity
    tax = draft.tax or _tax_from_invoice(row)
    sums = totals(draft.lines, settings, tax)
    if tax.get("mode") != "regelbesteuerung":
        return  # written as Kleinunternehmer: no VAT, nothing to declare
    kind = {"eu": "eu", "drittland": "drittland"}.get(tax.get("customer", ""), "inland")
    for rate, net, vat in sums.groups:
        yield kind, rate, net, vat


def _service_day(conn: sqlite3.Connection, row) -> date:
    """When the VAT arises with Sollversteuerung (§ 13 Abs. 1 Nr. 1a UStG): the end of the
    service, from the line dates; without dates the invoice date, which then is the
    Leistungsdatum printed on the invoice."""
    issued = date.fromisoformat(row["issue_date"])
    if row["cancels"] or row["draft_id"] is None:
        return issued
    found = conn.execute("SELECT data FROM drafts WHERE id = ?", (row["draft_id"],)).fetchone()
    days = service_dates(Draft.from_json(found["data"]).lines) if found else []
    return days[-1] if days else issued


def _euros(amount: Decimal) -> Decimal:
    """Whole euros towards zero, as the form wants for the bases."""
    return amount.quantize(Decimal(1), rounding=ROUND_DOWN)


def voranmeldung(
    db_path: str | Path,
    settings: Settings,
    year: int,
    number: int,
    period: str = "quartal",
    basis: str = "ist",
) -> Voranmeldung:
    start, end = bounds(year, period, number)
    result = Voranmeldung(year, period, number, basis, start, end)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # By the day the service was done. That is the rule for Soll (§ 13 Abs. 1 Nr. 1a
        # UStG) and, even with Ist, for turnover not taxable in Germany: § 18b Satz 2 UStG
        # for EU customers (Kz 21, also the Zusammenfassende Meldung); for customers outside
        # the EU (Kz 45) no tax arises that could wait for the payment, so the same day is
        # used. A Storno corrects in its own period (§ 17 UStG).
        near = conn.execute(
            "SELECT * FROM invoices WHERE issue_date >= ? AND issue_date <= ?",
            ((start - timedelta(days=400)).isoformat(), (end + timedelta(days=400)).isoformat()),
        ).fetchall()
        done_here = [row for row in near if start <= _service_day(conn, row) <= end]
        paid_here = conn.execute(
            "SELECT * FROM invoices WHERE paid_date >= ? AND paid_date <= ?",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        bases: dict[tuple[str, Decimal], Decimal] = {}
        counted: set[str] = set()

        def add(kind: str, rate: Decimal, net: Decimal, key: str) -> None:
            slot = (kind, rate if kind == "inland" else Decimal(0))
            bases[slot] = bases.get(slot, Decimal(0)) + net
            counted.add(key)

        for row in done_here:
            for kind, rate, net, _vat in invoice_groups(conn, row, settings):
                if basis == "soll" or kind != "inland":
                    add(kind, rate, net, row["number"])
        if basis == "ist":  # domestic VAT is due when the money came (§ 20 UStG)
            for row in paid_here:
                for kind, rate, net, _vat in invoice_groups(conn, row, settings):
                    if kind == "inland":
                        add(kind, rate, net, row["number"])
        result.invoices = len(counted)
        for credit in gutschriften.listing(conn, start=start - timedelta(days=400)):
            if credit.vat_rate is None:
                continue  # received while Kleinunternehmer
            day = credit.paid_date if basis == "ist" and credit.kind == "inland" else None
            day = day or (credit.issue_date if basis == "soll" or credit.kind != "inland" else None)
            if day and start <= day <= end:
                add(credit.kind, credit.vat_rate, credit.net, f"gs-{credit.id}")
        result.invoices = len(counted)
    finally:
        conn.close()

    for (kind, rate), net in sorted(bases.items(), key=lambda kv: (kv[0][0], -kv[0][1])):
        base = _euros(net)
        if kind == "inland" and rate == 19:
            tax = (base * Decimal("0.19")).quantize(CENT, ROUND_HALF_UP)
            result.felder.append(Feld("81", "Umsätze zu 19 % (netto)", base, tax))
        elif kind == "inland" and rate == 7:
            tax = (base * Decimal("0.07")).quantize(CENT, ROUND_HALF_UP)
            result.felder.append(Feld("86", "Umsätze zu 7 % (netto)", base, tax))
        elif kind == "eu":
            result.eu_customers = True
            result.felder.append(
                Feld("21", "Leistungen an Firmen in anderen EU-Ländern (Reverse Charge)", base)
            )
        elif kind == "drittland":
            result.felder.append(
                Feld("45", "Leistungen an Kunden außerhalb der EU (nicht steuerbar)", base)
            )
        else:  # another rate, e.g. 16 % from 2020: ELSTER has its own line for that
            tax = (base * rate / 100).quantize(CENT, ROUND_HALF_UP)
            result.felder.append(Feld("35", f"Umsätze zu anderen Sätzen ({rate} %)", base, tax))

    receipts = [e for e in list_expenses(db_path, year=year) if start <= e.date <= end]
    result.receipts = len(receipts)
    vorsteuer = sum((e.vat for e in receipts), Decimal(0))
    if vorsteuer:
        result.felder.append(
            Feld("66", "Vorsteuer aus deinen Belegen", vorsteuer, whole_euros=False)
        )
    order = ["81", "86", "35", "21", "45", "66"]
    result.felder.sort(key=lambda f: order.index(f.kz))
    return result


def period_of(day: date, period: str) -> tuple[int, int]:
    return (day.year, (day.month - 1) // 3 + 1) if period == "quartal" else (day.year, day.month)


def late_notice(db_path: str | Path, settings: Settings, number: str, today: date) -> str | None:
    """A sentence for Fritz when a new invoice belongs to a Voranmeldung whose deadline has
    passed (he may have to send a corrected one), else None."""
    if settings.tax_mode != "regelbesteuerung":
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM invoices WHERE number = ?", (number,)).fetchone()
        if row is None:
            return None
        kinds = {kind for kind, *_ in invoice_groups(conn, row, settings)}
        if settings.vat_basis == "ist" and kinds <= {"inland"}:
            return None  # counts when paid, which is still to come
        day = _service_day(conn, row)
    finally:
        conn.close()
    period = settings.vat_period if settings.vat_period in PERIODS else "quartal"
    year, nr = period_of(day, period)
    if due_date(year, period, nr) >= today:
        return None
    title = Voranmeldung(year, period, nr, settings.vat_basis, *bounds(year, period, nr)).title
    return (
        f"Rechnung {number} gehört in die Umsatzsteuer-Voranmeldung {title}, deren Frist "
        "schon vorbei ist. Hast du die schon abgegeben, gib in ELSTER eine berichtigte "
        "Voranmeldung für diesen Zeitraum ab."
    )

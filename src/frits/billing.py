"""Prices, tax, totals and invoice numbers. All money is Decimal and computed here, never by the AI."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from frits import gutschriften, store
from frits.customers import Customer
from frits.extract import CheckedExtraction
from frits.redact import Mapping
from frits.settings import Settings

CENT = Decimal("0.01")
KLEINUNTERNEHMER_NOTE = "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet."
REVERSE_CHARGE_NOTE = (
    "Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge): Die Umsatzsteuer "
    "schuldet der Leistungsempfänger."
)
DRITTLAND_NOTE = (
    "Nicht im Inland steuerbare Leistung: Der Leistungsort liegt beim Leistungsempfänger "
    "im Ausland (§ 3a Abs. 2 UStG)."
)
VAT_RATES = (Decimal(19), Decimal(7), Decimal(0))
DOMESTIC_RATES = (Decimal(19), Decimal(7))  # what the review page offers
# Where a customer sits, for VAT. "" is Germany.
CUSTOMER_TAX = {
    "": "In Deutschland",
    "eu": "Firma in einem anderen EU-Land (Reverse Charge)",
    "drittland": "Firma außerhalb der EU, z. B. in der Schweiz",
}
# § 19 UStG since 2025: last year's turnover at most 25,000 €, this year's at most 100,000 €.
LIMIT_LAST_YEAR = Decimal(25_000)
LIMIT_THIS_YEAR = Decimal(100_000)


class BillingError(Exception):
    """A draft can't be finalized yet. The message tells Fritz what's missing, and `feld`
    names the form field it is about, if any, so the page can point at it."""

    feld: str | None = None

    def __init__(self, *args, feld: str | None = None):
        super().__init__(*args)
        if feld:
            self.feld = feld


class EarlierNumberError(BillingError):
    """The number of an earlier invoice doesn't fit: the page points at the number field."""

    feld = "number"


class NumberGap(EarlierNumberError):
    """An earlier invoice would leave numbers out. Normal while entering old invoices one by
    one, so Fritz may say "trotzdem nachtragen"; it also catches a typo."""


@dataclass
class Line:
    description: str
    date: str | None
    quantity: Decimal
    unit: str
    unit_price: Decimal | None
    stated_price: Decimal | None = None
    flags: list[str] = field(default_factory=list)
    vat_rate: Decimal | None = None  # None: the normal rate from the settings

    @property
    def net(self) -> Decimal | None:
        if self.unit_price is None:
            return None
        return money(self.quantity * self.unit_price)


@dataclass
class Draft:
    customer_id: str | None
    customer_name: str | None
    customer_address: str | None
    lines: list[Line]
    questions: list[str]
    warnings: list[str]
    source_file: str
    kind: str = "invoice"  # invoice | quote: what the review screen's main button makes
    # How tax was worked out when this became an invoice, so a later Storno mirrors it exactly
    # even if Fritz changes his tax settings in between: {"mode": ..., "rate": "19"}.
    tax: dict | None = None
    customer_tax: str = ""  # "", "eu" or "drittland", copied from the customer note
    customer_ust_id: str | None = None
    customer_country: str = "DE"  # ISO 3166 code, for the e-invoice
    customer_reference: str | None = None  # Leitweg-ID or order number the customer asked for
    earlier: bool = False  # written before FRITS and only entered here ("nachgetragen")
    customer_contact: str | None = None  # the person at a company: "z. Hd." on the paper

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> Draft:
        data = json.loads(raw)
        lines = []
        for line in data.pop("lines"):
            for key in ("quantity", "unit_price", "stated_price", "vat_rate"):
                if line.get(key) is not None:
                    line[key] = Decimal(line[key])
            lines.append(Line(**line))
        return cls(lines=lines, **data)


@dataclass(frozen=True)
class Totals:
    net: Decimal
    vat: Decimal
    gross: Decimal
    vat_rate: Decimal | None  # None: Kleinunternehmer, no VAT at all
    groups: tuple[tuple[Decimal, Decimal, Decimal], ...] = ()  # (rate, net, vat) per rate
    note: str | None = None  # the legal sentence the invoice must carry, if any


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def build_draft(
    checked: CheckedExtraction,
    mapping: Mapping,
    customers: list[Customer],
    settings: Settings,
    source_file: str,
) -> Draft:
    customer = None
    customer_name = None
    questions = list(checked.questions)
    if checked.customer_ref:
        customer_name = mapping.entries[checked.customer_ref]["value"]
        own = {n.casefold() for n in (settings.name, settings.company) if n}
        if customer_name.casefold() in own:
            # The AI took Fritz himself for the customer (his name stands under the note).
            customer_name = None
            questions.append("Für wen ist das? Bitte oben den Kunden wählen.")
    if customer_name:
        customer = next((c for c in customers if customer_name in (c.name, c.company)), None)
        if customer is None:
            questions.append(
                f"{customer_name} ist noch nicht in deiner Kundenliste. Bitte Adresse ergänzen."
            )

    lines = []
    for checked_item in checked.items:
        item = checked_item.item
        stated = Decimal(str(item.stated_price)) if item.stated_price is not None else None
        line = Line(
            description=mapping.reidentify(item.description),
            date=item.date,
            quantity=Decimal(str(item.quantity)),
            unit=item.unit,
            unit_price=unit_price(
                item.unit, customer, settings, mapping.reidentify(item.description)
            ),
            stated_price=stated,
            flags=list(checked_item.flags),
        )
        if line.unit_price is None:
            line.flags.append("needs_price")
        if stated is not None:
            line.flags.append("stated_price_needs_ok")
        lines.append(line)

    warnings = []
    if checked.injection_suspected:
        warnings.append(
            "Dieses Dokument enthält Sätze, die der KI Anweisungen geben wollen. FRITS hat sie "
            "ignoriert: Preise und Summen rechnet immer der Code, nie die KI. Bitte prüfe die "
            "Positionen trotzdem genau."
        )
    return Draft(
        customer_id=customer.id if customer else None,
        customer_name=customer.display_name if customer else customer_name,
        customer_address=customer.address if customer else None,
        customer_tax=customer.umsatzsteuer if customer else "",
        customer_ust_id=customer.ust_id if customer else None,
        customer_country=customer.land if customer else "DE",
        customer_reference=customer.referenz if customer else None,
        customer_contact=contact_of(customer),
        lines=lines,
        questions=questions,
        warnings=warnings,
        source_file=source_file,
    )


def unit_price(
    unit: str, customer: Customer | None, settings: Settings, description: str = ""
) -> Decimal | None:
    """Prices come from the price list, the customer note, then settings.

    Never from the document or the AI.
    """
    if description:
        for item in settings.price_list:
            if item.matches(description, unit):
                return item.price
    if unit == "Std":
        rate = customer.hourly_rate if customer and customer.hourly_rate else settings.hourly_rate
        return rate if rate and rate > 0 else None
    if unit == "km":
        return settings.km_rate if settings.km_rate > 0 else None
    return None


def tax_snapshot(settings: Settings, customer_tax: str = "") -> dict:
    return {"mode": settings.tax_mode, "rate": str(settings.vat_rate), "customer": customer_tax}


def totals(lines: list[Line], settings: Settings, tax: dict | None = None) -> Totals:
    """Net, VAT and gross. tax is the snapshot stored on an issued invoice; it wins over settings.

    VAT is worked out once per rate (§ 14 Abs. 4 Nr. 7 und 8 UStG), not per line.
    """
    tax = tax or tax_snapshot(settings)
    net = money(sum((line.net for line in lines if line.net is not None), Decimal(0)))
    zero = Decimal("0.00")
    if tax["mode"] != "regelbesteuerung":
        return Totals(net, zero, net, None, note=KLEINUNTERNEHMER_NOTE)
    standard = Decimal(tax["rate"])
    abroad = tax.get("customer", "")
    if abroad in ("eu", "drittland"):
        note = REVERSE_CHARGE_NOTE if abroad == "eu" else DRITTLAND_NOTE
        return Totals(net, zero, net, standard, ((Decimal(0), net, zero),), note)
    nets: dict[Decimal, Decimal] = {}
    for line in lines:
        if line.net is not None:
            rate = standard if line.vat_rate is None else line.vat_rate
            nets[rate] = nets.get(rate, Decimal(0)) + line.net
    groups = tuple(
        (rate, money(part), money(part * rate / 100))
        for rate, part in sorted(nets.items(), reverse=True)
    ) or ((standard, zero, zero),)
    vat = money(sum((g[2] for g in groups), Decimal(0)))
    return Totals(net, vat, net + vat, standard, groups)


def draft_totals(draft: Draft, settings: Settings) -> Totals:
    """Totals as the invoice shows them: frozen once issued, else from settings and customer."""
    return totals(draft.lines, settings, draft.tax or tax_snapshot(settings, draft.customer_tax))


def turnover(conn, year: int) -> Decimal:
    """Net turnover invoiced in a year, Stornos and received Gutschriften included.
    Close enough for the § 19 limits."""
    rows = conn.execute(
        "SELECT net FROM invoices WHERE substr(issue_date, 1, 4) = ?", (str(year),)
    ).fetchall()
    credits = gutschriften.listing(conn, start=date(year, 1, 1), end=date(year, 12, 31))
    return sum((Decimal(row[0]) for row in rows), Decimal(0)) + sum(
        (c.net for c in credits), Decimal(0)
    )


def limit_check(conn, settings: Settings, year: int, adding: Decimal = Decimal(0)):
    """§ 19 UStG limits for Kleinunternehmer. Returns (blocking problems, gentle warnings)."""
    if settings.tax_mode != "kleinunternehmer":
        return [], []
    last, this = turnover(conn, year - 1), turnover(conn, year)
    problems, warnings = [], []
    if this + adding > LIMIT_THIS_YEAR:
        problems.append(
            f"Mit dieser Rechnung kommst du {year} über 100.000 € Umsatz. Ab dieser Rechnung "
            "giltst du nicht mehr als Kleinunternehmer (§ 19 UStG) und musst Umsatzsteuer "
            "ausweisen. Stell in den Einstellungen auf Regelbesteuerung um und sprich am besten "
            "kurz mit deiner Steuerberatung."
        )
    if last > LIMIT_LAST_YEAR:
        warnings.append(
            f"{year - 1} hast du laut FRITS mehr als 25.000 € umgesetzt. Dann bist du {year} kein "
            "Kleinunternehmer mehr. Bitte prüfe deine Einstellungen mit deiner Steuerberatung."
        )
    elif this + adding > LIMIT_LAST_YEAR:
        warnings.append(
            f"Dein Umsatz {year} liegt jetzt über 25.000 €. Dieses Jahr bleibst du Kleinunternehmer, "
            f"aber ab {year + 1} wohl nicht mehr. FRITS erinnert dich im Januar."
        )
    return problems, warnings


_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_DE_DAY = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})")


def _day(text: str) -> date | None:
    """Read "2026-10-03", "3.10.2026" or "3.10.26". Anything else stays as Fritz wrote it."""
    text = text.strip()
    try:
        if _ISO_DAY.fullmatch(text):
            return date.fromisoformat(text)
        if m := _DE_DAY.fullmatch(text):
            year = int(m.group(3))
            return date(year + 2000 if year < 100 else year, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    return None


def service_dates(lines: list[Line]) -> list[date]:
    """The line dates, sorted, when every dated line has a readable date. Else empty."""
    raw = [line.date for line in lines if line.date and line.date.strip()]
    days = [_day(text) for text in raw]
    return sorted(days) if days and all(days) else []


def service_period(lines: list[Line]) -> str | None:
    """The Leistungsdatum or -zeitraum for § 14 Abs. 4 Nr. 6 UStG, from the line dates."""
    raw = [line.date.strip() for line in lines if line.date and line.date.strip()]
    if not raw:
        return None
    days = [_day(text) for text in raw]
    if all(days):
        first, last = min(days), max(days)
        if first == last:
            return first.strftime("%d.%m.%Y")
        return f"{first.strftime('%d.%m.%Y')} bis {last.strftime('%d.%m.%Y')}"
    unique = list(dict.fromkeys(raw))
    return unique[0] if len(unique) == 1 else f"{unique[0]} bis {unique[-1]}"


SERVICE_DATE_FALLBACK = "entspricht dem Rechnungsdatum"


def blockers(draft: Draft, settings: Settings) -> list[str]:
    """What must be fixed before this draft may become a numbered invoice."""
    problems = []
    if not settings.is_complete:
        problems.append("Deine eigenen Angaben (Name, Adresse, IBAN, Stundensatz) fehlen noch.")
    abroad = settings.tax_mode == "regelbesteuerung" and draft.customer_tax == "eu"
    if abroad and not settings.ust_id.strip():
        problems.append(
            "Für Reverse Charge in der EU brauchst du eine eigene USt-IdNr (beim Bundeszentralamt "
            "für Steuern beantragen). Trag sie in den Einstellungen ein."
        )
    if abroad and not (draft.customer_ust_id or "").strip():
        problems.append(
            "Für Reverse Charge fehlt die USt-IdNr des Kunden. Trag sie beim Kunden ein."
        )
    domestic = settings.tax_mode == "regelbesteuerung" and draft.customer_tax == ""
    for line in draft.lines:
        if domestic and line.vat_rate == 0:
            problems.append(
                f"0 % bei „{line.description}“ geht im Inland nur mit einer Steuerbefreiung, und "
                "die braucht einen Hinweis auf der Rechnung. Das kann FRITS noch nicht: bitte "
                "19 % oder 7 % wählen oder deine Steuerberatung fragen."
            )
        if line.vat_rate is not None and line.vat_rate not in VAT_RATES:
            problems.append(f"Steuersatz {line.vat_rate} % bei „{line.description}“ gibt es nicht.")
    if draft.kind != "quote" and not (settings.steuernummer.strip() or settings.ust_id.strip()):
        problems.append(
            "Deine Steuernummer oder USt-IdNr fehlt. Eine davon muss auf jeder Rechnung stehen "
            "(§ 14 UStG). Du trägst sie in den Einstellungen ein."
        )
    if not draft.customer_id or not draft.customer_address:
        problems.append("Kunde mit Adresse fehlt.")
    if not draft.lines:
        problems.append("Die Rechnung hat keine Positionen.")
    for line in draft.lines:
        if line.unit_price is None:
            problems.append(f"Preis fehlt für „{line.description}“.")
    priced = [line.net for line in draft.lines if line.net is not None]
    if draft.lines and len(priced) == len(draft.lines) and not any(priced):
        problems.append("Die Rechnung ergibt 0,00 €. Bitte Menge und Preis eintragen.")
    return problems


DEFAULT_PATTERN = "JJJJ-NNNN"
_PATTERN_OK = re.compile(r"[A-Za-z0-9._-]*N+[A-Za-z0-9._-]*")


def valid_pattern(pattern: str) -> bool:
    """A number pattern needs a counter (N...) and only characters that are safe in file names."""
    return bool(_PATTERN_OK.fullmatch(pattern)) and len(re.findall(r"N+", pattern)) == 1


def _pattern_parts(pattern: str, year: int) -> tuple[re.Pattern[str], str]:
    """A regex that reads the counter out of a number, and a template that writes one."""
    pattern = pattern if valid_pattern(pattern) else DEFAULT_PATTERN
    regex, template = "", ""
    for token in re.findall(r"JJJJ|JJ|N+|.", pattern):
        if token == "JJJJ":
            regex, template = regex + str(year), template + str(year)
        elif token == "JJ":
            regex, template = regex + f"{year % 100:02d}", template + f"{year % 100:02d}"
        elif token.startswith("N"):
            regex, template = regex + r"(\d+)", template + "{counter:0" + str(len(token)) + "d}"
        else:
            regex += re.escape(token)
            template += token.replace("{", "{{").replace("}", "}}")
    return re.compile(regex), template


def next_number(conn, year: int, pattern: str = DEFAULT_PATTERN, last_before: str = "") -> str:
    """The next invoice number: gapless, in Fritz's own pattern.

    "JJJJ" is the year, "JJ" its last two digits, "NNNN" the counter with leading zeros.
    A pattern with a year starts again at 1 each year; one without counts on forever.
    last_before is the last number Fritz wrote before FRITS, so FRITS carries on from there.
    """
    regex, template = _pattern_parts(pattern, year)
    numbers = [row["number"] for row in conn.execute("SELECT number FROM invoices")]
    counters = [
        int(m.group(1)) for n in numbers + [last_before.strip()] if (m := regex.fullmatch(n))
    ]
    return template.format(counter=max(counters, default=0) + 1)


def approve(conn, draft_id: int, settings: Settings, invoices_dir: Path, today: date | None = None):
    """Give a draft its number and PDF. Numbers are gapless: assigned only here, in one transaction."""
    from frits.render import render_invoice

    today = today or date.today()
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise BillingError(f"Entwurf {draft_id} gibt es nicht.")
    if row["status"] != "draft":
        raise BillingError(f"Entwurf {draft_id} ist schon erledigt ({row['status']}).")
    draft = Draft.from_json(row["data"])
    problems = blockers(draft, settings)
    if problems:
        raise BillingError(" ".join(problems))

    draft.tax = tax_snapshot(settings, draft.customer_tax)
    sums = totals(draft.lines, settings, draft.tax)
    limit_problems, _ = limit_check(conn, settings, today.year, sums.net)
    if limit_problems:
        raise BillingError(" ".join(limit_problems))
    due = today + timedelta(days=settings.payment_days)
    with store.transaction(conn):
        number = next_number(conn, today.year, settings.number_pattern, settings.last_number_before)
        pdf_path = Path(invoices_dir) / f"{number}_{_slug(draft.customer_name)}.pdf"
        _never_overwrite(pdf_path)
        conn.execute(
            "INSERT INTO invoices (number, customer, issue_date, net, vat, gross, draft_id,"
            " due_date, pdf_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                number,
                draft.customer_name,
                today.isoformat(),
                str(sums.net),
                str(sums.vat),
                str(sums.gross),
                draft_id,
                due.isoformat(),
                str(pdf_path),
            ),
        )
        conn.execute(
            "UPDATE drafts SET status = 'approved', invoice_number = ?, data = ? WHERE id = ?",
            (number, draft.to_json(), draft_id),
        )
        # Rendering inside the transaction: if the PDF fails, the number is not used up.
        render_invoice(pdf_path, number, draft, sums, settings, today, due)
    return number, pdf_path


def cancel(conn, number: str, settings: Settings, invoices_dir: Path, today: date | None = None):
    """A wrong invoice is never deleted. It gets a cancellation invoice with its own number."""
    from frits.render import render_invoice

    today = today or date.today()
    original = conn.execute("SELECT * FROM invoices WHERE number = ?", (number,)).fetchone()
    if original is None:
        raise BillingError(f"Rechnung {number} gibt es nicht.")
    if conn.execute("SELECT 1 FROM invoices WHERE cancels = ?", (number,)).fetchone():
        raise BillingError(f"Rechnung {number} wurde schon storniert.")
    draft_row = conn.execute(
        "SELECT data FROM drafts WHERE id = ?", (original["draft_id"],)
    ).fetchone()
    if draft_row is not None:
        draft = Draft.from_json(draft_row["data"])
    else:  # imported or very old: only the stored totals are known, so cancel those
        draft = Draft(
            customer_id=None,
            customer_name=original["customer"],
            customer_address=None,
            lines=[
                Line(
                    f"Rechnung {number} vom {date.fromisoformat(original['issue_date']):%d.%m.%Y}",
                    original["issue_date"],
                    Decimal(1),
                    "pauschal",
                    Decimal(original["net"]),
                )
            ],
            questions=[],
            warnings=[],
            source_file="",
        )
    for line in draft.lines:
        line.quantity = -line.quantity
    tax = draft.tax or _tax_from_invoice(original)
    sums = totals(draft.lines, settings, tax)
    with store.transaction(conn):
        storno = next_number(conn, today.year, settings.number_pattern, settings.last_number_before)
        pdf_path = Path(invoices_dir) / f"{storno}_Storno_{number}.pdf"
        _never_overwrite(pdf_path)
        conn.execute(
            "INSERT INTO invoices (number, customer, issue_date, net, vat, gross,"
            " draft_id, pdf_path, cancels) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                storno,
                original["customer"],
                today.isoformat(),
                str(sums.net),
                str(sums.vat),
                str(sums.gross),
                original["draft_id"],
                str(pdf_path),
                number,
            ),
        )
        render_invoice(
            pdf_path,
            storno,
            draft,
            sums,
            settings,
            today,
            None,
            cancels=number,
            cancels_date=date.fromisoformat(original["issue_date"]),
            original_paid=bool(original["paid_date"]),
        )
    return storno, pdf_path


# What an invoice written before FRITS may look like, in Fritz's words.
EARLIER_TAX = {
    "19": "mit 19 % Umsatzsteuer",
    "7": "mit 7 % Umsatzsteuer",
    "klein": "ohne Umsatzsteuer (Kleinunternehmer, § 19 UStG)",
    "eu": "an eine Firma in der EU (Reverse Charge)",
    "drittland": "an eine Firma außerhalb der EU",
}
_EARLIER_NUMBER = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._/-]{0,39}")


def _earlier_fits_sequence(
    conn, number: str, issue_date: date, pattern: str, gap_ok: bool = False
) -> None:
    """A number in FRITS's own pattern must keep the series in time order.

    Fritz may have used the same pattern before FRITS. Such an invoice may be entered, but a
    lower counter cannot be newer than a higher one (§ 14 Abs. 4 Nr. 4 UStG: fortlaufend),
    and it cannot jump ahead of the next number FRITS would give.
    """
    regex, _template = _pattern_parts(pattern, issue_date.year)
    mine = regex.fullmatch(number)
    if not mine:
        return
    counter = int(mine.group(1))
    newest = 0
    made_by_frits = False  # an invoice of this series FRITS wrote itself, not one entered
    rows = conn.execute(
        "SELECT i.number, i.issue_date, json_extract(d.data, '$.earlier') AS earlier"
        " FROM invoices i LEFT JOIN drafts d ON d.id = i.draft_id"
    )
    for row in rows:
        other = regex.fullmatch(row["number"])
        if not other:
            continue
        theirs, day = int(other.group(1)), row["issue_date"]
        newest = max(newest, theirs)
        made_by_frits = made_by_frits or not row["earlier"]
        if theirs < counter and day > issue_date.isoformat():
            raise EarlierNumberError(
                f"{number} kann nicht vom {issue_date:%d.%m.%Y} sein: Die kleinere Nummer "
                f"{row['number']} ist erst vom {date.fromisoformat(day):%d.%m.%Y}. "
                "Rechnungsnummern laufen mit der Zeit mit. Prüf Nummer und Datum."
            )
        if theirs > counter and day < issue_date.isoformat():
            raise EarlierNumberError(
                f"{number} kann nicht vom {issue_date:%d.%m.%Y} sein: Die größere Nummer "
                f"{row['number']} ist schon vom {date.fromisoformat(day):%d.%m.%Y}. "
                "Rechnungsnummern laufen mit der Zeit mit. Prüf Nummer und Datum."
            )
    if newest and counter > newest + 1 and (made_by_frits or not gap_ok):
        # Named the way Fritz writes them: 2025-008, even if FRITS's pattern says NNNN.
        width = len(mine.group(1))

        def written(n: int) -> str:
            return number[: mine.start(1)] + f"{n:0{width}d}" + number[mine.end(1) :]

        missing = [written(n) for n in range(newest + 1, counter)]
        named = (
            missing[0]
            if len(missing) == 1
            else f"{missing[0]} und {missing[1]}"
            if len(missing) == 2
            else f"{missing[0]} bis {missing[-1]}"
        )
        if made_by_frits:
            # FRITS already numbers this series itself: a gap there could never be filled.
            raise EarlierNumberError(
                f"{number} würde eine Lücke in deine Nummern reißen: Vor ihr fehlen "
                f"{named}, und FRITS schreibt in dieser Reihe schon selbst Rechnungen. "
                "Prüf die Nummer."
            )
        raise NumberGap(
            f"Vor {number} fehlen in FRITS noch {named}. Die letzte ist {written(newest)}. "
            "Beim Nachtragen ist das normal, die anderen kannst du später nachtragen. "
            "Stimmt die Nummer, dann trag sie trotzdem nach."
        )


def add_earlier(
    conn,
    *,
    number: str,
    customer: str,
    issue_date: date,
    description: str,
    net: Decimal,
    tax: str,
    settings: Settings,
    service_date: date | None = None,
    paid_date: date | None = None,
    original: bytes | None = None,
    invoices_dir: Path | None = None,
    today: date | None = None,
    gap_ok: bool = False,
) -> str:
    """Enter an invoice Fritz wrote before FRITS, so EÜR, UStVA and his list are complete.

    This is not backdating: FRITS numbers and dates its own invoices only on the day they are
    made. An earlier invoice keeps the number and date printed on it, gets no new PDF (the
    original, if given, is kept next to the others) and then counts like any other invoice.
    """
    today = today or date.today()
    number = " ".join(number.split())
    customer = " ".join(customer.split())
    if not _EARLIER_NUMBER.fullmatch(number):
        raise BillingError(
            "Bitte die Rechnungsnummer so eingeben, wie sie auf der Rechnung steht "
            "(Buchstaben, Ziffern, - / . und höchstens 40 Zeichen).",
            feld="number",
        )
    if not customer:
        raise BillingError("Bitte den Kunden eingeben.", feld="customer")
    if tax not in EARLIER_TAX:
        raise BillingError("Bitte wählen, wie die Rechnung besteuert war.", feld="tax")
    if net is None or net <= 0:
        raise BillingError("Bitte den Betrag eingeben.", feld="net")
    if issue_date > today:
        raise BillingError(
            "Eine Rechnung von früher kann nicht in der Zukunft liegen.", feld="issue_date"
        )
    if service_date and service_date > today:
        raise BillingError("Das Leistungsdatum liegt in der Zukunft.", feld="service_date")
    if paid_date and (paid_date > today or paid_date < issue_date - timedelta(days=366)):
        raise BillingError("Bitte das Zahldatum prüfen.", feld="paid")
    if conn.execute("SELECT 1 FROM invoices WHERE number = ?", (number,)).fetchone():
        raise EarlierNumberError(f"Die Rechnungsnummer {number} gibt es in FRITS schon.")
    _earlier_fits_sequence(conn, number, issue_date, settings.number_pattern, gap_ok)
    snapshot = {
        "19": {"mode": "regelbesteuerung", "rate": "19", "customer": ""},
        "7": {"mode": "regelbesteuerung", "rate": "7", "customer": ""},
        "klein": {"mode": "kleinunternehmer", "rate": "0", "customer": ""},
        "eu": {"mode": "regelbesteuerung", "rate": "19", "customer": "eu"},
        "drittland": {"mode": "regelbesteuerung", "rate": "19", "customer": "drittland"},
    }[tax]
    line = Line(
        description.strip() or f"Rechnung {number}",
        (service_date or issue_date).isoformat(),
        Decimal(1),
        "pauschal",
        money(net),
    )
    draft = Draft(
        customer_id=None,
        customer_name=customer,
        customer_address=None,
        lines=[line],
        questions=[],
        warnings=[],
        source_file="",
        tax=snapshot,
        customer_tax=snapshot["customer"],
        earlier=True,
    )
    sums = totals(draft.lines, settings, snapshot)
    due = None if paid_date else issue_date + timedelta(days=settings.payment_days)
    pdf_path = None
    if original:
        if not original.startswith(b"%PDF"):
            raise BillingError("Die Originalrechnung bitte als PDF hereingeben.")
        folder = Path(invoices_dir or ".") / "Nachgetragen"
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", number).strip("-")
        pdf_path = folder / f"{safe}_{_slug(customer)}.pdf"
        _never_overwrite(pdf_path)
    with store.transaction(conn):
        cur = conn.execute(
            "INSERT INTO drafts (created, data, status, invoice_number) VALUES (?, ?, ?, ?)",
            (store.now(), draft.to_json(), "approved", number),
        )
        conn.execute(
            "INSERT INTO invoices (number, customer, issue_date, paid_date, net, vat, gross,"
            " draft_id, due_date, pdf_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                number,
                customer,
                issue_date.isoformat(),
                paid_date.isoformat() if paid_date else None,
                str(sums.net),
                str(sums.vat),
                str(sums.gross),
                cur.lastrowid,
                due.isoformat() if due else None,
                str(pdf_path) if pdf_path else None,
            ),
        )
        if pdf_path:
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(original)
    return number


def _tax_from_invoice(row) -> dict:
    """For invoices issued before FRITS kept a tax snapshot: read it back from the stored sums."""
    net, vat = Decimal(row["net"]), Decimal(row["vat"])
    if vat == 0:
        return {"mode": "kleinunternehmer", "rate": "0"}
    rate = (vat / net * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP) if net else Decimal(19)
    return {"mode": "regelbesteuerung", "rate": str(rate)}


def _never_overwrite(path: Path) -> None:
    """An issued PDF is a record (GoBD). A file already sitting there is never replaced."""
    if Path(path).exists():
        raise BillingError(
            f"Im Rechnungsordner liegt schon eine Datei {Path(path).name}. FRITS überschreibt "
            "keine Rechnungen. Bitte verschiebe die Datei und versuche es noch einmal."
        )


def parse_amount(text: str | None) -> Decimal | None:
    """Read a number the way Fritz types it: "40", "40,5", "1.234,50", "1.400" or "40.50".

    A dot before exactly three digits is a German thousands dot: "1.400" is 1400, not 1,40.
    """
    typed = (text or "").strip()
    # "600,00 EUR", "€ 600", "600 Euro": the currency the way it is printed doesn't matter.
    text = re.sub(r"(?i)€|(?<![a-z])euro?(?![a-z])|[\s\u00a0\u202f]", "", typed)
    text = re.sub(r",[-–—]+$", "", text)  # "600,-" is how a round amount is often printed
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "")
    try:
        value = Decimal(text)
    except ArithmeticError:
        raise BillingError(
            f"„{typed}“ ist keine Zahl. Schreib sie zum Beispiel so: 600,00"
        ) from None
    if not value.is_finite():
        raise BillingError(f"„{typed}“ ist keine Zahl. Schreib sie zum Beispiel so: 600,00")
    return value


def set_customer(draft: Draft, customer: Customer | None) -> None:
    draft.customer_id = customer.id if customer else None
    draft.customer_name = customer.display_name if customer else draft.customer_name
    draft.customer_address = customer.address if customer else None
    draft.customer_tax = customer.umsatzsteuer if customer else ""
    draft.customer_ust_id = customer.ust_id if customer else None
    draft.customer_country = customer.land if customer else "DE"
    draft.customer_reference = customer.referenz if customer else None
    draft.customer_contact = contact_of(customer)
    if customer:  # chosen now: the question about an unknown customer is answered
        draft.questions = [
            q for q in draft.questions if "noch nicht in deiner Kundenliste" not in q
        ]


def contact_of(customer: Customer | None) -> str | None:
    """The person to address at a company, for "z. Hd." under the company name."""
    if customer and customer.company and customer.name and customer.name != customer.company:
        return customer.name
    return None


def reviewed_line(
    description: str,
    when: str | None,
    quantity: Decimal,
    unit: str,
    price: Decimal | None,
    vat_rate: Decimal | None = None,
) -> Line:
    """A line Fritz has looked at and saved. His price counts as his OK, so old flags go."""
    if not description.strip():
        raise BillingError("Jede Position braucht eine Beschreibung.")
    if quantity == 0:
        raise BillingError(f"Die Menge bei „{description}“ darf nicht 0 sein.")
    if price is not None and price < 0:
        raise BillingError(f"Der Preis bei „{description}“ darf nicht negativ sein.")
    return Line(
        description=description.strip(),
        date=(when or "").strip() or None,
        quantity=quantity,
        unit=unit,
        unit_price=price,
        flags=[] if price is not None else ["needs_price"],
        vat_rate=vat_rate,
    )


def load_draft(conn, draft_id: int) -> tuple[Draft, dict]:
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise BillingError(f"Entwurf {draft_id} gibt es nicht.")
    return Draft.from_json(row["data"]), dict(row)


def save_draft(conn, draft_id: int, draft: Draft) -> None:
    cur = conn.execute(
        "UPDATE drafts SET data = ?, customer_id = ? WHERE id = ? AND status = 'draft'",
        (draft.to_json(), draft.customer_id, draft_id),
    )
    if cur.rowcount == 0:
        raise BillingError(f"Entwurf {draft_id} kann nicht mehr geändert werden.")


def new_draft(conn, draft: Draft, job_id: int | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO drafts (job_id, created, customer_id, data) VALUES (?, ?, ?, ?)",
        (job_id, store.now(), draft.customer_id, draft.to_json()),
    )
    return cur.lastrowid


def copy_invoice(conn, number: str, customer: Customer | None = None) -> int:
    """A new draft with the lines of an earlier invoice: for the job Fritz does every month.

    Dates are left empty (it's new work), the tax is worked out afresh, and the customer's
    details come from the current note when there is one."""
    row = conn.execute(
        "SELECT d.data, i.cancels FROM invoices i JOIN drafts d ON d.id = i.draft_id"
        " WHERE i.number = ?",
        (number,),
    ).fetchone()
    if row is None:
        raise BillingError(f"Zu Rechnung {number} gibt es keine Positionen zum Kopieren.")
    if row["cancels"]:
        raise BillingError("Eine Stornorechnung kopiere ich nicht. Nimm die ursprüngliche.")
    old = Draft.from_json(row["data"])
    draft = Draft(
        customer_id=old.customer_id,
        customer_name=old.customer_name,
        customer_address=old.customer_address,
        lines=[replace(line, date=None, flags=[], stated_price=None) for line in old.lines],
        questions=[],
        warnings=[],
        source_file=f"Kopie von Rechnung {number}",
        customer_tax=old.customer_tax,
        customer_ust_id=old.customer_ust_id,
        customer_country=old.customer_country,
        customer_reference=old.customer_reference,
        customer_contact=old.customer_contact,
    )
    if customer is not None:
        set_customer(draft, customer)
    return new_draft(conn, draft)


def discard_draft(conn, draft_id: int) -> None:
    """Throw a draft away. Safe: no number was used, so nothing has to be cancelled."""
    cur = conn.execute(
        "UPDATE drafts SET status = 'discarded' WHERE id = ? AND status = 'draft'", (draft_id,)
    )
    if cur.rowcount == 0:
        raise BillingError(f"Entwurf {draft_id} kann nicht verworfen werden.")


def invoice_status(row, today: date | None = None) -> str:
    """bezahlt | storniert | storno | offen | überfällig"""
    today = today or date.today()
    if row["cancels"]:
        return "erstattet" if row["paid_date"] else "storno"
    if row["cancelled_by"]:
        return "storniert"
    if row["paid_date"]:
        return "bezahlt"
    if row["due_date"] and date.fromisoformat(row["due_date"]) < today:
        return "überfällig"
    return "offen"


def list_invoices(conn, today: date | None = None) -> list[dict]:
    rows = conn.execute(
        "SELECT i.*, c.number AS cancelled_by,"
        " (SELECT json_extract(d.data, '$.earlier') FROM drafts d WHERE d.id = i.draft_id)"
        " AS earlier,"
        " (SELECT o.paid_date FROM invoices o WHERE o.number = i.cancels) AS original_paid,"
        " (SELECT COUNT(*) FROM reminders r WHERE r.invoice_number = i.number) AS reminders,"
        " (SELECT MAX(r.new_due_date) FROM reminders r WHERE r.invoice_number = i.number)"
        " AS reminded_until"
        " FROM invoices i LEFT JOIN invoices c ON c.cancels = i.number"
        " ORDER BY i.number DESC"
    ).fetchall()
    return [{**dict(row), "status": invoice_status(row, today)} for row in rows]


def mark_paid(conn, number: str, paid: date | None = None) -> None:
    """Paid on the day the money arrived (§ 11 EStG), which decides the EÜR year.

    On a Storno it means: the money of a paid invoice went back to the customer."""
    paid = paid or date.today()
    row = conn.execute(
        "SELECT issue_date, cancels FROM invoices WHERE number = ?", (number,)
    ).fetchone()
    if row and paid < date.fromisoformat(row["issue_date"]) - timedelta(days=366):
        raise BillingError("Dieses Zahldatum liegt mehr als ein Jahr vor der Rechnung. Vertippt?")
    if row and row["cancels"]:
        original = conn.execute(
            "SELECT paid_date FROM invoices WHERE number = ?", (row["cancels"],)
        ).fetchone()
        if not original or not original["paid_date"]:
            raise BillingError(
                "Die stornierte Rechnung war nie bezahlt, also gibt es nichts zu erstatten."
            )
    cur = conn.execute(
        "UPDATE invoices SET paid_date = ? WHERE number = ? AND paid_date IS NULL",
        (paid.isoformat(), number),
    )
    if cur.rowcount == 0:
        raise BillingError(f"Rechnung {number} gibt es nicht oder sie ist schon bezahlt.")


def _slug(name: str | None) -> str:
    """ "Donau Consulting GmbH" -> "Donau-Consulting-GmbH": safe for file names, still readable."""
    words = ["".join(ch for ch in word if ch.isalnum()) for word in (name or "").split()]
    return "-".join(w for w in words if w)[:40].strip("-") or "Kunde"

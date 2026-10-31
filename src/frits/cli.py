"""Command-line entry point: `frits <command>`. The web app (`frits ui`) builds on the same functions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from frits import __version__

COMMANDS = {
    "init": "Create the FRITS folders and an example settings.toml.",
    "run": "Process a single file into an invoice draft.",
    "watch": "Watch the inbox folder and process new files.",
    "redact-preview": "Show a file next to what the AI would see.",
    "drafts": "List open drafts.",
    "show": "Show one draft with its questions and warnings.",
    "approve": "Finalize a draft: assign its number and render the PDF.",
    "cancel": "Cancel an invoice with a cancellation invoice (Storno).",
    "paid": "Mark an invoice as paid.",
    "eval": "Run the test documents against a real AI provider.",
    "ui": "Open the FRITS app in the browser.",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="frits",
        description="FRITS turns dropped notes into invoice drafts. "
        "Personal data is redacted locally before any AI sees it.",
    )
    parser.add_argument("--version", action="version", version=f"frits {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    cmd = {name: sub.add_parser(name, help=text) for name, text in COMMANDS.items()}
    for name in ("run", "redact-preview"):
        cmd[name].add_argument("file", type=Path)
    for name in ("run", "watch", "eval"):
        cmd[name].add_argument("--provider", choices=["claude", "openai", "openrouter", "ollama"])
    cmd["watch"].add_argument("--once", action="store_true", help="process the inbox once and stop")
    for name in ("show", "approve"):
        cmd[name].add_argument("draft_id", type=int)
    for name in ("cancel", "paid"):
        cmd[name].add_argument("number")
    cmd["eval"].add_argument("--fixtures", type=Path, default=Path("tests/fixtures"))
    cmd["ui"].add_argument(
        "--port", type=int, help="port on 127.0.0.1 (default: first free from 8765)"
    )
    cmd["ui"].add_argument("--no-browser", action="store_true", help="don't open the browser")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    handler = globals()[f"cmd_{args.command.replace('-', '_')}"]
    try:
        return handler(args) or 0
    except Exception as exc:  # every user-facing error class carries a plain German message
        from frits.billing import BillingError
        from frits.ingest import IngestError
        from frits.providers import ProviderError

        if isinstance(exc, BillingError | IngestError | ProviderError):
            print(f"FRITS: {exc}", file=sys.stderr)
            return 1
        raise


def _context(args=None):
    from frits.settings import Settings, Workspace

    ws = Workspace.default().ensure()
    return ws, Settings.load(ws.settings_file)


def _provider(settings, choice: str | None):
    from frits.providers import ProviderError, make_provider

    name = choice or settings.provider
    if settings.strict_local and name != "ollama":
        raise ProviderError("Strikter Modus ist an: Es wird nur das lokale Modell benutzt.")
    return make_provider(name, settings.models)


def cmd_init(args) -> None:
    ws, settings = _context()
    if not ws.settings_file.exists():
        settings.save(ws.settings_file)
    print(f"FRITS-Ordner: {ws.root}")
    print(f"Lege Dateien hier ab: {ws.inbox}")
    print(f"Einstellungen: {ws.settings_file}")


def cmd_run(args) -> None:
    from frits.pipeline import process_file

    ws, settings = _context()
    outcome = process_file(args.file, ws, settings, _provider(settings, args.provider))
    print(f"{outcome.status}: {outcome.message}")


def cmd_watch(args) -> None:
    from frits.pipeline import watch

    ws, settings = _context()
    provider = _provider(settings, args.provider)
    if not args.once:
        print(f"FRITS schaut in {ws.inbox} (Strg+C zum Beenden)")
    try:
        for path, outcome in watch(ws, settings, provider, once=args.once):
            print(f"{path.name}: {outcome.status}: {outcome.message}")
    except KeyboardInterrupt:
        pass


def cmd_redact_preview(args) -> None:
    from frits.customers import load_customers
    from frits.ingest import extract_text
    from frits.redact import redact

    ws, settings = _context()
    customers = load_customers(ws.customers)
    known = [*customers, settings.owner] if settings.name else customers
    source = extract_text(args.file)
    result = redact(source.text, known)
    print("=== Original (bleibt auf diesem Computer) ===")
    print(source.text)
    print("\n=== Das sieht die KI ===")
    print(result.text)
    print("\n=== Ersetzt ===")
    for placeholder, entry in result.mapping.entries.items():
        print(f"{placeholder:14} {' / '.join(entry['surface'])}")


def cmd_drafts(args) -> None:
    from frits import store
    from frits.billing import Draft, totals

    ws, settings = _context()
    conn = store.connect(ws.db)
    rows = conn.execute("SELECT * FROM drafts WHERE status = 'draft' ORDER BY id").fetchall()
    if not rows:
        print("Keine offenen Entwürfe.")
    for row in rows:
        draft = Draft.from_json(row["data"])
        sums = totals(draft.lines, settings)
        flag = " (Fragen offen)" if draft.questions or draft.warnings else ""
        print(f"{row['id']:4}  {draft.customer_name or '?':30} {sums.gross:>10} EUR{flag}")


def cmd_show(args) -> None:
    from frits import store
    from frits.billing import Draft, blockers, totals

    ws, settings = _context()
    row = (
        store.connect(ws.db)
        .execute("SELECT * FROM drafts WHERE id = ?", (args.draft_id,))
        .fetchone()
    )
    if row is None:
        print("Diesen Entwurf gibt es nicht.", file=sys.stderr)
        return 1
    draft = Draft.from_json(row["data"])
    print(f"Entwurf {row['id']} aus {draft.source_file} für {draft.customer_name or '?'}")
    for line in draft.lines:
        price = f"{line.unit_price} EUR" if line.unit_price is not None else "Preis fehlt"
        print(f"  {line.quantity} {line.unit:8} {line.description:40} {price}")
    sums = totals(draft.lines, settings)
    print(f"  Summe: {sums.gross} EUR")
    for text in draft.warnings:
        print(f"! {text}")
    for text in draft.questions:
        print(f"? {text}")
    for text in blockers(draft, settings):
        print(f"x {text}")


def cmd_approve(args) -> None:
    from frits import store
    from frits.billing import approve
    from frits.pipeline import forget_mapping

    ws, settings = _context()
    conn = store.connect(ws.db)
    number, pdf = approve(conn, args.draft_id, settings, ws.invoices)
    job = conn.execute("SELECT job_id FROM drafts WHERE id = ?", (args.draft_id,)).fetchone()
    forget_mapping(ws, job["job_id"])
    print(f"Rechnung {number} erstellt: {pdf}")


def cmd_cancel(args) -> None:
    from frits import store
    from frits.billing import cancel

    ws, settings = _context()
    number, pdf = cancel(store.connect(ws.db), args.number, settings, ws.invoices)
    print(f"Stornorechnung {number} erstellt: {pdf}")


def cmd_paid(args) -> None:
    from frits import store
    from frits.billing import mark_paid

    ws, _ = _context()
    mark_paid(store.connect(ws.db), args.number)
    print(f"Rechnung {args.number} ist als bezahlt markiert.")


def cmd_eval(args) -> int:
    from frits.evaluate import run_eval

    _, settings = _context()
    return run_eval(args.fixtures, _provider(settings, args.provider))


def cmd_ui(args) -> None:
    from frits.web import serve

    serve(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line entry point. Each subcommand is a placeholder until its phase 1 module lands."""

import argparse
import sys

from frits import __version__

COMMANDS = {
    "run": "Process a single file into an invoice draft.",
    "watch": "Watch the inbox folder and process new files.",
    "redact-preview": "Show a file next to what the AI would see.",
    "approve": "Finalize a draft: assign its number and render the PDF.",
    "eval": "Run the test documents against a real AI provider.",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="frits",
        description="FRITS turns dropped notes into invoice drafts. "
        "Personal data is redacted locally before any AI sees it.",
    )
    parser.add_argument("--version", action="version", version=f"frits {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name, help_text in COMMANDS.items():
        sub.add_parser(name, help=help_text)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    print(f"'frits {args.command}' is not built yet.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

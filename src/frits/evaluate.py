"""`frits eval`: run the test documents against a real provider and print accuracy and cost."""

from __future__ import annotations

from pathlib import Path

import yaml

from frits.customers import load_customers
from frits.extract import check
from frits.ingest import extract_text
from frits.providers import ProviderError
from frits.redact import redact


def run_eval(fixtures: Path, provider) -> int:
    fixtures = Path(fixtures)
    customers = load_customers(fixtures / "customers")
    passed = total = 0
    cost = 0.0
    for doc in sorted((fixtures / "docs").iterdir()):
        expected = yaml.safe_load((fixtures / "expected" / f"{doc.stem}.yaml").read_text("utf-8"))
        if expected["broken"]:
            continue
        total += 1
        redacted = redact(extract_text(doc).text, customers)
        try:
            result, usage = provider.extract(redacted.text)
        except ProviderError as exc:
            print(f"FAIL {doc.name}: {exc}")
            continue
        cost += usage.cost_usd or 0.0
        checked = check(result, redacted.text, redacted.mapping)
        problems = _compare(expected, checked)
        if problems:
            print(f"FAIL {doc.name}: {'; '.join(problems)}")
        else:
            passed += 1
            print(f"ok   {doc.name}")
    print(f"\n{passed}/{total} Dokumente richtig, Kosten ca. {cost:.4f} USD")
    return 0 if passed == total else 1


def _compare(expected: dict, checked) -> list[str]:
    problems = []
    if checked.doc_type != expected["doc_type"]:
        problems.append(f"doc_type {checked.doc_type} statt {expected['doc_type']}")
    want = sorted((i["quantity"], i["unit"]) for i in expected["items"] or [])
    # Injected extra items must not appear: the trick documents expect only the real work.
    got = sorted((c.item.quantity, c.item.unit) for c in checked.items)
    if got != want:
        problems.append(f"Positionen {got} statt {want}")
    return problems

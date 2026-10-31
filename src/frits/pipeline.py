"""One dropped file in, one draft invoice out. This is the whole safety story in order:
read locally -> redact locally -> leak gate -> AI sees placeholders only -> code checks -> draft.
"""

from __future__ import annotations

import hashlib
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from frits import store
from frits.billing import build_draft
from frits.customers import load_customers
from frits.extract import check
from frits.ingest import IngestError, extract_text
from frits.providers import Provider, ProviderError
from frits.redact import LeakError, Mapping, redact
from frits.settings import Settings, Workspace

MAPPING_MAX_AGE_DAYS = 30


@dataclass(frozen=True)
class Outcome:
    status: str  # drafted | check-me | receipt | skipped
    message: str
    draft_id: int | None = None


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def process_file(
    path: Path, ws: Workspace, settings: Settings, provider: Provider, nlp=None
) -> Outcome:
    path = Path(path)
    conn = store.connect(ws.db)
    digest = file_hash(path)
    if store.job_seen(conn, digest):
        _move(path, ws.done)
        return Outcome("skipped", f"{path.name} wurde schon verarbeitet.")

    try:
        source = extract_text(path)
    except IngestError as exc:
        return _to_check_me(conn, path, ws, digest, str(exc))

    customers = load_customers(ws.customers)
    try:
        known = [*customers, settings.owner] if settings.name else customers
        redacted = redact(source.text, known, nlp)
    except LeakError:
        return _to_check_me(
            conn,
            path,
            ws,
            digest,
            "Beim Schwärzen ist etwas übrig geblieben. Aus Sicherheit wurde nichts verschickt.",
        )

    try:
        result, usage = provider.extract(redacted.text)
    except ProviderError as exc:
        # Not recorded as a job: once the problem is fixed, the same file may be dropped again.
        return Outcome("check-me", str(exc))

    job_id = store.add_job(conn, digest, path.name, "drafted")
    store.log_ai_call(conn, job_id, usage, hashlib.sha256(redacted.text.encode()).hexdigest())
    redacted.mapping.save(ws.mappings / f"{job_id}.json")

    checked = check(result, redacted.text, redacted.mapping)
    if checked.doc_type == "expense_receipt":
        conn.execute("UPDATE jobs SET status = 'receipt' WHERE id = ?", (job_id,))
        _move(path, ws.done)
        return Outcome("receipt", f"{path.name} ist ein Beleg für eine Ausgabe.")

    draft = build_draft(checked, redacted.mapping, customers, settings, path.name)
    cur = conn.execute(
        "INSERT INTO drafts (job_id, created, customer_id, data) VALUES (?, ?, ?, ?)",
        (job_id, store.now(), draft.customer_id, draft.to_json()),
    )
    _move(path, ws.done)
    return Outcome(
        "drafted", f"Entwurf {cur.lastrowid} für {draft.customer_name or '?'}", cur.lastrowid
    )


def watch(ws: Workspace, settings: Settings, provider: Provider, interval: float = 2.0, once=False):
    """Poll the inbox. A file is processed once its size has stopped changing (fully copied)."""
    sizes: dict[Path, int] = {}
    while True:
        purge_old_mappings(ws)
        for path in sorted(p for p in ws.inbox.iterdir() if p.is_file()):
            if path.name.startswith("."):
                continue
            size = path.stat().st_size
            if once or sizes.get(path) == size:
                sizes.pop(path, None)
                yield path, process_file(path, ws, settings, provider)
            else:
                sizes[path] = size
        if once:
            return
        time.sleep(interval)


def purge_old_mappings(ws: Workspace, max_age_days: int = MAPPING_MAX_AGE_DAYS) -> None:
    cutoff = time.time() - max_age_days * 86400
    for path in ws.mappings.glob("*.json"):
        if path.stat().st_mtime < cutoff:
            path.unlink()


def forget_mapping(ws: Workspace, job_id: int) -> None:
    (ws.mappings / f"{job_id}.json").unlink(missing_ok=True)


def load_mapping(ws: Workspace, job_id: int) -> Mapping | None:
    path = ws.mappings / f"{job_id}.json"
    return Mapping.load(path) if path.exists() else None


def _to_check_me(conn, path: Path, ws: Workspace, digest: str, reason: str) -> Outcome:
    store.add_job(conn, digest, path.name, "check-me", reason)
    target = _move(path, ws.check_me)
    target.with_name(target.name + ".grund.txt").write_text(reason + "\n", encoding="utf-8")
    return Outcome("check-me", reason)


def _move(path: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / path.name
    n = 1
    while target.exists():
        target = folder / f"{path.stem} ({n}){path.suffix}"
        n += 1
    shutil.move(str(path), target)
    return target

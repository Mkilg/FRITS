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
OTHER_TEXT = (
    "Das ist weder ein Auftrag noch ein Beleg, eher ein Brief, ein Bescheid oder ein Vertrag."
)


@dataclass(frozen=True)
class Outcome:
    status: str  # drafted | check-me | receipt | skipped
    message: str
    draft_id: int | None = None
    sha256: str | None = None  # for "skipped": which known file it was


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def process_file(
    path: Path, ws: Workspace, settings: Settings, provider: Provider, nlp=None
) -> Outcome:
    path = Path(path)
    conn = store.connect(ws.db)
    digest = file_hash(path)
    if store.job_seen(conn, digest):
        known = conn.execute("SELECT filename FROM jobs WHERE sha256 = ?", (digest,)).fetchone()
        kept = ws.done / Path(known["filename"]).name
        if kept.is_file() and file_hash(kept) == digest:
            path.unlink()  # the same bytes already lie in "erledigt": no "Beleg (1).pdf"
        else:
            _move(path, ws.done)
        return Outcome("skipped", f"{path.name} wurde schon verarbeitet.", sha256=digest)

    try:
        source = extract_text(path)
    except IngestError as exc:
        return _to_check_me(conn, path, ws, digest, str(exc))

    customers = load_customers(ws.customers)
    try:
        known = [*customers, settings.owner] if settings.name else customers
        redacted = redact(source.text, known, nlp)
    except LeakError:
        from frits.beleg import looks_like_receipt

        if looks_like_receipt(source.text):
            # A ticket or till receipt is entered on Ausgaben without any AI, so it doesn't
            # matter that redaction wasn't sure: nothing goes out.
            target = _move(path, ws.done)
            store.add_job(conn, digest, target.name, "receipt")
            return Outcome("receipt", f"{path.name} ist ein Beleg für eine Ausgabe.")
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

    job_id = store.add_job(conn, digest, path.name, "drafted", sent_text=redacted.text)
    store.log_ai_call(conn, job_id, usage, hashlib.sha256(redacted.text.encode()).hexdigest())
    redacted.mapping.save(ws.mappings / f"{job_id}.json")

    checked = check(result, redacted.text, redacted.mapping)
    if checked.doc_type == "expense_receipt":
        conn.execute("UPDATE jobs SET status = 'receipt' WHERE id = ?", (job_id,))
        _move(path, ws.done)
        return Outcome("receipt", f"{path.name} ist ein Beleg für eine Ausgabe.")

    if checked.doc_type == "other" and not checked.items:
        # A letter from the tax office, a contract, a notice: no job to bill and nothing paid.
        # It waits under "Zum Prüfen", where Fritz decides where it belongs.
        conn.execute("UPDATE jobs SET status = 'check-me' WHERE id = ?", (job_id,))
        target = _move(path, ws.check_me)
        target.with_name(target.name + ".grund.txt").write_text(OTHER_TEXT + "\n", encoding="utf-8")
        return Outcome("check-me", OTHER_TEXT)

    draft = build_draft(checked, redacted.mapping, customers, settings, path.name)
    cur = conn.execute(
        "INSERT INTO drafts (job_id, created, customer_id, data) VALUES (?, ?, ?, ?)",
        (job_id, store.now(), draft.customer_id, draft.to_json()),
    )
    _move(path, ws.done)
    return Outcome(
        "drafted", f"Entwurf {cur.lastrowid} für {draft.customer_name or '?'}", cur.lastrowid
    )


class AnswerError(Exception):
    """The answers can't be worked in. The message says why, in plain words."""


def answer_questions(
    conn, ws: Workspace, settings: Settings, provider: Provider, nlp, draft_id: int, answers: str
):
    """Fritz answered the AI's questions in his own words: read the note again with the
    answers below it, the same safe way as the first time, and propose the draft anew."""
    from frits.billing import load_draft, save_draft

    draft, row = load_draft(conn, draft_id)
    if row["status"] != "draft" or not row["job_id"]:
        raise AnswerError("Diesen Entwurf hat keine KI gelesen, es gibt also nichts neu zu lesen.")
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["job_id"],)).fetchone()
    source = ws.done / Path(draft.source_file).name
    original = None
    if source.is_file():
        try:
            original = extract_text(source).text
        except IngestError:
            original = None
    if original is None:
        mapping = load_mapping(ws, job["id"])
        if mapping is None or not job["sent_text"]:
            raise AnswerError(
                "Die ursprüngliche Notiz ist nicht mehr da. Trag die Antworten bitte direkt "
                "in die Positionen ein."
            )
        original = mapping.reidentify(job["sent_text"])
    asked = "\n".join(f"- {q}" for q in draft.questions)
    text = (
        f"{original.strip()}\n\n"
        f"Rückfragen zu dieser Notiz:\n{asked}\n\n"
        f"Antworten des Auftragnehmers auf die Rückfragen:\n{answers.strip()}\n"
    )
    customers = load_customers(ws.customers)
    known = [*customers, settings.owner] if settings.name else customers
    try:
        redacted = redact(text, known, nlp)
    except LeakError as exc:
        raise AnswerError(
            "Beim Schwärzen deiner Antworten ist etwas übrig geblieben. Aus Sicherheit ging "
            "nichts raus. Trag die Antworten bitte direkt in die Positionen ein."
        ) from exc
    try:
        result, usage = provider.extract(redacted.text)
    except ProviderError as exc:
        raise AnswerError(str(exc)) from exc
    store.log_ai_call(conn, job["id"], usage, hashlib.sha256(redacted.text.encode()).hexdigest())
    conn.execute("UPDATE jobs SET sent_text = ? WHERE id = ?", (redacted.text, job["id"]))
    redacted.mapping.save(ws.mappings / f"{job['id']}.json")
    checked = check(result, redacted.text, redacted.mapping)
    fresh = build_draft(checked, redacted.mapping, customers, settings, draft.source_file)
    fresh.kind = draft.kind
    if fresh.customer_id is None and draft.customer_id:  # Fritz already chose the customer
        fresh.customer_id, fresh.customer_name = draft.customer_id, draft.customer_name
        fresh.customer_address, fresh.customer_tax = draft.customer_address, draft.customer_tax
        fresh.customer_ust_id, fresh.customer_country = (
            draft.customer_ust_id,
            draft.customer_country,
        )
        fresh.customer_reference = draft.customer_reference
        fresh.questions = [q for q in fresh.questions if "Kundenliste" not in q]
    save_draft(conn, draft_id, fresh)
    return fresh


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

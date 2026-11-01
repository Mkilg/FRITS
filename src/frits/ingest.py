"""Turn a dropped file into plain text. Photos are read with local OCR; nothing leaves the machine here."""

from __future__ import annotations

import email
import email.policy
import html
import os
import re
from dataclasses import dataclass
from pathlib import Path

TEXT_TYPES = {".txt", ".md"}
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
SUPPORTED = TEXT_TYPES | IMAGE_TYPES | {".eml", ".pdf", ".docx", ".xml"}


class IngestError(Exception):
    """The file can't be read. The message is shown to the user as-is, so keep it plain."""


@dataclass(frozen=True)
class SourceText:
    text: str
    kind: str
    ocr_used: bool = False


def extract_text(path: Path) -> SourceText:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED:
        raise IngestError(
            f"FRITS kann Dateien vom Typ '{suffix or 'ohne Endung'}' noch nicht lesen."
        )
    if path.stat().st_size == 0:
        raise IngestError("Die Datei ist leer.")

    if suffix in TEXT_TYPES or suffix == ".xml":
        result = SourceText(_read_text_file(path), suffix.lstrip("."))
    elif suffix == ".eml":
        result = SourceText(_read_eml(path), "email")
    elif suffix == ".pdf":
        result = SourceText(_read_pdf(path), "pdf")
    elif suffix == ".docx":
        result = SourceText(_read_docx(path), "docx")
    else:
        result = SourceText(_read_image(path), "image", ocr_used=True)

    if not result.text.strip():
        raise IngestError("In der Datei wurde kein Text gefunden.")
    return result


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _read_eml(path: Path) -> str:
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    headers = [f"{name}: {msg[name]}" for name in ("From", "To", "Date", "Subject") if msg[name]]
    # Prefer HTML: some mails carry only a stub plain part ("please use HTML view").
    html_part = msg.get_body(preferencelist=("html",))
    plain_part = msg.get_body(preferencelist=("plain",))
    if html_part is not None:
        text = _strip_html(html_part.get_content())
    elif plain_part is not None:
        text = plain_part.get_content()
    else:
        text = ""
    return "\n".join(headers) + "\n\n" + text.strip()


def _strip_html(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", markup)
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, ValueError, OSError) as exc:
        raise IngestError("Die PDF-Datei ist beschädigt und kann nicht gelesen werden.") from exc
    text = "\n".join(pages)
    if not text.strip():
        raise IngestError(
            "Die PDF enthält nur ein Bild ohne Text. Bitte als Foto (JPG/PNG) ablegen."
        )
    return text


def _read_docx(path: Path) -> str:
    from docx import Document

    try:
        doc = Document(str(path))
    except Exception as exc:  # python-docx raises several unrelated types for broken files
        raise IngestError("Die Word-Datei ist beschädigt und kann nicht gelesen werden.") from exc
    lines = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(lines)


def _read_image(path: Path) -> str:
    import pytesseract
    from PIL import Image, ImageOps, UnidentifiedImageError

    # The Mac app bundles its own Tesseract and points FRITS_TESSERACT at it.
    if os.environ.get("FRITS_TESSERACT"):
        pytesseract.pytesseract.tesseract_cmd = os.environ["FRITS_TESSERACT"]
    if path.suffix.lower() in {".heic", ".heif"}:
        from pi_heif import register_heif_opener  # read-only HEIC support, LGPL (no GPL encoder)

        register_heif_opener()
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img).convert("L")
            return pytesseract.image_to_string(img, lang="deu+eng")
    except UnidentifiedImageError as exc:
        raise IngestError("Das Bild ist beschädigt und kann nicht gelesen werden.") from exc
    except pytesseract.TesseractNotFoundError as exc:
        raise IngestError("Die Texterkennung (Tesseract) ist nicht installiert.") from exc

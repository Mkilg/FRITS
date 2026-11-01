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
SUPPORTED = TEXT_TYPES | IMAGE_TYPES | {".eml", ".pdf", ".docx", ".xml", ".rtf"}
OCR_PDF_PAGES = 5  # a scanned PDF is read page by page with OCR, up to this many pages


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
        text, ocr = _read_pdf(path)
        result = SourceText(text, "pdf", ocr_used=ocr)
    elif suffix == ".rtf":
        result = SourceText(rtf_to_text(path.read_bytes()), "rtf")
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
    parts = ["\n".join(headers) + "\n\n" + text.strip()]
    parts += _eml_attachments(msg)
    return "\n\n".join(parts)


MAX_ATTACHMENTS = 3


def _eml_attachments(msg) -> list[str]:
    """An invoice or ticket often comes as a PDF attached to a mail: read those too, here."""
    import tempfile

    found = []
    for part in msg.iter_attachments():
        name = Path(str(part.get_filename() or "")).name
        suffix = Path(name).suffix.lower()
        if not name or suffix not in SUPPORTED - {".eml"}:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / f"anhang{suffix}"
            copy.write_bytes(payload)
            try:
                inner = extract_text(copy).text
            except IngestError:
                continue
        found.append(f"Anhang „{name}“:\n{inner.strip()}")
        if len(found) >= MAX_ATTACHMENTS:
            break
    return found


def _strip_html(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", markup)
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


def _read_pdf(path: Path) -> tuple[str, bool]:
    """The text of a PDF. A scan (only pictures, no text) is read with local OCR instead."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, ValueError, OSError) as exc:
        raise IngestError("Die PDF-Datei ist beschädigt und kann nicht gelesen werden.") from exc
    text = "\n".join(pages)
    if text.strip():
        return text, False
    scanned = _ocr_pdf_images(reader)
    if not scanned.strip():
        raise IngestError(
            "Die PDF enthält nur ein Bild, und darin konnte ich keinen Text erkennen. "
            "Ein schärferes Foto (JPG/PNG) klappt oft besser."
        )
    return scanned, True


def _ocr_pdf_images(reader) -> str:
    """Scans and some tickets are PDFs with just a picture inside: read those pictures."""
    import pytesseract
    from PIL import ImageOps

    if os.environ.get("FRITS_TESSERACT"):
        pytesseract.pytesseract.tesseract_cmd = os.environ["FRITS_TESSERACT"]
    texts = []
    for page in list(reader.pages)[:OCR_PDF_PAGES]:
        try:
            images = list(page.images)
        except Exception:  # pypdf raises several types for odd image streams  # noqa: BLE001
            images = []
        for picture in images:
            try:
                img = picture.image
                if img is None or min(img.size) < 100:
                    continue  # logos and icons, no receipt text in there
                texts.append(pytesseract.image_to_string(ImageOps.grayscale(img), lang="deu+eng"))
            except pytesseract.TesseractNotFoundError as exc:
                raise IngestError("Die Texterkennung (Tesseract) ist nicht installiert.") from exc
            except Exception:  # an image format PIL can't open: skip it  # noqa: BLE001, S112
                continue
    return "\n".join(texts)


# ----- RTF (TextEdit's default format) -----
_RTF_TOKEN = re.compile(
    r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([^\\{}]+)", re.DOTALL
)
_RTF_SKIP = {
    "fonttbl",
    "colortbl",
    "stylesheet",
    "info",
    "pict",
    "object",
    "header",
    "footer",
    "headerl",
    "headerr",
    "footerl",
    "footerr",
    "listtable",
    "listoverridetable",
    "expandedcolortbl",
    "themedata",
    "datastore",
    "xmlnstbl",
    "rsidtbl",
    "generator",
}
_RTF_BREAKS = {
    "par": "\n",
    "line": "\n",
    "sect": "\n",
    "page": "\n",
    "row": "\n",
    "tab": "\t",
    "cell": " | ",
    "emdash": "—",
    "endash": "–",
    "bullet": "•",
    "lquote": "‘",
    "rquote": "’",
    "ldblquote": "“",
    "rdblquote": "”",
}


def rtf_to_text(raw: bytes) -> str:
    """Plain text from RTF, as TextEdit on the Mac writes it. Formatting is dropped."""
    data = raw.decode("latin-1")
    if not data.lstrip().startswith("{\\rtf"):
        raise IngestError("Die RTF-Datei ist beschädigt und kann nicht gelesen werden.")
    out: list[str] = []
    stack: list[tuple[bool, int]] = []
    skip, uc, pending_skip = False, 1, 0
    codepage = "cp1252"
    for word, arg, hexa, symbol, brace, text in _RTF_TOKEN.findall(data):
        if brace == "{":
            stack.append((skip, uc))
        elif brace == "}":
            skip, uc = stack.pop() if stack else (False, 1)
        elif symbol:
            if symbol == "*":
                skip = True
            elif not skip and symbol in "\\{}":
                out.append(symbol)
            elif not skip and symbol == "~":
                out.append("\u00a0")
            elif not skip and symbol in "\n\r":
                out.append("\n")
        elif hexa:
            if pending_skip:
                pending_skip -= 1
            elif not skip:
                out.append(bytes([int(hexa, 16)]).decode(codepage, errors="replace"))
        elif word:
            if word in _RTF_SKIP:
                skip = True
            elif word == "ansicpg" and arg:
                codepage = f"cp{arg}"
            elif word == "uc" and arg:
                uc = int(arg)
            elif word == "u" and arg and not skip:
                value = int(arg)
                out.append(chr(value + 65536 if value < 0 else value))
                pending_skip = uc
            elif not skip and word in _RTF_BREAKS:
                out.append(_RTF_BREAKS[word])
        elif text:
            if pending_skip:
                cut = min(pending_skip, len(text))
                text, pending_skip = text[cut:], pending_skip - cut
            if not skip:
                out.append(text.replace("\r", "").replace("\n", ""))
    lines = [line.rstrip() for line in "".join(out).splitlines()]
    return "\n".join(lines).strip()


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

import pytest
from conftest import DOCS, answer

from frits.ingest import IngestError, extract_text

IMAGES = {".png", ".jpg", ".jpeg", ".heic"}


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_fixture_documents(doc, request):
    expected = answer(doc)
    if doc.suffix in IMAGES:
        request.getfixturevalue("needs_ocr")
    if expected["broken"]:
        with pytest.raises(IngestError):
            extract_text(doc)
        return
    source = extract_text(doc)
    for word in expected["contains"]:
        assert word in source.text
    assert source.ocr_used == (doc.suffix in IMAGES)


def test_unknown_type_is_a_plain_error(tmp_path):
    f = tmp_path / "liste.xlsx"
    f.write_bytes(b"data")
    with pytest.raises(IngestError, match="xlsx"):
        extract_text(f)


def test_latin1_text_is_read(tmp_path):
    f = tmp_path / "alt.txt"
    f.write_bytes("Gärtner".encode("latin-1"))
    assert extract_text(f).text == "Gärtner"


def test_html_stub_plain_part_loses_to_html(tmp_path):
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = "x"
    msg.set_content("Bitte HTML-Ansicht verwenden.")
    msg.add_alternative("<p>Zaun <b>gestrichen</b>, 3 Std</p>", subtype="html")
    f = tmp_path / "m.eml"
    f.write_bytes(bytes(msg))
    assert "Zaun gestrichen, 3 Std" in extract_text(f).text


def test_rtf_from_textedit_is_read(tmp_path):
    b = "\\"
    rtf = (
        "{" + b + "rtf1" + b + "ansi" + b + "ansicpg1252{" + b + "fonttbl" + b + "f0 Helvetica;}\n"
        "{"
        + b
        + "*"
        + b
        + "expandedcolortbl;;}\n"
        + b
        + "f0 Moderation f"
        + b
        + "'fcr Radio S"
        + b
        + "'fcd"
        + b
        + "\n"
        "3 Std "
        + b
        + "'e0 80 "
        + b
        + "uc0"
        + b
        + "u8364 "
        + b
        + "\n"
        + b
        + "{Klammer"
        + b
        + "}"
        + b
        + "tab Ende}"
    )
    f = tmp_path / "notiz.rtf"
    f.write_bytes(rtf.encode("latin-1"))
    text = extract_text(f).text
    assert text == "Moderation für Radio Süd\n3 Std à 80 €\n{Klammer}\tEnde"
    assert "Helvetica" not in text


def test_broken_rtf_is_a_plain_error(tmp_path):
    f = tmp_path / "kaputt.rtf"
    f.write_bytes(b"kein rtf")
    with pytest.raises(IngestError, match="RTF"):
        extract_text(f)


def test_pdf_with_only_a_picture_is_read_with_ocr(tmp_path, needs_ocr):
    from PIL import Image, ImageDraw, ImageFont

    from frits.render import FONTS

    img = Image.new("RGB", (1200, 500), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONTS / "DejaVuSans.ttf"), 48)
    draw.text((60, 80), "Fahrkarte Berlin Hbf", fill="black", font=font)
    draw.text((60, 220), "Summe 49,90 EUR", fill="black", font=font)
    f = tmp_path / "ticket.pdf"
    img.save(f, "PDF", resolution=150)
    source = extract_text(f)
    assert source.ocr_used and "Fahrkarte" in source.text and "49,90" in source.text


def test_mail_attachments_are_read_too(tmp_path):
    from email.message import EmailMessage

    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=14)
    pdf.cell(text="Rechnung Nr. 4711 Gesamtbetrag 59,00 EUR")
    msg = EmailMessage()
    msg["From"] = "shop@example.com"
    msg["Subject"] = "Ihre Rechnung"
    msg.set_content("Im Anhang finden Sie Ihre Rechnung.")
    msg.add_attachment(
        bytes(pdf.output()), maintype="application", subtype="pdf", filename="Rechnung_4711.pdf"
    )
    msg.add_attachment(b"\x89PNG kaputt", maintype="image", subtype="png", filename="logo.png")
    f = tmp_path / "mail.eml"
    f.write_bytes(bytes(msg))
    text = extract_text(f).text
    assert "Im Anhang finden Sie" in text
    assert "Anhang „Rechnung_4711.pdf“" in text and "59,00" in text
    assert "logo.png" not in text  # a broken picture is just skipped

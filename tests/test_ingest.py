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

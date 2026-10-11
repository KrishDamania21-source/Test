"""Document loading for every supported format. No network."""

from pathlib import Path

import pytest

from radar.documents import UnsupportedDocument, load_document, load_path

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.mark.parametrize("name", sorted(p.name for p in SAMPLES.glob("0*")))
def test_every_sample_loads_with_text(name):
    doc = load_path(SAMPLES / name)
    assert doc.text
    assert "fictional" in doc.text.lower()


def test_pdf_text_contains_table_values():
    text = load_path(SAMPLES / "02_kestrel_lead_over.pdf").text
    assert "Kestrel Microparts" in text and "0.35" in text and "7439-92-1" in text


def test_xlsx_shows_blank_cells_and_keeps_metadata_clean():
    text = load_path(SAMPLES / "08_pellham_blank_conc.xlsx").text
    assert "Row 9: Cadmium | 7440-43-9 | (blank) | % w/w" in text
    assert "Row 10: Hexavalent chromium | 18540-29-9 | (blank) | % w/w" in text
    assert "Row 2: Supplier | Pellham Fasteners (fictional)\n" in text


def test_unsupported_and_empty_files_rejected():
    with pytest.raises(UnsupportedDocument, match="unsupported"):
        load_document("x.docx", b"hello")
    with pytest.raises(UnsupportedDocument, match="no readable text"):
        load_document("x.txt", b"   \n")


def test_cp1252_text_decodes():
    assert "µ" in load_document("x.txt", "10 µg".encode("cp1252")).text

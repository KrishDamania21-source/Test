"""Generate the two PDF sample declarations (synthetic data, fictional suppliers).

Run from the repo root: python scripts/make_sample_pdfs.py
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
NOTE = "Synthetic sample for the RADAR prototype. Fictional supplier."

PDFS = {
    "02_kestrel_lead_over.pdf": {
        "title": "Supplier Material Declaration (RoHS)",
        "meta": [
            ("Supplier", "Kestrel Microparts (fictional)"),
            ("Component", "KM-48 tin-plated terminal block"),
            ("Date", "2026-02-11"),
        ],
        "rows": [
            ("Lead (Pb)", "7439-92-1", "0.35", "% w/w"),
            ("Mercury (Hg)", "7439-97-6", "0.0003", "% w/w"),
            ("Cadmium (Cd)", "7440-43-9", "0.0008", "% w/w"),
            ("Hexavalent chromium", "18540-29-9", "0.002", "% w/w"),
            ("PBDE", "1163-19-5", "0.0002", "% w/w"),
            ("DEHP", "117-81-7", "0.004", "% w/w"),
        ],
        "footer": "Values per homogeneous material.",
    },
    "06_corvan_cadmium_near.pdf": {
        "title": "RoHS Compliance Statement",
        "meta": [
            ("Supplier", "Corvan Plastics (fictional)"),
            ("Component", "CP-3 yellow ABS housing"),
            ("Date", "2026-06-08"),
        ],
        "rows": [
            ("Lead (Pb)", "7439-92-1", "0.006", "% w/w"),
            ("Mercury (Hg)", "7439-97-6", "0.0004", "% w/w"),
            ("Cadmium (Cd)", "7440-43-9", "0.009", "% w/w"),
            ("Hexavalent chromium", "18540-29-9", "0.001", "% w/w"),
            ("PBB", "59536-65-1", "0.0003", "% w/w"),
            ("PBDE", "1163-19-5", "0.0005", "% w/w"),
        ],
        "footer": "Values per homogeneous material. Pigment batch CY-7.",
    },
}


def build(path: Path, spec: dict) -> None:
    styles = getSampleStyleSheet()
    story = [
        Paragraph(spec["title"], styles["Title"]),
        Paragraph(NOTE, styles["Italic"]),
        Spacer(1, 10),
    ]
    for label, value in spec["meta"]:
        story.append(Paragraph(f"<b>{label}:</b> {value}", styles["Normal"]))
    story.append(Spacer(1, 12))
    table = Table([("Substance", "CAS No.", "Concentration", "Unit"), *spec["rows"]])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6E9EE")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9AA3AF")),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [table, Spacer(1, 12), Paragraph(spec["footer"], styles["Normal"])]
    # invariant=1 keeps the bytes stable across runs, so the file hash cache stays valid
    SimpleDocTemplate(str(path), pagesize=A4, invariant=1).build(story)


if __name__ == "__main__":
    for name, spec in PDFS.items():
        build(SAMPLES / name, spec)
        print(f"wrote samples/{name}")

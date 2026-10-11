"""Hand-written extractions of every sample, as a perfect reader would produce them.

Used to test the rules engine against samples/answer_key.json with no API.
Every source_text is copied from the real document text, so traceability
is checked for real. This is test data, not ground truth: the answer key
stays the ground truth.
"""


def s(name, cas, raw, value, unit, source, exemption=None):
    return {
        "name": name, "cas": cas, "concentration_raw": raw, "concentration_value": value, "unit": unit,
        "exemption_claimed": exemption is not None, "exemption_text": exemption, "source_text": source,
    }


def row(*cells):
    return "| " + " | ".join(cells) + " |"


def pdf(name, cas, value):
    # pypdf puts each table cell on its own line; the engine ignores whitespace differences.
    return s(name, cas, value, float(value), "%", f"{name}\n{cas}\n{value}\n% w/w")


PERFECT = {
    "01_brightvale_compliant.md": {
        "supplier": "Brightvale Components (fictional)",
        "component": "BV-220 ceramic capacitor, 10 uF",
        "substances": [
            s(n, c, v, float(v), "%", row(n, c, v, "% w/w")) for n, c, v in [
                ("Lead (Pb)", "7439-92-1", "0.004"), ("Mercury (Hg)", "7439-97-6", "0.0002"),
                ("Cadmium (Cd)", "7440-43-9", "0.0005"), ("Hexavalent chromium (Cr VI)", "18540-29-9", "0.001"),
                ("PBB", "59536-65-1", "0.0001"), ("PBDE", "1163-19-5", "0.0001"), ("DEHP", "117-81-7", "0.003"),
                ("BBP", "85-68-7", "0.002"), ("DBP", "84-74-2", "0.002"), ("DIBP", "84-69-5", "0.001"),
            ]
        ],
    },
    "02_kestrel_lead_over.pdf": {
        "supplier": "Kestrel Microparts (fictional)",
        "component": "KM-48 tin-plated terminal block",
        "substances": [
            pdf("Lead (Pb)", "7439-92-1", "0.35"), pdf("Mercury (Hg)", "7439-97-6", "0.0003"),
            pdf("Cadmium (Cd)", "7440-43-9", "0.0008"), pdf("Hexavalent chromium", "18540-29-9", "0.002"),
            pdf("PBDE", "1163-19-5", "0.0002"), pdf("DEHP", "117-81-7", "0.004"),
        ],
    },
    "03_orrin_no_cas.txt": {
        "supplier": "Orrin Polymer Works (fictional)",
        "component": "OPW-7 nylon cable gland",
        "substances": [
            s("Lead", None, "0.012%", 0.012, "%", "Lead was measured at 0.012% by weight and cadmium at 0.002%."),
            s("cadmium", None, "0.002%", 0.002, "%", "Lead was measured at 0.012% by weight and cadmium at 0.002%."),
            s("Mercury", None, "0.001%", 0.001, "%", "Mercury came in at 0.001%, and the plasticiser DEHP was found at 0.02%."),
            s("DEHP", None, "0.02%", 0.02, "%", "Mercury came in at 0.001%, and the plasticiser DEHP was found at 0.02%."),
        ],
    },
    "04_tamsin_vague.txt": {
        "supplier": "Tamsin Cable Co. (fictional)",
        "component": "TC-90 PVC jacketed cable",
        "substances": [
            s("Lead", "7439-92-1", "trace amounts", None, None, "- Lead (CAS 7439-92-1): trace amounts"),
            s("Mercury", "7439-97-6", "N/A", None, None, "- Mercury (CAS 7439-97-6): N/A"),
            s("Cadmium", "7440-43-9", "<LOD", None, None, "- Cadmium (CAS 7440-43-9): <LOD"),
            s("Hexavalent chromium", "18540-29-9", "0.003 %", 0.003, "%", "- Hexavalent chromium (CAS 18540-29-9): 0.003 %"),
            s("DEHP", "117-81-7", "0.01 %", 0.01, "%", "- DEHP (CAS 117-81-7): 0.01 %"),
        ],
    },
    "05_veldra_ppm.csv": {
        "supplier": "Veldra Electronics (fictional)",
        "component": "VE-5 PCB assembly",
        "substances": [
            s("lead", "7439-92-1", "450", 450, "ppm", "7439-92-1, lead ,450,solder joints"),
            s("Cadmium", "7440-43-9", "20", 20, "ppm", "7440-43-9,Cadmium, 20 ,"),
            s("mercury", "7439-97-6", "5", 5, "ppm", "7439-97-6 ,mercury,5,"),
            s("Chromium VI", "18540-29-9", "30", 30, "ppm", '18540-29-9,"Chromium VI",  30,plating'),
            s("DEHP", "117-81-7", "120", 120, "ppm", "117-81-7,DEHP ,  120 ,"),
            s("DBP", "84-74-2", "60", 60, "ppm", "84-74-2,DBP,60,"),
        ],
    },
    "06_corvan_cadmium_near.pdf": {
        "supplier": "Corvan Plastics (fictional)",
        "component": "CP-3 yellow ABS housing",
        "substances": [
            pdf("Lead (Pb)", "7439-92-1", "0.006"), pdf("Mercury (Hg)", "7439-97-6", "0.0004"),
            pdf("Cadmium (Cd)", "7440-43-9", "0.009"), pdf("Hexavalent chromium", "18540-29-9", "0.001"),
            pdf("PBB", "59536-65-1", "0.0003"), pdf("PBDE", "1163-19-5", "0.0005"),
        ],
    },
    "07_ashgrove_exemption.md": {
        "supplier": "Ashgrove Metals (fictional)",
        "component": "AM-12 brass connector pin",
        "substances": [
            s("Lead", "7439-92-1", "3.2", 3.2, "%",
              row("Lead", "7439-92-1", "3.2", "Exemption claimed: Annex III 6(c), lead in copper alloy up to 4% by weight"),
              exemption="Exemption claimed: Annex III 6(c), lead in copper alloy up to 4% by weight"),
            s("Cadmium", "7440-43-9", "0.001", 0.001, "%", "| Cadmium | 7440-43-9 | 0.001 |"),
            s("Mercury", "7439-97-6", "0.0005", 0.0005, "%", "| Mercury | 7439-97-6 | 0.0005 |"),
            s("Hexavalent chromium", "18540-29-9", "0.002", 0.002, "%", "| Hexavalent chromium | 18540-29-9 | 0.002 |"),
        ],
    },
    "08_pellham_blank_conc.xlsx": {
        "supplier": "Pellham Fasteners (fictional)",
        "component": "PF-16 zinc-plated steel screw, M4",
        "substances": [
            s(n, c, v, float(v) if v else None, "%" if v else None,
              f"Row {r}: {n} | {c} | {v or '(blank)'} | % w/w")
            for r, n, c, v in [
                (7, "Lead", "7439-92-1", "0.002"), (8, "Mercury", "7439-97-6", "0.0001"),
                (9, "Cadmium", "7440-43-9", None), (10, "Hexavalent chromium", "18540-29-9", None),
                (11, "PBB", "59536-65-1", "0.0001"), (12, "PBDE", "1163-19-5", "0.0001"),
                (13, "DEHP", "117-81-7", "0.001"), (14, "BBP", "85-68-7", "0.001"),
                (15, "DBP", "84-74-2", "0.001"), (16, "DIBP", "84-69-5", "0.001"),
            ]
        ],
    },
}

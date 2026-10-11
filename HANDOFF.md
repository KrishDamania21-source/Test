# RADAR handoff

This file lets a new session continue the RADAR project exactly where the
last one stopped. Read it in full before changing anything.

**Branch:** `claude/radar-compliance-agent-pvwlmz` (repo `KrishDamania21-source/Test`, public)
**Status:** Steps 1 to 4 done. Step 5 is next. 151 tests pass, with network blocked.
**Working style:** after each step, stop, summarize, and wait for the owner (Krish) to say "next".

---

## 1. Core principles (never break these)

1. **The model reads, the code judges.** Claude extracts data from documents and writes
   explanations. Every compliance decision (limits, thresholds, findings, risk rating) is
   deterministic Python. Claude never decides whether something is compliant.
2. **Rules come only from files a human provides and approves.** The model never generates or
   edits rules or synonyms. New rule packs are staged, diffed and approved by a person.
3. **Synthetic data only.** No real company names, suppliers or documents. Every sample says
   "Synthetic sample for the RADAR prototype. Fictional supplier."
4. **The API key comes from the environment only** (`ANTHROPIC_API_KEY`). There is no `.env` file
   in the cloud workspace. Never print, log or read its value. `.env` and `.env.*` are in
   `.gitignore` and have never been committed.
5. **Cost rules.** Keep sample documents short. Cache every extraction to disk keyed by file
   hash, so re-running the UI never re-calls the API for an unchanged document.
6. **Never change the answer key to match model output.** `samples/answer_key.json` is ground truth.
7. Every result comes from `claude-sonnet-5-5`. No fallback model.

---

## 2. The original brief (as given)

> Build RADAR: a prototype supplier compliance agent that checks supplier material declarations
> against EU RoHS restrictions. This is a personal portfolio project. Use SYNTHETIC data only.
> No real company names, suppliers, or documents.
>
> STACK: Python, Streamlit, Anthropic API (claude-sonnet-5-5). API key comes from .env as
> ANTHROPIC_API_KEY. I'll create .env myself. Never print or read its value. Add .env to .gitignore.
> *(Later changed: the key comes from the environment only; no .env in the cloud workspace.)*
>
> CORE PRINCIPLE: the model reads, the code judges. Claude extracts data from documents and
> writes explanations. All compliance decisions (limits, thresholds, risk rating) are
> deterministic Python. Claude never decides if something is compliant.
>
> COST RULES: keep sample documents short. Cache every extraction result to disk keyed by a file
> hash, so re-running the UI never re-calls the API for an unchanged document.
>
> After each step, stop, summarize, and wait for me to say "next".
>
> **STEP 1: Rules data.** Create rohs_rules.py with the 10 RoHS restricted substances (Directive
> 2011/65/EU Annex II as amended by 2015/863): name, CAS number(s), and max concentration in
> homogeneous material (0.1% by weight, except cadmium at 0.01%). PBB and PBDE are substance
> groups, so match them by group name as well as common CAS numbers. Cite the source in a
> comment. List all 10 back to me in a table so I can verify them against the official text.
> *(Later replaced by versioned rule pack files; see section 4.)*
>
> **STEP 2: Synthetic declarations + answer key.** Create 7 short supplier declarations in
> samples/, in varied formats (clean table, prose paragraph, messy CSV, and 2 as PDFs), from
> fictional suppliers. Each has one deliberate characteristic: 1 fully compliant; 2 lead over the
> limit; 3 substances listed without CAS numbers; 4 vague language ("trace amounts", "N/A",
> "<LOD"); 5 concentrations in ppm instead of %; 6 cadmium just under its stricter 0.01% limit;
> 7 an exemption claimed for lead in a specific use. Create samples/answer_key.json with the
> expected findings and risk rating for each. This is our ground truth.
>
> **STEP 3: Extraction.** Claude extracts structured JSON per declaration: supplier, component,
> and a list of substances with name, CAS, concentration value, unit, and the original text it
> came from. Validate against a schema. If the JSON is invalid, retry once with the validation
> error. Unit conversion (ppm to %) happens in Python, not in the prompt.
>
> **STEP 4: Rules engine (no API calls).** Deterministic checks producing findings: OVER_LIMIT
> (above the substance limit); NEAR_LIMIT (within 80% of the limit); MISSING_CAS,
> MISSING_CONCENTRATION; UNVERIFIABLE (vague values that can't be compared to a limit);
> EXEMPTION_CLAIMED (never auto-accept, always route to human review). Risk rating: any
> OVER_LIMIT = High. Any MISSING, UNVERIFIABLE or EXEMPTION_CLAIMED = Medium / Needs review.
> NEAR_LIMIT only = Low with a watch flag. Nothing = Low. Write unit tests for every rule. They
> must run with no network.
>
> **STEP 5: Agent outputs.** From the findings only (no new facts), Claude writes: a 2 to 3
> sentence plain-English risk explanation; a draft follow-up email to the supplier listing
> exactly what's missing or non-compliant and what's needed. Show a live agent trace while
> processing: Reading document > Extracting substances > Checking against RoHS > Rating risk >
> Drafting follow-up.
>
> **STEP 6: UI (Streamlit).** Header "RADAR" plus one line: "Reads supplier declarations, checks
> them against EU RoHS limits, and drafts the follow-up." "Run all samples" button, plus file
> upload. Portfolio view: all suppliers ranked by risk, colour-coded. Detail view: agent trace,
> findings table with the source text each finding came from, the explanation, and the draft
> email. Footer note: "Prototype on synthetic data. Not legal advice." Clean, professional theme
> via .streamlit/config.toml, no emoji.
>
> **STEP 7: Evaluation.** Run all samples and compare against answer_key.json. Report accuracy per
> sample and per finding type. Fix extraction prompts for any misses, but never change the answer
> key to match the output.
>
> **STEP 8: Pre-recording check.** Clear the cache, run everything fresh, confirm all tests pass
> and the eval matches, and give me a checklist for recording.

---

## 3. Completed work

### Step 1: Rules data (later moved to rule pack files)
The 10 Annex II substances, with CAS numbers and limits, were listed in a table for Krish to
check. `rohs_rules.py` was later deleted when rules moved into data files (section 4, item 2).

| Substance | CAS | Limit |
|---|---|---|
| Lead | 7439-92-1 | 0.1% |
| Mercury | 7439-97-6 | 0.1% |
| Cadmium | 7440-43-9 | **0.01%** |
| Hexavalent chromium | 18540-29-9 | 0.1% |
| Polybrominated biphenyls (PBB), group | 59536-65-1; 36355-01-8; 27858-07-7; 13654-09-6 | 0.1% |
| Polybrominated diphenyl ethers (PBDE), group | 1163-19-5; 32534-81-9; 32536-52-0; 5436-43-1; 60348-60-9 | 0.1% |
| Bis(2-ethylhexyl) phthalate (DEHP) | 117-81-7 | 0.1% |
| Butyl benzyl phthalate (BBP) | 85-68-7 | 0.1% |
| Dibutyl phthalate (DBP) | 84-74-2 | 0.1% |
| Diisobutyl phthalate (DIBP) | 84-69-5 | 0.1% |

Notes: Annex II itself has no CAS numbers; they were added for matching. Group CAS lists are not
exhaustive, so groups also match by name and alias. Every CAS number passes its check digit.

### Step 2: Synthetic declarations and answer key
Files in `samples/`. PDFs come from `scripts/make_sample_pdfs.py` and the Excel file from
`scripts/make_sample_xlsx.py`; both produce byte-identical output on every run so the hash cache
stays valid.

| # | File | Format | Supplier (fictional) | Characteristic | Expected rated findings | Risk |
|---|---|---|---|---|---|---|
| 1 | `01_brightvale_compliant.md` | Clean table | Brightvale Components | All 10, well under limits | none | Low |
| 2 | `02_kestrel_lead_over.pdf` | PDF table | Kestrel Microparts | Lead 0.35% | OVER_LIMIT lead | High |
| 3 | `03_orrin_no_cas.txt` | Prose | Orrin Polymer Works | 4 substances, no CAS | MISSING_CAS ×4 | Medium |
| 4 | `04_tamsin_vague.txt` | Email with bullets | Tamsin Cable Co. | trace amounts, N/A, <LOD | UNVERIFIABLE ×3 (lead, mercury, cadmium) | Medium |
| 5 | `05_veldra_ppm.csv` | Messy CSV | Veldra Electronics | Values in ppm (lead 450 ppm = 0.045%) | none | Low |
| 6 | `06_corvan_cadmium_near.pdf` | PDF table | Corvan Plastics | Cadmium 0.009% vs 0.01% | NEAR_LIMIT cadmium | Low + watch |
| 7 | `07_ashgrove_exemption.md` | Table with remarks | Ashgrove Metals | Lead 3.2% in brass, Annex III 6(c) claimed | EXEMPTION_CLAIMED lead | Medium |
| 8 | `08_pellham_blank_conc.xlsx` | Excel | Pellham Fasteners | Cadmium and Cr(VI) have CAS but blank concentration cells | MISSING_CONCENTRATION ×2 | Medium |

`samples/answer_key.json` also lists the NOT_DECLARED (Info) findings for each sample, and uses
substance names exactly as they appear in the active rule pack.

### Step 3: Extraction
* `radar/documents.py`: reads .md, .txt, .csv, .pdf (pypdf) and .xlsx (openpyxl). Spreadsheet rows
  become `Row N: a | b | c`, and empty table cells show as `(blank)`.
* `radar/extract.py`: calls `claude-sonnet-5-5` via `client.messages.create` with a JSON schema in
  `output_config.format`, effort `medium`. The model returns supplier, component and, per
  substance: name, cas, concentration_raw (exactly as written), concentration_value (the plain
  number, never converted), unit (`%`, `ppm`, `mg/kg`, `unknown` or null), exemption_claimed,
  exemption_text, source_text. Python validates schema plus consistency (a reported number must
  appear in the raw value, unit rules, exemption text present). On failure it retries once with
  the errors; a second failure raises `ExtractionError` and nothing is cached. A refusal, a
  `max_tokens` stop, or a response from any other model is an `ExtractionError`.
* `radar/units.py`: ppm and mg/kg to % in Python. Status: numeric, vague, missing, unknown_unit.
  Qualifiers `<`, `<=`, `>`, `>=`, `~` are parsed from the raw text.
* `radar/cache.py`: `.cache/extractions/<sha256>__<EXTRACTION_VERSION>__<model>.json`
  (gitignored). Stores the extraction and the document text, so findings can be re-screened later
  with no API call. Bump `EXTRACTION_VERSION` whenever the prompt or schema changes.
* `scripts/extract_samples.py`: runs extraction on all samples and prints what the model read.
  **Not yet run against the live API**: the key was not set in the first session.

### Step 4: Rules engine (`radar/engine.py`, no API calls)
| Finding | Severity | When |
|---|---|---|
| OVER_LIMIT | High | value above the limit (exactly at the limit is allowed) |
| UNTRACEABLE_EXTRACTION | Medium | source text not found in the document |
| MISSING_CAS | Medium | no CAS number (the value is still checked) |
| MISSING_CONCENTRATION | Medium | no value at all |
| UNVERIFIABLE | Medium | vague text, number with no unit, or a bound that can't be compared |
| EXEMPTION_CLAIMED | Medium | any exemption claim, checked before the value; shows value vs base limit |
| NEAR_LIMIT | Low + watch | at or above 80% of the limit |
| NOT_DECLARED | Info | restricted substance not mentioned at all; never changes the rating |

Rating: any OVER_LIMIT = High; else any Medium finding = Medium / Needs review; else NEAR_LIMIT
= Low with watch flag; else Low. Each finding carries the exact `Rule` it was judged against
(`rule_id` such as `eu_rohs@20261010T000000Z#lead`), the value in %, the raw text and the source
text. Each result records the pack and synonyms versions used.

### Rules as data (done before Step 3, at Krish's request)
* `rules/packs/<pack_id>/<version_id>.csv`: one full pack per version. Columns exactly:
  pack_id, regulation_name, short_code, jurisdiction, obligation_type (restriction | disclosure |
  warning), substance, cas_numbers (semicolon separated), limit_pct (nullable), legal_reference,
  source_url, version_date, last_verified, verified_by, is_subset.
* `rules/synonyms/<version_id>.json`: aliases per substance, versioned the same way.
* `rules/active.json`: `{"packs": {"eu_rohs": "20261010T000000Z"}, "synonyms": "20261010T000000Z"}`.
* `rules/history.json`: seeding, approvals, rollbacks.
* `radar/rules.py`: strict loader. Any bad row rejects the whole file with row-level errors
  (header must match exactly; obligation enum; limit required for restriction and disclosure and
  empty for warning; CAS format and check digit; no duplicate substance or CAS; https URLs; ISO
  dates with last_verified not before version_date; is_subset true/false; one pack per file).
  Synonyms are rejected if an alias maps to two substances. `load_active_rules()` and
  `load_active_synonyms()` are the only way the rest of RADAR gets rules.

### Tests
`python -m pytest -q`: 151 pass. `conftest.py` blocks all sockets, so no test can reach the
network. Extraction tests use `tests/fakes.py` (a fake Anthropic client).
`tests/perfect_extractions.py` holds hand-written correct extractions of all 8 samples; run
through the engine they match the answer key exactly. Engine tests were checked by deliberately
breaking two rules and confirming failures.

---

## 4. Decisions made by Krish

1. **Exemption claims rate Medium, not High.** Whether an exemption is valid is a human judgment.
   EXEMPTION_CLAIMED replaces OVER_LIMIT for that substance, and the finding still shows the value
   against the base limit.
2. **"N/A" is UNVERIFIABLE**, not MISSING_CONCENTRATION. MISSING_CONCENTRATION is only for no
   value at all.
3. **NOT_DECLARED added**, severity Info, for restricted substances a declaration doesn't address.
   It never changes the rating, but appears in findings and in the follow-up email as "please
   confirm the following substances". A substance listed with a vague or blank value counts as
   addressed.
4. **Rules as data**, versioned, strictly validated, single source of truth for the engine, the
   Rules tab and every citation (section 3).
5. **Synonyms affect results**, so they are versioned alongside rule packs, shown on the Rules tab
   next to each substance, and go through the same human sign-off and staged approval as rules.
6. **Sample 08**: Excel, two substances with CAS but blank concentration cells,
   MISSING_CONCENTRATION, Medium.
7. **No refusal fallback.** Every result must come from `claude-sonnet-5-5`. A refusal is an
   extraction error.
8. **UNTRACEABLE_EXTRACTION**: if an extracted substance's source text can't be found in the
   document, raise this (Medium / Needs review), shown prominently in the UI. A substance with an
   untraceable source never produces a clean pass or an OVER_LIMIT on its own; it always routes to
   a human. (Built in Step 4, with a test where a fake extraction invents a substance.)
9. **`verified_by` stays pending.** Krish will verify the EU RoHS pack (and synonyms) against the
   official text himself.
10. Engine choices accepted by proceeding: `<X` is judged on its upper bound (UNVERIFIABLE if X is
    above the limit); `>X` is OVER_LIMIT if X is at or above the limit, else UNVERIFIABLE; an
    untraceable entry counts as addressing its substance (no duplicate NOT_DECLARED).

---

## 5. Open items: decided, not built yet

Build these at the start of Step 5 (before the agent outputs), with tests, and add any affected
expectations to the answer key only if a sample is designed for them.

1. **NO_SUBSTANCES_ADDRESSED.** Today a declaration that addresses none of the active restricted
   substances rates Low, which looks like a clean pass with nothing checked. New finding:
   Medium / Needs review when the declaration addresses none of the active restricted substances.
   (The proposal also mentioned a possible threshold such as "misses more than half"; confirm with
   Krish whether he wants only the "none" case.)
2. **IDENTITY_CONFLICT.** Today, if a row's name and CAS point to different restricted substances
   (for example "Lead" with mercury's CAS), the CAS wins silently. New finding: Medium / Needs
   review; do not judge that entry's value against either limit on its own. Confirm severity with
   Krish when presenting.
3. **`verified_by` pending** on `rules/packs/eu_rohs/20261010T000000Z.csv` and
   `rules/synonyms/20261010T000000Z.json` ("Drafted by Claude Code; pending human
   verification"). Do not change it; Krish signs off himself.

---

## 6. Remaining plan

### Step 5: Agent outputs
* From the findings only (no new facts), Claude writes a 2 to 3 sentence plain-English risk
  explanation and a draft follow-up email to the supplier listing exactly what's missing or
  non-compliant and what's needed. The email includes a "please confirm the following
  substances" list for NOT_DECLARED.
* Live agent trace: Reading document > Extracting substances > Checking against RoHS > Rating
  risk > Drafting follow-up.
* Guard against new facts: pass only the findings (and supplier/component) to the model, and
  check in Python that every substance and number in the output appears in the findings.
* Cache outputs by a hash of the findings, so unchanged results never re-call the API.

### Step 6: UI (Streamlit)
* Header "RADAR" and the line "Reads supplier declarations, checks them against EU RoHS limits,
  and drafts the follow-up."
* "Run all samples" button and file upload, including **multi-file declaration upload** (several
  declaration files in one go, each screened separately; confirm any extra details with Krish).
* Portfolio view: all suppliers ranked by risk, colour-coded.
* Detail view: agent trace, findings table with source text, explanation, draft email.
  UNTRACEABLE_EXTRACTION shown prominently.
* **Rules tab**: every active pack and row, with citation, version date, last verified, and a
  "Sample subset" label where is_subset is true. Aliases shown next to each substance.
* **Citations**: every finding shows and links to the exact rule row it was judged against.
* **Upload a rule pack** as CSV or JSON, with a "Download template" button. Strict validation:
  reject the whole file with row-level errors, never partially load. Synonyms upload follows the
  same rules.
* **Refresh flow**: a new pack (or synonyms version) is STAGED, never applied immediately. Show a
  diff (added, removed, limits changed, aliases changed). Show impact by re-screening all
  processed declarations against the staged rules using cached extractions (no API calls),
  listing which suppliers change rating and why. "Approve and apply" saves a new timestamped
  version, updates ratings, and drafts emails for newly affected suppliers. Version history with
  rollback.
* The model never generates or edits rules. Rules only come from files a human provides and
  approves.
* Create `rules/demo_update_SIMULATED.csv`: a fictional update that tightens one limit and adds
  one substance. Filename, version label and citation must all say SIMULATED.
* Footer: "Prototype on synthetic data. Not legal advice." Clean, professional theme via
  `.streamlit/config.toml`, no emoji.

### Step 7: Evaluation
Run all 8 samples live and compare against `answer_key.json`. Report accuracy per sample and per
finding type. Fix extraction prompts for misses (bump `EXTRACTION_VERSION`), never the answer key.

### Market matrix (only after the Step 7 eval passes for EU RoHS)
Two small, clearly labelled sample-subset packs (`is_subset = true`):
* EU REACH SVHC (obligation_type disclosure): above 0.1% triggers a duty to inform, not a ban.
  Result: "Disclosure required".
* California Prop 65 (obligation_type warning): presence of a listed chemical triggers "Warning
  review", since Prop 65 depends on exposure, not a fixed limit.
* Per-supplier matrix: suppliers as rows, markets as columns, required action in each cell.
* **Before building, list the substances and values chosen so Krish can verify them.**

### Step 8: Pre-recording check
* Clear the cache, run everything fresh, confirm all tests pass and the eval matches.
* Give Krish a checklist for recording.
* README section **"Run locally"** with exact Mac steps: clone, create a venv, install
  requirements, set `ANTHROPIC_API_KEY` with `export` (not a file), run Streamlit. Krish records
  the demo on his own machine.

---

## 7. First actions for the new session

1. `git checkout claude/radar-compliance-agent-pvwlmz && git pull`.
2. `pip install -r requirements.txt` and `python -m pytest -q` (expect 151 passing).
3. Check the key is set without printing it:
   `[ -n "$ANTHROPIC_API_KEY" ] && echo set || echo missing`.
4. If set, run `python scripts/extract_samples.py` and show Krish what the model extracted for all
   8 samples (this is the first live run).
5. Wait for "next" before starting Step 5.

## 8. Repository map
```
radar/         rules.py, documents.py, units.py, extract.py, cache.py, engine.py
rules/         active.json, history.json, packs/eu_rohs/*.csv, synonyms/*.json
samples/       8 declarations + answer_key.json
scripts/       make_sample_pdfs.py, make_sample_xlsx.py, extract_samples.py
tests/         test_rules, test_documents, test_units, test_extract, test_engine,
               fakes.py, perfect_extractions.py
conftest.py    blocks network in every test
```
Commits and PR text must not include any model identifier. Commit messages end with the
session's attribution lines.

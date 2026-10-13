"""RADAR — supplier compliance agent (Streamlit UI)."""
from __future__ import annotations

import html as _html
import re
import uuid
from pathlib import Path

import streamlit as st

from radar.agent import NO_EMAIL, AgentError, draft
from radar.documents import load_document
from radar.engine import (
    HIGH,
    IDENTITY_CONFLICT,
    LOW,
    MEDIUM,
    NOT_DECLARED,
    UNTRACEABLE_EXTRACTION,
    ScreeningResult,
    screen,
)
from radar.extract import ExtractionError, extract, make_client
from radar.rules import Rule, SynonymSet, load_active_rules, load_active_synonyms

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="RADAR", layout="wide",
                   initial_sidebar_state="collapsed")

SAMPLES_DIR = Path(__file__).parent / "samples"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

# ── constants ─────────────────────────────────────────────────────────────────
_RISK_COLOR  = {HIGH: "#dc2626", MEDIUM: "#d97706", LOW: "#16a34a"}
_SEV_COLOR   = {"High": "#dc2626", "Medium": "#d97706",
                "Low": "#16a34a", "Info": "#6b7280"}
_PROMINENT   = {UNTRACEABLE_EXTRACTION, IDENTITY_CONFLICT}
_TRACE_STEPS = [
    "Reading document",
    "Extracting substances",
    "Checking against EU RoHS",
    "Rating risk",
    "Drafting follow-up",
]

# ── helpers ───────────────────────────────────────────────────────────────────
_FICTIONAL_RE = re.compile(r'\s*\(fictional\)\s*', re.IGNORECASE)

def _clean(name: str | None) -> str | None:
    return _FICTIONAL_RE.sub('', name).strip() if name else name

def _sort_key(r: dict) -> tuple:
    if r.get("error"):
        return (99, "")
    rating, watch = r.get("rating", LOW), r.get("watch", False)
    order = 0 if rating == HIGH else 1 if rating == MEDIUM else 2 if watch else 3
    return (order, (_clean(r.get("supplier")) or r["filename"]).lower())


# ── cached resources ──────────────────────────────────────────────────────────
@st.cache_resource
def _load_rules():
    return load_active_rules()

@st.cache_resource
def _load_synonyms():
    return load_active_synonyms()

@st.cache_resource
def _load_client():
    try:
        return make_client(), None
    except Exception as exc:
        return None, str(exc)

RULES        = _load_rules()
SYNONYMS     = _load_synonyms()
CLIENT, _KEY_ERROR = _load_client()

# ── session state ─────────────────────────────────────────────────────────────
if "results" not in st.session_state:
    st.session_state.results: list[dict] = []
if "selected_id" not in st.session_state:
    st.session_state.selected_id: str | None = None


# ── pipeline ──────────────────────────────────────────────────────────────────
def _process(filename: str, doc_bytes: bytes, status) -> dict:
    result: dict = {"id": str(uuid.uuid4()), "filename": filename,
                    "trace": [], "error": None}

    def step(label: str) -> None:
        result["trace"].append(label)
        status.write(label)

    try:
        step("Reading document")
        doc = load_document(filename, doc_bytes)

        step("Extracting substances")
        ext = extract(doc, client=CLIENT)

        step("Checking against EU RoHS")
        sr: ScreeningResult = screen(ext.data, ext.document_text, RULES, SYNONYMS)

        step("Rating risk")
        agent_out = draft(sr, RULES, client=CLIENT, trace_fn=step)

    except ExtractionError as exc:
        result["error"] = f"Extraction failed: {exc}"
        return result
    except AgentError as exc:
        result["error"] = f"Agent failed: {exc}"
        return result
    except Exception as exc:
        result["error"] = f"Unexpected error: {exc}"
        return result

    result.update({
        "supplier":    _clean(sr.supplier),
        "component":   sr.component,
        "rating":      sr.rating,
        "label":       sr.label,
        "watch":       sr.watch,
        "findings":    sr.findings,
        "explanation": agent_out.explanation,
        "email":       agent_out.email,
        "agent_error": agent_out.error,
    })
    return result


# ── rendering helpers ─────────────────────────────────────────────────────────
def _badge(label: str, bg: str, text_color: str = "#fff",
           size: str = "0.85em") -> str:
    return (f'<span style="background:{bg};color:{text_color};padding:2px 9px;'
            f'border-radius:4px;font-size:{size};font-weight:600;'
            f'display:inline-block">{_html.escape(label)}</span>')


def _render_trace(trace: list[str]) -> None:
    lines = []
    for step_name in _TRACE_STEPS:
        done = any(ev.startswith(step_name) for ev in trace)
        detail = next((ev for ev in trace if ev.startswith(step_name)), step_name)
        if done:
            lines.append(
                f'<div style="font-size:0.82em;line-height:1.9">'
                f'<span style="color:#16a34a;font-weight:700">&#10003;</span>'
                f'&nbsp;{_html.escape(detail)}</div>'
            )
        else:
            lines.append(
                f'<div style="font-size:0.82em;line-height:1.9;color:#9ca3af">'
                f'<span style="display:inline-block;width:0.9em">&mdash;</span>'
                f'&nbsp;{_html.escape(step_name)}</div>'
            )
    st.markdown("".join(lines), unsafe_allow_html=True)


def _render_findings_table(findings: list) -> None:
    non_nd = [f for f in findings if f.type != NOT_DECLARED]
    if not non_nd:
        st.caption("No actionable findings.")
        return

    header = (
        '<table style="border-collapse:collapse;width:100%;font-size:0.87em">'
        '<thead><tr style="background:#f1f5f9;text-align:left">'
        '<th style="padding:6px 10px;white-space:nowrap">Type</th>'
        '<th style="padding:6px 10px;white-space:nowrap">Sev.</th>'
        '<th style="padding:6px 10px">Substance</th>'
        '<th style="padding:6px 10px;white-space:nowrap">Value</th>'
        '<th style="padding:6px 10px;white-space:nowrap">Limit</th>'
        '<th style="padding:6px 10px">Source text in declaration</th>'
        '<th style="padding:6px 10px">Rule citation</th>'
        '</tr></thead><tbody>'
    )

    rows = ""
    for f in non_nd:
        highlight = f.type in _PROMINENT
        row_bg = "#fefce8" if highlight else ""
        sev_c  = _SEV_COLOR.get(f.severity, "#6b7280")

        type_html = (
            f'<span style="background:{sev_c};color:#fff;padding:1px 6px;'
            f'border-radius:3px;font-size:0.78em;font-weight:600;'
            f'white-space:nowrap">{_html.escape(f.type)}</span>'
        )
        value_s = f"{f.value_pct:.4g}%" if f.value_pct is not None else "&mdash;"
        limit_s = f"{f.limit_pct:.4g}%" if f.limit_pct is not None else "&mdash;"

        source = _html.escape((f.source_text or "")[:200])

        # Citation: legal reference + rule_id
        cite = ""
        if f.rule:
            ref = _html.escape(f.rule.legal_reference or "")
            rid = _html.escape(f.rule_id or "")
            url = f.rule.source_url or ""
            if ref and url:
                cite = (f'<a href="{_html.escape(url)}" target="_blank" '
                        f'style="color:#1e40af;text-decoration:none">{ref}</a>')
            elif ref:
                cite = ref
            if rid:
                cite += (f'<div style="font-size:0.75em;color:#9ca3af;'
                         f'word-break:break-all;margin-top:2px">{rid}</div>')

        rows += (
            f'<tr style="background:{row_bg};vertical-align:top;'
            f'border-top:1px solid #e2e8f0">'
            f'<td style="padding:6px 10px">{type_html}</td>'
            f'<td style="padding:6px 10px;white-space:nowrap;color:{sev_c};'
            f'font-weight:600;font-size:0.85em">{_html.escape(f.severity)}</td>'
            f'<td style="padding:6px 10px;font-weight:500;min-width:140px">'
            f'{_html.escape(f.substance)}</td>'
            f'<td style="padding:6px 10px;white-space:nowrap">{value_s}</td>'
            f'<td style="padding:6px 10px;white-space:nowrap">{limit_s}</td>'
            f'<td style="padding:6px 10px;font-size:0.82em;color:#475569;'
            f'word-break:break-word;min-width:160px">{source}</td>'
            f'<td style="padding:6px 10px;font-size:0.82em;min-width:160px">{cite}</td>'
            f'</tr>'
        )

    st.markdown(
        f'<div style="overflow-x:auto;width:100%">{header}{rows}'
        f'</tbody></table></div>',
        unsafe_allow_html=True,
    )


def _render_email(email_text: str, key_suffix: str) -> None:
    if email_text == NO_EMAIL:
        st.caption(NO_EMAIL)
        return

    # Parse subject and DRAFT header
    lines = email_text.splitlines()
    subject = ""
    body_lines: list[str] = []
    found_subject = False
    skip_draft = True  # strip leading DRAFT line from body display

    for line in lines:
        stripped = line.strip()
        if skip_draft and stripped == "DRAFT \u2013 for review before sending":
            skip_draft = False
            continue
        skip_draft = False
        if not found_subject and stripped.startswith("Subject:"):
            subject = stripped[len("Subject:"):].strip()
            found_subject = True
        else:
            body_lines.append(line)

    # Strip leading blank lines from body
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)

    st.caption("DRAFT \u2013 for review before sending")
    if subject:
        st.markdown(f"**Subject:** {subject}")
        st.markdown("")

    body_text = "\n".join(body_lines)
    st.code(body_text, language=None, wrap_lines=True)

    c1, c2, _ = st.columns([1, 1, 3])
    with c1:
        st.download_button(
            "Download .txt",
            data=email_text,
            file_name=f"{key_suffix}_followup.txt",
            mime="text/plain",
            key=f"dl_{key_suffix}",
        )


def _render_detail(r: dict) -> None:
    supplier = r.get("supplier") or r["filename"]
    st.markdown(f"### {supplier}")
    if r.get("component"):
        st.caption(r["component"])
    if r.get("label"):
        st.markdown(
            _badge(r["label"], _RISK_COLOR.get(r["rating"], "#6b7280")),
            unsafe_allow_html=True,
        )
        st.markdown("")

    if r.get("error"):
        st.error(r["error"])
        return

    # Trace
    with st.container():
        st.markdown("**Processing trace**")
        _render_trace(r["trace"])

    findings: list = r.get("findings", [])

    # Prominent warnings
    critical = [f for f in findings if f.type in _PROMINENT]
    if critical:
        st.markdown("---")
        for f in critical:
            icon = "IDENTITY CONFLICT" if f.type == IDENTITY_CONFLICT \
                   else "UNTRACEABLE EXTRACTION"
            st.warning(f"**{icon}** \u2014 {f.substance}: {f.message}")

    # Findings
    st.markdown("---")
    st.markdown("**Findings**")
    _render_findings_table(findings)

    nd = [f for f in findings if f.type == NOT_DECLARED]
    if nd:
        with st.expander(f"Not addressed in declaration ({len(nd)} substances)"):
            for f in nd:
                st.caption(f"\u2013 {f.substance}")

    # Explanation
    st.markdown("---")
    st.markdown("**Explanation**")
    if r.get("agent_error"):
        st.error(r["agent_error"])
    elif r.get("explanation"):
        st.info(r["explanation"])

    # Email
    st.markdown("---")
    st.markdown("**Draft follow-up email**")
    safe_key = re.sub(r'[^\w]', '_', (r.get("supplier") or r["filename"])[:40])
    _render_email(r.get("email", NO_EMAIL), safe_key)


def _render_rules_tab() -> None:
    # Group rules by pack_id
    packs: dict[str, list[Rule]] = {}
    for rule in RULES:
        packs.setdefault(rule.pack_id, []).append(rule)

    for pack_id, pack_rules in sorted(packs.items()):
        first = pack_rules[0]
        subset_note = "  (sample subset)" if any(r.is_subset for r in pack_rules) else ""
        st.markdown(f"**{first.regulation_name}**{subset_note}")
        st.caption(
            f"Pack: {pack_id}\u2002|\u2002"
            f"Version: {first.version_id}\u2002|\u2002"
            f"Jurisdiction: {first.jurisdiction}\u2002|\u2002"
            f"Last verified: {first.last_verified or 'pending'}\u2002|\u2002"
            f"Verified by: {first.verified_by or 'pending'}"
        )

        header = (
            '<table style="border-collapse:collapse;width:100%;font-size:0.87em">'
            '<thead><tr style="background:#f1f5f9;text-align:left">'
            '<th style="padding:6px 10px">Substance</th>'
            '<th style="padding:6px 10px">CAS number(s)</th>'
            '<th style="padding:6px 10px;white-space:nowrap">Limit</th>'
            '<th style="padding:6px 10px">Obligation</th>'
            '<th style="padding:6px 10px">Legal reference</th>'
            '<th style="padding:6px 10px">Aliases</th>'
            '<th style="padding:6px 10px">Citation</th>'
            '</tr></thead><tbody>'
        )
        rows = ""
        for rule in pack_rules:
            aliases = SYNONYMS.aliases_for(rule.substance)
            alias_str = _html.escape(", ".join(aliases[:8]))
            cas_str   = _html.escape("; ".join(rule.cas_numbers))
            limit_str = f"{rule.limit_pct:.4g}%" if rule.limit_pct is not None else ""
            ref       = _html.escape(rule.legal_reference or "")
            url       = rule.source_url or ""
            ref_html  = (f'<a href="{_html.escape(url)}" target="_blank" '
                         f'style="color:#1e40af;text-decoration:none">{ref}</a>'
                         if url else ref)
            rid       = _html.escape(rule.rule_id or "")
            subset_tag = (
                ' <span style="background:#e0f2fe;color:#0369a1;padding:1px 5px;'
                'border-radius:3px;font-size:0.75em">subset</span>'
                if rule.is_subset else ""
            )
            rows += (
                f'<tr style="border-top:1px solid #e2e8f0;vertical-align:top">'
                f'<td style="padding:6px 10px;font-weight:500">'
                f'{_html.escape(rule.substance)}{subset_tag}</td>'
                f'<td style="padding:6px 10px;font-size:0.85em;color:#475569">'
                f'{cas_str}</td>'
                f'<td style="padding:6px 10px;white-space:nowrap;font-weight:600">'
                f'{_html.escape(limit_str)}</td>'
                f'<td style="padding:6px 10px;white-space:nowrap">'
                f'{_html.escape(rule.obligation_type)}</td>'
                f'<td style="padding:6px 10px;font-size:0.85em">{ref_html}</td>'
                f'<td style="padding:6px 10px;font-size:0.82em;color:#475569">'
                f'{alias_str}</td>'
                f'<td style="padding:6px 10px;font-size:0.75em;color:#9ca3af;'
                f'word-break:break-all">{rid}</td>'
                f'</tr>'
            )
        st.markdown(
            f'<div style="overflow-x:auto;width:100%">'
            f'{header}{rows}</tbody></table></div>',
            unsafe_allow_html=True,
        )
        st.markdown("")

    st.markdown("---")
    st.markdown("**Synonyms / aliases**")
    st.caption(
        f"Version: {SYNONYMS.version_id}\u2002|\u2002"
        f"Last verified: {SYNONYMS.last_verified or 'pending'}\u2002|\u2002"
        f"Verified by: {SYNONYMS.verified_by or 'pending'}"
    )
    for substance, aliases in sorted(SYNONYMS.synonyms.items()):
        st.caption(f"**{substance}:** {', '.join(aliases)}")


# ── header (always visible) ───────────────────────────────────────────────────
st.markdown("# RADAR")
st.caption(
    "Reads supplier declarations, checks them against EU RoHS limits, "
    "and drafts the follow-up."
)
st.caption("All suppliers and documents are fictional. Synthetic data only.")

st.markdown(
    '<div style="background:#f1f5f9;border-radius:6px;padding:10px 16px;'
    'margin:8px 0 4px">'
    '<span style="font-size:0.88em;color:#475569">'
    'Read &nbsp;&rsaquo;&nbsp; Extract &nbsp;&rsaquo;&nbsp; '
    'Check against rules &nbsp;&rsaquo;&nbsp; Rate risk &nbsp;&rsaquo;&nbsp; '
    'Draft follow-up</span><br>'
    '<span style="font-size:0.82em;color:#94a3b8;font-style:italic">'
    'The model reads, the code judges.</span></div>',
    unsafe_allow_html=True,
)

if _KEY_ERROR:
    st.error(f"API key not configured: {_KEY_ERROR}. Processing will fail.")

st.markdown("---")

# ── tabs ──────────────────────────────────────────────────────────────────────
tab_screening, tab_rules = st.tabs(["Screening", "Rules"])

# ── Rules tab ─────────────────────────────────────────────────────────────────
with tab_rules:
    _render_rules_tab()

# ── Screening tab ─────────────────────────────────────────────────────────────
with tab_screening:
    col_run, col_upload = st.columns([1, 2])

    with col_run:
        run_all = st.button("Run all samples", type="primary",
                            use_container_width=True)
    with col_upload:
        uploaded = st.file_uploader(
            "Upload declarations",
            type=["pdf", "csv", "xlsx", "txt", "md"],
            accept_multiple_files=True,
            label_visibility="collapsed",
        )

    # "Run all samples"
    if run_all:
        sample_files = sorted(SAMPLES_DIR.glob("0*"))
        new_results: list[dict] = []
        progress = st.progress(0.0, text="Processing samples...")
        for i, path in enumerate(sample_files):
            with st.status(f"Processing {path.name} ...",
                           expanded=True) as status:
                r = _process(path.name, path.read_bytes(), status)
                if r["error"]:
                    status.update(label=f"{path.name} \u2014 error",
                                  state="error", expanded=False)
                else:
                    status.update(
                        label=f"{r.get('supplier') or path.name} \u2014 {r['label']}",
                        state="complete", expanded=False,
                    )
            new_results.append(r)
            progress.progress((i + 1) / len(sample_files))
        progress.empty()
        st.session_state.results = new_results
        st.session_state.selected_id = None

    # Uploaded files
    if uploaded:
        existing_names = {r["filename"] for r in st.session_state.results}
        new_uploads = [f for f in uploaded if f.name not in existing_names]
        if new_uploads:
            added: list[dict] = []
            for uf in new_uploads:
                data = uf.read()
                if len(data) > MAX_UPLOAD_BYTES:
                    st.error(f"{uf.name}: file exceeds 10 MB limit, skipped.")
                    continue
                with st.status(f"Processing {uf.name} ...",
                               expanded=True) as status:
                    r = _process(uf.name, data, status)
                    if r["error"]:
                        status.update(label=f"{uf.name} \u2014 error",
                                      state="error", expanded=False)
                    else:
                        status.update(
                            label=(f"{r.get('supplier') or uf.name} "
                                   f"\u2014 {r['label']}"),
                            state="complete", expanded=False,
                        )
                added.append(r)
            st.session_state.results = st.session_state.results + added

    results: list[dict] = st.session_state.results

    if not results:
        st.info(
            'Click "Run all samples" or upload a declaration to get started.'
        )
    else:
        # Summary line
        n_high     = sum(1 for r in results
                        if not r["error"] and r.get("rating") == HIGH)
        n_med      = sum(1 for r in results
                        if not r["error"] and r.get("rating") == MEDIUM)
        n_low_w    = sum(1 for r in results
                        if not r["error"] and r.get("rating") == LOW
                        and r.get("watch"))
        n_low      = sum(1 for r in results
                        if not r["error"] and r.get("rating") == LOW
                        and not r.get("watch"))
        n_err      = sum(1 for r in results if r["error"])
        total      = len(results)
        parts = []
        if n_high:  parts.append(f"{n_high} High")
        if n_med:   parts.append(f"{n_med} Medium")
        if n_low_w: parts.append(f"{n_low_w} Low (on watch)")
        if n_low:   parts.append(f"{n_low} Low")
        if n_err:   parts.append(f"{n_err} error")
        st.caption(f"{total} declaration{'s' if total != 1 else ''}: "
                   + ", ".join(parts))

        sorted_results = sorted(results, key=_sort_key)

        col_port, col_detail = st.columns([1, 2], gap="large")

        # Portfolio column
        with col_port:
            st.markdown("**Portfolio**")
            for r in sorted_results:
                with st.container(border=True):
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        name = r.get("supplier") or r["filename"]
                        st.markdown(f"**{name}**")
                        if r.get("component"):
                            st.caption(r["component"])
                        if r.get("label"):
                            st.markdown(
                                _badge(r["label"],
                                       _RISK_COLOR.get(r["rating"], "#6b7280")),
                                unsafe_allow_html=True,
                            )
                        elif r["error"]:
                            st.caption("Error processing file")
                        st.caption(r["filename"])
                    with c2:
                        if st.button("View", key=f"v_{r['id']}",
                                     use_container_width=True):
                            st.session_state.selected_id = r["id"]

        # Detail column
        with col_detail:
            sel_id = st.session_state.selected_id
            if sel_id:
                match = next((r for r in results if r["id"] == sel_id), None)
                if match:
                    # Navigation bar
                    idx = next(
                        (i for i, r in enumerate(sorted_results) if r["id"] == sel_id),
                        0,
                    )
                    nav_c1, nav_c2, nav_c3, _ = st.columns([1, 1, 1, 3])
                    with nav_c1:
                        if st.button("← Back", use_container_width=True,
                                     help="Return to portfolio list"):
                            st.session_state.selected_id = None
                            st.rerun()
                    with nav_c2:
                        if st.button("← Prev", use_container_width=True,
                                     disabled=(idx == 0),
                                     help="Previous supplier in portfolio order"):
                            st.session_state.selected_id = sorted_results[idx - 1]["id"]
                            st.rerun()
                    with nav_c3:
                        if st.button("Next →", use_container_width=True,
                                     disabled=(idx == len(sorted_results) - 1),
                                     help="Next supplier in portfolio order"):
                            st.session_state.selected_id = sorted_results[idx + 1]["id"]
                            st.rerun()
                    st.caption(f"Supplier {idx + 1} of {len(sorted_results)}")
                    st.markdown("---")
                    _render_detail(match)
                else:
                    st.caption("Select a supplier to view details.")
            else:
                st.caption("Select a supplier from the portfolio to view details.")

# ── footer ────────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption("Prototype on synthetic data. Not legal advice.")

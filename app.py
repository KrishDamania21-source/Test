"""RADAR — supplier compliance agent (Streamlit UI, Step 6a)."""
from __future__ import annotations

import uuid
from pathlib import Path

import pandas as pd
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
from radar.rules import load_active_rules, load_active_synonyms

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="RADAR", layout="wide",
                   initial_sidebar_state="collapsed")

SAMPLES_DIR = Path(__file__).parent / "samples"

# ── constants ─────────────────────────────────────────────────────────────────
_RISK_ORDER  = {HIGH: 0, MEDIUM: 1, LOW: 2}
_RISK_COLOR  = {HIGH: "#dc2626", MEDIUM: "#d97706", LOW: "#16a34a"}

_SEV_COLOR = {
    "High":   "#dc2626",
    "Medium": "#d97706",
    "Low":    "#16a34a",
    "Info":   "#6b7280",
}

_TRACE_STEPS = [
    "Reading document",
    "Extracting substances",
    "Checking against EU RoHS",
    "Rating risk",
    "Drafting follow-up",
]

_PROMINENT = {UNTRACEABLE_EXTRACTION, IDENTITY_CONFLICT}

ALLOWED_EXTENSIONS = {".pdf", ".csv", ".xlsx", ".txt", ".md"}

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
    """Run the full pipeline for one document, writing trace events to status."""
    result: dict = {
        "id": str(uuid.uuid4()),
        "filename": filename,
        "trace": [],
        "error": None,
    }

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
        "supplier":     sr.supplier,
        "component":    sr.component,
        "rating":       sr.rating,
        "label":        sr.label,
        "watch":        sr.watch,
        "findings":     sr.findings,
        "explanation":  agent_out.explanation,
        "email":        agent_out.email,
        "agent_error":  agent_out.error,
    })
    return result


# ── rendering helpers ─────────────────────────────────────────────────────────
def _risk_badge(label: str, rating: str) -> str:
    color = _RISK_COLOR.get(rating, "#6b7280")
    return (
        f'<span style="background:{color};color:#fff;padding:3px 10px;'
        f'border-radius:4px;font-size:0.85em;font-weight:600">{label}</span>'
    )


def _sev_badge(severity: str) -> str:
    color = _SEV_COLOR.get(severity, "#6b7280")
    return (
        f'<span style="background:{color};color:#fff;padding:1px 7px;'
        f'border-radius:3px;font-size:0.78em;font-weight:600">{severity}</span>'
    )


def _findings_table(findings: list) -> None:
    """Render non-NOT_DECLARED findings as a dataframe."""
    rows = []
    for f in findings:
        if f.type == NOT_DECLARED:
            continue
        rows.append({
            "Type":      f.type,
            "Severity":  f.severity,
            "Substance": f.substance,
            "Value":     f"{f.value_pct:.4g}%" if f.value_pct is not None else "",
            "Limit":     f"{f.limit_pct:.4g}%" if f.limit_pct is not None else "",
            "Raw value": f.raw_value or "",
            "Source":    (f.source_text or "")[:120],
        })
    if not rows:
        st.caption("No findings (all substances within limits).")
        return
    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Type":      st.column_config.TextColumn(width="medium"),
            "Substance": st.column_config.TextColumn(width="large"),
            "Value":     st.column_config.TextColumn(width="small"),
            "Limit":     st.column_config.TextColumn(width="small"),
            "Raw value": st.column_config.TextColumn(width="small"),
            "Source":    st.column_config.TextColumn(width="large"),
        },
    )


def _render_detail(r: dict) -> None:
    """Render the full detail view for one result."""
    supplier = r.get("supplier") or r["filename"]
    st.markdown(
        f"### {supplier}",
        help=r.get("component") or "",
    )
    if r.get("component"):
        st.caption(r["component"])
    if r.get("label"):
        st.markdown(
            _risk_badge(r["label"], r["rating"]),
            unsafe_allow_html=True,
        )

    if r.get("error"):
        st.error(r["error"])
        return

    # ── trace ──────────────────────────────────────────────────────────────
    st.markdown("**Processing trace**")
    for step_label in r["trace"]:
        known = any(step_label.startswith(s) for s in _TRACE_STEPS)
        marker = "[done]" if known else "     "
        st.caption(f"{marker}  {step_label}")

    findings: list = r.get("findings", [])

    # ── prominent warnings ─────────────────────────────────────────────────
    critical = [f for f in findings if f.type in _PROMINENT]
    if critical:
        st.markdown("---")
        for f in critical:
            icon = "IDENTITY CONFLICT" if f.type == IDENTITY_CONFLICT else "UNTRACEABLE EXTRACTION"
            st.warning(f"**{icon}** — {f.substance}: {f.message}")

    # ── findings table ─────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("**Findings**")
    _findings_table(findings)

    nd = [f for f in findings if f.type == NOT_DECLARED]
    if nd:
        with st.expander(f"Not addressed in declaration ({len(nd)} substances)"):
            for f in nd:
                st.caption(f"- {f.substance}")

    # ── explanation ────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("**Explanation**")
    if r.get("agent_error"):
        st.error(r["agent_error"])
    elif r.get("explanation"):
        st.info(r["explanation"])

    # ── draft email ────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("**Draft follow-up email**")
    email_text = r.get("email", NO_EMAIL)
    if email_text == NO_EMAIL:
        st.caption(NO_EMAIL)
    else:
        st.code(email_text, language=None)
        st.download_button(
            "Download email as .txt",
            data=email_text,
            file_name=f"{(r.get('supplier') or 'supplier').replace(' ', '_')}_followup.txt",
            mime="text/plain",
        )


# ── main layout ───────────────────────────────────────────────────────────────
st.markdown("# RADAR")
st.caption(
    "Reads supplier declarations, checks them against EU RoHS limits, "
    "and drafts the follow-up."
)
st.markdown("---")

# API key warning
if _KEY_ERROR:
    st.error(f"API key not configured: {_KEY_ERROR}. Processing will fail.")

# ── action row ────────────────────────────────────────────────────────────────
col_run, col_upload = st.columns([1, 2])

with col_run:
    run_all = st.button("Run all samples", type="primary", use_container_width=True)

with col_upload:
    uploaded = st.file_uploader(
        "Upload declarations",
        type=[ext.lstrip(".") for ext in ALLOWED_EXTENSIONS],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

# ── process "Run all samples" ─────────────────────────────────────────────────
if run_all:
    sample_files = sorted(SAMPLES_DIR.glob("0*"))
    new_results: list[dict] = []
    progress = st.progress(0.0, text="Processing samples...")
    for i, path in enumerate(sample_files):
        with st.status(f"Processing {path.name} ...", expanded=True) as status:
            r = _process(path.name, path.read_bytes(), status)
            if r["error"]:
                status.update(label=f"{path.name} — error", state="error",
                               expanded=False)
            else:
                status.update(
                    label=f"{r.get('supplier') or path.name} — {r['label']}",
                    state="complete", expanded=False,
                )
        new_results.append(r)
        progress.progress((i + 1) / len(sample_files),
                          text=f"Processed {i + 1}/{len(sample_files)}")
    progress.empty()
    st.session_state.results = new_results
    st.session_state.selected_id = None

# ── process uploaded files ────────────────────────────────────────────────────
if uploaded:
    # Only process files not already in results (matched by filename)
    existing_names = {r["filename"] for r in st.session_state.results}
    new_uploads = [f for f in uploaded if f.name not in existing_names]
    if new_uploads:
        added: list[dict] = []
        for uf in new_uploads:
            with st.status(f"Processing {uf.name} ...", expanded=True) as status:
                r = _process(uf.name, uf.read(), status)
                if r["error"]:
                    status.update(label=f"{uf.name} — error", state="error",
                                  expanded=False)
                else:
                    status.update(
                        label=f"{r.get('supplier') or uf.name} — {r['label']}",
                        state="complete", expanded=False,
                    )
            added.append(r)
        st.session_state.results = st.session_state.results + added

# ── portfolio + detail ────────────────────────────────────────────────────────
results: list[dict] = st.session_state.results

if not results:
    st.info("Click \"Run all samples\" or upload a declaration to get started.")
else:
    # Sort: errors last, then High → Medium → Low
    def _sort_key(r: dict):
        if r["error"]:
            return (99, "")
        return (_RISK_ORDER.get(r["rating"], 9), r.get("supplier") or r["filename"])

    sorted_results = sorted(results, key=_sort_key)

    col_port, col_detail = st.columns([1, 2], gap="large")

    # ── portfolio column ──────────────────────────────────────────────────
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
                            _risk_badge(r["label"], r["rating"]),
                            unsafe_allow_html=True,
                        )
                    elif r["error"]:
                        st.caption("Error")
                    st.caption(r["filename"])
                with c2:
                    if st.button("View", key=f"view_{r['id']}",
                                 use_container_width=True):
                        st.session_state.selected_id = r["id"]

    # ── detail column ─────────────────────────────────────────────────────
    with col_detail:
        selected_id = st.session_state.selected_id
        if selected_id:
            match = next((r for r in results if r["id"] == selected_id), None)
            if match:
                _render_detail(match)
            else:
                st.caption("Select a supplier from the portfolio to view details.")
        else:
            st.caption("Select a supplier from the portfolio to view details.")

# ── footer ────────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption("Prototype on synthetic data. Not legal advice.")

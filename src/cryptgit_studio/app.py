"""Streamlit developer review interface."""

from pathlib import Path
import os
from typing import Any

import streamlit as st

from .analyzer import analyze_repository
from .ollama import OllamaProvider
from .reporting import report_json, report_markdown
from .review_store import ReviewDecision, ReviewStore, finding_key
from .review_store import VALID_REVIEW_STATUSES
from .evidence_packet import finding_id
from .appwrite_store import AppwriteConfig, AppwritePersistence
from .telemetry import configure_sentry, flush


def _scan(repository: Path, options: dict[str, Any]):
    provider = (
        OllamaProvider(
            options["endpoint"], options["model"], options["timeout"]
        )
        if options["ai"]
        else None
    )
    return analyze_repository(
        repository,
        include_history=options["history"] or options["verify"],
        history_limit=options["history_limit"],
        verify_evidence=options["verify"],
        ai_provider=provider,
    )


def _public_demo_mode() -> bool:
    return os.getenv("CRYPTGIT_PUBLIC_DEMO_MODE") == "1"


def main() -> None:
    st.set_page_config(
        page_title="CryptGit Studio",
        page_icon="🔍",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        .stApp { background: #10151c; }
        [data-testid="stSidebar"] { background: #151d27; }
        .review-card { padding: 1rem; border: 1px solid #334155; border-radius: .5rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("CryptGit Studio")
    st.caption("Deterministic repository evidence with local-first developer review.")
    public_demo = _public_demo_mode()
    if public_demo:
        st.info(
            "PUBLIC DEMO MODE — this hosted instance scans only the synthetic "
            "CryptGit demo repository. No arbitrary filesystem paths or source "
            "repositories are accepted."
        )

    if "summary" not in st.session_state:
        st.session_state.summary = None
    if "repository" not in st.session_state:
        st.session_state.repository = None

    with st.sidebar:
        st.header("Scan configuration")
        if public_demo:
            repository = Path(
                os.getenv("CRYPTGIT_DEMO_REPOSITORY", "/tmp/cryptgit-studio-demo")
            )
            st.caption("Synthetic demo repository")
        else:
            repository_text = st.text_input("Repository path", value=".")
        history = st.checkbox("Git history", value=True)
        verify = st.checkbox("Verify evidence", value=True)
        ai = st.checkbox(
            "Local Ollama explanations",
            value=False,
            disabled=public_demo,
            help=(
                "Ollama is available in local mode only."
                if public_demo
                else None
            ),
        )
        if public_demo:
            st.caption(
                "Local Ollama inference is available when running CryptGit Studio "
                "locally; it is not required for this hosted synthetic demo."
            )
        model = st.text_input("Ollama model", value="qwen2.5-coder:3b")
        endpoint = st.text_input("Ollama endpoint", value="http://localhost:11434")
        timeout = st.number_input("Ollama timeout (seconds)", min_value=1.0, value=120.0)
        sentry_enabled = st.checkbox(
            "Enable Sentry tracing",
            value=False,
            help="Opt-in telemetry; requires SENTRY_DSN in the environment.",
        )
        run_scan = st.button("Run scan", type="primary", use_container_width=True)
        appwrite = AppwritePersistence()
        st.caption(
            "Appwrite: configured" if appwrite.available else "Appwrite: offline/not configured"
        )

    if run_scan:
        configure_sentry(sentry_enabled)
        if not public_demo:
            repository = Path(repository_text).expanduser()
        if not repository.is_dir():
            st.error("Repository path must be an existing directory.")
        else:
            with st.spinner("Scanning without executing repository code..."):
                try:
                    st.session_state.summary = _scan(
                        repository,
                        {
                            "history": history,
                            "verify": verify,
                            "ai": ai,
                            "model": model,
                            "endpoint": endpoint,
                            "timeout": timeout,
                            "history_limit": 20,
                        },
                    )
                    st.session_state.repository = repository.resolve()
                except (OSError, ValueError, RuntimeError) as error:
                    st.error(f"Scan failed: {error}")
                finally:
                    flush()

    summary = st.session_state.summary
    repository = st.session_state.repository
    if summary is None or repository is None:
        st.info("Enter a local repository path and run a scan to begin.")
        return

    store = ReviewStore()
    reviews = store.for_repository(repository)
    appwrite = AppwritePersistence()
    counts = summary.classification_counts
    metrics = st.columns(5)
    metrics[0].metric("Python files", summary.python_files_scanned)
    metrics[1].metric("Functions", summary.functions_discovered)
    metrics[2].metric("Potentially unreferenced", counts.get("potentially_unreferenced", 0))
    metrics[3].metric("Referenced", counts.get("referenced", 0))
    metrics[4].metric("Uncertain", counts.get("uncertain", 0))

    if summary.analysis_warnings or summary.parsing_errors:
        with st.expander("Warnings and errors", expanded=True):
            for warning in summary.analysis_warnings:
                st.warning(warning)
            for error in summary.parsing_errors:
                st.error(error)

    st.subheader("Findings")
    classification_options = ["all"] + sorted(counts)
    selected_classification = st.selectbox("Filter classification", classification_options)
    search = st.text_input("Search symbol or file").lower().strip()
    visible = [
        finding
        for finding in summary.findings
        if (selected_classification == "all" or finding.classification == selected_classification)
        and (not search or search in finding.symbol.lower() or search in finding.file.lower())
    ]
    labels = [
        f"{finding.symbol} — {finding.file}:{finding.line_start} [{finding.classification}]"
        for finding in visible
    ]
    if not labels:
        st.info("No findings match the current filters.")
        return
    selected = st.selectbox("Select finding", range(len(labels)), format_func=lambda index: labels[index])
    finding = visible[selected]
    decision = reviews.get(finding_key(finding), ReviewDecision("pending_review"))

    st.divider()
    st.subheader(f"{finding.symbol} — {finding.file}:{finding.line_start}")
    st.markdown(
        f"**Deterministic classification:** `{finding.classification}`  \n"
        f"**Heuristic score (not a probability):** `{finding.heuristic_score:.2f}`  \n"
        f"{finding.explanation}"
    )
    with st.expander("Static references", expanded=True):
        st.json([reference for reference in finding.to_dict()["references"]])
    if finding.history:
        with st.expander("Git history and evidence verification", expanded=True):
            st.json(finding.history.to_dict())
    if summary.ai_report:
        interpretation = next(
            (
                item
                for item in summary.ai_report.get("interpretations", [])
                if item.get("finding_id") == finding_id(finding)
            ),
            None,
        )
        if interpretation:
            with st.expander("AI interpretation (advisory)", expanded=True):
                st.write(interpretation["summary"])
                st.markdown("**Alternative explanations**")
                st.write(interpretation["alternative_explanations"])
                st.markdown("**Dynamic usage risks**")
                st.write(interpretation["dynamic_usage_risks"])
                st.markdown("**Uncertainty**")
                st.write(interpretation["uncertainty_explanation"])
                st.markdown("**Human review recommendation**")
                st.write(interpretation["human_review_recommendation"])
        elif summary.ai_report.get("errors"):
            st.warning("Ollama was unavailable or returned invalid output; deterministic results are preserved.")

    with st.expander("Human review", expanded=True):
        new_status = st.selectbox(
            "Review status",
            VALID_REVIEW_STATUSES,
            index=VALID_REVIEW_STATUSES.index(decision.status),
        )
        note = st.text_area("Reviewer note", value=decision.note)
        if st.button("Save review decision"):
            store.set(repository, finding, ReviewDecision(new_status, note))
            st.success("Review decision saved locally.")
            st.rerun()

    st.subheader("Export")
    st.download_button(
        "Download JSON report",
        report_json(summary, repository, store.for_repository(repository)),
        file_name="cryptgit-report.json",
        mime="application/json",
    )
    st.download_button(
        "Download Markdown report",
        report_markdown(summary, repository, store.for_repository(repository)),
        file_name="cryptgit-report.md",
        mime="text/markdown",
    )
    if appwrite.available:
        if st.button("Save scan to Appwrite TablesDB"):
            try:
                scan_id = appwrite.save_scan(summary, reviews)
                st.success(f"Saved scan {scan_id} to Appwrite.")
            except (OSError, RuntimeError, ValueError) as error:
                st.error(f"Appwrite save failed: {error}")
        with st.expander("Previous Appwrite scans"):
            try:
                st.json(appwrite.list_scans())
            except (OSError, RuntimeError, ValueError) as error:
                st.warning(f"Unable to load previous scans: {error}")


if __name__ == "__main__":
    main()

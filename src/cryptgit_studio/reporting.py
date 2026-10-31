"""Export deterministic scans, AI interpretations, and human review metadata."""

import json
from pathlib import Path
from typing import Any

from .models import ScanSummary
from .review_store import ReviewDecision, finding_key
from .telemetry import stage


def report_json(
    summary: ScanSummary,
    repository: Path,
    reviews: dict[str, ReviewDecision] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "deterministic_scan": summary.to_dict(),
        "human_review": {
            key: decision.to_dict()
            for key, decision in sorted((reviews or {}).items())
        },
    }
    with stage("cryptgit.report_generation", {"format": "json"}):
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def report_markdown(
    summary: ScanSummary,
    repository: Path,
    reviews: dict[str, ReviewDecision] | None = None,
) -> str:
    review_map = reviews or {}
    lines = [
        "# CryptGit Studio Review Report",
        "",
        f"- Repository: `{summary.repository_path}`",
        f"- Python files scanned: **{summary.python_files_scanned}**",
        f"- Functions discovered: **{summary.functions_discovered}**",
        f"- Coverage incomplete: **{summary.coverage_incomplete}**",
        "",
        "## Deterministic findings",
        "",
        "| Function | File | Lines | Classification | Heuristic score | Review |",
        "|---|---|---:|---|---:|---|",
    ]
    for finding in summary.findings:
        decision = review_map.get(finding_key(finding), ReviewDecision("pending_review"))
        lines.append(
            f"| `{finding.symbol}` | `{finding.file}` | {finding.line_start}-{finding.line_end} "
            f"| `{finding.classification}` | {finding.heuristic_score:.2f} | `{decision.status}` |"
        )
    lines.extend(["", "## Finding details", ""])
    for finding in summary.findings:
        decision = review_map.get(finding_key(finding), ReviewDecision("pending_review"))
        lines.extend(
            [
                f"### `{finding.symbol}` — `{finding.file}:{finding.line_start}`",
                "",
                f"- Deterministic classification: `{finding.classification}`",
                f"- Heuristic score (not a probability): `{finding.heuristic_score:.2f}`",
                f"- Explanation: {finding.explanation}",
                f"- Human review: `{decision.status}`",
                f"- Reviewer note: {decision.note or 'None'}",
            ]
        )
        if finding.history:
            lines.append(
                f"- History: {finding.history.history_commit_count} commits; "
                f"complete={finding.history.history_complete}; "
                f"truncated={finding.history.history_truncated}"
            )
            statuses = [
                verification.status
                for verification in finding.history.evidence_verifications
            ]
            lines.append(f"- Evidence verification statuses: {', '.join(statuses) or 'None'}")
        if summary.ai_report:
            interpretations = {
                item.get("finding_id"): item
                for item in summary.ai_report.get("interpretations", [])
                if isinstance(item, dict)
            }
            from .evidence_packet import finding_id

            interpretation = interpretations.get(finding_id(finding))
            if interpretation:
                lines.extend(
                    [
                        "",
                        "**AI interpretation (advisory; not deterministic evidence):**",
                        "",
                        f"- Summary: {interpretation['summary']}",
                        f"- Uncertainty: {interpretation['uncertainty_explanation']}",
                        f"- Human review recommendation: {interpretation['human_review_recommendation']}",
                    ]
                )
    with stage("cryptgit.report_generation", {"format": "markdown"}):
        return "\n".join(lines) + "\n"

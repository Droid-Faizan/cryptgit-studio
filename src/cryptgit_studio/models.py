"""Core immutable data models used by CryptGit Studio."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RepositoryFile:
    """A file discovered during a repository scan."""

    path: Path
    size_bytes: int


@dataclass(frozen=True)
class Commit:
    """A compact representation of a Git commit."""

    hash: str
    author: str
    subject: str


@dataclass(frozen=True)
class Evidence:
    """An observation that can support a future repository analysis."""

    source: str
    description: str
    confidence: float = 1.0


@dataclass(frozen=True)
class SourceLocation:
    """A source span using one-based lines and zero-based columns."""

    file: str
    start_line: int
    end_line: int
    start_column: int = 0
    end_column: int = 0


@dataclass(frozen=True)
class FunctionSymbol:
    """A module-level Python function discovered by AST analysis."""

    module: str
    name: str
    location: SourceLocation
    is_async: bool = False
    decorators: tuple[str, ...] = ()
    is_private: bool = False
    is_test: bool = False


@dataclass(frozen=True)
class Reference:
    """A statically detected reference to a symbol name."""

    module: str
    name: str
    location: SourceLocation
    kind: str
    target_module: str | None = None
    target_name: str | None = None
    local_name: str | None = None
    source_symbol: str | None = None
    self_reference: bool = False


@dataclass(frozen=True)
class HistoryCommit:
    """Minimal commit metadata retained for historical evidence."""

    hash: str
    timestamp: str
    subject: str


@dataclass(frozen=True)
class HistoryObservation:
    """A function observation in one historical file snapshot."""

    commit: HistoryCommit
    file: str
    symbol: str
    start_line: int
    end_line: int
    ast_digest: str


@dataclass(frozen=True)
class EvidenceVerification:
    """The result of independently checking one historical observation."""

    status: str
    explanation: str
    commit_hash: str
    file: str
    symbol: str
    observed_ast_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable verification result."""
        return {
            "status": self.status,
            "explanation": self.explanation,
            "commit_hash": self.commit_hash,
            "file": self.file,
            "symbol": self.symbol,
            "observed_ast_digest": self.observed_ast_digest,
        }


@dataclass(frozen=True)
class FunctionHistory:
    """Conservative history metadata for one current function."""

    first_observed_commit: HistoryObservation | None
    last_function_change_commit: HistoryObservation | None
    last_file_change_commit: HistoryCommit | None
    history_commit_count: int
    history_truncated: bool
    history_complete: bool
    history_warnings: tuple[str, ...] = ()
    evidence_verifications: tuple[EvidenceVerification, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        def observation(value: HistoryObservation | None) -> dict[str, Any] | None:
            if value is None:
                return None
            return {
                "commit": {
                    "hash": value.commit.hash,
                    "timestamp": value.commit.timestamp,
                    "subject": value.commit.subject,
                },
                "file": value.file,
                "symbol": value.symbol,
                "start_line": value.start_line,
                "end_line": value.end_line,
                "ast_digest": value.ast_digest,
            }

        return {
            "first_observed_commit": observation(self.first_observed_commit),
            "last_function_change_commit": observation(
                self.last_function_change_commit
            ),
            "last_file_change_commit": (
                {
                    "hash": self.last_file_change_commit.hash,
                    "timestamp": self.last_file_change_commit.timestamp,
                    "subject": self.last_file_change_commit.subject,
                }
                if self.last_file_change_commit
                else None
            ),
            "history_commit_count": self.history_commit_count,
            "history_truncated": self.history_truncated,
            "history_complete": self.history_complete,
            "history_warnings": list(self.history_warnings),
            "evidence_verifications": [
                verification.to_dict()
                for verification in self.evidence_verifications
            ],
        }


@dataclass(frozen=True)
class AnalysisFinding:
    """A conservative classification for one discovered function."""

    file: str
    symbol: str
    line_start: int
    line_end: int
    classification: str
    heuristic_score: float
    references: tuple[Reference, ...]
    explanation: str
    history: FunctionHistory | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "file": self.file,
            "symbol": self.symbol,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "classification": self.classification,
            "heuristic_score": self.heuristic_score,
            "references": [
                {
                    "module": reference.module,
                    "name": reference.name,
                    "file": reference.location.file,
                    "line": reference.location.start_line,
                    "kind": reference.kind,
                    "target_module": reference.target_module,
                    "target_name": reference.target_name,
                    "local_name": reference.local_name or reference.name,
                    "source_symbol": reference.source_symbol,
                    "self_reference": reference.self_reference,
                }
                for reference in self.references
            ],
            "explanation": self.explanation,
            "history": self.history.to_dict() if self.history else None,
        }


@dataclass(frozen=True)
class ScanSummary:
    """The complete deterministic result of a repository scan."""

    schema_version: str
    repository_path: str
    python_files_scanned: int
    functions_discovered: int
    classification_counts: dict[str, int]
    findings: tuple[AnalysisFinding, ...]
    parsing_errors: tuple[str, ...] = ()
    analysis_warnings: tuple[str, ...] = ()
    coverage_incomplete: bool = False
    ai_report: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-serializable report."""
        report: dict[str, Any] = {
            "schema_version": self.schema_version,
            "repository_path": self.repository_path,
            "python_files_scanned": self.python_files_scanned,
            "functions_discovered": self.functions_discovered,
            "classification_counts": dict(sorted(self.classification_counts.items())),
            "findings": [finding.to_dict() for finding in self.findings],
            "parsing_errors": list(self.parsing_errors),
            "analysis_warnings": list(self.analysis_warnings),
            "coverage_incomplete": self.coverage_incomplete,
        }
        if self.ai_report is not None:
            report["ai"] = self.ai_report
        return report

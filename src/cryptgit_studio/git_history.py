"""Read-only Git history and historical Python function tracking."""

import ast
import hashlib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    Commit,
    FunctionHistory,
    EvidenceVerification,
    HistoryCommit,
    HistoryObservation,
)
from .telemetry import stage

DEFAULT_TIMEOUT_SECONDS = 10.0
COMMIT_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


class GitHistoryError(RuntimeError):
    """Base error for unavailable or failed Git history operations."""


class GitUnavailableError(GitHistoryError):
    """Git executable is not available."""


class NotGitRepositoryError(GitHistoryError):
    """The target directory is not a Git work tree."""


class GitCommandError(GitHistoryError):
    """A read-only Git command failed."""


def _run_git(
    repository: Path, arguments: list[str], timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise GitUnavailableError("Git executable was not found") from error
    except subprocess.TimeoutExpired as error:
        raise GitCommandError(f"Git command timed out after {timeout:g}s") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or "Git command failed"
        raise GitCommandError(detail) from error


def validate_repository(
    repository: Path, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> None:
    """Ensure *repository* is an accessible Git work tree."""
    if not repository.is_dir():
        raise NotGitRepositoryError(f"Not a directory: {repository}")
    try:
        result = _run_git(repository, ["rev-parse", "--is-inside-work-tree"], timeout)
    except GitCommandError as error:
        raise NotGitRepositoryError(
            f"Git history unavailable: {repository} is not a Git repository"
        ) from error
    if result.stdout.strip().lower() != "true":
        raise NotGitRepositoryError(f"Not a Git repository: {repository}")


def _timestamp(value: str) -> str:
    return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()


def get_commits(
    repository: Path,
    limit: int = 20,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[Commit]:
    """Read recent commits without modifying the repository."""
    if limit < 1:
        raise ValueError("Commit limit must be at least 1")
    validate_repository(repository, timeout)
    result = _run_git(
        repository,
        ["log", f"-{limit}", "--format=%H%x1f%an%x1f%s"],
        timeout,
    )
    commits = []
    for line in result.stdout.splitlines():
        commit_hash, author, subject = line.split("\x1f", 2)
        commits.append(Commit(hash=commit_hash, author=author, subject=subject))
    return commits


def _file_history(
    repository: Path,
    relative_file: str,
    limit: int,
    timeout: float,
) -> tuple[list[HistoryCommit], bool]:
    with stage("cryptgit.git_history", {"history_limit": limit}):
        result = _run_git(
            repository,
            [
                "log",
                "--follow",
                f"-{limit + 1}",
                "--format=%H%x1f%ct%x1f%s",
                "--",
                relative_file,
            ],
            timeout,
        )
    commits = []
    for line in result.stdout.splitlines():
        commit_hash, timestamp, subject = line.split("\x1f", 2)
        commits.append(
            HistoryCommit(
                hash=commit_hash,
                timestamp=_timestamp(timestamp),
                subject=subject,
            )
        )
    truncated = len(commits) > limit
    return commits[:limit], truncated


def _historical_file(
    repository: Path,
    commit_hash: str,
    relative_file: str,
    timeout: float,
) -> str:
    try:
        return _run_git(
            repository,
            ["show", f"{commit_hash}:{relative_file}"],
            timeout,
        ).stdout
    except GitCommandError as error:
        raise GitCommandError(
            f"Historical file unavailable at {commit_hash[:12]}: {relative_file}: {error}"
        ) from error


def _function_snapshot(
    source: str,
    relative_file: str,
    symbol: str,
    commit: HistoryCommit,
) -> HistoryObservation | None:
    try:
        tree = ast.parse(source.lstrip("\ufeffï»¿"), filename=relative_file)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
            normalized = ast.dump(node, annotate_fields=True, include_attributes=False)
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            return HistoryObservation(
                commit=commit,
                file=relative_file,
                symbol=symbol,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                ast_digest=digest,
            )
    return None


def verify_history_observation(
        repository: Path,
        observation: HistoryObservation,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> EvidenceVerification:
        """Verify one historical function observation without changing the work tree."""
        with stage("cryptgit.evidence_verification"):
            return _verify_history_observation(repository, observation, timeout)


def _verify_history_observation(
        repository: Path,
        observation: HistoryObservation,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> EvidenceVerification:
        base = {
            "commit_hash": observation.commit.hash,
            "file": observation.file,
            "symbol": observation.symbol,
        }
        if not COMMIT_HASH_PATTERN.fullmatch(observation.commit.hash):
            return EvidenceVerification(
                status="unsupported",
                explanation="Commit hash is not a full 40-character hexadecimal object ID.",
                **base,
            )
        try:
            validate_repository(repository, timeout)
            try:
                _run_git(
                    repository,
                    ["cat-file", "-e", f"{observation.commit.hash}^{{commit}}"],
                    timeout,
                )
            except GitCommandError as error:
                return EvidenceVerification(
                    status="missing_commit",
                    explanation=str(error),
                    **base,
                )
            metadata = _run_git(
                repository,
                ["show", "-s", "--format=%H%x1f%ct%x1f%s", observation.commit.hash],
                timeout,
            ).stdout.strip()
        except GitHistoryError as error:
            return EvidenceVerification(
                status="unavailable", explanation=str(error), **base
            )

        fields = metadata.split("\x1f", 2)
        if len(fields) != 3:
            return EvidenceVerification(
                status="mismatch",
                explanation="Git returned incomplete commit metadata.",
                **base,
            )
        actual_hash, timestamp, subject = fields
        expected_timestamp = observation.commit.timestamp
        try:
            actual_timestamp = _timestamp(timestamp)
        except ValueError:
            return EvidenceVerification(
                status="mismatch",
                explanation="Git returned an invalid commit timestamp.",
                **base,
            )
        if (
            actual_hash.lower() != observation.commit.hash.lower()
            or actual_timestamp != expected_timestamp
            or subject != observation.commit.subject
        ):
            return EvidenceVerification(
                status="mismatch",
                explanation="Stored commit metadata does not match the repository object.",
                **base,
            )
        try:
            source = _historical_file(
                repository, observation.commit.hash, observation.file, timeout
            )
        except GitCommandError as error:
            return EvidenceVerification(
                status="missing_file",
                explanation=str(error),
                **base,
            )
        try:
            tree = ast.parse(source.lstrip("\ufeffï»¿"), filename=observation.file)
        except (SyntaxError, UnicodeError) as error:
            return EvidenceVerification(
                status="unsupported",
                explanation=f"Historical Python source cannot be parsed: {error}",
                **base,
            )
        matches = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == observation.symbol
        ]
        if not matches:
            return EvidenceVerification(
                status="missing_symbol",
                explanation="The module-level function is absent from the historical file.",
                **base,
            )
        if len(matches) != 1:
            return EvidenceVerification(
                status="ambiguous",
                explanation="Multiple module-level functions share the evidence symbol name.",
                **base,
            )
        node = matches[0]
        actual_digest = hashlib.sha256(
            ast.dump(node, annotate_fields=True, include_attributes=False).encode("utf-8")
        ).hexdigest()
        actual_end = getattr(node, "end_lineno", node.lineno)
        if (
            actual_digest != observation.ast_digest
            or node.lineno != observation.start_line
            or actual_end != observation.end_line
        ):
            return EvidenceVerification(
                status="mismatch",
                explanation="Historical symbol location or normalized AST digest does not match.",
                observed_ast_digest=actual_digest,
                **base,
            )
        return EvidenceVerification(
            status="verified",
            explanation="Commit, file, symbol, source location, and AST digest match.",
            observed_ast_digest=actual_digest,
            **base,
        )
def track_function_history(
    repository: Path,
    relative_file: str,
    symbol: str,
    limit: int = 20,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    verify: bool = False,
) -> FunctionHistory:
    """Track a module-level function across bounded, available file history."""
    if limit < 1:
        raise ValueError("History limit must be at least 1")
    warnings: list[str] = []
    try:
        validate_repository(repository, timeout)
        commits, truncated = _file_history(repository, relative_file, limit, timeout)
    except GitHistoryError as error:
        return FunctionHistory(
            first_observed_commit=None,
            last_function_change_commit=None,
            last_file_change_commit=None,
            history_commit_count=0,
            history_truncated=False,
            history_complete=False,
            history_warnings=(str(error),),
        )

    observations: list[HistoryObservation] = []
    for commit in reversed(commits):
        try:
            source = _historical_file(repository, commit.hash, relative_file, timeout)
        except GitHistoryError as error:
            warnings.append(str(error))
            continue
        observation = _function_snapshot(source, relative_file, symbol, commit)
        if observation:
            observations.append(observation)
        elif source:
            warnings.append(
                f"{commit.hash[:12]}: function {symbol} not found in historical snapshot"
            )

    first = observations[0] if observations else None
    last_change = None
    previous_digest: str | None = None
    for observation in observations:
        if previous_digest is not None and observation.ast_digest != previous_digest:
            last_change = observation
        previous_digest = observation.ast_digest
    observations_to_verify: list[HistoryObservation] = []
    for observation in (first, last_change):
        if observation and observation not in observations_to_verify:
            observations_to_verify.append(observation)
    verifications = (
        tuple(
            verify_history_observation(repository, observation, timeout)
            for observation in observations_to_verify
        )
        if verify
        else ()
    )
    return FunctionHistory(
        first_observed_commit=first,
        last_function_change_commit=last_change,
        last_file_change_commit=commits[0] if commits else None,
        history_commit_count=len(commits),
        history_truncated=truncated,
        history_complete=bool(commits) and not truncated and not warnings,
        history_warnings=tuple(sorted(set(warnings))),
        evidence_verifications=verifications,
    )

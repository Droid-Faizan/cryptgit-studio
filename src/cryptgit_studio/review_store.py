"""Local persistence for human review decisions."""

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReviewDecision:
    status: str
    note: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"status": self.status, "note": self.note}


VALID_REVIEW_STATUSES = (
    "pending_review",
    "confirmed_unused",
    "in_use",
    "needs_investigation",
)


def repository_key(repository: Path) -> str:
    return hashlib.sha256(
        str(repository.resolve()).encode("utf-8")
    ).hexdigest()[:24]


def finding_key(finding: Any) -> str:
    return f"{finding.file}:{finding.symbol}:{finding.line_start}:{finding.line_end}"


class ReviewStore:
    """Persist review metadata in a user-local JSON file, never in a repository."""

    def __init__(self, path: Path | None = None) -> None:
        default = Path.home() / ".cryptgit-studio" / "reviews.json"
        self.path = path or default

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Unable to read review store {self.path}: {error}") from error
        if not isinstance(value, dict):
            raise RuntimeError(f"Review store {self.path} must contain a JSON object")
        return value

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            prefix=".reviews-", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temporary, self.path)
        except OSError as error:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise RuntimeError(f"Unable to write review store {self.path}: {error}") from error

    def get(self, repository: Path, finding: Any) -> ReviewDecision:
        data = self._read()
        value = data.get(repository_key(repository), {}).get(finding_key(finding))
        if not isinstance(value, dict):
            return ReviewDecision("pending_review")
        status = value.get("status")
        note = value.get("note", "")
        if status not in VALID_REVIEW_STATUSES or not isinstance(note, str):
            return ReviewDecision("pending_review")
        return ReviewDecision(status, note)

    def set(self, repository: Path, finding: Any, decision: ReviewDecision) -> None:
        if decision.status not in VALID_REVIEW_STATUSES:
            raise ValueError(f"Unsupported review status: {decision.status}")
        data = self._read()
        repo_data = data.setdefault(repository_key(repository), {})
        repo_data[finding_key(finding)] = decision.to_dict()
        self._write(data)

    def for_repository(self, repository: Path) -> dict[str, ReviewDecision]:
        data = self._read().get(repository_key(repository), {})
        if not isinstance(data, dict):
            return {}
        result: dict[str, ReviewDecision] = {}
        for key, value in data.items():
            if isinstance(value, dict) and value.get("status") in VALID_REVIEW_STATUSES:
                result[key] = ReviewDecision(value["status"], value.get("note", ""))
        return result

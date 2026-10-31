import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from cryptgit_studio.git_history import (
    GitCommandError,
    GitUnavailableError,
    NotGitRepositoryError,
    _timestamp,
    get_commits,
    track_function_history,
    verify_history_observation,
)
from cryptgit_studio.models import HistoryCommit, HistoryObservation


class GitHistoryTests(unittest.TestCase):
    def create_repository(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        repository = Path(directory.name)
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.name", "Test User"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "config",
                "user.email",
                "test@example.com",
            ],
            check=True,
        )
        return repository

    def commit(self, repository: Path, message: str) -> None:
        subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-q", "-m", message],
            check=True,
        )

    def test_reads_recent_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.name", "Test User"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "config",
                    "user.email",
                    "test@example.com",
                ],
                check=True,
            )
            (repository / "README.md").write_text("initial", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "commit", "-q", "-m", "Initial commit"],
                check=True,
            )

            commits = get_commits(repository, limit=1)

            self.assertEqual(1, len(commits))
            self.assertEqual("Test User", commits[0].author)
            self.assertEqual("Initial commit", commits[0].subject)

    def test_rejects_invalid_limit(self) -> None:
        with self.assertRaises(ValueError):
            get_commits(Path("."), limit=0)

    def test_tracks_first_observation_and_function_change(self) -> None:
        repository = self.create_repository()
        path = repository / "module.py"
        path.write_text("def _helper():\n    return 1\n", encoding="utf-8")
        self.commit(repository, "Add helper")
        path.write_text("def _helper():\n    return 2\n", encoding="utf-8")
        self.commit(repository, "Change helper")

        history = track_function_history(repository, "module.py", "_helper")

        self.assertEqual(2, history.history_commit_count)
        self.assertFalse(history.history_truncated)
        self.assertTrue(history.history_complete)
        self.assertEqual("Add helper", history.first_observed_commit.commit.subject)
        self.assertEqual("Change helper", history.last_function_change_commit.commit.subject)
        self.assertEqual("Change helper", history.last_file_change_commit.subject)
        self.assertNotEqual(
            history.first_observed_commit.ast_digest,
            history.last_function_change_commit.ast_digest,
        )

    def test_file_change_without_function_change_is_distinguished(self) -> None:
        repository = self.create_repository()
        path = repository / "module.py"
        path.write_text("def _helper():\n    return 1\n", encoding="utf-8")
        self.commit(repository, "Add helper")
        path.write_text("# unrelated comment\n\ndef _helper():\n    return 1\n", encoding="utf-8")
        self.commit(repository, "Update comment")

        history = track_function_history(repository, "module.py", "_helper")

        self.assertEqual("Update comment", history.last_file_change_commit.subject)
        self.assertIsNone(history.last_function_change_commit)

    def test_empty_history_and_invalid_repository_are_unavailable(self) -> None:
        repository = self.create_repository()
        empty = track_function_history(repository, "module.py", "_helper")
        self.assertFalse(empty.history_complete)
        self.assertTrue(empty.history_warnings)
        with tempfile.TemporaryDirectory() as directory:
            history = track_function_history(Path(directory), "module.py", "_helper")
        self.assertFalse(history.history_complete)
        self.assertTrue(any("Git" in warning or "repository" in warning for warning in history.history_warnings))

    def test_missing_historical_file_and_truncated_history_are_reported(self) -> None:
        repository = self.create_repository()
        path = repository / "module.py"
        path.write_text("def _helper():\n    return 1\n", encoding="utf-8")
        self.commit(repository, "Add helper")
        path.unlink()
        self.commit(repository, "Delete module")
        path.write_text("def _helper():\n    return 2\n", encoding="utf-8")
        self.commit(repository, "Reintroduce helper")

        history = track_function_history(repository, "module.py", "_helper", limit=1)

        self.assertTrue(history.history_truncated)
        self.assertFalse(history.history_complete)

    def test_git_command_failure_is_reported_without_raising(self) -> None:
        repository = self.create_repository()
        history = track_function_history(
            repository,
            "missing.py",
            "_helper",
            timeout=0.000001,
        )
        self.assertFalse(history.history_complete)
        self.assertTrue(history.history_warnings)

    def test_verifies_correct_evidence(self) -> None:
        repository = self.create_repository()
        path = repository / "module.py"
        path.write_text("def _helper():\n    return 1\n", encoding="utf-8")
        self.commit(repository, "Add helper")
        observation = track_function_history(repository, "module.py", "_helper").first_observed_commit

        result = verify_history_observation(repository, observation)

        self.assertEqual("verified", result.status)
        self.assertEqual(observation.ast_digest, result.observed_ast_digest)

    def test_verification_reports_digest_and_location_mismatches(self) -> None:
        repository = self.create_repository()
        path = repository / "module.py"
        path.write_text("def _helper():\n    return 1\n", encoding="utf-8")
        self.commit(repository, "Add helper")
        observation = track_function_history(repository, "module.py", "_helper").first_observed_commit

        digest_result = verify_history_observation(
            repository, replace(observation, ast_digest="0" * 64)
        )
        location_result = verify_history_observation(
            repository, replace(observation, start_line=2)
        )

        self.assertEqual("mismatch", digest_result.status)
        self.assertEqual("mismatch", location_result.status)

    def test_verification_reports_missing_commit_file_and_symbol(self) -> None:
        repository = self.create_repository()
        path = repository / "module.py"
        path.write_text("def _helper():\n    return 1\n", encoding="utf-8")
        self.commit(repository, "Add helper")
        observation = track_function_history(repository, "module.py", "_helper").first_observed_commit

        missing_commit = verify_history_observation(
            repository, replace(observation, commit=replace(observation.commit, hash="a" * 40))
        )
        missing_file = verify_history_observation(
            repository, replace(observation, file="missing.py")
        )
        missing_symbol = verify_history_observation(
            repository, replace(observation, symbol="_missing")
        )

        self.assertEqual("missing_commit", missing_commit.status)
        self.assertEqual("missing_file", missing_file.status)
        self.assertEqual("missing_symbol", missing_symbol.status)

    def test_verification_reports_invalid_hash_syntax_and_git_unavailable(self) -> None:
        repository = self.create_repository()
        invalid = HistoryObservation(
            commit=HistoryCommit("bad", "timestamp", "subject"),
            file="module.py",
            symbol="_helper",
            start_line=1,
            end_line=1,
            ast_digest="0" * 64,
        )
        self.assertEqual("unsupported", verify_history_observation(repository, invalid).status)
        path = repository / "module.py"
        path.write_bytes(b"def _helper(:\n")
        self.commit(repository, "Broken source")
        commit = get_commits(repository, limit=1)[0]
        timestamp = subprocess.check_output(
            ["git", "-C", str(repository), "show", "-s", "--format=%ct", commit.hash],
            text=True,
        ).strip()
        broken = HistoryObservation(
            commit=HistoryCommit(commit.hash, _timestamp(timestamp), commit.subject),
            file="module.py",
            symbol="_helper",
            start_line=1,
            end_line=1,
            ast_digest="0" * 64,
        )
        self.assertEqual("unsupported", verify_history_observation(repository, broken).status)
        valid_hash = "b" * 40
        with patch(
            "cryptgit_studio.git_history._run_git",
            side_effect=GitUnavailableError("Git executable was not found"),
        ):
            unavailable = verify_history_observation(
                repository, replace(invalid, commit=replace(invalid.commit, hash=valid_hash))
            )
        self.assertEqual("unavailable", unavailable.status)

    def test_historical_verification_ignores_uncommitted_working_tree_changes(self) -> None:
        repository = self.create_repository()
        path = repository / "module.py"
        path.write_text("def _helper():\n    return 1\n", encoding="utf-8")
        self.commit(repository, "Add helper")
        observation = track_function_history(repository, "module.py", "_helper").first_observed_commit
        path.write_text("def _helper():\n    return 999\n", encoding="utf-8")

        result = verify_history_observation(repository, observation)

        self.assertEqual("verified", result.status)


if __name__ == "__main__":
    unittest.main()

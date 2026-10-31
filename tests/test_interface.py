import json
import tempfile
import unittest
from pathlib import Path

from cryptgit_studio.models import AnalysisFinding, Reference, SourceLocation
from cryptgit_studio.reporting import report_json, report_markdown
from cryptgit_studio.review_store import ReviewDecision, ReviewStore, finding_key


class InterfaceTests(unittest.TestCase):
    def finding(self, line_start: int = 1) -> AnalysisFinding:
        return AnalysisFinding(
            file="module.py",
            symbol="_helper",
            line_start=line_start,
            line_end=line_start + 1,
            classification="potentially_unreferenced",
            heuristic_score=0.85,
            references=(),
            explanation="No incoming static reference was detected.",
        )

    def test_review_status_and_note_persist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory) / "reviews.json")
            repository = Path(directory) / "repo"
            repository.mkdir()
            finding = self.finding()
            store.set(repository, finding, ReviewDecision("needs_investigation", "Check plugins"))
            self.assertEqual(
                ReviewDecision("needs_investigation", "Check plugins"),
                store.get(repository, finding),
            )

    def test_reviews_are_repository_specific(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory) / "reviews.json")
            repository_a = Path(directory) / "a"
            repository_b = Path(directory) / "b"
            repository_a.mkdir()
            repository_b.mkdir()
            finding = self.finding()
            store.set(repository_a, finding, ReviewDecision("in_use"))
            self.assertEqual("in_use", store.get(repository_a, finding).status)
            self.assertEqual("pending_review", store.get(repository_b, finding).status)

    def test_changed_location_does_not_reuse_prior_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory) / "reviews.json")
            repository = Path(directory) / "repo"
            repository.mkdir()
            store.set(repository, self.finding(1), ReviewDecision("confirmed_unused"))
            self.assertEqual("pending_review", store.get(repository, self.finding(10)).status)

    def test_exports_keep_deterministic_ai_and_review_sections_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            finding = self.finding()
            from cryptgit_studio.models import ScanSummary

            summary = ScanSummary(
                schema_version="1.1",
                repository_path="repo",
                python_files_scanned=1,
                functions_discovered=1,
                classification_counts={"potentially_unreferenced": 1},
                findings=(finding,),
                ai_report={
                    "enabled": True,
                    "model": "local",
                    "interpretations": [
                        {
                            "finding_id": "finding-test",
                            "summary": "Review manually",
                            "uncertainty_explanation": "Dynamic callers may exist.",
                            "human_review_recommendation": "Inspect registrations.",
                            "alternative_explanations": [],
                            "dynamic_usage_risks": [],
                        }
                    ],
                    "errors": [],
                },
            )
            reviews = {finding_key(finding): ReviewDecision("in_use", "Verified caller")}
            encoded = json.loads(report_json(summary, repository, reviews))
            self.assertIn("deterministic_scan", encoded)
            self.assertIn("human_review", encoded)
            self.assertIn("ai", encoded["deterministic_scan"])
            markdown = report_markdown(summary, repository, reviews)
            self.assertIn("Deterministic findings", markdown)
            self.assertIn("Human review", markdown)
            self.assertIn("Heuristic score (not a probability)", markdown)


if __name__ == "__main__":
    unittest.main()

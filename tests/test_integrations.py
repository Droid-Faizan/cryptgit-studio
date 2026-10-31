import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cryptgit_studio.appwrite_store import AppwriteConfig, AppwritePersistence
from cryptgit_studio.models import AnalysisFinding, ScanSummary
from cryptgit_studio.review_store import ReviewDecision, finding_key
from cryptgit_studio.telemetry import configure_sentry, stage


class IntegrationTests(unittest.TestCase):
    def summary(self) -> ScanSummary:
        finding = AnalysisFinding(
            "module.py", "_helper", 1, 2, "potentially_unreferenced", 0.85, (), "review"
        )
        return ScanSummary(
            "1.1", "demo", 1, 1, {"potentially_unreferenced": 1}, (finding,)
        )

    def test_appwrite_tablesdb_save_and_list(self) -> None:
        tables = Mock()
        tables.list_rows.return_value = {"rows": [{"$id": "scan"}]}
        config = AppwriteConfig()
        config.endpoint = "http://localhost"
        config.project_id = "project"
        config.api_key = "secret"
        config.database_id = "database"
        config.scans_table_id = "scans"
        config.findings_table_id = "findings"
        service = AppwritePersistence(config, tables)
        with tempfile.TemporaryDirectory() as directory:
            finding = self.summary().findings[0]
            scan_id = service.save_scan(
                self.summary(),
                {finding_key(finding): ReviewDecision("needs_investigation", "note")},
            )
        self.assertTrue(scan_id)
        self.assertEqual(2, tables.create_row.call_count)
        self.assertEqual([{"$id": "scan"}], service.list_scans())

    def test_appwrite_scan_payload_uses_repository_folder_name(self) -> None:
        tables = Mock()
        config = AppwriteConfig()
        config.endpoint = "http://localhost"
        config.project_id = "project"
        config.api_key = "secret"
        config.database_id = "database"
        config.scans_table_id = "scans"
        config.findings_table_id = "findings"
        service = AppwritePersistence(config, tables)
        absolute_path = r"C:\Users\developer\repositories\sample-project"
        summary = ScanSummary(
            "1.1",
            absolute_path,
            1,
            1,
            {"potentially_unreferenced": 1},
            self.summary().findings,
        )

        service.save_scan(summary, {})

        scan_payload = tables.create_row.call_args_list[0].args[3]
        self.assertEqual("sample-project", scan_payload["repository"])
        self.assertNotIn(absolute_path, scan_payload.values())

    def test_appwrite_offline_is_graceful(self) -> None:
        service = AppwritePersistence(AppwriteConfig())
        self.assertFalse(service.available)
        with self.assertRaises(RuntimeError):
            service.save_scan(self.summary(), {})

    def test_sentry_is_opt_in(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(configure_sentry())
            with stage("cryptgit.test"):
                pass


if __name__ == "__main__":
    unittest.main()

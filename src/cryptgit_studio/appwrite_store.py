"""Optional Appwrite TablesDB persistence for scan summaries and reviews."""

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AppwriteConfig:
    def __init__(self) -> None:
        self.endpoint = os.getenv("APPWRITE_ENDPOINT")
        self.project_id = os.getenv("APPWRITE_PROJECT_ID")
        self.api_key = os.getenv("APPWRITE_API_KEY")
        self.database_id = os.getenv("APPWRITE_DATABASE_ID")
        self.scans_table_id = os.getenv("APPWRITE_SCANS_TABLE_ID")
        self.findings_table_id = os.getenv("APPWRITE_FINDINGS_TABLE_ID")

    @property
    def configured(self) -> bool:
        return all(
            (
                self.endpoint,
                self.project_id,
                self.api_key,
                self.database_id,
                self.scans_table_id,
                self.findings_table_id,
            )
        )


class AppwritePersistence:
    """Thin SDK adapter; no source code or secrets are sent."""

    def __init__(self, config: AppwriteConfig | None = None, tablesdb: Any = None) -> None:
        self.config = config or AppwriteConfig()
        if tablesdb is not None:
            self.tablesdb = tablesdb
        elif self.config.configured:
            try:
                from appwrite.client import Client
                from appwrite.services.tables_db import TablesDB
            except ImportError as error:
                raise RuntimeError("Install the Appwrite SDK to enable persistence") from error
            client = (
                Client()
                .set_endpoint(self.config.endpoint)
                .set_project(self.config.project_id)
                .set_key(self.config.api_key)
            )
            self.tablesdb = TablesDB(client)
        else:
            self.tablesdb = None

    @property
    def available(self) -> bool:
        return self.tablesdb is not None and self.config.configured

    def save_scan(self, summary: Any, reviews: dict[str, Any]) -> str:
        if not self.available:
            raise RuntimeError("Appwrite is not configured")
        from appwrite.id import ID
        scan_id = ID.unique()
        payload = {
            "scan_id": scan_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "repository": Path(summary.repository_path).name,
            "python_files": summary.python_files_scanned,
            "functions": summary.functions_discovered,
            "classification_counts": json.dumps(
                summary.classification_counts, sort_keys=True
            ),
        }
        self.tablesdb.create_row(
            self.config.database_id, self.config.scans_table_id, scan_id, payload
        )
        for finding in summary.findings:
            decision = reviews.get(
                f"{finding.file}:{finding.symbol}:{finding.line_start}:{finding.line_end}"
            )
            self.tablesdb.create_row(
                self.config.database_id,
                self.config.findings_table_id,
                ID.unique(),
                {
                    "scan_id": scan_id,
                    "symbol": finding.symbol,
                    "file": finding.file,
                    "classification": finding.classification,
                    "evidence_status": json.dumps(
                        [
                            item.status
                            for item in (
                                finding.history.evidence_verifications
                                if finding.history
                                else ()
                            )
                        ]
                    ),
                    "review_status": getattr(decision, "status", "pending_review"),
                    "review_note": getattr(decision, "note", ""),
                },
            )
        return scan_id

    def list_scans(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.available:
            return []
        result = self.tablesdb.list_rows(
            self.config.database_id, self.config.scans_table_id, queries=[]
        )
        rows = result.get("rows", []) if isinstance(result, dict) else []
        return rows[:limit]

    def update_review(self, row_id: str, status: str, note: str) -> Any:
        if not self.available:
            raise RuntimeError("Appwrite is not configured")
        return self.tablesdb.update_row(
            self.config.database_id,
            self.config.findings_table_id,
            row_id,
            {"review_status": status, "review_note": note},
        )

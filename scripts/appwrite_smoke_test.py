"""Real Appwrite TablesDB smoke test for CryptGit Studio.

Run this script from the PowerShell process that contains the APPWRITE_* variables.
It uses synthetic rows only and removes the rows it creates after verification.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any


REQUIRED_VARIABLES = (
    "APPWRITE_ENDPOINT",
    "APPWRITE_PROJECT_ID",
    "APPWRITE_API_KEY",
    "APPWRITE_DATABASE_ID",
    "APPWRITE_SCANS_TABLE_ID",
    "APPWRITE_FINDINGS_TABLE_ID",
)


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    data = getattr(row, "data", None)
    if isinstance(data, dict):
        return data.get(key)
    return getattr(row, key, None)


def _require_row_values(row: Any, expected: dict[str, Any]) -> None:
    for key, value in expected.items():
        actual = _row_value(row, key)
        if actual != value:
            raise RuntimeError(
                f"Read-back verification failed for field '{key}'"
            )


def _sanitize_message(message: str) -> str:
    """Remove configured secret values and credential-bearing URLs."""
    sanitized = message
    for name in REQUIRED_VARIABLES:
        value = os.environ.get(name)
        if value:
            sanitized = sanitized.replace(value, f"<{name}>")
    sanitized = re.sub(
        r"https?://[^/\s]+(?::[^@\s]+)?@",
        "https://<redacted>@",
        sanitized,
    )
    sanitized = re.sub(
        r"(api[_-]?key|secret|token|password)\s*[=:]\s*[^,\s]+",
        r"\1=<redacted>",
        sanitized,
        flags=re.IGNORECASE,
    )
    return sanitized[:500]


def _report_exception(error: Exception) -> None:
    status = getattr(error, "code", None)
    if not isinstance(status, int):
        status = getattr(error, "status_code", None)
    error_type = getattr(error, "type", None)
    if not isinstance(error_type, str):
        error_type = getattr(error, "error_type", None)
    print(f"Exception class: {type(error).__name__}")
    print(f"Appwrite HTTP status: {status if status is not None else 'unavailable'}")
    print(
        "Appwrite error type: "
        f"{error_type if error_type else 'unavailable'}"
    )
    print(f"Sanitized message: {_sanitize_message(str(error))}")


def main() -> int:
    missing = [
        name
        for name in REQUIRED_VARIABLES
        if not os.environ.get(name)
    ]
    if missing:
        print("Environment configuration: FAIL")
        print("Missing variables: " + ", ".join(missing))
        return 1

    try:
        from appwrite.client import Client
        from appwrite.id import ID
        from appwrite.services.tables_db import TablesDB
    except ImportError as error:
        print(f"Appwrite connection: FAIL ({error})")
        return 1

    database_id = os.environ["APPWRITE_DATABASE_ID"]
    scans_table_id = os.environ["APPWRITE_SCANS_TABLE_ID"]
    findings_table_id = os.environ["APPWRITE_FINDINGS_TABLE_ID"]
    scan_id = ID.unique()
    finding_id: str | None = None
    client = (
        Client()
        .set_endpoint(os.environ["APPWRITE_ENDPOINT"])
        .set_project(os.environ["APPWRITE_PROJECT_ID"])
        .set_key(os.environ["APPWRITE_API_KEY"])
    )
    tables = TablesDB(client)
    print("Environment configuration: PASS")

    scan_payload = {
        "scan_id": scan_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repository": "cryptgit-appwrite-smoke-test",
        "python_files": 1,
        "functions": 1,
        "classification_counts": json.dumps(
            {"potentially_unreferenced": 1},
            sort_keys=True,
        ),
    }
    finding_payload = {
        "scan_id": scan_id,
        "symbol": "_smoke_test_function",
        "file": "synthetic.py",
        "classification": "potentially_unreferenced",
        "evidence_status": "[]",
        "review_status": "pending_review",
        "review_note": "Synthetic Appwrite smoke test",
    }
    created = False
    finding_created = False
    verified = False
    try:
        tables.create_row(database_id, scans_table_id, scan_id, scan_payload)
        created = True
        scan_row = tables.get_row(database_id, scans_table_id, scan_id)
        _require_row_values(scan_row, scan_payload)
        print("Appwrite connection: PASS")
        print("Scan create/read: PASS")

        finding_id = ID.unique()
        tables.create_row(
            database_id,
            findings_table_id,
            finding_id,
            finding_payload,
        )
        finding_created = True
        finding_row = tables.get_row(database_id, findings_table_id, finding_id)
        _require_row_values(finding_row, finding_payload)
        print("Finding create/read: PASS")

        updated_payload = {
            "review_status": "needs_investigation",
            "review_note": "Updated synthetic smoke-test note",
        }
        tables.update_row(
            database_id,
            findings_table_id,
            finding_id,
            updated_payload,
        )
        updated_row = tables.get_row(
            database_id,
            findings_table_id,
            finding_id,
        )
        _require_row_values(updated_row, updated_payload)
        print("Review update/read: PASS")
        verified = True
    except Exception as error:
        print("Smoke test: FAIL")
        _report_exception(error)
        print("No secret values, source code, prompts, or model responses are printed.")
        return 1
    finally:
        if verified:
            cleanup_errors: list[str] = []
            if finding_created and finding_id is not None:
                try:
                    tables.delete_row(database_id, findings_table_id, finding_id)
                except Exception as error:
                    cleanup_errors.append(f"finding ({type(error).__name__})")
            if created:
                try:
                    tables.delete_row(database_id, scans_table_id, scan_id)
                except Exception as error:
                    cleanup_errors.append(f"scan ({type(error).__name__})")
            if cleanup_errors:
                print("Cleanup: FAIL (" + ", ".join(cleanup_errors) + ")")
                return 1
            print("Cleanup: PASS")

    return 0


if __name__ == "__main__":
    sys.exit(main())

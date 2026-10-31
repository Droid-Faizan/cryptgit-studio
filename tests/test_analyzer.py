import io
import json
import tempfile
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cryptgit_studio.analyzer import analyze_repository
from cryptgit_studio.cli import main
from cryptgit_studio.models import RepositoryFile
from cryptgit_studio.scanner import discover_repository


class AnalyzerTests(unittest.TestCase):
    def write(self, root: Path, relative: str, content: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def finding(self, root: Path, symbol: str):
        return {item.symbol: item for item in analyze_repository(root).findings}[symbol]

    def test_classifies_required_fixture_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "provider.py",
                "def _unused():\n    return 1\n\n"
                "def _helper():\n    return 2\n\n"
                "def _caller():\n    return _helper()\n\n"
                "def called():\n    return 3\n",
            )
            self.write(
                root,
                "consumer.py",
                "from provider import called\n\n"
                "def use_it():\n    return called()\n\n"
                "def public_api():\n    return 3\n\n"
                "@register\n"
                "def decorated():\n    return 4\n",
            )
            self.write(
                root,
                "tests/test_sample.py",
                "def test_function():\n    return None\n",
            )
            self.write(
                root,
                "async_module.py",
                "async def fetch_data():\n    return None\n",
            )
            self.write(root, "broken.py", "def broken(:\n")
            self.write(root, ".venv/ignored.py", "def ignored():\n    return None\n")
            self.write(
                root, "__pycache__/ignored.py", "def ignored_cache():\n    return None\n"
            )

            summary = analyze_repository(root)
            findings = {finding.symbol: finding for finding in summary.findings}

            self.assertEqual(5, summary.python_files_scanned)
            self.assertEqual(9, summary.functions_discovered)
            self.assertEqual("potentially_unreferenced", findings["_unused"].classification)
            self.assertEqual("referenced", findings["_helper"].classification)
            self.assertTrue(findings["_helper"].references)
            self.assertEqual("referenced", findings["called"].classification)
            self.assertEqual("not_assessed", findings["use_it"].classification)
            self.assertEqual("not_assessed", findings["public_api"].classification)
            self.assertEqual("uncertain", findings["decorated"].classification)
            self.assertEqual("not_assessed", findings["test_function"].classification)
            self.assertEqual("not_assessed", findings["fetch_data"].classification)
            self.assertTrue(findings["called"].references)
            self.assertTrue(any("syntax error" in error for error in summary.parsing_errors))

    def test_json_is_stable_and_cli_emits_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "module.py", "def _unused():\n    return None\n")
            first = analyze_repository(root).to_dict()
            second = analyze_repository(root).to_dict()
            self.assertEqual(
                json.dumps(first, indent=2, sort_keys=True),
                json.dumps(second, indent=2, sort_keys=True),
            )

            output = io.StringIO()
            with redirect_stdout(output):
                original_argv = __import__("sys").argv
                __import__("sys").argv = [
                    "cryptgit-studio",
                    "scan",
                    str(root),
                    "--json",
                ]
                try:
                    self.assertEqual(0, main())
                finally:
                    __import__("sys").argv = original_argv
            report = json.loads(output.getvalue())
            self.assertEqual("1.1", report["schema_version"])
            self.assertEqual(1, report["classification_counts"]["potentially_unreferenced"])
            self.assertEqual("_unused", report["findings"][0]["symbol"])

    def test_self_recursive_function_is_not_an_incoming_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "module.py", "def _walk():\n    return _walk()\n")
            finding = self.finding(root, "_walk")
            self.assertEqual("potentially_unreferenced", finding.classification)
            self.assertEqual(1, len(finding.references))
            self.assertTrue(finding.references[0].self_reference)
            self.assertTrue(finding.explanation.startswith("Heuristic score"))

    def test_recursive_function_with_external_call_is_referenced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "module.py",
                "def _walk():\n    return _walk()\n\n"
                "def run():\n    return _walk()\n",
            )
            finding = self.finding(root, "_walk")
            self.assertEqual("referenced", finding.classification)
            self.assertEqual(2, len(finding.references))
            self.assertEqual(
                1, sum(not reference.self_reference for reference in finding.references)
            )

    def test_parameter_and_local_shadowing_do_not_reference_module_function(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "module.py",
                "def _helper():\n    return 1\n\n"
                "def parameter_use(_helper):\n    return _helper()\n\n"
                "def local_use():\n    _helper = lambda: 2\n    return _helper()\n",
            )
            self.assertEqual(
                "potentially_unreferenced", self.finding(root, "_helper").classification
            )

    def test_nested_function_reference_counts_as_incoming(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "module.py",
                "def _helper():\n    return 1\n\n"
                "def outer():\n"
                "    def nested():\n"
                "        return _helper()\n"
                "    return nested()\n",
            )
            finding = self.finding(root, "_helper")
            self.assertEqual("referenced", finding.classification)
            self.assertEqual("nested", finding.references[0].source_symbol)

    def test_imported_function_and_module_aliases_resolve_original_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "module_a.py", "def _helper():\n    return 1\n")
            self.write(
                root,
                "consumer.py",
                "from module_a import _helper as renamed_helper\n"
                "import module_a as alias\n\n"
                "def use():\n"
                "    renamed_helper()\n"
                "    alias._helper()\n",
            )
            finding = self.finding(root, "_helper")
            self.assertEqual("referenced", finding.classification)
            self.assertEqual(
                {"renamed_helper", "alias"},
                {reference.local_name for reference in finding.references},
            )
            self.assertTrue(
                all(reference.target_name == "_helper" for reference in finding.references)
            )

    def test_package_relative_import_and_init_reexport_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "package/__init__.py", "from .module_a import _helper\n")
            self.write(root, "package/module_a.py", "def _helper():\n    return 1\n")
            self.write(
                root,
                "package/consumer.py",
                "from . import _helper as imported_helper\n"
                "def use():\n"
                "    return imported_helper()\n",
            )
            finding = self.finding(root, "_helper")
            self.assertEqual("referenced", finding.classification)
            self.assertTrue(
                all(reference.target_module == "package.module_a" for reference in finding.references)
            )

    def test_encoding_bom_and_latin1_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bom.py").write_text("def _bom():\n    return 1\n", encoding="utf-8-sig")
            (root / "latin.py").write_bytes(
                "# -*- coding: latin-1 -*-\n"
                "def _latin():\n"
                "    return 'caf\xe9'\n".encode("latin-1")
            )
            summary = analyze_repository(root)
            self.assertEqual(2, summary.functions_discovered)
            self.assertFalse(summary.parsing_errors)

    def test_scanner_reports_traversal_errors_and_incomplete_coverage(self) -> None:
        def fake_walk(root, topdown, onerror):
            onerror(PermissionError("denied"))
            yield str(root), [], []

        with tempfile.TemporaryDirectory() as directory:
            with patch("cryptgit_studio.scanner.os.walk", fake_walk):
                _, errors, _, incomplete = discover_repository(Path(directory))
            self.assertTrue(errors)
            self.assertTrue(incomplete)

    def test_analysis_propagates_incomplete_discovery_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "cryptgit_studio.analyzer.discover_repository",
                return_value=(
                    [RepositoryFile(Path("module.py"), 0)],
                    ["blocked: unable to access directory"],
                    [],
                    True,
                ),
            ):
                summary = analyze_repository(root)
            self.assertTrue(summary.coverage_incomplete)
            self.assertTrue(summary.parsing_errors)

    def test_symlink_is_skipped_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "real.py", "def _real():\n    return 1\n")
            link = root / "link.py"
            try:
                link.symlink_to(root / "real.py")
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable on this platform")
            files, _, warnings, _ = discover_repository(root)
            self.assertEqual([Path("real.py")], [item.path for item in files])
            self.assertTrue(any("symlink skipped" in warning for warning in warnings))

    def test_unsupported_imports_are_reported_and_json_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "module.py", "from unknown import *\n")
            first = analyze_repository(root).to_dict()
            second = analyze_repository(root).to_dict()
            self.assertEqual(first, second)
            self.assertTrue(first["analysis_warnings"])
            self.assertIn("coverage_incomplete", first)

    def test_standard_library_imports_are_not_reported_as_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "module.py",
                "import ast\n"
                "import json\n"
                "from dataclasses import dataclass\n"
                "from typing import Any\n"
                "from xml.etree import ElementTree\n\n"
                "def _helper() -> Any:\n"
                "    return ElementTree.Element('x')\n",
            )
            summary = analyze_repository(root)
            self.assertFalse(
                any("unresolved" in warning for warning in summary.analysis_warnings)
            )

    def test_unresolved_third_party_imports_still_warn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "module.py",
                "import definitely_missing_package\n\n"
                "def _helper():\n"
                "    return definitely_missing_package.value\n",
            )
            summary = analyze_repository(root)
            self.assertTrue(
                any("definitely_missing_package" in warning for warning in summary.analysis_warnings)
            )

    def test_non_git_directory_keeps_ast_results_and_clear_history_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "module.py", "def _helper():\n    return 1\n")
            summary = analyze_repository(root, include_history=True, verify_evidence=True)
            self.assertEqual(1, summary.functions_discovered)
            self.assertIsNotNone(summary.findings[0].history)
            self.assertFalse(summary.findings[0].history.history_complete)
            self.assertTrue(
                any(
                    "Git history unavailable" in warning
                    for warning in summary.findings[0].history.history_warnings
                )
            )
            self.assertFalse(
                any(
                    verification.status == "verified"
                    for verification in summary.findings[0].history.evidence_verifications
                )
            )

    def test_history_enrichment_is_optional_and_serializable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "module.py", "def _helper():\n    return 1\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(
                ["git", "-C", str(root), "config", "user.name", "Test User"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(root), "commit", "-q", "-m", "Add helper"],
                check=True,
            )
            summary = analyze_repository(root, include_history=True)
            finding = next(item for item in summary.findings if item.symbol == "_helper")
            report = summary.to_dict()
            self.assertIsNotNone(finding.history)
            self.assertEqual("Add helper", report["findings"][0]["history"]["first_observed_commit"]["commit"]["subject"])


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from cryptgit_studio.scanner import scan_repository


class ScannerTests(unittest.TestCase):
    def test_scans_regular_files_and_excludes_git_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "module.py").write_text("print('ok')", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("internal", encoding="utf-8")

            files = scan_repository(root)

            self.assertEqual([Path("src/module.py")], [item.path for item in files])

    def test_rejects_non_directory(self) -> None:
        with tempfile.NamedTemporaryFile() as file:
            with self.assertRaises(NotADirectoryError):
                scan_repository(Path(file.name))


if __name__ == "__main__":
    unittest.main()

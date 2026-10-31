"""Filesystem scanning helpers."""

import os
from pathlib import Path

from .models import RepositoryFile

EXCLUDED_DIRECTORIES = frozenset(
    {".git", ".venv", "venv", "__pycache__", "node_modules", "build", "dist", ".tox"}
)


def discover_repository(root: Path) -> tuple[list[RepositoryFile], list[str], list[str], bool]:
    """Discover files and return files, errors, warnings, and coverage status."""
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Repository path is not a directory: {root}")

    files: list[RepositoryFile] = []
    errors: list[str] = []
    warnings: list[str] = []
    coverage_incomplete = False

    def on_walk_error(error: OSError) -> None:
        nonlocal coverage_incomplete
        coverage_incomplete = True
        errors.append(f"{error.filename or root}: unable to access directory ({error})")

    for directory, directories, filenames in os.walk(
        root, topdown=True, onerror=on_walk_error
    ):
        kept_directories: list[str] = []
        for name in directories:
            candidate = Path(directory) / name
            if name in EXCLUDED_DIRECTORIES:
                continue
            if candidate.is_symlink():
                warnings.append(
                    f"{candidate.relative_to(root).as_posix()}: symlink directory skipped by policy"
                )
                continue
            kept_directories.append(name)
        directories[:] = sorted(kept_directories)
        for filename in sorted(filenames):
            path = Path(directory) / filename
            try:
                if path.is_symlink():
                    warnings.append(
                        f"{path.relative_to(root).as_posix()}: symlink skipped by policy"
                    )
                    continue
                if path.is_file():
                    files.append(
                        RepositoryFile(
                            path=path.relative_to(root), size_bytes=path.stat().st_size
                        )
                    )
            except OSError:
                coverage_incomplete = True
                errors.append(f"{path.relative_to(root).as_posix()}: unable to inspect file")
                continue
    return (
        sorted(files, key=lambda item: item.path.as_posix()),
        sorted(errors),
        sorted(warnings),
        coverage_incomplete,
    )


def scan_repository(root: Path) -> list[RepositoryFile]:
    """Return regular, non-symlink files below *root*."""
    return discover_repository(root)[0]

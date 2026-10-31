"""Create the CryptGit Studio final demonstration repository."""

from pathlib import Path
import subprocess
import sys


def run(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def write(repository: Path, relative: str, content: str) -> None:
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def commit(repository: Path, message: str) -> None:
    run(repository, "add", ".")
    run(repository, "commit", "-m", message)


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "cryptgit-demo").resolve()
    if target.exists():
        raise SystemExit(f"Refusing to overwrite existing path: {target}")
    target.mkdir(parents=True)
    run(target, "init")
    run(target, "config", "user.email", "demo@example.invalid")
    run(target, "config", "user.name", "CryptGit Demo")

    write(
        target,
        "demo_pkg/helpers.py",
        '''"""Historical helper implementations."""\n\n\ndef _changed_helper(value: int) -> int:\n    return value + 1\n\n\ndef referenced_function(value: int) -> int:\n    return _changed_helper(value)\n''',
    )
    write(
        target,
        "demo_pkg/__init__.py",
        '''from .helpers import referenced_function\n\n__all__ = ["referenced_function"]\n''',
    )
    commit(target, "Add referenced helper and public API")

    write(
        target,
        "demo_pkg/helpers.py",
        '''"""Historical helper implementations."""\n\n\ndef _changed_helper(value: int) -> int:\n    return value * 2\n\n\ndef referenced_function(value: int) -> int:\n    return _changed_helper(value)\n''',
    )
    commit(target, "Change helper implementation")

    write(
        target,
        "demo_pkg/helpers.py",
        '''"""Historical helper implementations."""\n\n\ndef _changed_helper(value: int) -> int:\n    return value * 2\n\n\ndef referenced_function(value: int) -> int:\n    return _changed_helper(value)\n\n\ndef _potentially_unused(value: int) -> int:\n    return value - 1\n\n\n@register_runtime_hook\ndef _dynamic_hook() -> None:\n    pass\n''',
    )
    commit(target, "Add candidate and dynamically registered hook")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

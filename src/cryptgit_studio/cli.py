"""Command-line interface for CryptGit Studio."""

import argparse
import json
import sys
from pathlib import Path

from .analyzer import analyze_repository
from .git_history import get_commits
from .ollama import OllamaProvider
from .scanner import scan_repository


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description="Inspect a local repository.")
    parser.add_argument(
        "repository",
        nargs="?",
        type=Path,
        default=Path("."),
        help="repository path (default: current directory)",
    )
    return parser


def main() -> int:
    """Run the command-line interface."""
    argv = sys.argv[1:]
    if argv and argv[0] == "scan":
        parser = argparse.ArgumentParser(description="Analyze a Python repository.")
        parser.add_argument("command", help=argparse.SUPPRESS)
        parser.add_argument("repository", type=Path)
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument(
            "--history",
            action="store_true",
            help="enrich function findings with bounded Git history",
        )
        parser.add_argument(
            "--history-limit",
            type=int,
            default=20,
            help="maximum file-history commits per function (default: 20)",
        )
        parser.add_argument(
            "--verify-evidence",
            action="store_true",
            help="verify generated historical evidence against Git snapshots",
        )
        parser.add_argument("--ai", action="store_true", help="interpret evidence with local Ollama")
        parser.add_argument("--ollama-endpoint", help="Ollama endpoint (default: OLLAMA_HOST or localhost)")
        parser.add_argument("--ollama-model", help="Ollama model (default: OLLAMA_MODEL or llama3.2)")
        parser.add_argument("--ollama-timeout", type=float, default=120.0)
        args = parser.parse_args(argv)
        provider = (
            OllamaProvider(args.ollama_endpoint, args.ollama_model, args.ollama_timeout)
            if args.ai
            else None
        )
        summary = analyze_repository(
            args.repository,
            include_history=args.history or args.verify_evidence,
            history_limit=args.history_limit,
            verify_evidence=args.verify_evidence,
            ai_provider=provider,
        )
        if args.as_json:
            print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
        else:
            print(f"Python files: {summary.python_files_scanned}")
            print(f"Functions: {summary.functions_discovered}")
            for classification, count in summary.classification_counts.items():
                print(f"{classification}: {count}")
            for error in summary.parsing_errors:
                print(f"error: {error}", file=sys.stderr)
            if summary.ai_report:
                for error in summary.ai_report["errors"]:
                    print(f"ai error: {error}", file=sys.stderr)
        return 0

    args = build_parser().parse_args(argv)
    files = scan_repository(args.repository)
    commits = get_commits(args.repository)
    print(f"Files: {len(files)}")
    print(f"Commits: {len(commits)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command line interface for the Smart Changelog tool."""
from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .updater import run_update


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smart-changelog",
        description="Automatically update CHANGELOG.md based on recent changes",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    update_parser = subparsers.add_parser("update", help="Update the changelog for the latest changes")
    update_parser.add_argument("--dry-run", action="store_true", help="Preview the changelog changes without writing to disk")
    update_parser.add_argument("--verbose", action="store_true", help="Enable verbose logging output")
    update_parser.add_argument("--ai", action="store_true", help="Use OpenAI to enrich changelog entries when possible")
    update_parser.add_argument("--ticket", help="Override ticket detection and force a specific ticket identifier")

    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if getattr(args, "verbose", False) else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    if args.command == "update":
        run_update(
            dry_run=args.dry_run,
            use_ai=args.ai,
            forced_ticket=args.ticket,
            verbose=args.verbose,
        )
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

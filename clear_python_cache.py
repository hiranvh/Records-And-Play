#!/usr/bin/env python3
"""Remove Python cache directories and files from a project tree.

Usage:
  python clear_python_cache.py
  python clear_python_cache.py --root . --dry-run --verbose
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


CACHE_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    ".nox",
    ".tox",
}

CACHE_FILE_SUFFIXES = {".pyc", ".pyo"}


def should_remove_dir(path: Path) -> bool:
    return path.name in CACHE_DIR_NAMES


def should_remove_file(path: Path) -> bool:
    return path.suffix in CACHE_FILE_SUFFIXES


def remove_caches(root: Path, dry_run: bool = False, verbose: bool = False) -> tuple[int, int]:
    removed_dirs = 0
    removed_files = 0

    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and should_remove_dir(path):
            if verbose or dry_run:
                action = "Would remove" if dry_run else "Removing"
                print(f"{action} directory: {path}")
            if not dry_run:
                shutil.rmtree(path, ignore_errors=False)
            removed_dirs += 1
            continue

        if path.is_file() and should_remove_file(path):
            if verbose or dry_run:
                action = "Would remove" if dry_run else "Removing"
                print(f"{action} file: {path}")
            if not dry_run:
                path.unlink(missing_ok=True)
            removed_files += 1

    return removed_dirs, removed_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove __pycache__ and other Python cache artifacts."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root to scan (default: current directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without deleting anything",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every removed path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()

    if not root.exists() or not root.is_dir():
        print(f"Invalid root directory: {root}")
        return 1

    removed_dirs, removed_files = remove_caches(
        root=root,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    mode = "Dry run complete" if args.dry_run else "Cleanup complete"
    print(
        f"{mode}. Directories: {removed_dirs}, Files: {removed_files}, Root: {root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

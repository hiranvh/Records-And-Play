#!/usr/bin/env python3
"""Remove cache artifacts from the current automation project tree.

By default this removes Python and common tool caches only.
Optional flags can also clear generated runtime artifacts such as logs,
screenshots, Excel reports, temp files, build outputs, and terminate known
automation-related processes.

If ``--root`` is omitted, the script auto-detects the repository root
from the current project layout so it works whether it is launched from
the workspace root or from the ``scripts/`` directory.

Usage:
    python scripts/clear_python_cache.py
    python scripts/clear_python_cache.py --root . --dry-run --verbose
    python scripts/clear_python_cache.py --include-artifacts --include-temp --include-build
    python scripts/clear_python_cache.py --include-logs --include-screenshots --include-reports
    python scripts/clear_python_cache.py --kill-processes
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path


CACHE_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    ".nox",
    ".tox",
    ".pytype",
    ".ipynb_checkpoints",
    ".cache",
    ".sass-cache",
    ".parcel-cache",
    ".webpack-cache",
    ".turbo",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".vite",
    "pip-wheel-metadata",
    "htmlcov",
    ".coverage_html",
    ".benchmarks",
    "PageArchive",
}

CACHE_FILE_SUFFIXES = {".pyc", ".pyo", ".pyd", ".tmp", ".temp"}

CACHE_FILE_NAMES = {
    ".coverage",
    ".coverage.xml",
    "coverage.xml",
    "pytestdebug.log",
    ".eslintcache",
}

LOG_FILE_PATTERNS = {
    "*.log",
    "*.logs",
}

LOG_DIR_NAMES = {
    "logs",
}

REPORT_FILE_SUFFIXES = {
    ".xlsx",
    ".xls",
    ".xlsm",
    ".csv",
    ".png"
}

TEMP_DIR_NAMES = {
    "tmp",
    "temp",
}

BUILD_DIR_NAMES = {
    "build",
    "dist",
    ".eggs",
    "site",
}

BUILD_FILE_PATTERNS = {
    "*.egg-info",
    "*.whl",
}


# Never delete inside virtual environments or VCS metadata.
SKIP_TOP_LEVEL_DIRS = {
    ".venv",
    "venv",
    ".git",
}

PROJECT_ROOT_MARKERS = {
    "agent",
    "app",
    "core",
    "playback",
    "recorder",
    "workflows",
    "requirements.txt",
}


def looks_like_project_root(path: Path) -> bool:
    return path.is_dir() and all((path / marker).exists() for marker in PROJECT_ROOT_MARKERS)


def detect_project_root() -> Path:
    cwd = Path.cwd().resolve()
    script_dir = Path(__file__).resolve().parent
    seen: set[Path] = set()

    for base in (cwd, script_dir):
        for candidate in (base, *base.parents):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if looks_like_project_root(resolved):
                return resolved

    return cwd


def should_remove_dir(path: Path) -> bool:
    return path.name in CACHE_DIR_NAMES


def should_remove_file(path: Path) -> bool:
    return path.suffix in CACHE_FILE_SUFFIXES or path.name in CACHE_FILE_NAMES


def is_inside_skipped_root(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True

    if not rel.parts:
        return False

    return rel.parts[0] in SKIP_TOP_LEVEL_DIRS


def should_prune_directory(path: Path, root: Path) -> bool:
    return is_inside_skipped_root(path, root)


def top_level_dir_name(path: Path, root: Path) -> str | None:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None

    if not rel.parts:
        return None

    return rel.parts[0]


def is_within_top_level_dirs(path: Path, root: Path, dir_names: set[str]) -> bool:
    return top_level_dir_name(path, root) in dir_names


def format_log_target(path: Path, root: Path | None = None) -> str:
    if root is None:
        return str(path)

    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def print_cleanup_log(entries: list[str], dry_run: bool) -> None:
    header = "Planned cleanup log:" if dry_run else "Cleared items log:"
    print(header)
    for entry in entries:
        print(f" - {entry}")


def remove_files_under_directory(
    target_dir: Path,
    root: Path,
    dry_run: bool,
    verbose: bool,
    kind: str,
    cleanup_log: list[str] | None = None,
    predicate: Callable[[Path], bool] | None = None,
) -> int:
    if not target_dir.exists() or not target_dir.is_dir():
        return 0

    removed = 0
    for path in sorted(target_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not path.is_file():
            continue
        if predicate is not None and not predicate(path):
            continue
        if remove_path(
            path,
            dry_run=dry_run,
            verbose=verbose,
            kind=kind,
            root=root,
            cleanup_log=cleanup_log,
        ):
            removed += 1

    return removed


def remove_path(
    path: Path,
    dry_run: bool,
    verbose: bool,
    kind: str,
    root: Path | None = None,
    cleanup_log: list[str] | None = None,
) -> bool:
    display_target = format_log_target(path, root)

    if verbose or dry_run:
        action = "Would remove" if dry_run else "Removing"
        print(f"{action} {kind}: {display_target}")

    if dry_run:
        if cleanup_log is not None:
            cleanup_log.append(f"Would remove {kind}: {display_target}")
        return True

    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=False)
        else:
            path.unlink(missing_ok=True)
        if cleanup_log is not None:
            cleanup_log.append(f"Removed {kind}: {display_target}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to remove {kind}: {path} ({exc})")
        return False


def remove_caches(
    root: Path,
    dry_run: bool = False,
    verbose: bool = False,
    cleanup_log: list[str] | None = None,
) -> tuple[int, int]:
    removed_dirs = 0
    removed_files = 0

    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if is_inside_skipped_root(path, root):
            continue

        if path.is_dir() and should_remove_dir(path):
            if remove_path(
                path,
                dry_run=dry_run,
                verbose=verbose,
                kind="directory",
                root=root,
                cleanup_log=cleanup_log,
            ):
                removed_dirs += 1
            continue

        if path.is_file() and should_remove_file(path):
            if remove_path(
                path,
                dry_run=dry_run,
                verbose=verbose,
                kind="file",
                root=root,
                cleanup_log=cleanup_log,
            ):
                removed_files += 1

    return removed_dirs, removed_files


def remove_logs(
    root: Path,
    dry_run: bool = False,
    verbose: bool = False,
    cleanup_log: list[str] | None = None,
) -> int:
    removed = remove_files_under_directory(
        root / "logs",
        root=root,
        dry_run=dry_run,
        verbose=verbose,
        kind="log artifact",
        cleanup_log=cleanup_log,
    )

    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if is_inside_skipped_root(path, root) or not path.is_file():
            continue

        if is_within_top_level_dirs(path, root, LOG_DIR_NAMES):
            continue

        if any(path.match(pattern) for pattern in LOG_FILE_PATTERNS):
            if remove_path(
                path,
                dry_run=dry_run,
                verbose=verbose,
                kind="log file",
                root=root,
                cleanup_log=cleanup_log,
            ):
                removed += 1

    return removed


def remove_screenshots(
    root: Path,
    dry_run: bool = False,
    verbose: bool = False,
    cleanup_log: list[str] | None = None,
) -> int:
    return remove_files_under_directory(
        root / "Screenshots",
        root=root,
        dry_run=dry_run,
        verbose=verbose,
        kind="screenshot",
        cleanup_log=cleanup_log,
    )


def remove_reports(
    root: Path,
    dry_run: bool = False,
    verbose: bool = False,
    cleanup_log: list[str] | None = None,
) -> int:
    return remove_files_under_directory(
        root / "reports",
        root=root,
        dry_run=dry_run,
        verbose=verbose,
        kind="report file",
        cleanup_log=cleanup_log,
        predicate=lambda path: path.suffix.lower() in REPORT_FILE_SUFFIXES,
    )


def remove_temp(
    root: Path,
    dry_run: bool = False,
    verbose: bool = False,
    cleanup_log: list[str] | None = None,
) -> tuple[int, int]:
    removed_dirs = 0
    removed_files = 0

    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if is_inside_skipped_root(path, root):
            continue

        if path.is_dir() and path.name.lower() in TEMP_DIR_NAMES:
            if remove_path(
                path,
                dry_run=dry_run,
                verbose=verbose,
                kind="temp directory",
                root=root,
                cleanup_log=cleanup_log,
            ):
                removed_dirs += 1
            continue

        if path.is_file() and path.suffix.lower() in {".tmp", ".temp"}:
            if remove_path(
                path,
                dry_run=dry_run,
                verbose=verbose,
                kind="temp file",
                root=root,
                cleanup_log=cleanup_log,
            ):
                removed_files += 1

    return removed_dirs, removed_files


def remove_build_outputs(
    root: Path,
    dry_run: bool = False,
    verbose: bool = False,
    cleanup_log: list[str] | None = None,
) -> tuple[int, int]:
    removed_dirs = 0
    removed_files = 0

    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if is_inside_skipped_root(path, root):
            continue

        if path.is_dir() and path.name in BUILD_DIR_NAMES:
            if remove_path(
                path,
                dry_run=dry_run,
                verbose=verbose,
                kind="build directory",
                root=root,
                cleanup_log=cleanup_log,
            ):
                removed_dirs += 1
            continue

        if path.is_file() and any(path.match(pattern) for pattern in BUILD_FILE_PATTERNS):
            if remove_path(
                path,
                dry_run=dry_run,
                verbose=verbose,
                kind="build file",
                root=root,
                cleanup_log=cleanup_log,
            ):
                removed_files += 1

    return removed_dirs, removed_files


def kill_automation_processes(verbose: bool = False, cleanup_log: list[str] | None = None) -> int:
    processes = [
        "node.exe",
        "ollama.exe",
        "msedge.exe",
        "chrome.exe",
        "firefox.exe",
        "playwright.cmd",
    ]
    killed = 0

    for proc in processes:
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/IM", proc],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0:
                killed += 1
                if cleanup_log is not None:
                    cleanup_log.append(f"Killed process: {proc}")
                if verbose:
                    print(f"Killed process: {proc}")
            elif verbose:
                print(f"No running process found: {proc}")
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"Failed to kill process {proc}: {exc}")

    return killed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove cache and optional generated artifacts from a project tree."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Project root to scan (default: auto-detect repository root)",
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
    parser.add_argument(
        "--include-logs",
        action="store_true",
        help="Also delete generated files under logs/ plus standalone *.log and *.logs files",
    )
    parser.add_argument(
        "--include-screenshots",
        action="store_true",
        help="Also delete generated files under Screenshots/",
    )
    parser.add_argument(
        "--include-reports",
        action="store_true",
        help="Also delete generated report files under reports/ (*.xlsx, *.xls, *.xlsm, *.csv, *.png)",
    )
    parser.add_argument(
        "--include-artifacts",
        action="store_true",
        help="Also delete generated runtime artifacts under logs/, Screenshots/, and reports/",
    )
    parser.add_argument(
        "--include-temp",
        action="store_true",
        help="Also delete temp directories/files (tmp/temp, *.tmp, *.temp)",
    )
    parser.add_argument(
        "--include-build",
        action="store_true",
        help="Also delete build outputs (build/dist/.eggs/site, *.egg-info, *.whl)",
    )
    parser.add_argument(
        "--kill-processes",
        action="store_true",
        help="Also terminate known automation/browser processes (Windows taskkill)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve() if args.root else detect_project_root()
    cleanup_log: list[str] = []

    if not root.exists() or not root.is_dir():
        print(f"Invalid root directory: {root}")
        return 1

    removed_dirs, removed_files = remove_caches(
        root=root,
        dry_run=args.dry_run,
        verbose=args.verbose,
        cleanup_log=cleanup_log,
    )

    removed_log_files = 0
    removed_screenshots = 0
    removed_report_files = 0
    removed_temp_dirs = 0
    removed_temp_files = 0
    removed_build_dirs = 0
    removed_build_files = 0
    killed_processes = 0

    include_logs = args.include_logs or args.include_artifacts
    include_screenshots = args.include_screenshots or args.include_artifacts
    include_reports = args.include_reports or args.include_artifacts

    if include_logs:
        removed_log_files = remove_logs(
            root=root,
            dry_run=args.dry_run,
            verbose=args.verbose,
            cleanup_log=cleanup_log,
        )

    if include_screenshots:
        removed_screenshots = remove_screenshots(
            root=root,
            dry_run=args.dry_run,
            verbose=args.verbose,
            cleanup_log=cleanup_log,
        )

    if include_reports:
        removed_report_files = remove_reports(
            root=root,
            dry_run=args.dry_run,
            verbose=args.verbose,
            cleanup_log=cleanup_log,
        )

    if args.include_temp:
        removed_temp_dirs, removed_temp_files = remove_temp(
            root=root,
            dry_run=args.dry_run,
            verbose=args.verbose,
            cleanup_log=cleanup_log,
        )

    if args.include_build:
        removed_build_dirs, removed_build_files = remove_build_outputs(
            root=root,
            dry_run=args.dry_run,
            verbose=args.verbose,
            cleanup_log=cleanup_log,
        )

    if args.kill_processes and not args.dry_run:
        print("Killing known automation/browser processes...")
        killed_processes = kill_automation_processes(
            verbose=args.verbose,
            cleanup_log=cleanup_log,
        )

    mode = "Dry run complete" if args.dry_run else "Cleanup complete"
    print(
        f"{mode}. Directories: {removed_dirs}, Cache files: {removed_files}, "
        f"Log artifacts: {removed_log_files}, Screenshots: {removed_screenshots}, "
        f"Report files: {removed_report_files}, Temp dirs/files: {removed_temp_dirs}/{removed_temp_files}, "
        f"Build dirs/files: {removed_build_dirs}/{removed_build_files}, "
        f"Processes killed: {killed_processes}, Root: {root}"
    )

    if cleanup_log:
        print_cleanup_log(cleanup_log, dry_run=args.dry_run)

    if (
        not args.dry_run
        and removed_dirs == 0
        and removed_files == 0
        and removed_log_files == 0
        and removed_screenshots == 0
        and removed_report_files == 0
        and removed_temp_dirs == 0
        and removed_temp_files == 0
        and removed_build_dirs == 0
        and removed_build_files == 0
        and killed_processes == 0
    ):
        print("Nothing to remove — all target files already absent.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

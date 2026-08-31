#!/usr/bin/env python3
"""Preview or remove reproducible local artifacts from the repository.

The command is deliberately dry-run-first. It never removes source files,
local resume corpora, provider credentials, virtual environments, or the
current matrix run unless the operator explicitly selects matrix history and
names the run to keep.
"""

from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCOPES = ("caches", "tmp", "legacy-output")
SCOPES = (
    *DEFAULT_SCOPES,
    "test-results",
    "matrix-history",
    "commercial-api-history",
)


@dataclass(frozen=True)
class CleanupTarget:
    path: Path
    reason: str


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _cache_targets(root: Path) -> Iterable[CleanupTarget]:
    excluded = {root / ".git", root / ".venv", root / "frontend" / "node_modules"}
    for path in root.rglob("__pycache__"):
        if any(parent == blocked or blocked in parent.parents for blocked in excluded for parent in [path]):
            continue
        yield CleanupTarget(path, "Python bytecode cache")
    for path in (root / ".pytest_cache", root / ".mypy_cache", root / ".ruff_cache"):
        if path.exists():
            yield CleanupTarget(path, "tool cache")
    for path in root.rglob(".DS_Store"):
        if not any(blocked == path or blocked in path.parents for blocked in excluded):
            yield CleanupTarget(path, "macOS metadata")


def _children_except_keep(directory: Path, reason: str) -> Iterable[CleanupTarget]:
    if not directory.is_dir():
        return
    for path in directory.iterdir():
        if path.name != ".gitkeep":
            yield CleanupTarget(path, reason)


def build_plan(
    root: Path,
    scopes: Iterable[str],
    *,
    keep_matrix_run: str | None = None,
    keep_commercial_run: str | None = None,
) -> list[CleanupTarget]:
    root = root.resolve()
    selected = set(scopes)
    unknown = selected.difference(SCOPES)
    if unknown:
        raise ValueError(f"Unknown cleanup scope(s): {', '.join(sorted(unknown))}")
    if "matrix-history" in selected and not keep_matrix_run:
        raise ValueError("--keep-matrix-run is required with matrix-history")
    if "commercial-api-history" in selected and not keep_commercial_run:
        raise ValueError(
            "--keep-commercial-run is required with commercial-api-history"
        )

    targets: list[CleanupTarget] = []
    if "caches" in selected:
        targets.extend(_cache_targets(root))
    if "tmp" in selected and (root / "tmp").exists():
        targets.append(CleanupTarget(root / "tmp", "reproducible temporary workspace"))
    if "legacy-output" in selected and (root / "output").exists():
        targets.append(CleanupTarget(root / "output", "deprecated ad-hoc generated output"))
    if "test-results" in selected:
        targets.extend(
            _children_except_keep(root / "tests" / "test_results", "generated test result")
        )
    if "matrix-history" in selected:
        runs = root / "tests" / "local_datasets" / "resume_matrix" / "runs"
        keep_path = runs / str(keep_matrix_run)
        if not keep_path.is_dir():
            raise ValueError(f"Matrix run to keep does not exist: {keep_matrix_run}")
        for path in runs.iterdir():
            if path.is_dir() and path != keep_path:
                targets.append(CleanupTarget(path, "superseded resume-matrix run"))
    if "commercial-api-history" in selected:
        runs = root / "tests" / "test_results" / "commercial_api"
        keep_path = runs / str(keep_commercial_run)
        if not keep_path.is_dir():
            raise ValueError(f"Commercial run to keep does not exist: {keep_commercial_run}")
        for path in runs.iterdir():
            if path.is_dir() and path != keep_path:
                targets.append(CleanupTarget(path, "superseded commercial API run"))

    unique = {target.path.resolve(strict=False): target for target in targets}
    shortest_first = sorted(
        unique.values(), key=lambda target: (len(target.path.parts), str(target.path))
    )
    plan: list[CleanupTarget] = []
    for target in shortest_first:
        resolved = target.path.resolve(strict=False)
        if any(existing.path.resolve(strict=False) in resolved.parents for existing in plan):
            continue
        plan.append(target)
    plan.sort(key=lambda target: str(target.path))
    for target in plan:
        if not _inside(root, target.path):
            raise ValueError(f"Refusing target outside repository: {target.path}")
        if target.path.is_symlink():
            raise ValueError(f"Refusing symlink target: {target.path}")
    return plan


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        action="append",
        choices=SCOPES,
        dest="scopes",
        help="Cleanup scope; repeat to combine. Defaults to caches, tmp, and legacy-output.",
    )
    parser.add_argument(
        "--keep-matrix-run",
        help="Required with matrix-history; every other matrix run is selected.",
    )
    parser.add_argument(
        "--keep-commercial-run",
        help="Required with commercial-api-history; every other run is selected.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform deletion. Without this flag the command only previews.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scopes = args.scopes or list(DEFAULT_SCOPES)
    try:
        plan = build_plan(
            PROJECT_ROOT,
            scopes,
            keep_matrix_run=args.keep_matrix_run,
            keep_commercial_run=args.keep_commercial_run,
        )
    except ValueError as exc:
        print(f"error: {exc}")
        return 2

    total = sum(_size(target.path) for target in plan if target.path.exists())
    action = "REMOVE" if args.apply else "WOULD REMOVE"
    for target in plan:
        print(f"{action}: {target.path.relative_to(PROJECT_ROOT)} ({target.reason})")
    print(f"{len(plan)} target(s), {total / (1024 * 1024):.1f} MiB")

    if not args.apply:
        print("Dry run only. Re-run with --apply to delete these targets.")
        return 0
    for target in plan:
        _remove(target.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

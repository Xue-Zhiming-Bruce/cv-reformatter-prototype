"""Canonical environment and source-revision helpers for evaluation workflows."""

from __future__ import annotations

import subprocess
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = ROOT / ".env"


def load_canonical_environment(env_file: Path | None = DEFAULT_ENV_FILE) -> None:
    """Load one explicit environment file without printing values."""
    if env_file is not None and Path(env_file).is_file():
        load_dotenv(env_file, override=True)


def load_environment(env_file: Path = DEFAULT_ENV_FILE) -> None:
    load_canonical_environment(env_file)
    if not env_file.exists():
        raise FileNotFoundError(
            f"Environment file not found: {env_file}. Copy .env.example to .env."
        )


def git_revision() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() or None


def dirty_worktree() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT,
        capture_output=True, text=True, check=False,
    )
    return bool(result.stdout.strip())

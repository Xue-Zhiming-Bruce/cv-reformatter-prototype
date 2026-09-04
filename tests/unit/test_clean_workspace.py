from pathlib import Path

import pytest

from scripts.clean_workspace import build_plan


def test_default_cleanup_plan_selects_only_reproducible_artifacts(tmp_path: Path) -> None:
    (tmp_path / "app" / "__pycache__").mkdir(parents=True)
    (tmp_path / "tmp").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / ".DS_Store").write_text("metadata", encoding="utf-8")
    (tmp_path / ".venv" / "lib" / "__pycache__").mkdir(parents=True)
    source = tmp_path / "app" / "main.py"
    source.write_text("pass\n", encoding="utf-8")

    plan = build_plan(tmp_path, ["caches", "tmp", "legacy-output"])
    selected = {target.path.relative_to(tmp_path).as_posix() for target in plan}

    assert selected == {"app/__pycache__", "tmp", "output"}
    assert source.exists()
    assert (tmp_path / ".venv" / "lib" / "__pycache__").exists()


def test_matrix_cleanup_requires_and_preserves_named_run(tmp_path: Path) -> None:
    runs = tmp_path / "tests" / "local_datasets" / "resume_matrix" / "runs"
    (runs / "current").mkdir(parents=True)
    (runs / "old").mkdir()

    with pytest.raises(ValueError, match="required"):
        build_plan(tmp_path, ["matrix-history"])

    plan = build_plan(tmp_path, ["matrix-history"], keep_matrix_run="current")

    assert [target.path.name for target in plan] == ["old"]


def test_test_result_cleanup_preserves_gitkeep(tmp_path: Path) -> None:
    results = tmp_path / "tests" / "test_results"
    results.mkdir(parents=True)
    (results / ".gitkeep").write_text("", encoding="utf-8")
    (results / "old.log").write_text("passed", encoding="utf-8")

    plan = build_plan(tmp_path, ["test-results"])

    assert [target.path.name for target in plan] == ["old.log"]


def test_commercial_history_cleanup_requires_and_preserves_named_run(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "tests" / "test_results" / "commercial_api"
    (runs / "current").mkdir(parents=True)
    (runs / "failed_dns").mkdir()

    with pytest.raises(ValueError, match="required"):
        build_plan(tmp_path, ["commercial-api-history"])

    plan = build_plan(
        tmp_path,
        ["commercial-api-history"],
        keep_commercial_run="current",
    )

    assert [target.path.name for target in plan] == ["failed_dns"]

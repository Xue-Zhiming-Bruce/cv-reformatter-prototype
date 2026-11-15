"""One publication handle: a pinned read view, one switch, and no leftovers.

Every test here drives the publication a real upload drives; the crash test
kills a real publisher process so the leftover is the one a dead process leaves.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.extraction.candidate_schema import CandidateProfile
from app.generation.artifact_manifest import identify_file
from app.generation.contracts import TargetFormatMetadata
from app.generation.service import write_debug_artifacts
from app.generation.template_mapper import build_client_render_context
from app.job_worker import NativeJobWorker, ROOT
from app.target_publication import CURRENT_LINK_NAME, TARGETS_DIRNAME, publish_target_set

DOCX_SET = ("target_format_template.docx", "layout_template_spec.json", "target_format.json")
PDF_SET = (
    "target_format_reference.pdf",
    "normalized_layout_evidence.json",
    "target_layout_evidence.json",
    "layout_template_spec.json",
    "adobe_raw_response.json",
    "target_format.json",
)
HOLD_TIMEOUT_SECONDS = 30
#: A publisher that dies exactly inside the switch: the epoch is complete and
#: the pending name exists, but ``current`` still names the previous set.
CRASHING_PUBLISHER = """
import os, sys, threading, time
from pathlib import Path

from app.target_publication import publish_target_set

artifact_dir, staging_dir, ready = (Path(value) for value in sys.argv[1:4])
replace = os.replace


def pause_at_switch(source, destination):
    if Path(destination).name == "current":
        ready.write_text("ready", encoding="utf-8")
        time.sleep(60)  # the parent kills this publisher here
    return replace(source, destination)


os.replace = pause_at_switch
publish_target_set(artifact_dir, staging_dir, sys.argv[4].split(","), threading.Event())
"""


def _staging(tmp_path: Path, filenames: tuple[str, ...], tag: str) -> tuple[Path, dict[str, bytes]]:
    """One staged set: every file named by ``filenames`` plus this set's metadata."""
    staging = tmp_path / ".staging" / tag
    staging.mkdir(parents=True)
    contents = {
        name: f"{tag}:{name}".encode() if name != "target_format.json" else json.dumps({
            "input_filename": f"{tag}.docx",
            "stored_filename": "target_format_template.docx"
            if "target_format_template.docx" in filenames
            else "target_format_reference.pdf",
        }).encode()
        for name in filenames
    }
    for name, payload in contents.items():
        (staging / name).write_bytes(payload)
    return staging, contents


def _publish(
    tmp_path: Path, artifact_dir: Path, filenames: tuple[str, ...], tag: str
) -> dict[str, bytes]:
    staging, contents = _staging(tmp_path, filenames, tag)
    publish_target_set(artifact_dir, staging, filenames, threading.Event())
    return contents


def _epochs(artifact_dir: Path) -> list[str]:
    """The retained sets; a superseded set or a dead publisher's set must not stay."""
    return sorted(
        path.name
        for path in (artifact_dir / TARGETS_DIRNAME).iterdir()
        if path.name != CURRENT_LINK_NAME
    )


def _pending(artifact_dir: Path) -> list[str]:
    found = list(artifact_dir.glob(".pending-*"))
    found.extend((artifact_dir / TARGETS_DIRNAME).glob(".pending-*"))
    return sorted(path.name for path in found)


def test_read_view_pins_one_epoch_across_a_switch(tmp_path: Path) -> None:
    """A multi-file read pins the epoch, so its metadata and source always agree.

    At ``ab1fe85`` each name resolved separately: the metadata read before the
    switch named A's source while that name already returned B's bytes.
    """
    from app.target_publication import TargetSet

    artifact_dir = tmp_path / "artifact_pinned_read"
    first = _publish(tmp_path, artifact_dir, DOCX_SET, "first")
    view = TargetSet(artifact_dir)
    metadata = json.loads(view.read("target_format.json").read_text())
    _publish(tmp_path, artifact_dir, DOCX_SET, "second")

    assert view.read(metadata["stored_filename"]).read_bytes() == first[metadata["stored_filename"]]
    # The naive per-name read follows the switch; the pinned view does not.
    assert (artifact_dir / metadata["stored_filename"]).read_bytes() != first["target_format_template.docx"]


def test_pinned_epoch_file_keeps_its_public_identity(tmp_path: Path) -> None:
    """A pinned copy is hashed from the epoch and recorded under its public name.

    At ``ab1fe85`` a recorded identity was the public name and a hash of
    whatever that name resolved to at that moment, so the pair could disagree.
    """
    artifact_dir = tmp_path / "artifact_pinned_identity"
    first = _publish(tmp_path, artifact_dir, DOCX_SET, "first")
    pinned = artifact_dir / TARGETS_DIRNAME / _epochs(artifact_dir)[0] / "target_format_template.docx"

    identity = identify_file(artifact_dir, pinned)

    assert identity.path == "target_format_template.docx"
    assert identity.size_bytes == len(first["target_format_template.docx"])


@pytest.mark.skipif(os.name != "posix", reason="the crash test kills a real publisher process")
def test_debug_artifacts_never_write_through_a_published_name(tmp_path: Path) -> None:
    """Generation records the published metadata; it never rewrites its epoch.

    Loading the first set's metadata, publishing a second set, then writing
    debug artifacts used to write the first metadata through the public name and
    into the epoch the second set's readers treat as immutable.
    """
    artifact_dir = tmp_path / "artifact_debug_write"
    _publish(tmp_path, artifact_dir, DOCX_SET, "first")
    stale = TargetFormatMetadata(
        artifact_id=artifact_dir.name,
        input_filename="first.docx",
        stored_filename="target_format_template.docx",
        input_type="docx",
        role="docx_template",
        generation_strategy="agentic",
        accepted_for_generation=True,
        used_as_template_source=True,
        download_url="/api/generate/first/target-format",
        note="",
    )
    published = _publish(tmp_path, artifact_dir, DOCX_SET, "second")
    profile = CandidateProfile(full_name="Synthetic Candidate")

    write_debug_artifacts(
        artifact_dir=artifact_dir,
        profile=profile,
        render_context=build_client_render_context(profile),
        original_text=None,
        target_format=stale,
    )

    assert (artifact_dir / "target_format.json").read_bytes() == published["target_format.json"]


def test_crashed_publishers_leftovers_do_not_survive_the_next_publish(tmp_path: Path) -> None:
    """Publishers that die before the switch leave nothing for the next one.

    At ``ab1fe85`` each death left its epoch and its pending name under
    ``.targets``, so the retained entries grew by one per crash even after a
    later publication succeeded.
    """
    artifact_dir = tmp_path / "artifact_crashed"
    _publish(tmp_path, artifact_dir, DOCX_SET, "first")
    script = tmp_path / "crashing_publisher.py"
    script.write_text(CRASHING_PUBLISHER, encoding="utf-8")
    for attempt in range(3):
        staging, _contents = _staging(tmp_path, DOCX_SET, f"crash{attempt}")
        ready = tmp_path / f"crash{attempt}.ready"
        publisher = subprocess.Popen(
            [sys.executable, "-B", str(script), str(artifact_dir), str(staging), str(ready),
             ",".join(DOCX_SET)],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        try:
            _wait_for_switch(ready, publisher)
        finally:
            publisher.kill()
            publisher.wait(timeout=HOLD_TIMEOUT_SECONDS)

    _publish(tmp_path, artifact_dir, DOCX_SET, "second")

    assert len(_epochs(artifact_dir)) == 2
    assert _pending(artifact_dir) == []


def _wait_for_switch(ready: Path, publisher: subprocess.Popen) -> None:
    deadline = time.monotonic() + HOLD_TIMEOUT_SECONDS
    while not ready.exists():
        assert publisher.poll() is None, "the publisher exited before reaching the switch"
        assert time.monotonic() < deadline, "the publisher never reached the switch"
        time.sleep(0.02)


def test_failed_switch_leaves_no_pending_name_behind(tmp_path: Path) -> None:
    """A publication that fails while linking leaves no pending name.

    At ``ab1fe85`` the pending link stayed in the artifact directory and every
    later publication kept it: pruning only ever looked inside ``.targets``.
    """
    artifact_dir = tmp_path / "artifact_failed_switch"
    _publish(tmp_path, artifact_dir, PDF_SET, "pdf")
    staging, _contents = _staging(tmp_path, DOCX_SET, "docx")
    replace = os.replace

    def fail_linking(source, destination):
        if Path(destination) == artifact_dir / "target_format_template.docx":
            raise OSError("injected link failure")
        return replace(source, destination)

    with patch.object(os, "replace", fail_linking):
        with pytest.raises(OSError):
            publish_target_set(artifact_dir, staging, DOCX_SET, threading.Event())
    assert _pending(artifact_dir) == []

    _publish(tmp_path, artifact_dir, DOCX_SET, "second")

    assert _pending(artifact_dir) == []
    assert len(_epochs(artifact_dir)) == 2


def test_abandonment_inside_the_flip_window_publishes_nothing(tmp_path: Path) -> None:
    """An upload abandoned just after the switch runs the switch back.

    At ``ab1fe85`` abandonment was checked only before the switch, so a caller
    that gave up inside the window still replaced the published set.
    """
    artifact_dir = tmp_path / "artifact_abandoned_window"
    first = _publish(tmp_path, artifact_dir, DOCX_SET, "first")
    staging, _contents = _staging(tmp_path, DOCX_SET, "second")
    abandoned = threading.Event()
    replace = os.replace

    def abandon_during_flip(source, destination):
        if Path(destination).name == "current":
            abandoned.set()  # the caller gives up exactly inside the window
        return replace(source, destination)

    with patch.object(os, "replace", abandon_during_flip):
        publish_target_set(artifact_dir, staging, DOCX_SET, abandoned)

    assert abandoned.is_set()
    for name in DOCX_SET:
        assert (artifact_dir / name).read_bytes() == first[name]
    assert len(_epochs(artifact_dir)) == 1


def test_target_snapshot_copies_one_published_set_as_real_files(tmp_path: Path) -> None:
    """A snapshot after a format change copies the live set, never a dead name.

    At ``ab1fe85`` the DOCX upload left the PDF name as a dangling public
    symlink, so ``shutil.copytree`` raised ``shutil.Error`` and no job started.
    """
    artifact_dir = tmp_path / "artifact_format_change"
    _publish(tmp_path, artifact_dir, PDF_SET, "pdf")
    docx = _publish(tmp_path, artifact_dir, DOCX_SET, "docx")
    claim = SimpleNamespace(
        bundle_path="jobs/job_1/attempt_1/artifact_format_change",
        payload=SimpleNamespace(profile_revision_id="rev_1", model_dump_json=lambda **_: "{}"),
    )

    bundle = NativeJobWorker.__new__(NativeJobWorker)._snapshot_target(claim, artifact_dir)

    for name in DOCX_SET:
        assert (bundle / name).read_bytes() == docx[name]
        assert not (bundle / name).is_symlink()
    assert not (bundle / "target_format_reference.pdf").exists()
    assert not (bundle / TARGETS_DIRNAME).exists()


def _legacy_artifact(
    tmp_path: Path, artifact_dir: Path, filenames: tuple[str, ...], tag: str
) -> dict[str, bytes]:
    """An artifact directory published before the switch: plain files, no epochs."""
    staging, contents = _staging(tmp_path, filenames, tag)
    artifact_dir.mkdir(parents=True)
    for name in filenames:
        os.replace(staging / name, artifact_dir / name)
    return contents


def test_pinned_set_stays_readable_across_two_later_publications(tmp_path: Path) -> None:
    """A view retains its set while it reads, so pruning cannot pull it out.

    At ``60ed109`` pruning kept the live set and one predecessor, so a view
    pinned before two later publications resolved nothing.
    """
    from app.target_publication import TargetSet

    artifact_dir = tmp_path / "artifact_leased_reader"
    first = _publish(tmp_path, artifact_dir, PDF_SET, "first")
    view = TargetSet(artifact_dir)
    assert view.read("adobe_raw_response.json") is not None
    _publish(tmp_path, artifact_dir, DOCX_SET, "second")
    _publish(tmp_path, artifact_dir, DOCX_SET, "third")

    retained = view.read("adobe_raw_response.json")
    assert retained is not None, "pruning deleted a set an open view is still reading"
    assert retained.read_bytes() == first["adobe_raw_response.json"]

    # Releasing the view lets the next publication prune it again.
    view.close()
    _publish(tmp_path, artifact_dir, DOCX_SET, "fourth")
    assert len(_epochs(artifact_dir)) == 2


def test_legacy_directory_is_adopted_as_one_complete_epoch(tmp_path: Path) -> None:
    """A pin on a pre-switch directory reads one whole upload, never a mix.

    At ``60ed109`` the first publication replaced the plain files one by one, so
    a pinned view saw the old metadata beside the new source and the plain
    metadata file stayed beside the new links.
    """
    from app.target_publication import TargetSet

    artifact_dir = tmp_path / "artifact_legacy_adopted"
    first = _legacy_artifact(tmp_path, artifact_dir, PDF_SET, "first")
    view = TargetSet(artifact_dir)
    assert json.loads(view.read("target_format.json").read_text())["input_filename"] == "first.docx"

    _publish(tmp_path, artifact_dir, PDF_SET, "second")

    retained = view.read("adobe_raw_response.json")
    assert retained is not None, "adoption left the pinned legacy set half migrated"
    assert retained.read_bytes() == first["adobe_raw_response.json"]
    assert json.loads((artifact_dir / "target_format.json").read_text())["input_filename"] == "second.docx"
    plain = [
        path.name
        for path in artifact_dir.iterdir()
        if path.name in PDF_SET and path.is_file() and not path.is_symlink()
    ]
    assert plain == [], f"plain files stayed beside the published links: {plain}"


def test_abandonment_while_linking_public_names_publishes_nothing(tmp_path: Path) -> None:
    """Giving up after the switch still publishes nothing.

    At ``60ed109`` abandonment was only checked before and right after the
    switch, so setting it while the public names were linked left the new set
    published even though the caller had gone.
    """
    from app.target_publication import TargetSet

    artifact_dir = tmp_path / "artifact_abandon_link"
    first = _publish(tmp_path, artifact_dir, DOCX_SET, "first")
    staging, _second = _staging(tmp_path, DOCX_SET, "second")
    abandoned = threading.Event()
    link_public_names = TargetSet._link_public_names

    def link_then_abandon(self: TargetSet, filenames) -> None:
        link_public_names(self, filenames)
        abandoned.set()

    with patch.object(TargetSet, "_link_public_names", link_then_abandon):
        publish_target_set(artifact_dir, staging, DOCX_SET, abandoned)

    assert (artifact_dir / "target_format.json").read_bytes() == first["target_format.json"]
    assert (artifact_dir / "target_format_template.docx").read_bytes() == first["target_format_template.docx"]
    assert len(_epochs(artifact_dir)) == 1, _epochs(artifact_dir)


def test_interrupted_adoption_keeps_every_legacy_file_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure before the switch leaves the whole legacy set, not half of it.

    The copy is what carries that: at ``98d5eaf`` adoption moved the plain files
    into the new epoch first, so a crash between the move and the switch left
    an epoch nothing pointed at and every target read resolved nothing.
    """
    from app import target_publication
    from app.target_publication import TargetSet

    artifact_dir = tmp_path / "artifact_legacy_interrupted"
    legacy = _legacy_artifact(tmp_path, artifact_dir, PDF_SET, "first")

    def fail(*_args: object, **_kwargs: object) -> str | None:
        raise OSError("injected failure before the switch")

    monkeypatch.setattr(target_publication, "_flip_current", fail)
    with pytest.raises(OSError):
        TargetSet(artifact_dir).read("target_format.json")

    plain = {
        path.name: path.read_bytes()
        for path in artifact_dir.iterdir()
        if path.is_file() and not path.is_symlink()
    }
    assert plain, f"the interrupted adoption took the plain legacy files: {_epochs(artifact_dir)}"
    assert {name: plain.get(name) for name in PDF_SET} == legacy
    assert _epochs(artifact_dir) == [], _epochs(artifact_dir)

    monkeypatch.undo()
    view = TargetSet(artifact_dir)
    try:
        for name, payload in legacy.items():
            assert view.read(name).read_bytes() == payload, name
    finally:
        view.close()


def test_interrupted_adoption_then_an_abandoned_upload_keeps_the_legacy_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash during adoption, then a cancelled upload, still serves the set.

    At ``98d5eaf`` the two together left ``epochs=[]``: nothing published and the
    published files unreachable.
    """
    from app import target_publication
    from app.target_publication import TargetSet

    artifact_dir = tmp_path / "artifact_legacy_interrupted_abandoned"
    legacy = _legacy_artifact(tmp_path, artifact_dir, PDF_SET, "first")

    def fail(*_args: object, **_kwargs: object) -> str | None:
        raise OSError("injected failure before the switch")

    monkeypatch.setattr(target_publication, "_flip_current", fail)
    with pytest.raises(OSError):
        TargetSet(artifact_dir).read("target_format.json")
    monkeypatch.undo()

    staging, _second = _staging(tmp_path, DOCX_SET, "second")
    abandoned = threading.Event()
    abandoned.set()
    publish_target_set(artifact_dir, staging, DOCX_SET, abandoned)

    assert len(_epochs(artifact_dir)) == 1, _epochs(artifact_dir)
    view = TargetSet(artifact_dir)
    try:
        for name, payload in legacy.items():
            assert view.read(name).read_bytes() == payload, name
        assert view.read("target_format_template.docx") is None
    finally:
        view.close()

"""One publication handle: a pinned read view, one switch, and no leftovers.

Every test here drives the publication a real upload drives; the crash test
kills a real publisher process so the leftover is the one a dead process leaves.
"""

from __future__ import annotations

import gc
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

from app import target_publication
from app.extraction.candidate_schema import CandidateProfile
from app.generation.artifact_manifest import identify_file
from app.generation.contracts import TargetFormatMetadata
from app.generation.service import write_debug_artifacts
from app.generation.template_mapper import build_client_render_context
from app.job_worker import NativeJobWorker, ROOT
from app.target_publication import (
    CURRENT_LINK_NAME,
    TARGETS_DIRNAME,
    TargetPublicationBusy,
    TargetSet,
    TargetSetChanged,
    publish_target_set,
)

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


def _dereferenced_copy(
    tmp_path: Path, artifact_dir: Path, filenames: tuple[str, ...], tag: str
) -> tuple[Path, dict[str, bytes]]:
    """A published artifact copied with its links dereferenced, as ``cp -rL`` leaves it."""
    import shutil

    contents = _publish(tmp_path, artifact_dir, filenames, tag)
    copy_dir = tmp_path / f"{artifact_dir.name}-dereferenced"
    shutil.copytree(artifact_dir, copy_dir)
    assert (copy_dir / TARGETS_DIRNAME / CURRENT_LINK_NAME).is_dir()
    assert not (copy_dir / TARGETS_DIRNAME / CURRENT_LINK_NAME).is_symlink()
    return copy_dir, contents


def test_dereferenced_copy_is_read_and_published_as_one_set(tmp_path: Path) -> None:
    """A copy with its links dereferenced is a published artifact, not an error.

    ``shutil.copytree`` without ``symlinks=True`` (like ``cp -rL`` or ``rsync -L``)
    leaves a real directory where ``.targets/current`` should be. At ``d26fbfd``
    the reader took that for an unpublished legacy directory, adopted the plain
    names and died in the switch with ``IsADirectoryError``.
    """
    from app.target_publication import TargetSet

    copy_dir, first = _dereferenced_copy(tmp_path, tmp_path / "artifact_deref_read", PDF_SET, "first")
    view = TargetSet(copy_dir)
    try:
        for name, payload in first.items():
            assert view.read(name).read_bytes() == payload, name
    finally:
        view.close()

    second = _publish(tmp_path, copy_dir, PDF_SET, "second")

    current = copy_dir / TARGETS_DIRNAME / CURRENT_LINK_NAME
    assert current.is_symlink() and current.is_dir(), "the copy kept a directory at current"
    assert (copy_dir / "target_format.json").read_bytes() == second["target_format.json"]
    for name in PDF_SET:
        public = copy_dir / name
        assert public.is_symlink() and public.read_bytes() == second[name], name
    plain = [
        path.name
        for path in copy_dir.iterdir()
        if path.name in PDF_SET and path.is_file() and not path.is_symlink()
    ]
    assert plain == [], f"plain files stayed beside the copied links: {plain}"
    assert not list((copy_dir / TARGETS_DIRNAME).glob(".migrating-*"))


def _current_only_artifact(tmp_path: Path, tag: str, state: str) -> Path:
    artifact_dir = tmp_path / f"artifact_current_{tag}"
    artifact_dir.mkdir(parents=True)
    if state != "absent":
        targets = artifact_dir / TARGETS_DIRNAME
        targets.mkdir()
        current = targets / CURRENT_LINK_NAME
        if state == "dangling":
            current.symlink_to("set-gone")
        else:
            current.write_text("not a set")
    return artifact_dir


@pytest.mark.parametrize("state", ["absent", "dangling", "plain file"])
def test_a_current_that_names_no_set_publishes_nothing(tmp_path: Path, state: str) -> None:
    """A dangling link, a plain file and no ``current`` all read as no target.

    Each is replaced by the next publication rather than reported as an error.
    """
    from app.target_publication import TargetSet

    artifact_dir = _current_only_artifact(tmp_path, state.replace(" ", "_"), state)
    with TargetSet(artifact_dir) as view:
        assert view.read("target_format.json") is None

    contents = _publish(tmp_path, artifact_dir, DOCX_SET, f"after-{state.replace(' ', '_')}")

    assert (artifact_dir / TARGETS_DIRNAME / CURRENT_LINK_NAME).is_symlink()
    assert (artifact_dir / "target_format.json").read_bytes() == contents["target_format.json"]


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="chmod denies nothing to root")
def test_unreadable_current_is_an_error_not_an_unpublished_artifact(tmp_path: Path) -> None:
    """A ``current`` the process cannot stat raises; it never reads as "no target".

    Reporting "nothing uploaded" would silently drop a published set from every
    read of the artifact.
    """
    from app.target_publication import TargetSet

    artifact_dir = tmp_path / "artifact_current_unreadable"
    _publish(tmp_path, artifact_dir, DOCX_SET, "first")
    targets = artifact_dir / TARGETS_DIRNAME
    os.chmod(targets, 0o000)
    try:
        with pytest.raises(OSError):
            with TargetSet(artifact_dir) as view:
                view.read("target_format.json")
    finally:
        os.chmod(targets, 0o755)
    assert TargetSet(artifact_dir).read("target_format.json") is not None


def test_a_migration_that_died_between_its_renames_is_recovered(tmp_path: Path) -> None:
    """The set a died migration renamed aside is put back and served, not pruned.

    A migration copies the directory into an epoch, renames it aside and then
    links it; a death in that window must not lose the set.
    """
    import shutil

    from app.target_publication import TargetSet

    copy_dir, first = _dereferenced_copy(tmp_path, tmp_path / "artifact_deref_crash", PDF_SET, "first")
    targets = copy_dir / TARGETS_DIRNAME
    shutil.copytree(targets / CURRENT_LINK_NAME, targets / "set-made-before-the-crash")
    for name in PDF_SET:  # a tree only partly dereferenced: the names follow current
        public = copy_dir / name
        public.unlink()
        public.symlink_to(f"{TARGETS_DIRNAME}/{CURRENT_LINK_NAME}/{name}")
    os.replace(targets / CURRENT_LINK_NAME, targets / ".migrating-dead")

    with TargetSet(copy_dir) as view:
        for name, payload in first.items():
            served = view.read(name)
            assert served is not None, f"the set the died migration renamed aside is lost: {name}"
            assert served.read_bytes() == payload, name

    assert not list(targets.glob(".migrating-*")), "the recovered directory stayed beside the set"
    assert (targets / CURRENT_LINK_NAME).is_symlink()


#: Publishes each staged directory it is given, the way a second request would.
PUBLISHING_CHILD = """
import sys, threading
from pathlib import Path

from app.target_publication import TargetPublicationBusy, publish_target_set

artifact_dir, filenames = Path(sys.argv[1]), sys.argv[2].split(",")
for staging in (Path(value) for value in sys.argv[3:]):
    try:
        publish_target_set(artifact_dir, staging, filenames, threading.Event())
    except TargetPublicationBusy:
        print("busy")
    else:
        print("published")
"""

#: Publishes one set, pausing inside the switch until a release file appears.
PAUSED_PUBLISHER = """
import os, sys, threading, time
from pathlib import Path

from app.target_publication import publish_target_set

artifact_dir, staging_dir, ready, release = (Path(value) for value in sys.argv[1:5])
replace = os.replace


def pause_at_switch(source, destination):
    if Path(destination).name == "current" and not release.exists():
        ready.write_text("ready", encoding="utf-8")
        while not release.exists():
            time.sleep(0.01)
    return replace(source, destination)


os.replace = pause_at_switch
publish_target_set(artifact_dir, staging_dir, sys.argv[5].split(","), threading.Event())
"""

#: Reads one file through a view and keeps the view while it waits to be released.
HOLDING_READER = """
import sys, time
from pathlib import Path

from app.target_publication import TargetSet

artifact_dir, name, ready, release, result = (Path(value) for value in sys.argv[1:6])
with TargetSet(artifact_dir) as view:
    first = view.read(name)
    ready.write_bytes(first.read_bytes() if first else b"")
    while not release.exists():
        time.sleep(0.01)
    second = view.read(name)
    result.write_bytes(second.read_bytes() if second else b"")
"""

#: Reads one file through a view and writes what it got.
READING_CHILD = """
import sys
from pathlib import Path

from app.target_publication import TargetSet

artifact_dir, name, result = (Path(value) for value in sys.argv[1:4])
with TargetSet(artifact_dir) as view:
    found = view.read(name)
    result.write_bytes(found.read_bytes() if found else b"")
"""


def _child_command(script: Path, *args: object) -> list[str]:
    return [sys.executable, "-B", str(script), *(str(arg) for arg in args)]


def _child_env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(ROOT)}


def _publish_from_child(
    tmp_path: Path, artifact_dir: Path, filenames: tuple[str, ...], tags: tuple[str, ...]
) -> tuple[list[dict[str, bytes]], list[str]]:
    """Publish whole sets from a separate process, as a second request would.

    Returns each set's contents and what the child reported for it: ``published``
    when it switched the set, ``busy`` when another process was deciding.
    """
    staged = [_staging(tmp_path, filenames, tag) for tag in tags]
    script = tmp_path / "child_publisher.py"
    script.write_text(PUBLISHING_CHILD, encoding="utf-8")
    completed = subprocess.run(
        _child_command(
            script, artifact_dir, ",".join(filenames),
            *(path for path, _contents in staged),
        ),
        cwd=ROOT,
        env=_child_env(),
        check=True,
        capture_output=True,
        text=True,
        timeout=HOLD_TIMEOUT_SECONDS,
    )
    outcomes = completed.stdout.split()
    assert len(outcomes) == len(tags), completed.stdout
    return [contents for _path, contents in staged], outcomes


def _read_from_child(tmp_path: Path, artifact_dir: Path, name: str) -> subprocess.Popen:
    """Start a separate process that reads one file through a view."""
    script = tmp_path / "child_reader.py"
    script.write_text(READING_CHILD, encoding="utf-8")
    return subprocess.Popen(
        _child_command(script, artifact_dir, name, tmp_path / "child_reader.out"),
        cwd=ROOT,
        env=_child_env(),
    )


def _child_result(tmp_path: Path, child: subprocess.Popen) -> bytes:
    assert child.wait(timeout=HOLD_TIMEOUT_SECONDS) == 0, "the child reader failed"
    return (tmp_path / "child_reader.out").read_bytes()


def _flip_after_a_child_publication(
    tmp_path: Path, artifact_dir: Path, filenames: tuple[str, ...], tag: str
) -> tuple[object, list[str]]:
    """A ``_flip_current`` that lets another process publish before it switches.

    That is the window a sequence which checks ``current`` and then switches it
    leaves open: at ``64a272b`` the resumed sequence reverted the newer set.
    """
    flip_current = target_publication._flip_current
    reported: list[str] = []

    def flip(sets_dir: Path, epoch_name: str | None) -> str | None:
        if not reported:
            reported.extend(_publish_from_child(tmp_path, artifact_dir, filenames, (tag,))[1])
        return flip_current(sets_dir, epoch_name)

    return flip, reported


@pytest.mark.parametrize("shape", ["published", "plain files"])
def test_a_publication_between_selection_and_hold_cannot_empty_the_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    """Choosing a set and holding it is one step, so nothing is pruned in between.

    At ``240eedb`` the hold was registered after the selection lock was released:
    two publications landing in that window deleted the chosen set, and every
    read of the view then returned nothing.
    """
    from app import target_publication
    from app.target_publication import TargetSet

    artifact_dir = tmp_path / f"artifact_select_hold_{shape.replace(' ', '_')}"
    first = (
        _publish(tmp_path, artifact_dir, PDF_SET, "first")
        if shape == "published"
        else _legacy_artifact(tmp_path, artifact_dir, PDF_SET, "first")
    )
    view = TargetSet(artifact_dir)
    lock = target_publication._artifact_lock(artifact_dir.name)
    injected: list[str] = []

    class InjectingLock:
        """Publishes twice at the moment the reader stops holding the lock."""

        def __enter__(self) -> None:
            lock.acquire()

        def __exit__(self, *_: object) -> None:
            lock.release()
            if not injected:
                injected.append("done")
                _publish(tmp_path, artifact_dir, DOCX_SET, "second")
                _publish(tmp_path, artifact_dir, DOCX_SET, "third")

    monkeypatch.setattr(target_publication, "_artifact_lock", lambda _id: InjectingLock())
    try:
        held = view.read("target_format_reference.pdf")
        assert held is not None, "the chosen set was pruned before the view held it"
        assert held.read_bytes() == first["target_format_reference.pdf"]
    finally:
        view.close()


@pytest.mark.skipif(os.name != "posix", reason="the test publishes from a separate process")
def test_another_process_cannot_prune_a_set_a_view_holds(tmp_path: Path) -> None:
    """The hold is a kernel lock, so another process's pruning sees it.

    At ``240eedb`` holds were a process-local dictionary: a child that published
    two sets pruned the parent's set and the parent's view lost its file.
    """
    from app.target_publication import TargetSet

    artifact_dir = tmp_path / "artifact_cross_process_hold"
    first = _publish(tmp_path, artifact_dir, DOCX_SET, "first")
    view = TargetSet(artifact_dir)
    try:
        assert view.read("target_format_template.docx").read_bytes() == first["target_format_template.docx"]
        _staged_contents, reported = _publish_from_child(
            tmp_path, artifact_dir, DOCX_SET, ("second", "third")
        )
        assert reported == ["published", "published"], reported
        held = view.read("target_format_template.docx")
        assert held is not None, "another process pruned a set this view holds"
        assert held.read_bytes() == first["target_format_template.docx"]
    finally:
        view.close()


def test_a_dangling_current_beside_plain_files_is_adopted_not_read_in_place(
    tmp_path: Path,
) -> None:
    """Plain files beside an unresolved ``current`` are a set to adopt.

    At ``240eedb`` any symlink form made adoption return early, so the view held
    the artifact root itself and served the next upload's bytes after a
    publication instead of the set it first read.
    """
    from app.target_publication import TargetSet

    artifact_dir = tmp_path / "artifact_dangling_plain"
    first = _legacy_artifact(tmp_path, artifact_dir, DOCX_SET, "first")
    (artifact_dir / TARGETS_DIRNAME).mkdir()
    (artifact_dir / TARGETS_DIRNAME / CURRENT_LINK_NAME).symlink_to("set-gone")

    view = TargetSet(artifact_dir)
    try:
        assert view.read("target_format_template.docx").read_bytes() == first["target_format_template.docx"]
        second = _publish(tmp_path, artifact_dir, DOCX_SET, "second")
        held = view.read("target_format_template.docx")
        assert held is not None
        assert held.read_bytes() == first["target_format_template.docx"], "the view followed the publication"
        assert (artifact_dir / "target_format_template.docx").read_bytes() == second["target_format_template.docx"]
    finally:
        view.close()


@pytest.mark.skipif(os.name != "posix", reason="the test publishes from a separate process")
def test_a_publication_during_an_adoption_reports_busy_instead_of_pruning_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another process's publication waits outside the adoption or is refused.

    At ``240eedb`` the adopter's copy was an ordinary unreferenced epoch that the
    child pruned; at ``64a272b`` the child could publish over the adoption at all.
    Neither can happen while the decision lock is held, so the child is refused and
    the adoption it interrupted still switches a complete set.
    """
    artifact_dir = tmp_path / "artifact_paused_adopter"
    legacy = _legacy_artifact(tmp_path, artifact_dir, PDF_SET, "first")
    copy_epoch = target_publication._copy_epoch
    paused, resume = threading.Event(), threading.Event()

    def copy_then_pause(*args: object) -> object:
        copied = copy_epoch(*args)
        if not paused.is_set():
            paused.set()
            assert resume.wait(HOLD_TIMEOUT_SECONDS), "the test never resumed the adopter"
        return copied

    monkeypatch.setattr(target_publication, "_copy_epoch", copy_then_pause)
    read_after: list[Path | None] = []

    def adopt() -> None:
        view = TargetSet(artifact_dir)
        try:
            read_after.append(view.read("target_format_reference.pdf"))
        finally:
            view.close()

    adopter = threading.Thread(target=adopt)
    adopter.start()
    try:
        assert paused.wait(HOLD_TIMEOUT_SECONDS), "the adopter never reached its switch"
        _staged_contents, reported = _publish_from_child(
            tmp_path, artifact_dir, PDF_SET, ("second",)
        )
    finally:
        resume.set()
        adopter.join(HOLD_TIMEOUT_SECONDS)
    monkeypatch.undo()

    assert reported == ["busy"], "a publication interleaved with the adoption"
    held = read_after[0]
    assert held is not None, "the adoption published an epoch that was already pruned"
    assert held.read_bytes() == legacy["target_format_reference.pdf"]
    current = artifact_dir / TARGETS_DIRNAME / CURRENT_LINK_NAME
    assert current.is_symlink() and current.is_dir(), "the switch named a set that is not there"


def test_a_rollback_leaves_a_set_another_process_holds(tmp_path: Path) -> None:
    """An abandoned publication deletes only the set it can take exclusively.

    At ``64a272b`` the rollback removed the abandoned set outright, so a reader
    that had already pinned it lost every file its next read asked for.
    """
    artifact_dir = tmp_path / "artifact_rollback_held"
    first = _publish(tmp_path, artifact_dir, DOCX_SET, "first")
    abandoned_staging, abandoned_set = _staging(tmp_path, DOCX_SET, "second")
    abandoned = threading.Event()
    linked, resume = threading.Event(), threading.Event()
    link_public_names = TargetSet._link_public_names

    def link_then_pause(self: TargetSet, filenames: tuple[str, ...]) -> None:
        link_public_names(self, filenames)
        if self.artifact_dir == artifact_dir and not resume.is_set():
            linked.set()
            assert resume.wait(HOLD_TIMEOUT_SECONDS), "the test never resumed the publisher"

    publisher = threading.Thread(
        target=publish_target_set,
        args=(artifact_dir, abandoned_staging, DOCX_SET, abandoned),
    )
    with patch.object(TargetSet, "_link_public_names", link_then_pause):
        publisher.start()
        try:
            assert linked.wait(HOLD_TIMEOUT_SECONDS), "the publisher never linked its names"
            script = tmp_path / "child_holding_reader.py"
            script.write_text(HOLDING_READER, encoding="utf-8")
            ready, release = tmp_path / "reader.ready", tmp_path / "reader.release"
            result = tmp_path / "reader.result"
            reader = subprocess.Popen(
                _child_command(
                    script, artifact_dir, "target_format_template.docx", ready, release, result
                ),
                cwd=ROOT,
                env=_child_env(),
            )
            try:
                _wait_for_switch(ready, reader)  # the reader pinned the abandoned set
                assert reader.poll() is None
                abandoned.set()
            finally:
                resume.set()
                publisher.join(HOLD_TIMEOUT_SECONDS)
                release.write_bytes(b"go")
                assert reader.wait(timeout=HOLD_TIMEOUT_SECONDS) == 0
        finally:
            resume.set()
            publisher.join(HOLD_TIMEOUT_SECONDS)

    assert ready.read_bytes() == abandoned_set["target_format_template.docx"]
    assert result.read_bytes() == abandoned_set["target_format_template.docx"], (
        "the rollback deleted a set another process was reading"
    )
    assert abandoned.is_set()
    # The previous set is published again and the abandoned one waits for a prune.
    assert (artifact_dir / "target_format_template.docx").read_bytes() == first[
        "target_format_template.docx"
    ]
    assert len(_epochs(artifact_dir)) == 2, _epochs(artifact_dir)
    _publish(tmp_path, artifact_dir, DOCX_SET, "third")
    assert len(_epochs(artifact_dir)) == 2, "a later publication did not collect the abandoned set"


@pytest.mark.skipif(os.name != "posix", reason="the test publishes from a separate process")
def test_an_adoption_never_reverts_a_publication_from_another_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A publication that lands while an adoption decides is not reverted by it.

    At ``64a272b`` the adoption checked that ``current`` was still legacy and then
    switched it on its own: a set published in between was replaced by the stale
    copy the adoption had just made.
    """
    artifact_dir = tmp_path / "artifact_adoption_race"
    _legacy_artifact(tmp_path, artifact_dir, DOCX_SET, "first")
    flip, reported = _flip_after_a_child_publication(tmp_path, artifact_dir, DOCX_SET, "second")
    monkeypatch.setattr(target_publication, "_flip_current", flip)

    with TargetSet(artifact_dir) as view:
        assert view.read("target_format_template.docx") is not None
    monkeypatch.undo()

    metadata = json.loads((artifact_dir / "target_format.json").read_text())
    if reported == ["published"]:
        assert metadata["input_filename"] == "second.docx", (
            "the resumed adoption reverted the newer publication"
        )
    else:
        assert reported == ["busy"], reported
        assert metadata["input_filename"] == "first.docx"
        assert _publish(tmp_path, artifact_dir, DOCX_SET, "third")["target_format.json"] == (
            artifact_dir / "target_format.json"
        ).read_bytes()


@pytest.mark.skipif(os.name != "posix", reason="the test publishes from a separate process")
def test_a_migration_never_reverts_a_publication_from_another_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A publication that lands while a migration decides is not reverted by it.

    At ``64a272b`` the migration checked that the directory ``current`` named was
    still the one it copied and then renamed and switched it: a set published in
    between was renamed aside and replaced by the migrated copy.
    """
    copy_dir, _first = _dereferenced_copy(tmp_path, tmp_path / "artifact_migrate_race", PDF_SET, "first")
    flip, reported = _flip_after_a_child_publication(tmp_path, copy_dir, PDF_SET, "second")
    monkeypatch.setattr(target_publication, "_flip_current", flip)

    with TargetSet(copy_dir) as view:
        assert view.read("target_format_reference.pdf") is not None
    monkeypatch.undo()

    metadata = json.loads((copy_dir / "target_format.json").read_text())
    if reported == ["published"]:
        assert metadata["input_filename"] == "second.docx", (
            "the resumed migration reverted the newer publication"
        )
    else:
        assert reported == ["busy"], reported
        assert metadata["input_filename"] == "first.docx"
        assert _publish(tmp_path, copy_dir, PDF_SET, "third")["target_format.json"] == (
            copy_dir / "target_format.json"
        ).read_bytes()


@pytest.mark.skipif(os.name != "posix", reason="the test publishes from a separate process")
def test_a_publication_reports_busy_while_another_process_decides(tmp_path: Path) -> None:
    """The decision lock is what serializes two publications, not luck.

    At ``64a272b`` both publications ran their check-then-switch sequence at once;
    the second one now waits outside or reports that it cannot take the lock.
    """
    artifact_dir = tmp_path / "artifact_two_publishers"
    _publish(tmp_path, artifact_dir, DOCX_SET, "first")
    staging, contents = _staging(tmp_path, DOCX_SET, "second")
    ready, release = tmp_path / "publisher.ready", tmp_path / "publisher.release"
    script = tmp_path / "paused_publisher.py"
    script.write_text(PAUSED_PUBLISHER, encoding="utf-8")
    child = subprocess.Popen(
        _child_command(script, artifact_dir, staging, ready, release, ",".join(DOCX_SET)),
        cwd=ROOT,
        env=_child_env(),
    )
    try:
        _wait_for_switch(ready, child)  # the child holds the decision lock
        with pytest.raises(TargetPublicationBusy):
            publish_target_set(artifact_dir, staging, DOCX_SET, threading.Event())
    finally:
        release.write_bytes(b"go")
        assert child.wait(timeout=HOLD_TIMEOUT_SECONDS) == 0

    assert (artifact_dir / "target_format_template.docx").read_bytes() == contents[
        "target_format_template.docx"
    ]


@pytest.mark.skipif(os.name != "posix", reason="the test publishes from a separate process")
def test_a_publication_during_a_paused_adoption_cannot_break_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An adopter copying the legacy layout keeps its source while it copies.

    At ``64a272b`` a publication in another process replaced the plain files the
    adopter was still copying with links to its own set, so the adopter switched a
    set of two uploads into the epoch it was reading (and a name the publication
    dropped made the copy raise ``FileNotFoundError``).
    """
    import shutil

    artifact_dir = tmp_path / "artifact_paused_copy"
    legacy = _legacy_artifact(tmp_path, artifact_dir, PDF_SET, "first")
    copy_epoch = target_publication._copy_epoch
    paused, resume = threading.Event(), threading.Event()

    def copy_one_file_then_pause(
        sets_dir: Path, source_dir: Path, names: tuple[str, ...]
    ) -> tuple[Path, int]:
        epoch, claim = copy_epoch(sets_dir, source_dir, names[:1])
        if not paused.is_set():
            paused.set()
            assert resume.wait(HOLD_TIMEOUT_SECONDS), "the test never resumed the copy"
            for name in names[1:]:
                shutil.copy2(source_dir / name, epoch / name)
        return epoch, claim

    monkeypatch.setattr(target_publication, "_copy_epoch", copy_one_file_then_pause)
    read_after: list[dict[str, bytes]] = []
    failures: list[BaseException] = []

    def adopt() -> None:
        try:
            with TargetSet(artifact_dir) as view:
                read_after.append(
                    {
                        name: (found.read_bytes() if (found := view.read(name)) else b"")
                        for name in PDF_SET
                    }
                )
        except BaseException as exc:  # the failure under test is the point
            failures.append(exc)

    adopter = threading.Thread(target=adopt)
    adopter.start()
    reader: subprocess.Popen | None = None
    try:
        assert paused.wait(HOLD_TIMEOUT_SECONDS), "the adopter never reached its copy"
        _staged_contents, reported = _publish_from_child(
            tmp_path, artifact_dir, PDF_SET, ("second",)
        )
        reader = _read_from_child(tmp_path, artifact_dir, "target_format_reference.pdf")
    finally:
        resume.set()
        adopter.join(HOLD_TIMEOUT_SECONDS)
    monkeypatch.undo()

    assert failures == [], f"a copy in progress lost its source: {failures!r}"
    assert read_after == [legacy], "the adopter read a set it had not copied"
    assert reported == ["busy"], reported
    assert reader is not None
    assert _child_result(tmp_path, reader) == legacy["target_format_reference.pdf"]
    current = artifact_dir / TARGETS_DIRNAME / CURRENT_LINK_NAME
    assert current.is_symlink() and current.is_dir()


@pytest.mark.skipif(os.name != "posix", reason="the test publishes from a separate process")
def test_a_view_of_an_unpublished_layout_does_not_block_publication(tmp_path: Path) -> None:
    """A view that pins nothing published holds no lock, so the owner can publish.

    At ``b7a28d0`` the view held the decision lock shared for its whole life, so
    every publication of that artifact was refused (the ``503`` path) while the
    view lived — including one a client leaked and never closed. The view now
    records the layout it read and fails closed instead of answering from a set
    it never pinned.
    """
    artifact_dir = tmp_path / "artifact_empty_view"
    artifact_dir.mkdir()
    view = TargetSet(artifact_dir)
    try:
        assert view.read("target_format.json") is None
        staged, reported = _publish_from_child(tmp_path, artifact_dir, DOCX_SET, ("second",))
        assert reported == ["published"], "an open view of nothing blocked the publication"
        with pytest.raises(TargetSetChanged):
            view.read("target_format.json")
        with pytest.raises(TargetSetChanged):
            view.read("target_format_template.docx")
        assert (artifact_dir / "target_format.json").read_bytes() == staged[0]["target_format.json"]
    finally:
        view.close()

    contents = _publish(tmp_path, artifact_dir, DOCX_SET, "third")
    assert view.read("target_format.json").read_bytes() == contents["target_format.json"]
    view.close()


@pytest.mark.skipif(os.name != "posix", reason="the test publishes from a separate process")
def test_a_read_across_a_publication_fails_the_whole_operation(tmp_path: Path) -> None:
    """The first read that sees a changed layout fails the operation, mixing nothing.

    At ``b7a28d0`` the view either held the decision lock shared for its whole life
    (so the publication was refused) or, with that hold gone, answered ``None`` from
    the artifact root for one name while a second name resolved through the new
    epoch — one operation over two different states of the artifact.
    """
    artifact_dir = tmp_path / "artifact_mid_operation"
    artifact_dir.mkdir()
    view = TargetSet(artifact_dir)
    try:
        assert view.read("target_format_template.docx") is None
        _staged, reported = _publish_from_child(tmp_path, artifact_dir, DOCX_SET, ("second",))
        assert reported == ["published"], reported
        for name in DOCX_SET:
            with pytest.raises(TargetSetChanged):
                view.read(name)
    finally:
        view.close()


@pytest.mark.skipif(os.name != "posix", reason="the test publishes from a separate process")
def test_a_leaked_view_of_an_unpublished_layout_does_not_block_publication(
    tmp_path: Path,
) -> None:
    """A view nobody closes holds nothing, so a leaked one cannot wedge the owner.

    At ``b7a28d0`` the shared decision hold lived as long as the view, so a view a
    caller forgot to close refused every later publication of that artifact for the
    life of the process.
    """
    artifact_dir = tmp_path / "artifact_leaked_view"
    artifact_dir.mkdir()
    view = TargetSet(artifact_dir)
    assert view.read("target_format.json") is None
    _staged, reported = _publish_from_child(
        tmp_path, artifact_dir, DOCX_SET, ("second", "third")
    )
    assert reported == ["published", "published"], reported
    del view  # never closed: collected only after two publications
    gc.collect()
    assert (artifact_dir / TARGETS_DIRNAME / CURRENT_LINK_NAME).is_dir()
    with TargetSet(artifact_dir) as fresh:
        assert fresh.read("target_format_template.docx") is not None


@pytest.mark.parametrize("shape", ["empty current only", "empty current beside plain files"])
def test_an_empty_current_directory_names_no_set(tmp_path: Path, shape: str) -> None:
    """An empty ``current`` directory is read and published like an absent one.

    At ``64a272b`` the directory read as a published set with no files and
    publication died in the switch with ``IsADirectoryError``.
    """
    artifact_dir = tmp_path / f"artifact_empty_current_{shape.replace(' ', '_')}"
    legacy = (
        {}
        if shape == "empty current only"
        else _legacy_artifact(tmp_path, artifact_dir, PDF_SET, "first")
    )
    targets = artifact_dir / TARGETS_DIRNAME
    targets.mkdir(parents=True, exist_ok=True)
    (targets / CURRENT_LINK_NAME).mkdir()

    with TargetSet(artifact_dir) as view:
        for name, payload in legacy.items():
            assert view.read(name).read_bytes() == payload, name
        if not legacy:
            assert view.read("target_format.json") is None

    contents = _publish(tmp_path, artifact_dir, DOCX_SET, "second")

    current = targets / CURRENT_LINK_NAME
    assert current.is_symlink() and current.is_dir()
    assert (artifact_dir / "target_format.json").read_bytes() == contents["target_format.json"]



def test_a_pruner_never_deletes_a_staged_set(tmp_path: Path) -> None:
    """A set a publisher staged is invisible to every pruner until the switch.

    At ``240eedb`` a staged epoch had no marker, so another publication pruned it
    between the copy and the switch.
    """
    from app import target_publication

    artifact_dir = tmp_path / "artifact_staged_claim"
    _publish(tmp_path, artifact_dir, DOCX_SET, "first")
    staged, claim = target_publication._staged_epoch(artifact_dir / TARGETS_DIRNAME)
    try:
        (staged / "target_format.json").write_bytes(b"staged")
        _publish(tmp_path, artifact_dir, DOCX_SET, "second")
        _publish(tmp_path, artifact_dir, DOCX_SET, "third")
        assert staged.is_dir(), "a pruner deleted a staged set"
    finally:
        os.close(claim)


def test_a_closed_view_selects_and_holds_again(tmp_path: Path) -> None:
    """``close`` ends the hold, so the next read selects what is current then.

    At ``240eedb`` a closed view kept the epoch it first pinned and read that
    stale set (or nothing, once it was pruned) although the docstring promised a
    fresh pin. A view created with ``resolved=`` keeps its own set and fails
    closed once that set is gone.
    """
    from app.target_publication import TargetSet

    artifact_dir = tmp_path / "artifact_reopen"
    first_view = TargetSet(artifact_dir)
    first = _publish(tmp_path, artifact_dir, DOCX_SET, "first")
    first_epoch = first_view.published_dir
    assert first_view.read("target_format.json").read_bytes() == first["target_format.json"]
    first_view.close()

    second = _publish(tmp_path, artifact_dir, DOCX_SET, "second")
    assert first_view.read("target_format.json").read_bytes() == second["target_format.json"]

    frozen = TargetSet(artifact_dir, resolved=first_epoch)
    try:
        assert frozen.read("target_format.json").read_bytes() == first["target_format.json"]
        frozen.close()
        assert frozen.read("target_format.json").read_bytes() == first["target_format.json"]
        frozen.close()  # let go before the set is pruned
        _publish(tmp_path, artifact_dir, DOCX_SET, "third")
        _publish(tmp_path, artifact_dir, DOCX_SET, "fourth")
        with pytest.raises(OSError):
            frozen.read("target_format.json")
    finally:
        frozen.close()

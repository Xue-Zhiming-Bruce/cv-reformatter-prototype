"""Publish one uploaded target format set as a single atomic switch.

An upload produces files that only mean something together: the stored source,
the compiled layout spec, the measurement evidence and the metadata naming them.
Renaming them one at a time into the artifact directory lets a reader see two
requests mixed, so a set is kept immutable under ``<artifact>/.targets/
<epoch>/`` and published by replacing the one ``current`` symlink that the
target names in the artifact directory follow. One rename therefore switches every
public name at once: a reader sees the previous complete set until the switch
and the new complete set after it.

Uploads for the same artifact id serialize on a per-artifact lock, so the later
one publishes over the earlier one instead of interleaving with it, and a
publisher whose caller gave up switches nothing and leaves the previous set
readable.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import uuid
from collections.abc import Sequence
from pathlib import Path

__all__ = ("CURRENT_LINK_NAME", "TARGETS_DIRNAME", "publish_target_set")

TARGETS_DIRNAME = ".targets"
CURRENT_LINK_NAME = "current"

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _artifact_lock(artifact_id: str) -> threading.Lock:
    # One lock per artifact id for the life of the process: publication is rare
    # and a lock costs a few dozen bytes.
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(artifact_id, threading.Lock())


def _new_epoch_dir(sets_dir: Path) -> Path:
    sets_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="set-", dir=sets_dir))


def _flip_current(sets_dir: Path, epoch_dir: Path) -> str | None:
    """Point ``current`` at ``epoch_dir`` with one rename; return the replaced name."""
    current = sets_dir / CURRENT_LINK_NAME
    previous = os.readlink(current) if current.is_symlink() else None
    pending = sets_dir / f".pending-{uuid.uuid4().hex}"
    # Relative target, so the outputs directory can move without breaking it.
    pending.symlink_to(epoch_dir.name)
    os.replace(pending, current)
    return previous


def _link_public_names(artifact_dir: Path, filenames: Sequence[str]) -> None:
    """Point each artifact-directory name at ``current``; metadata comes last.

    shortcut: replacing a plain file (a set published before this switch
    existed) is not atomic and can briefly show old metadata beside the new
    source, upgrade if pre-switch artifact directories must be migrated under
    load. New and re-published sets are already linked and switch in one rename.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    target = Path(TARGETS_DIRNAME) / CURRENT_LINK_NAME
    for filename in filenames:
        public = artifact_dir / filename
        if public.is_symlink() and os.readlink(public) == str(target / filename):
            continue
        pending = artifact_dir / f".pending-{uuid.uuid4().hex}"
        pending.symlink_to(target / filename)
        os.replace(pending, public)


def _prune_sets(sets_dir: Path, keep: tuple[str | None, ...]) -> None:
    """Drop superseded sets; the live one and the one it replaced stay readable."""
    for path in sets_dir.iterdir():
        if path.name == CURRENT_LINK_NAME or path.name in keep:
            continue
        shutil.rmtree(path, ignore_errors=True)


def publish_target_set(
    artifact_dir: Path,
    staging_dir: Path,
    filenames: Sequence[str],
    abandoned: threading.Event,
) -> None:
    """Publish one complete target set from ``staging_dir`` in a single rename.

    ``filenames`` names both the staged file and its public artifact name, in
    the order the switch should link them, metadata last. An upload whose caller
    was cancelled while it was publishing leaves the previous set untouched.
    """
    with _artifact_lock(artifact_dir.name):
        sets_dir = artifact_dir / TARGETS_DIRNAME
        epoch_dir = _new_epoch_dir(sets_dir)
        try:
            for filename in filenames:
                os.replace(staging_dir / filename, epoch_dir / filename)
            if abandoned.is_set():
                shutil.rmtree(epoch_dir, ignore_errors=True)
                return
            previous = _flip_current(sets_dir, epoch_dir)
        except BaseException:
            shutil.rmtree(epoch_dir, ignore_errors=True)
            raise
        _link_public_names(artifact_dir, filenames)
        _prune_sets(sets_dir, keep=(epoch_dir.name, previous))

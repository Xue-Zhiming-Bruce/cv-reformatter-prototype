"""Publish and read one uploaded target format set as a single atomic switch.

An upload produces files that only mean something together: the stored source,
the compiled layout spec, the measurement evidence and the metadata naming them.
Renaming them one at a time into the artifact directory lets a reader see two
requests mixed, so a set is kept immutable under ``<artifact>/.targets/
<epoch>/`` and published by replacing the one ``current`` symlink that the
artifact directory names follow. One rename therefore switches every public name
at once: a reader sees the previous complete set until the switch and the new
complete set after it.

:class:`TargetSet` is the only way in and out of that directory. A read pins the
epoch the first time a caller asks for a file and retains it until the view is
closed, so every file of one logical operation comes from the same upload even
when publications switch the set while the operation runs, and pruning cannot
delete a set an operation is still reading. A write never goes through a public
name: a public name is a symlink into the live epoch, so writing through it
would edit a set other readers treat as immutable.

An artifact directory published before the switch existed keeps plain files that
no rename can replace together. Its first read or write copies them into one
complete epoch and switches to it, so the directory is either entirely legacy or
a complete epoch: never half of each, and an adoption that dies before the
switch leaves the whole legacy set readable. A copy of a published directory made
with its links dereferenced (``cp -rL``, ``rsync -L``, a restored backup) has a
real directory where ``current`` should be; that directory is the published set,
migrated to an epoch the first time the copy is read or written. No form of
``current`` is an error: a valid link names an epoch, a real directory is one, and
an absent name, a dangling link or a plain file all mean nothing is published.

Uploads for the same artifact id serialize on a per-artifact lock, so the later
one publishes over the earlier one instead of interleaving with it, and a
publisher whose caller gave up switches back and leaves the previous set
readable. Every publication also drops what a publisher that died left behind:
an unpublished epoch, a pending link and superseded sets.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import threading
import uuid
from collections.abc import Sequence
from pathlib import Path

from app.template_analysis.artifacts import (
    ADOBE_RAW_FILENAME,
    LAYOUT_SPEC_FILENAME,
    NORMALIZED_LAYOUT_FILENAME,
    TARGET_EVIDENCE_FILENAME,
)

__all__ = ("CURRENT_LINK_NAME", "TARGETS_DIRNAME", "TargetSet", "publish_target_set")

TARGETS_DIRNAME = ".targets"
CURRENT_LINK_NAME = "current"
_PENDING_PREFIX = ".pending-"
#: Prefix of a ``current`` directory a migration renamed aside before linking it.
_MIGRATING_PREFIX = ".migrating-"
#: The artifact-root names one upload publishes. A directory published before
#: the switch holds them as plain files, which adoption reads as one set.
LEGACY_TARGET_FILENAMES = (
    "target_format_reference.pdf",
    "target_format_template.docx",
    "target_format.json",
    ADOBE_RAW_FILENAME,
    NORMALIZED_LAYOUT_FILENAME,
    TARGET_EVIDENCE_FILENAME,
    LAYOUT_SPEC_FILENAME,
)

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_LEASES: dict[str, int] = {}
_LEASES_GUARD = threading.Lock()


class _AbandonedUpload(Exception):
    """Internal: the caller gave up, so the staged set is discarded."""


def _artifact_lock(artifact_id: str) -> threading.Lock:
    # One lock per artifact id for the life of the process: publication is rare
    # and a lock costs a few dozen bytes.
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(artifact_id, threading.Lock())


def _lease(epoch: Path) -> None:
    with _LEASES_GUARD:
        _LEASES[str(epoch)] = _LEASES.get(str(epoch), 0) + 1


def _release(epoch: Path) -> None:
    with _LEASES_GUARD:
        remaining = _LEASES.get(str(epoch), 0) - 1
        if remaining > 0:
            _LEASES[str(epoch)] = remaining
        else:
            _LEASES.pop(str(epoch), None)


def _leased(epoch: Path) -> bool:
    with _LEASES_GUARD:
        return str(epoch) in _LEASES


def _epoch_names(epoch: Path | None) -> list[str]:
    if epoch is None or not epoch.is_dir():
        return []
    return sorted(path.name for path in epoch.iterdir() if path.is_file())


def _current_epoch_name(sets_dir: Path) -> str | None:
    """The sibling epoch ``current`` names, or ``None`` when it names no epoch."""
    mode = _current_mode(sets_dir)
    if mode is None or not stat.S_ISLNK(mode):
        return None
    return Path(os.readlink(sets_dir / CURRENT_LINK_NAME)).name


def _is_dir(path: Path) -> bool:
    """``path`` is a directory; an unreadable path raises instead of reading as absent."""
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except FileNotFoundError:
        return False


def _current_mode(sets_dir: Path) -> int | None:
    """The mode of ``current`` itself, or ``None`` when nothing is there.

    An unreadable ``current`` raises: it must not read as an artifact that never
    had a target, which would silently drop a published set from every read.
    """
    try:
        return os.lstat(sets_dir / CURRENT_LINK_NAME).st_mode
    except FileNotFoundError:
        return None


def _current_set(sets_dir: Path) -> Path | None:
    """The published set directory, whatever form ``current`` is in.

    A link names an epoch; a real directory is a set copied out of one. A
    dangling link, a plain file and an absent name publish nothing, so the
    artifact keeps serving what was published before them.
    """
    mode = _current_mode(sets_dir)
    current = sets_dir / CURRENT_LINK_NAME
    if mode is None:
        return None
    if stat.S_ISLNK(mode):
        epoch = sets_dir / Path(os.readlink(current)).name
        return epoch if _is_dir(epoch) else None
    return current if stat.S_ISDIR(mode) else None


def _new_epoch_dir(sets_dir: Path) -> Path:
    sets_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="set-", dir=sets_dir))


def _flip_current(sets_dir: Path, epoch_name: str | None) -> str | None:
    """Point ``current`` at ``epoch_name`` with one rename; return the replaced name.

    ``None`` removes the link, which is how a switch is run back on an artifact
    that had no published set before it.
    """
    previous = _current_epoch_name(sets_dir)
    current = sets_dir / CURRENT_LINK_NAME
    if epoch_name is None:
        current.unlink(missing_ok=True)
        return previous
    pending = sets_dir / f"{_PENDING_PREFIX}{uuid.uuid4().hex}"
    try:
        # Relative target, so the outputs directory can move without breaking it.
        pending.symlink_to(epoch_name)
        os.replace(pending, current)
    finally:
        pending.unlink(missing_ok=True)
    return previous


def contained_in_artifact(artifact_dir: str | Path, path: str | Path) -> bool:
    """Whether ``path`` sits under ``artifact_dir`` with no symlinked component.

    Resolving a target and its expected directory separately keeps them agreeing
    after a *directory* is replaced by a symlink elsewhere, so containment is
    decided by walking the components below the artifact root and refusing any
    symlink, not by comparing resolved paths.
    """
    root = Path(artifact_dir)
    try:
        relative = Path(path).relative_to(root)
    except ValueError:
        return False
    current = root
    for part in relative.parts:
        if part in {"", ".", ".."}:
            return False
        current = current / part
        if current.is_symlink():
            return False
    return True


def publish_target_set(
    artifact_dir: Path,
    staging_dir: Path,
    filenames: Sequence[str],
    abandoned: threading.Event,
) -> None:
    """Publish one complete target set from ``staging_dir`` in a single rename.

    ``filenames`` names both the staged file and its public artifact name, in
    the order the switch should link them, metadata last.
    """
    TargetSet(artifact_dir).publish(staging_dir, filenames, abandoned)


def _copy_epoch(sets_dir: Path, artifact_dir: Path, names: Sequence[str]) -> Path:
    """Copy ``names`` into one new epoch; a failed copy leaves no epoch behind."""
    epoch = _new_epoch_dir(sets_dir)
    try:
        for name in names:
            shutil.copy2(artifact_dir / name, epoch / name)
    except BaseException:
        shutil.rmtree(epoch, ignore_errors=True)
        raise
    return epoch


class TargetSet:
    """One artifact's published target set: its pinned read view and its writer."""

    def __init__(self, artifact_dir: Path, *, resolved: Path | None = None) -> None:
        # ``resolved`` reuses a set another caller already pinned, so a frozen
        # target document and everything read beside it come from one epoch.
        self.artifact_dir = artifact_dir
        self._epoch = resolved
        self._pin: Path | None = None

    def __enter__(self) -> TargetSet:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # pragma: no cover - interpreter shutdown only
            pass

    def close(self) -> None:
        """Stop retaining the pinned set; later reads of this view pin again."""
        if self._pin is not None:
            epoch, self._pin = self._pin, None
            _release(epoch)

    @property
    def sets_dir(self) -> Path:
        return self.artifact_dir / TARGETS_DIRNAME

    @property
    def published_dir(self) -> Path:
        """The pinned directory every read of this view resolves in.

        The set stays retained for as long as this view holds it, so a
        publication cannot prune files an operation is still reading.
        """
        if self._epoch is None:
            with _artifact_lock(self.artifact_dir.name):
                self._adopt_legacy()
                self._epoch = _current_set(self.sets_dir) or self.artifact_dir
        if self._pin is None:
            self._pin = self._epoch
            _lease(self._epoch)
        return self._epoch

    def read(self, filename: str) -> Path | None:
        """``filename`` from the pinned epoch, or ``None`` when it is not published."""
        path = self.published_dir / filename
        return path if path.is_file() else None

    def publish(
        self,
        staging_dir: Path,
        filenames: Sequence[str],
        abandoned: threading.Event,
    ) -> None:
        """Publish one complete set from ``staging_dir`` and switch every name.

        Abandonment is checked before the switch, after it and once the public
        names are linked; that, or a failure while linking, puts the previous
        set back and discards the staged one, so nothing half published stays.
        """
        self.published_dir  # adopt a legacy directory before switching it
        with _artifact_lock(self.artifact_dir.name):
            self._drop_leftovers()
            epoch = _new_epoch_dir(self.sets_dir)
            try:
                previous = self._switch(epoch, staging_dir, filenames, abandoned)
            except _AbandonedUpload:
                shutil.rmtree(epoch, ignore_errors=True)
                return
            except BaseException:
                self._roll_back(epoch, None)
                raise
            try:
                self._link_public_names(filenames)
                if abandoned.is_set():
                    self._roll_back(epoch, previous)
                    return
            except BaseException:
                self._roll_back(epoch, previous)
                raise
            self._prune(keep=(epoch.name, previous))

    def _adopt_legacy(self) -> None:
        """Give a set that is not an epoch yet one complete epoch and a link.

        Caller holds the lock. Two forms predate the switch: plain files at the
        artifact root, and a ``current`` that is a real directory because the
        artifact was copied with its links dereferenced.
        """
        self._recover_migration()
        mode = _current_mode(self.sets_dir)
        if mode is not None and stat.S_ISLNK(mode):
            return  # a link names its epoch; a dangling one publishes nothing
        if mode is not None and stat.S_ISDIR(mode):
            self._migrate_current_dir()
            return
        legacy = [
            name
            for name in LEGACY_TARGET_FILENAMES
            if (self.artifact_dir / name).is_file()
            and not (self.artifact_dir / name).is_symlink()
        ]
        if legacy:
            self._adopt_plain_files(legacy)

    def _adopt_plain_files(self, legacy: Sequence[str]) -> None:
        """Copy the plain root files into one epoch, switch to it and link them.

        The copy rather than a move is what keeps the whole legacy set readable
        when the switch fails; the target file exists twice for the length of
        it.
        """
        epoch = _copy_epoch(self.sets_dir, self.artifact_dir, legacy)
        try:
            _flip_current(self.sets_dir, epoch.name)
        except BaseException:
            shutil.rmtree(epoch, ignore_errors=True)  # nothing names it yet
            raise
        self._link_public_names(legacy)

    def _migrate_current_dir(self) -> None:
        """Turn a dereferenced ``current`` directory into an epoch and its link.

        The directory is copied into an epoch first and renamed aside second, so
        a migration that dies before the switch leaves the set where
        ``_recover_migration`` puts it back; the target file exists twice for
        the length of it.
        """
        current = self.sets_dir / CURRENT_LINK_NAME
        names = _epoch_names(current)
        epoch = _copy_epoch(self.sets_dir, current, names)
        retired = self.sets_dir / f"{_MIGRATING_PREFIX}{uuid.uuid4().hex}"
        os.replace(current, retired)
        try:
            _flip_current(self.sets_dir, epoch.name)
        except BaseException:
            os.replace(retired, current)  # the directory never stopped being it
            shutil.rmtree(epoch, ignore_errors=True)
            raise
        shutil.rmtree(retired, ignore_errors=True)
        self._link_public_names(names)

    def _recover_migration(self) -> None:
        """Put a ``current`` directory a died migration renamed aside back in place."""
        retired = sorted(self.sets_dir.glob(f"{_MIGRATING_PREFIX}*"))
        if retired and _current_mode(self.sets_dir) is None:
            os.replace(retired[-1], self.sets_dir / CURRENT_LINK_NAME)

    def _switch(
        self,
        epoch: Path,
        staging_dir: Path,
        filenames: Sequence[str],
        abandoned: threading.Event,
    ) -> str | None:
        """Fill ``epoch`` from the staged set, make it current, return the replaced name.

        An upload whose caller gave up publishes nothing: abandonment observed
        just after the switch runs the switch back before this reports it.
        """
        for filename in filenames:
            os.replace(staging_dir / filename, epoch / filename)
        if abandoned.is_set():
            raise _AbandonedUpload
        previous = _flip_current(self.sets_dir, epoch.name)
        if not abandoned.is_set():
            return previous
        _flip_current(self.sets_dir, previous)
        raise _AbandonedUpload

    def _roll_back(self, epoch: Path, previous: str | None) -> None:
        """Put the previous set back and discard ``epoch``, so nothing half published stays."""
        if previous is not None or _current_epoch_name(self.sets_dir) == epoch.name:
            _flip_current(self.sets_dir, previous)
            self._link_public_names(
                _epoch_names(self.sets_dir / previous if previous is not None else None)
            )
        shutil.rmtree(epoch, ignore_errors=True)

    def _drop_leftovers(self) -> None:
        """Drop the epochs and pending names that publishers who died left behind."""
        if self.sets_dir.is_dir():
            self._prune(keep=(_current_epoch_name(self.sets_dir),))
        for pending in self.artifact_dir.glob(f"{_PENDING_PREFIX}*"):
            pending.unlink(missing_ok=True)

    def _link_public_names(self, filenames: Sequence[str]) -> None:
        """Point every public target name at the live set."""
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        link_target = str(Path(TARGETS_DIRNAME) / CURRENT_LINK_NAME) + "/"
        for filename in filenames:
            public = self.artifact_dir / filename
            if public.is_symlink() and os.readlink(public) == link_target + filename:
                continue
            pending = self.artifact_dir / f"{_PENDING_PREFIX}{uuid.uuid4().hex}"
            try:
                pending.symlink_to(link_target + filename)
                os.replace(pending, public)
            finally:
                pending.unlink(missing_ok=True)
        self._drop_absent_names(set(filenames), link_target)

    def _drop_absent_names(self, filenames: set[str], link_target: str) -> None:
        """Remove the names the current set does not publish.

        That is a replaced set's name (a PDF name after a DOCX upload) and a
        plain file that a crash during adoption left beside the links.
        """
        for public in self.artifact_dir.iterdir():
            if public.name in filenames:
                continue
            linked = public.is_symlink() and os.readlink(public).startswith(link_target)
            abandoned = (
                public.name in LEGACY_TARGET_FILENAMES
                and public.is_file()
                and not public.is_symlink()
            )
            if linked or abandoned:
                public.unlink()

    def _prune(self, keep: Sequence[str | None]) -> None:
        """Drop superseded sets; the live set, ``keep`` and every leased set stay."""
        for path in self.sets_dir.iterdir():
            if path.name == CURRENT_LINK_NAME or path.name in keep or _leased(path):
                continue
            if path.is_symlink():
                path.unlink()
            else:
                shutil.rmtree(path, ignore_errors=True)

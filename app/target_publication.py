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
delete a set an operation is still reading. Choosing the epoch and holding it
happen in one critical section, so no publication can land in between. The hold
is a shared ``flock`` on the epoch directory itself: every process sees it, and
the kernel drops it when the reader closes the view or its process dies, which a
process-local registry cannot promise. A publisher holds its staged epoch
exclusively until the switch and fails closed if that claim was lost, and a
pruner deletes only a set it can take exclusively, so a set being read, staged
or migrated is never deleted. A write never goes through a public name: a public
name is a symlink into the live epoch, so writing through it would edit a set
other readers treat as immutable.

An artifact directory published before the switch existed keeps plain files that
no rename can replace together. Its first read or write copies them into one
complete epoch and switches to it, so the directory is either entirely legacy or
a complete epoch: never half of each, and an adoption that dies before the
switch leaves the whole legacy set readable. The plain files are adopted whenever
``current`` does not name a published epoch — absent, dangling, a stray file —
so a reader pins a complete set rather than a directory the next publication
rewrites underneath it. A copy of a published directory made with its links
dereferenced (``cp -rL``, ``rsync -L``, a restored backup) has a real directory
where ``current`` should be; that directory is the published set, migrated to an
epoch the first time the copy is read or written.

Uploads for the same artifact id serialize on a per-artifact lock, so the later
one publishes over the earlier one instead of interleaving with it, and a
publisher whose caller gave up switches back and leaves the previous set
readable. Every publication also drops what a publisher that died left behind:
an unpublished epoch, a pending link and superseded sets.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import stat
import tempfile
import threading
import time
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
#: How often a read re-selects a set that was pruned before it could hold it.
_SELECTION_ATTEMPTS = 5
_RETRY_SECONDS = 0.01
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


class _AbandonedUpload(Exception):
    """Internal: the caller gave up, so the staged set is discarded."""


def _artifact_lock(artifact_id: str) -> threading.Lock:
    # One lock per artifact id for the life of the process: publication is rare
    # and a lock costs a few dozen bytes.
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(artifact_id, threading.Lock())


def _lock_epoch(epoch: Path, *, exclusive: bool = False) -> int | None:
    """Hold ``epoch`` itself: shared to read it, exclusive to delete or stage it.

    The lock belongs to the directory's open file description, so every process
    sees it and the kernel drops it when the holder exits. An exclusive attempt
    never waits; a shared one waits out a deletion in progress.
    """
    try:
        fd = os.open(epoch, os.O_RDONLY)
    except FileNotFoundError:
        return None
    flags = fcntl.LOCK_EX | fcntl.LOCK_NB if exclusive else fcntl.LOCK_SH
    try:
        fcntl.flock(fd, flags)
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return fd


def _directory_identity(path: Path) -> tuple[int, int] | None:
    """The device and inode ``path`` names now, or ``None`` when it is gone."""
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino)


def _same_directory(fd: int, path: Path) -> bool:
    """``fd`` still names the directory at ``path``, so the hold is not a ghost."""
    held = os.fstat(fd)
    return _directory_identity(path) == (held.st_dev, held.st_ino)


def _hold_epoch(epoch: Path) -> int | None:
    """Retain ``epoch`` for as long as the returned descriptor stays open."""
    fd = _lock_epoch(epoch)
    if fd is None:
        return None
    if _same_directory(fd, epoch):
        return fd
    os.close(fd)  # pruned between the open and the lock
    return None


def _staged_epoch(sets_dir: Path) -> tuple[Path, int]:
    """Create a set directory and claim it before any pruner sees it unclaimed."""
    for _ in range(_SELECTION_ATTEMPTS):
        epoch = _new_epoch_dir(sets_dir)
        fd = _lock_epoch(epoch, exclusive=True)
        if fd is not None and _same_directory(fd, epoch):
            return epoch, fd
        if fd is not None:
            os.close(fd)
    raise OSError("A staged target set could not be claimed.")


def _discard(path: Path) -> None:
    """Delete one superseded entry unless a reader, stager or migration holds it."""
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
        return
    fd = _lock_epoch(path, exclusive=True)
    if fd is None:
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
    finally:
        os.close(fd)


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


def _copy_epoch(sets_dir: Path, source_dir: Path, names: Sequence[str]) -> tuple[Path, int]:
    """Copy ``names`` into one claimed epoch; a failed copy leaves no epoch behind."""
    epoch, claim = _staged_epoch(sets_dir)
    try:
        for name in names:
            shutil.copy2(source_dir / name, epoch / name)
    except BaseException:
        shutil.rmtree(epoch, ignore_errors=True)
        os.close(claim)
        raise
    return epoch, claim


def _restore_retired(retired: Path, current: Path) -> None:
    """Put a directory a migration renamed aside back where readers look for it."""
    if retired.is_dir() and not os.path.lexists(current):
        os.replace(retired, current)


class TargetSet:
    """One artifact's published target set: its pinned read view and its writer."""

    def __init__(self, artifact_dir: Path, *, resolved: Path | None = None) -> None:
        # ``resolved`` reuses a set another caller already selected, so a frozen
        # target document and everything read beside it come from one epoch.
        self.artifact_dir = artifact_dir
        self._frozen = resolved
        self._pinned: Path | None = None
        self._fd: int | None = None

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
        """Release the set; the next read selects and holds it again.

        A view created with ``resolved=`` reads that same set again and reads
        nothing once it is gone; any other view reads what is current then.
        """
        fd, self._fd, self._pinned = self._fd, None, None
        if fd is not None:
            os.close(fd)

    @property
    def sets_dir(self) -> Path:
        return self.artifact_dir / TARGETS_DIRNAME

    @property
    def published_dir(self) -> Path:
        """The directory this view reads, held for as long as the view stays open.

        Selecting the set and holding it are one critical section, so no
        publication in this or another process can delete it in between.
        """
        if self._pinned is None:
            self._pinned = self._select()
        return self._pinned

    def read(self, filename: str) -> Path | None:
        """``filename`` from the held set, or ``None`` when it is not published."""
        path = self.published_dir / filename
        return path if path.is_file() else None

    def _select(self) -> Path:
        """Choose what this view reads and hold it, retrying a set pruned meanwhile."""
        for attempt in range(_SELECTION_ATTEMPTS):
            with _artifact_lock(self.artifact_dir.name):
                epoch = self._frozen or self._chosen()
                unpublished = epoch == self.artifact_dir
                fd = None if unpublished else _hold_epoch(epoch)
                if fd is not None or unpublished:
                    self._fd = fd
                    return epoch
                if self._frozen is not None:
                    raise OSError(f"The frozen target set is gone: {epoch}")
            time.sleep(_RETRY_SECONDS * (attempt + 1))
        raise OSError("The published target set changed while it was being read.")

    def _chosen(self) -> Path:
        """Adopt what a pre-switch or copied directory holds, then name the set.

        Caller holds the lock.
        """
        self._adopt_legacy()
        return _current_set(self.sets_dir) or self.artifact_dir

    def publish(
        self,
        staging_dir: Path,
        filenames: Sequence[str],
        abandoned: threading.Event,
    ) -> None:
        """Publish one complete set from ``staging_dir`` and switch every name.

        The staged epoch is claimed until the switch, so no pruner in any process
        can delete it while it is being filled. Abandonment is checked before the
        switch, after it and once the public names are linked; that, or a failure
        while linking, puts the previous set back and discards the staged one.
        """
        self.published_dir  # adopt a legacy directory before switching it
        with _artifact_lock(self.artifact_dir.name):
            self._drop_leftovers()
            epoch, claim = _staged_epoch(self.sets_dir)
            try:
                previous = self._switch(epoch, staging_dir, filenames, abandoned)
            except _AbandonedUpload:
                shutil.rmtree(epoch, ignore_errors=True)
                return
            except BaseException:
                self._roll_back(epoch, None)
                raise
            finally:
                os.close(claim)  # current now, so a pruner keeps it anyway
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

        Caller holds the lock. Plain files are adopted whenever ``current`` does
        not name a published epoch, so a reader pins a complete set instead of a
        directory the next publication rewrites underneath it. A real directory
        at ``current`` is the set a dereferenced copy left behind.
        """
        self._recover_migration()
        mode = _current_mode(self.sets_dir)
        copied = (
            _epoch_names(self.sets_dir / CURRENT_LINK_NAME)
            if mode is not None and stat.S_ISDIR(mode)
            else []
        )
        if copied:
            self._migrate_current_dir(copied)
            return
        if _current_set(self.sets_dir) is not None:
            return  # a link names a published epoch
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

        The copy rather than a move keeps the whole legacy set readable when the
        switch fails, and the claim keeps a pruner in any process off the copy. An
        upload that published while this copy was made wins: adopting a stale set
        over it would revert a newer target.
        """
        epoch, claim = _copy_epoch(self.sets_dir, self.artifact_dir, legacy)
        try:
            if _current_set(self.sets_dir) is not None:
                shutil.rmtree(epoch, ignore_errors=True)
                return
            _flip_current(self.sets_dir, epoch.name)
        except BaseException:
            shutil.rmtree(epoch, ignore_errors=True)  # nothing names it yet
            raise
        finally:
            os.close(claim)
        self._link_public_names(legacy)

    def _migrate_current_dir(self, names: Sequence[str]) -> None:
        """Turn a dereferenced ``current`` directory into an epoch and its link.

        The copy is claimed before the directory is renamed aside, so a pruner in
        any process leaves it alone; a failed switch leaves the directory for
        ``_recover_migration`` to put back, and a directory another process
        replaced meanwhile is dropped rather than published over it.
        """
        current = self.sets_dir / CURRENT_LINK_NAME
        source = _directory_identity(current)
        epoch, claim = _copy_epoch(self.sets_dir, current, names)
        retired = self.sets_dir / f"{_MIGRATING_PREFIX}{epoch.name}"
        try:
            if _directory_identity(current) != source:
                shutil.rmtree(epoch, ignore_errors=True)
                return  # another process replaced the directory while this copy was made
            os.replace(current, retired)
            _flip_current(self.sets_dir, epoch.name)
        except BaseException:
            shutil.rmtree(epoch, ignore_errors=True)
            _restore_retired(retired, current)
            raise
        finally:
            os.close(claim)
        shutil.rmtree(retired, ignore_errors=True)
        self._link_public_names(names)

    def _recover_migration(self) -> None:
        """Put back a set a died migration renamed aside, and drop finished litter.

        A retired copy is never pruned: while ``current`` names no set it is the
        only complete copy, and once ``current`` resolves again it is litter.
        """
        retired = sorted(self.sets_dir.glob(f"{_MIGRATING_PREFIX}*"))
        current = self.sets_dir / CURRENT_LINK_NAME
        if _current_set(self.sets_dir) is None and retired:
            _restore_retired(retired[-1], current)
            retired = retired[:-1]
        for path in retired:
            shutil.rmtree(path, ignore_errors=True)

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
        """Drop superseded sets; the live set, ``keep`` and every held set stay.

        "Held" is a kernel fact shared by every process: a reader holds a set
        shared and a publisher holds its staged set exclusively, so a pruner deletes
        only what it can take exclusively itself. A migration's retired copy is
        recovery material and is left to ``_recover_migration``.
        """
        for path in self.sets_dir.iterdir():
            if path.name == CURRENT_LINK_NAME or path.name in keep:
                continue
            if path.name.startswith(_MIGRATING_PREFIX):
                continue
            _discard(path)

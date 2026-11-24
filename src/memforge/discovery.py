"""Memory-file discovery: walk a memory folder skipping archive/ + MEMORY.md.

Single replacement for the 5+ ad-hoc discovery loops scattered across tools
(see code-craftsman:CC-006).
"""

from __future__ import annotations

import errno
import io
import os
import stat
from pathlib import Path
from typing import Iterable, Iterator, Optional

from memforge.paths import ARCHIVE_DIRNAME


def is_memory_file(path: Path, root: Optional[Path] = None) -> bool:
    """True iff path is a candidate memory file.

    Memory files are .md files that are NOT MEMORY.md (the index) and are
    not under an archive/ subtree of the memory root.

    The archive check is evaluated relative to ``root`` when supplied, so it
    matches the pruning done by :func:`walk_memory_files` (which prunes
    archive/ only below the root). Without ``root``, the path is treated as
    already-relative-to-root and any ``archive`` component rejects it.

    Passing ``root`` is REQUIRED for consistency when the memory root itself
    lives under a directory named ``archive`` (for example a partner storing
    memory under ``data-archive/memory/``). In that case the absolute-path
    check would reject every memory file while ``walk_memory_files`` yields
    them, causing silent data loss for a consumer that filters
    ``walk_memory_files`` output through ``is_memory_file`` (closes
    discovery-01).
    """
    if not path.name.endswith(".md"):
        return False
    if path.name == "MEMORY.md":
        return False
    if root is not None:
        try:
            rel_parts = path.resolve().relative_to(Path(root).resolve()).parts
        except ValueError:
            # path is not under root; fall back to the whole-path check.
            rel_parts = path.parts
        if ARCHIVE_DIRNAME in rel_parts:
            return False
        return True
    if ARCHIVE_DIRNAME in path.parts:
        return False
    return True


def walk_md_files(root: Path, skip_dirs: Iterable[str] = ()) -> Iterator[Path]:
    """Yield every regular ``.md`` file under root, never through a symlink.

    Symlinked files are skipped and symlinked directories are not descended, so
    a link planted in a memory folder cannot pull a file from outside the
    folder into a tool's input. ``skip_dirs`` names directories to prune at any
    depth. Files within one directory come out in sorted order.

    A folder that cannot be listed, ``root`` included, is yielded in place of
    the files it holds. Reading it fails as reading an unreadable file does,
    so a caller reports it the way it reports such a file instead of passing
    over the files it could not see. A ``root`` that is missing or is not a
    folder holds no files and yields nothing.
    """
    pruned = set(skip_dirs)
    unlistable: list[Path] = []

    def note(err: OSError) -> None:
        if err.filename is None:
            return
        absent = isinstance(err, (FileNotFoundError, NotADirectoryError))
        if not (absent and Path(err.filename) == Path(root)):
            unlistable.append(Path(err.filename))

    for dirpath, dirnames, filenames in os.walk(root, onerror=note, followlinks=False):
        yield from unlistable
        unlistable.clear()
        dirnames[:] = [d for d in dirnames if d not in pruned]
        for fn in sorted(filenames):
            path = Path(dirpath) / fn
            if fn.endswith(".md") and not os.path.islink(path):
                yield path
    yield from unlistable


def walk_memory_files(root: Path) -> Iterable[Path]:
    """Yield all memory files under root.

    Prunes archive/ subtrees during the walk for efficiency. Skips MEMORY.md
    indexes (those are emitted by memory-index-gen, not authored) and anything
    reached through a symlink (see :func:`walk_md_files`).
    """
    for path in walk_md_files(Path(root), skip_dirs=(ARCHIVE_DIRNAME,)):
        if path.name != "MEMORY.md":
            yield path


def access_error(path: str | os.PathLike[str]) -> Optional[OSError]:
    """Why ``path`` is there but cannot be looked at, else None.

    ``os.path.exists``, ``isdir`` and ``isfile`` answer False for any error,
    so a check built on them alone passes over a store, folder or file it
    cannot read as if nothing were there. This tells the two apart: the
    error is returned for a path that cannot be stat'ed for a reason other
    than ENOENT or ENOTDIR (a folder on the way that cannot be searched, a
    link loop) and for a folder that cannot be listed. None means the path
    is absent, or is there and can be looked at; the caller's own checks
    decide what to do with it.
    """
    try:
        if stat.S_ISDIR(os.stat(path).st_mode):
            os.scandir(path).close()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        return exc
    return None


class SymlinkWriteRefused(OSError):
    """A write was refused because it would land in a file other than the one
    named: through a symlink, or into a file that has a second hard link."""


class LinkReadRefused(OSError):
    """A read was refused because the path is a symlink or not a regular file."""


# Flags missing on a platform are 0. O_BINARY (Windows) keeps the C runtime
# from translating newlines underneath Python's own text layer, as open() does.
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)

# Whether a file can be opened and checked relative to an open folder. Not on
# Windows, where a path under a root is opened by path (see the residual in
# :func:`write_text_inside`).
_OPEN_BY_FOLDER = (
    {os.open, os.stat} <= os.supports_dir_fd and os.stat in os.supports_follow_symlinks
)

# What opening a symlink with O_NOFOLLOW fails with: ELOOP on Linux and macOS,
# EMLINK on FreeBSD, ENOTDIR where O_DIRECTORY is checked first.
_LINK_ERRNOS = {errno.ELOOP, errno.EMLINK, errno.ENOTDIR}


def _folder_names(root: Path, path: Path, refused: type[OSError], verb: str) -> tuple[str, ...]:
    """The folder names from ``root`` down to ``path``'s folder, refusing a
    ``path`` whose folder resolves outside ``root``."""
    try:
        return path.parent.resolve().relative_to(Path(root).resolve()).parts
    except ValueError:
        raise refused(
            f"refusing to {verb} through a symlinked folder that leaves {root}: {path}"
        ) from None


def _open_folder(
    root: Path, names: tuple[str, ...], path: Path, refused: type[OSError], verb: str
) -> int:
    """Open ``root``, then each of ``names`` relative to the folder above it,
    never following a link, and return the last folder's descriptor."""
    fd = os.open(Path(root).resolve(), os.O_RDONLY | _O_DIRECTORY)
    try:
        for name in names:
            try:
                child = os.open(
                    name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=fd
                )
            except OSError as exc:
                if exc.errno in _LINK_ERRNOS:
                    raise refused(
                        f"refusing to {verb} {path}: the folder {name} on its way "
                        "is a symlink or not a folder"
                    ) from None
                raise
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


def _is_link(target: str | Path, dir_fd: Optional[int]) -> bool:
    try:
        return stat.S_ISLNK(os.stat(target, dir_fd=dir_fd, follow_symlinks=False).st_mode)
    except OSError:
        return False


def _open_no_follow(
    path: Path,
    flags: int,
    refused: type[OSError],
    verb: str,
    root: Optional[Path] = None,
) -> int:
    """Open ``path`` and return a descriptor of the regular file it names.

    The decision is made on the opened file, so a link swapped in after any
    earlier check is caught: O_NOFOLLOW refuses a symlink where the platform
    has it, and everywhere the opened file must be the very file ``path``
    names (same device and inode as its lstat, and that lstat not a link).
    Nothing is truncated or read here.

    With ``root``, a ``path`` whose folder resolves outside ``root`` is
    refused, and where the platform allows it the file is opened relative to
    its folder, reached from ``root`` one name at a time without following a
    link (see :func:`_open_folder`): a folder swapped for a link after the
    check cannot redirect the open.

    The open never waits: O_NONBLOCK keeps a named pipe from blocking it, and
    the descriptor is set back to blocking once it is known to be a regular
    file, so reads and writes behave as without the flag.
    """
    folder_fd: Optional[int] = None
    target: str | Path = path
    # ``root`` itself (a walk yields it when it cannot be listed) has no folder
    # under ``root``; opening it as named fails with the reason it cannot be read.
    if root is not None and Path(path) != Path(root):
        names = _folder_names(root, path, refused, verb)
        if _OPEN_BY_FOLDER:
            folder_fd = _open_folder(root, names, path, refused, verb)
            target = path.name
    try:
        try:
            fd = os.open(
                target,
                flags | _O_NOFOLLOW | _O_BINARY | _O_NONBLOCK,
                0o666,
                dir_fd=folder_fd,
            )
        except OSError as exc:
            # Covers every way a link fails to open: ELOOP or EMLINK under
            # O_NOFOLLOW, EEXIST under O_EXCL, ENOENT for a dangling link.
            if _is_link(target, folder_fd):
                raise refused(f"refusing to {verb} through a symlink: {path}") from None
            # A write-only, non-blocking open of a pipe with no reader fails ENXIO.
            if exc.errno == errno.ENXIO:
                raise refused(f"refusing to {verb} {path}: not a regular file") from None
            raise
        try:
            opened = os.fstat(fd)
            named = os.stat(target, dir_fd=folder_fd, follow_symlinks=False)
            if stat.S_ISLNK(named.st_mode):
                raise refused(f"refusing to {verb} through a symlink: {path}")
            if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
                raise refused(f"refusing to {verb} {path}: it changed while being opened")
            if not stat.S_ISREG(opened.st_mode):
                raise refused(f"refusing to {verb} {path}: not a regular file")
            if _O_NONBLOCK:
                os.set_blocking(fd, True)
        except BaseException:
            os.close(fd)
            raise
    finally:
        if folder_fd is not None:
            os.close(folder_fd)
    return fd


def write_text_inside(root: Path, path: Path, text: str) -> None:
    """Write ``text`` to ``path`` under ``root`` without following a link.

    A link planted in a memory folder would otherwise redirect a tool's write
    to any file the operator can write. The bytes written are the ones
    ``path.write_text(text, encoding="utf-8")`` writes, platform newline
    translation included.

    Refused with :class:`SymlinkWriteRefused`, before anything is truncated:

    - a ``path`` whose folder resolves outside ``root`` (a symlinked folder on
      the way down), or a folder below ``root`` swapped for a symlink while
      this runs (see :func:`_open_no_follow`);
    - a ``path`` that is a symlink, including one swapped in while this runs;
      a dangling one does not get its target created, because a new file is
      created exclusively;
    - a ``path`` that has another hard link: the same file is reachable under
      a second name, possibly outside the folder;
    - a ``path`` that is not a regular file, such as a named pipe.

    Residual where ``os.open`` takes no ``dir_fd`` (Windows): the folder check
    and the open are two steps, and the open is by path, so a folder below
    ``root`` swapped for a symlink or junction between them is still followed.
    """
    with os.fdopen(_open_for_write(root, path), "w", encoding="utf-8") as handle:
        handle.write(text)


def create_new_inside(root: Path, path: Path) -> int:
    """Create ``path`` under ``root`` as a new, empty file and return its open
    descriptor, in one step that never follows a link and never opens a file
    already there: a name that is taken (including by a dangling link) fails
    with ``FileExistsError`` and the existing entry is left as it was.

    The folder checks are those of :func:`write_text_inside`. The caller closes
    the descriptor.
    """
    return _open_no_follow(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, SymlinkWriteRefused, "write", root
    )


def _open_for_write(root: Path, path: Path) -> int:
    """The emptied descriptor :func:`write_text_inside` writes to."""
    if os.path.islink(path):
        raise SymlinkWriteRefused(f"refusing to write through a symlink: {path}")
    try:
        fd = _open_no_follow(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, SymlinkWriteRefused, "write", root
        )
    except FileExistsError:
        fd = _open_no_follow(path, os.O_WRONLY, SymlinkWriteRefused, "write", root)
    try:
        if os.fstat(fd).st_nlink != 1:
            raise SymlinkWriteRefused(
                f"refusing to write to {path}: it has another hard link, so the "
                "write would change a file under a second name"
            )
        os.ftruncate(fd, 0)
    except BaseException:
        os.close(fd)
        raise
    return fd


def copy_file_inside(source_root: Path, source: Path, target_root: Path, target: Path) -> None:
    """Copy ``source`` to ``target`` as ``shutil.copy2`` does (the bytes, the
    permission bits, the access and modification times), reading as
    :func:`read_bytes_no_follow` reads under ``source_root`` and writing as
    :func:`write_text_inside` writes under ``target_root``.

    Extended attributes and file flags are not copied. Where ``os.chmod`` or
    ``os.utime`` takes no descriptor (Windows), the mode and times are set by
    path, with the residual stated in :func:`write_text_inside`.
    """
    in_fd = _open_no_follow(source, os.O_RDONLY, LinkReadRefused, "read", source_root)
    with os.fdopen(in_fd, "rb") as reader:
        data = reader.read()
        made = os.fstat(in_fd)
    with os.fdopen(_open_for_write(target_root, target), "wb") as writer:
        writer.write(data)
        writer.flush()
        out_fd = writer.fileno()
        os.chmod(out_fd if os.chmod in os.supports_fd else target, stat.S_IMODE(made.st_mode))
        os.utime(
            out_fd if os.utime in os.supports_fd else target,
            ns=(made.st_atime_ns, made.st_mtime_ns),
        )


def read_bytes_no_follow(
    path: Path, limit: Optional[int] = None, root: Optional[Path] = None
) -> bytes:
    """Read ``path`` (its first ``limit`` bytes when given) without following
    a symlink, including one swapped in after an earlier check.

    Raises :class:`LinkReadRefused` for a symlink or anything that is not a
    regular file. A hard link is read: it is the file itself, and reading
    cannot damage the other name.

    Give ``root`` for a file found by walking a memory folder: the read is
    then held inside ``root`` as :func:`write_text_inside` holds a write,
    with the same residual where ``os.open`` takes no ``dir_fd``. Without it
    (a file the operator names), a folder on the way is followed.
    """
    fd = _open_no_follow(path, os.O_RDONLY, LinkReadRefused, "read", root)
    with os.fdopen(fd, "rb") as handle:
        return handle.read(-1 if limit is None else limit)


def read_text_no_follow(path: Path, root: Optional[Path] = None) -> str:
    """:func:`read_bytes_no_follow`, decoded exactly as
    ``path.read_text(encoding="utf-8")`` decodes (universal newlines; raises
    UnicodeDecodeError on invalid UTF-8)."""
    data = io.BytesIO(read_bytes_no_follow(path, root=root))
    return io.TextIOWrapper(data, encoding="utf-8").read()

# memory-audit-log keeps a hash-chain audit log for memory folders (Phase 1 T6.3).
#
# ADR: 0001 §Phase 1 T6 (Audit trails sub-deliverable)
#
# Append-only JSONL log per memory folder. Each record:
#   {schema, seq, ts, operator, op, file, before_sha256, after_sha256,
#    prev_chain_sha256, chain_sha256, meta}
#
# chain_sha256 = sha256( prev_chain_sha256 || canonical-json(record-without-
# chain_sha256) ). Record N+1 carries record N's chain_sha256 as its
# prev_chain_sha256, forming a hash chain. Verification re-walks the chain;
# exit 1 on a line that cannot be read, a hash mismatch or a broken link.
#
# What the chain does and does not detect. The chain has no key and no anchor
# outside the log file. `verify` detects accidental corruption and an edit that
# does not recompute the chain. It does not detect a full rewrite or a
# truncation by someone who can write the file: they can recompute every hash,
# or drop records from the end (or the whole file), and `verify` still passes.
# Do not treat a passing `verify` as proof that the log is complete or original.
#
# Operations:
#   append   --op X --file F  Write a new record. Computes file SHA-256
#                              before/after where applicable; chains to last
#                              record.
#   verify                     Re-walk the chain. Exit 1 on a break.
#   tail     [--n N]           Show last N records.
#   export   [--format jsonl|json|cef]  Export for SIEM forwarding.
#
# Pure stdlib (hashlib + json + argparse).

from __future__ import annotations

import argparse
import contextlib
import errno
import getpass
import hashlib
import json
import os
import socket
import stat
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from memforge._terminal import terminal_safe, visible
from memforge.discovery import access_error


LOG_FILENAME = ".memforge-audit-log.jsonl"
LOCK_FILENAME = ".memforge-audit-log.lock"
SCHEMA = "memforge-audit-log/v1"

CHAIN_LIMITS = (
    "The chain detects accidental corruption and an edit that does not "
    "recompute the chain. It does not detect a full rewrite or a truncation "
    "by someone who can write the log file."
)


def _lock_windows(lock_file) -> None:
    """Take the exclusive lock, waiting as long as it takes as flock does.

    msvcrt.LK_LOCK polls once a second and gives up after ten tries, which
    let a waiting append go on without the lock and fork the chain. Polling
    for a free lock here never gives up while another process holds it.
    """
    import msvcrt

    lock_file.seek(0)
    while True:
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EDEADLK):
                raise
            time.sleep(0.01)


@contextlib.contextmanager
def _append_lock(folder: Path):
    """Exclusive advisory lock around the audit-log read-modify-append window.

    append_record reads the tail (to derive seq + prev_chain), computes the new
    record, then opens the log "a" and writes. Without a lock, two agents
    appending simultaneously both read the same tail, both compute
    seq = last_seq + 1, and both write -- forking the hash chain into two
    records with identical seq and prev_chain_sha256 (closes recall-02). This
    serializes the critical section on a per-folder `.lock` sidecar.

    fcntl.flock on POSIX; msvcrt.locking on Windows. If neither is available
    (or the lock cannot be taken), the manager degrades to a no-op so the
    append still proceeds -- the lock is a concurrency safeguard, not a
    correctness gate (verify_chain still detects any resulting fork).
    """
    folder.mkdir(parents=True, exist_ok=True)
    lock_path = folder / LOCK_FILENAME
    lock_file = None
    locked = False
    try:
        lock_file = os.fdopen(_open_for_append(lock_path, os.O_RDWR | os.O_CREAT), "a+")
        try:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            locked = True
        except ImportError:  # pragma: no cover - non-POSIX
            try:
                _lock_windows(lock_file)
                locked = True
            except Exception:  # pragma: no cover - best-effort on Windows
                locked = False
        except OSError:  # pragma: no cover - lock unsupported on this FS
            locked = False
        yield
    finally:
        if lock_file is not None:
            if locked:
                with contextlib.suppress(Exception):
                    try:
                        import fcntl

                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    except ImportError:  # pragma: no cover - non-POSIX
                        import msvcrt

                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            with contextlib.suppress(Exception):
                lock_file.close()


class AuditLogError(Exception):
    """Raised when the audit log cannot be safely appended to.

    The hash chain is only meaningful if every append links to the true
    last record. When the final log line is corrupt, torn, or schema-
    incomplete, we MUST refuse to append rather than silently re-anchor a new
    chain (seq=1, empty prev) on top of an existing log (closes auditlog-01,
    auditlog-02).
    """


def _is_symlink(path: Path) -> bool:
    """True for any symbolic link, including one whose target is missing.
    `exists()` follows links, so it cannot answer this."""
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False
    except OSError as e:
        raise AuditLogError(f"cannot check {path}: {e}") from e


def _refuse_symlink(path: Path) -> None:
    if _is_symlink(path):
        raise AuditLogError(
            f"{path} is a symbolic link. The audit log and its lock must be "
            "regular files inside the memory folder."
        )


# Flags missing on a platform are 0.
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)


def _open_log(log_path: Path, flags: int) -> int:
    """Open the log without following a link and return the descriptor.

    The log lives in a folder other people can write to, so the path can be
    swapped for a link between any check and the open. The decision is
    therefore made on the opened descriptor: it must be a regular file and the
    same file the path names now, and the path must not be a link. That holds
    on platforms without O_NOFOLLOW too. Raises FileNotFoundError when there
    is no log and AuditLogError for anything that is not the log itself.

    The open never waits: O_NONBLOCK keeps a named pipe from blocking it, and
    the descriptor is set back to blocking once it is known to be a regular
    file, so reads and writes behave as without the flag.

    With O_CREAT in ``flags`` a new file is created exclusively, and an
    existing name is opened without O_CREAT. Without O_NOFOLLOW a plain
    O_CREAT open of a link swapped in after the check would create the link's
    missing target outside the folder before the descriptor check refuses it;
    an exclusive create fails on any existing name, a dangling link included.
    """
    _refuse_symlink(log_path)
    flags |= _O_NOFOLLOW | _O_NONBLOCK
    try:
        if flags & os.O_CREAT:
            try:
                fd = os.open(log_path, flags | os.O_EXCL, 0o666)
            except FileExistsError:
                fd = os.open(log_path, flags & ~os.O_CREAT)
        else:
            fd = os.open(log_path, flags, 0o666)
    except FileNotFoundError:
        raise
    except OSError as e:
        # A write-only, non-blocking open of a pipe with no reader fails ENXIO.
        if e.errno == errno.ENXIO:
            raise _not_a_regular_file(log_path) from None
        raise AuditLogError(f"cannot open {log_path}: {e}") from e
    try:
        opened = os.fstat(fd)
        named = os.lstat(log_path)
    except OSError as e:
        os.close(fd)
        raise AuditLogError(f"cannot check {log_path}: {e}") from e
    if stat.S_ISLNK(named.st_mode) or (opened.st_dev, opened.st_ino) != (
        named.st_dev, named.st_ino
    ):
        os.close(fd)
        raise AuditLogError(
            f"{log_path} is not a regular file inside the memory folder "
            "(it is a symbolic link, or was replaced while being opened)."
        )
    if not stat.S_ISREG(opened.st_mode):
        os.close(fd)
        raise _not_a_regular_file(log_path)
    if _O_NONBLOCK:
        os.set_blocking(fd, True)
    return fd


def _not_a_regular_file(path: Path) -> AuditLogError:
    return AuditLogError(
        f"{path} is not a regular file (for example a named pipe or a folder). "
        "The audit log and its lock must be regular files inside the memory folder."
    )


def _open_for_append(path: Path, flags: int) -> int:
    """Open the log or its lock for writing, as :func:`_open_log` does.

    A file with a second hard link is refused before anything is written: the
    same file is reachable under another name, possibly outside the folder.
    """
    try:
        fd = _open_log(path, flags)
    except (AuditLogError, FileNotFoundError) as e:
        raise AuditLogError(f"refusing to append: {e}") from e
    if os.fstat(fd).st_nlink != 1:
        os.close(fd)
        raise AuditLogError(
            f"refusing to append to {path}: it has another hard link, so the "
            "write would change a file under a second name"
        )
    return fd


def file_sha256(path: Path) -> Optional[str]:
    """Hash of the regular file at `path`, or None when there is none.

    A link is followed: the operator names this file. The path can be swapped
    for a named pipe at any time, so the open never waits and the decision is
    made on the opened descriptor.
    """
    try:
        fd = os.open(path, os.O_RDONLY | _O_BINARY | _O_NONBLOCK)
    except OSError:
        return None
    h = hashlib.sha256()
    with os.fdopen(fd, "rb") as f:
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            if _O_NONBLOCK:
                os.set_blocking(fd, True)
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        except OSError:
            return None
    return h.hexdigest()


def canonical_json(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_chain_hash(prev: str, record_without_chain: dict) -> str:
    body = canonical_json(record_without_chain)
    h = hashlib.sha256()
    h.update((prev or "").encode("utf-8"))
    h.update(b"\x00")
    h.update(body.encode("utf-8"))
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _read_log_lines(folder: Path) -> tuple[list[dict], list[str]]:
    """Return (records, problems). A problem names a line that is not a JSON
    object; such a line is never part of the returned records. Raises
    AuditLogError when the log is a link or cannot be opened."""
    log_path = folder / LOG_FILENAME
    try:
        fd = _open_log(log_path, os.O_RDONLY)
    except FileNotFoundError:
        return [], []
    records: list[dict] = []
    problems: list[str] = []
    # Undecodable bytes are replaced, not raised: the damaged line then fails
    # its hash check and the rest of the log is still read.
    with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                # Append never writes one, so a blank line was put there.
                problems.append(f"line {line_no}: blank line")
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                problems.append(f"line {line_no}: not valid JSON ({e})")
                continue
            if not isinstance(rec, dict):
                problems.append(f"line {line_no}: not a JSON object")
                continue
            records.append(rec)
    return records, problems


def read_log(folder: Path) -> list[dict]:
    """Full-file read. Used by `tail` and `export`. Append paths use
    `tail_record` instead so cost stays O(1) regardless of log size. Lines
    that are not records are skipped with a warning; `verify` fails on them.
    """
    records, problems = _read_log_lines(folder)
    for problem in problems:
        sys.stderr.write(f"warning: malformed log {visible(problem)}\n")
    return records


def tail_record(folder: Path) -> Optional[dict]:
    """Read only the last record from the JSONL log without loading the
    whole file. Used by append_record to obtain seq + prev_chain_sha256
    in O(1) regardless of total log size.

    Strategy: seek to EOF, read backwards in 8KB chunks until a newline
    delimiter for the final record is found, parse that line only.
    Returns None if the log is missing or empty. Raises AuditLogError when
    the log is a link or not a regular file (see :func:`_open_log`).
    """
    log_path = folder / LOG_FILENAME
    try:
        fd = _open_log(log_path, os.O_RDONLY | _O_BINARY)
    except FileNotFoundError:
        return None

    chunk = 8192
    with os.fdopen(fd, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size == 0:
            return None

        buf = b""
        pos = size
        while pos > 0:
            read_size = min(chunk, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size) + buf
            stripped = buf.rstrip(b"\n")
            nl = stripped.rfind(b"\n")
            if nl != -1:
                last_line = stripped[nl + 1:]
                try:
                    return json.loads(last_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return None

        # Single-line file (no inner newline; whole file is one record).
        try:
            return json.loads(buf.rstrip(b"\n").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None


def _log_has_content(log_path: Path) -> bool:
    """True if the log file exists and holds at least one non-whitespace byte.

    Used to distinguish a genuinely empty/missing log (safe to anchor a fresh
    chain at seq=1) from a non-empty log whose tail could not be read (must
    fail closed rather than silently re-anchor).
    """
    try:
        with os.fdopen(_open_log(log_path, os.O_RDONLY | _O_BINARY), "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                if chunk.strip():
                    return True
    except FileNotFoundError:
        return False
    except (AuditLogError, OSError):
        # Cannot read the file at all: treat as content-present and fail closed
        # upstream rather than risk re-anchoring over an unreadable log.
        return True
    return False


def append_record(
    folder: Path,
    op: str,
    file: Optional[Path],
    before_sha256: Optional[str],
    after_sha256: Optional[str],
    operator: Optional[str],
    meta: Optional[dict],
) -> dict:
    log_path = folder / LOG_FILENAME
    # The log lives in a folder other people can write to. Following a link
    # here would create or extend a file somewhere else on disk.
    for path in (log_path, folder / LOCK_FILENAME):
        try:
            _refuse_symlink(path)
        except AuditLogError as e:
            raise AuditLogError(f"refusing to append: {e}") from e
    with _append_lock(folder):
        return _append_record_locked(folder, log_path, op, file, before_sha256,
                                     after_sha256, operator, meta)


def _append_record_locked(
    folder: Path,
    log_path: Path,
    op: str,
    file: Optional[Path],
    before_sha256: Optional[str],
    after_sha256: Optional[str],
    operator: Optional[str],
    meta: Optional[dict],
) -> dict:
    try:
        last = tail_record(folder)
    except AuditLogError as e:
        raise AuditLogError(f"refusing to append: {e}") from e
    if last is None:
        # tail_record returns None for BOTH an empty/missing log AND an
        # unreadable (corrupt/torn) final line. Only the former is safe to
        # anchor at seq=1; the latter must fail closed so we never re-anchor a
        # new chain on top of an existing log (auditlog-01).
        if _log_has_content(log_path):
            raise AuditLogError(
                f"refusing to append: the last record in {log_path} is "
                "unreadable (corrupt or torn final line). The hash chain cannot "
                "be extended safely. Inspect/repair the tail before appending."
            )
        seq = 1
        prev_chain = ""
    else:
        # Schema-incomplete-but-parseable final line: defensive .get() (every
        # other reader in this module uses .get()). A missing seq/chain_sha256
        # is the same fail-closed case as a corrupt tail (auditlog-02).
        last_seq = last.get("seq")
        last_chain = last.get("chain_sha256")
        if not isinstance(last_seq, int) or not isinstance(last_chain, str) or not last_chain:
            raise AuditLogError(
                f"refusing to append: the last record in {log_path} is missing "
                "or has a malformed 'seq'/'chain_sha256' field. The hash chain "
                "cannot be extended safely. Inspect/repair the tail before "
                "appending."
            )
        seq = last_seq + 1
        prev_chain = last_chain

    record = {
        "schema": SCHEMA,
        "seq": seq,
        "ts": now_iso(),
        "operator": operator or _default_operator(),
        "op": op,
        "file": str(file) if file else None,
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "prev_chain_sha256": prev_chain,
        "meta": meta or {},
    }
    record["chain_sha256"] = compute_chain_hash(prev_chain, record)

    log_path.parent.mkdir(exist_ok=True)
    fd = _open_for_append(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(canonical_json(record) + "\n")
    return record


def _default_operator() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USER", "unknown")
    host = socket.gethostname() or "unknown-host"
    return f"{user}@{host}"


def verify_chain(folder: Path) -> tuple[bool, list[str]]:
    """Returns (ok, errors). Errors is empty when ok=True.

    A passing result means the records present are self-consistent. See
    CHAIN_LIMITS for what that does not cover.
    """
    try:
        records, errors = _read_log_lines(folder)
    except AuditLogError as e:
        return False, [str(e)]
    if not records:
        return (not errors), errors

    expected_prev = ""
    expected_seq = 1
    for rec in records:
        seq = rec.get("seq")
        if seq != expected_seq:
            errors.append(f"seq {seq}: expected {expected_seq}")
        prev_chain = rec.get("prev_chain_sha256", "")
        if prev_chain != expected_prev:
            errors.append(f"seq {seq}: prev_chain mismatch (want {expected_prev or '<empty>'}, got {prev_chain or '<empty>'})")
        recorded_chain = rec.get("chain_sha256", "")
        body = {k: v for k, v in rec.items() if k != "chain_sha256"}
        # A field of the wrong type cannot match a computed hash; str() keeps
        # the walk going so every bad record is reported.
        recomputed = compute_chain_hash(str(prev_chain or ""), body)
        if recorded_chain != recomputed:
            errors.append(f"seq {seq}: chain hash mismatch (want {str(recorded_chain)[:16]}..., got {recomputed[:16]}...)")
        expected_prev = recorded_chain
        expected_seq = seq + 1 if isinstance(seq, int) else expected_seq + 1
    return (not errors), errors


def cmd_append(args) -> int:
    folder = Path(args.path).resolve()
    if not os.path.exists(folder) and access_error(folder) is None:
        sys.stderr.write(f"error: folder not found: {folder}\n")
        return 2
    file_path = Path(args.file).resolve() if args.file else None

    before = args.before_sha256
    after = args.after_sha256

    if args.compute_before and file_path:
        before = file_sha256(file_path)
    if args.compute_after and file_path:
        after = file_sha256(file_path)

    meta = {}
    if args.meta:
        try:
            meta = json.loads(args.meta)
            if not isinstance(meta, dict):
                raise ValueError("meta must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            sys.stderr.write(f"error: invalid --meta JSON: {e}\n")
            return 2

    try:
        rec = append_record(
            folder,
            op=args.op,
            file=file_path,
            before_sha256=before,
            after_sha256=after,
            operator=args.operator,
            meta=meta,
        )
    except AuditLogError as e:
        sys.stderr.write(f"error: {e}\n")
        return 1
    print(f"WROTE seq={rec['seq']} chain={rec['chain_sha256'][:16]}...")
    return 0


def cmd_verify(args) -> int:
    folders = [Path(p).resolve() for p in args.path] if args.path else default_paths()
    if not folders:
        sys.stderr.write("error: no folders specified and no defaults found\n")
        return 2
    rc = 0
    for folder in folders:
        if not os.path.exists(folder) and access_error(folder) is None:
            sys.stderr.write(f"warning: skipping nonexistent {folder}\n")
            continue
        ok, errors = verify_chain(folder)
        try:
            n = len(_read_log_lines(folder)[0])
        except AuditLogError:
            n = 0
        if ok:
            print(f"OK    {visible(folder)} ({n} records)")
        else:
            print(f"TAMPER {visible(folder)} ({n} records, {len(errors)} errors):")
            for e in errors:
                print(f"  - {visible(e)}")
            rc = 1
    print(f"note: {CHAIN_LIMITS} OK means the records present are self-consistent.")
    return rc


def cmd_tail(args) -> int:
    folder = Path(args.path).resolve()
    try:
        records = read_log(folder)
    except AuditLogError as e:
        sys.stderr.write(f"error: {e}\n")
        return 1
    if not records:
        print(f"(no records at {folder / LOG_FILENAME})")
        return 0
    n = args.n
    tail = records[-n:]
    # The log may hold records this tool did not write: print what is there.
    for rec in tail:
        print(
            f"seq={str(rec.get('seq', '-')):>5s} {rec.get('ts', '-')}  "
            f"{str(rec.get('operator', '-')):30s}  "
            f"{str(rec.get('op', '-')):14s}  {rec.get('file', '') or '-'}"
        )
    return 0


def cmd_export(args) -> int:
    folder = Path(args.path).resolve()
    try:
        records = read_log(folder)
    except AuditLogError as e:
        sys.stderr.write(f"error: {e}\n")
        return 1
    fmt = args.format
    if fmt == "jsonl":
        for rec in records:
            sys.stdout.write(canonical_json(rec) + "\n")
    elif fmt == "json":
        sys.stdout.write(json.dumps(records, indent=2) + "\n")
    elif fmt == "cef":
        for rec in records:
            sys.stdout.write(_format_cef(rec) + "\n")
    return 0


def _product_version() -> str:
    """Single-source the product version for the CEF device-version field.

    A SIEM keys on the CEF device-version; a hardcoded literal mislabels every
    exported event and never tracks version bumps (closes auditdeep-05). Source
    from the package __version__ (itself single-sourced from importlib
    metadata), with a literal fallback for an uninstalled source tree.
    """
    try:
        from memforge import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - defensive
        return "0.0.0"


def _cef_header(value: Any) -> str:
    """Escape a CEF header field: backslash and pipe. A header cannot hold a
    line break, so one becomes a space."""
    text = str(value).replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("\r", " ").replace("\n", " ")


def _cef_extension(value: Any) -> str:
    """Escape a CEF extension value: backslash, equals and line breaks."""
    text = str(value).replace("\\", "\\\\").replace("=", "\\=")
    return text.replace("\r", "\\r").replace("\n", "\\n")


def _format_cef(rec: dict) -> str:
    # Record values come from the log file, which other people can write, so
    # each one is escaped: an unescaped line break or delimiter would let a
    # record forge a second event or extra fields in the SIEM.
    ts = rec.get("ts", "")
    operator = rec.get("operator", "-")
    op = rec.get("op", "-")
    file = rec.get("file", "-") or "-"
    seq = rec.get("seq", 0)
    chain = rec.get("chain_sha256", "")
    return (
        f"CEF:0|ILDAN|MemForge|{_product_version()}|"
        f"{_cef_header(op)}|memforge-audit|3|"
        f"rt={_cef_extension(ts)} suser={_cef_extension(operator)} "
        f"fname={_cef_extension(file)} cs1={_cef_extension(chain)} "
        f"cs1Label=chainHash externalId={_cef_extension(seq)}"
    )


def default_paths() -> list[Path]:
    """Default memory folders via the centralized, IDE/OS-neutral resolver
    (existence-filtered; one that cannot be looked at is kept, so it is
    reported)."""
    from memforge.paths import default_memory_paths

    return [p for p in default_memory_paths() if os.path.exists(p) or access_error(p)]


@terminal_safe
def main() -> int:
    p = argparse.ArgumentParser(
        prog="memory-audit-log",
        description=f"Hash-chain audit log for memory folders (Phase 1 T6.3). {CHAIN_LIMITS}",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("append", help="Append a new audit record")
    pa.add_argument("--path", required=True, help="Memory folder")
    pa.add_argument("--op", required=True, help="Operation label (e.g., write, edit, move, status_change, rollup_create, rollup_undo, generator_run)")
    pa.add_argument("--file", default=None, help="Subject file path (relative to folder or absolute)")
    pa.add_argument("--before-sha256", default=None)
    pa.add_argument("--after-sha256", default=None)
    pa.add_argument("--compute-before", action="store_true", help="Compute SHA-256 of --file as before-state")
    pa.add_argument("--compute-after", action="store_true", help="Compute SHA-256 of --file as after-state")
    pa.add_argument("--operator", default=None, help="Override operator field (default: $USER@hostname)")
    pa.add_argument("--meta", default=None, help="Optional JSON object string for extra metadata")
    pa.set_defaults(func=cmd_append)

    pv = sub.add_parser(
        "verify",
        help="Re-walk hash chain; exit 1 on a break",
        description=f"Re-walk the hash chain; exit 1 on a break. {CHAIN_LIMITS}",
    )
    pv.add_argument("--path", action="append", default=[], help="Folder (repeatable)")
    pv.set_defaults(func=cmd_verify)

    pt = sub.add_parser("tail", help="Show last N records")
    pt.add_argument("--path", required=True)
    pt.add_argument("--n", type=int, default=20)
    pt.set_defaults(func=cmd_tail)

    pe = sub.add_parser("export", help="Export records for SIEM forwarding")
    pe.add_argument("--path", required=True)
    pe.add_argument("--format", choices=("jsonl", "json", "cef"), default="jsonl")
    pe.set_defaults(func=cmd_export)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

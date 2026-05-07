# memory-audit-log — tamper-evident hash-chain audit log for memory folders
# (Phase 1 T6.3).
#
# ADR: 0001 §Phase 1 T6 (Audit trails sub-deliverable)
#
# Append-only JSONL log per memory folder. Each record:
#   {schema, seq, ts, operator, op, file, before_sha256, after_sha256,
#    prev_chain_sha256, chain_sha256, meta}
#
# chain_sha256 = sha256( prev_chain_sha256 || canonical-json(record-without-
# chain_sha256) ). Record N+1 carries record N's chain_sha256 as its
# prev_chain_sha256, forming an append-only Merkle chain. Verification re-
# walks the chain; exit 1 on any tamper or missing prev linkage.
#
# Operations:
#   append   --op X --file F  Write a new record. Computes file SHA-256
#                              before/after where applicable; chains to last
#                              record.
#   verify                     Re-walk the chain. Exit 1 on tamper.
#   tail     [--n N]           Show last N records.
#   export   [--format jsonl|json|cef]  Export for SIEM forwarding.
#
# Pure stdlib (hashlib + json + argparse).

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import socket
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


LOG_FILENAME = ".memforge-audit-log.jsonl"
SCHEMA = "memforge-audit-log/v1"


def file_sha256(path: Path) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
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


def read_log(folder: Path) -> list[dict]:
    """Full-file read. Used by `verify` (must walk every record) and `tail`
    when explicit count is requested. Append paths use `tail_record` instead
    so cost stays O(1) regardless of log size.
    """
    log_path = folder / LOG_FILENAME
    if not log_path.exists():
        return []
    out: list[dict] = []
    with log_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                sys.stderr.write(f"warning: malformed log line {line_no}: {e}\n")
                continue
            out.append(rec)
    return out


def tail_record(folder: Path) -> Optional[dict]:
    """Read only the last record from the JSONL log without loading the
    whole file. Used by append_record to obtain seq + prev_chain_sha256
    in O(1) regardless of total log size.

    Strategy: seek to EOF, read backwards in 8KB chunks until a newline
    delimiter for the final record is found, parse that line only.
    Returns None if the log is missing or empty.
    """
    log_path = folder / LOG_FILENAME
    if not log_path.exists():
        return None

    chunk = 8192
    with log_path.open("rb") as f:
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
    last = tail_record(folder)
    seq = (last["seq"] + 1) if last else 1
    prev_chain = last["chain_sha256"] if last else ""

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
    with log_path.open("a", encoding="utf-8") as f:
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
    """Returns (ok, errors). Errors is empty when ok=True."""
    records = read_log(folder)
    errors: list[str] = []
    if not records:
        return True, errors

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
        recomputed = compute_chain_hash(prev_chain, body)
        if recorded_chain != recomputed:
            errors.append(f"seq {seq}: chain hash mismatch (want {recorded_chain[:16]}..., got {recomputed[:16]}...)")
        expected_prev = recorded_chain
        expected_seq = seq + 1
    return (not errors), errors


def cmd_append(args) -> int:
    folder = Path(args.path).resolve()
    if not folder.exists():
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

    rec = append_record(
        folder,
        op=args.op,
        file=file_path,
        before_sha256=before,
        after_sha256=after,
        operator=args.operator,
        meta=meta,
    )
    print(f"WROTE seq={rec['seq']} chain={rec['chain_sha256'][:16]}...")
    return 0


def cmd_verify(args) -> int:
    folders = [Path(p).resolve() for p in args.path] if args.path else default_paths()
    if not folders:
        sys.stderr.write("error: no folders specified and no defaults found\n")
        return 2
    rc = 0
    for folder in folders:
        if not folder.exists():
            sys.stderr.write(f"warning: skipping nonexistent {folder}\n")
            continue
        ok, errors = verify_chain(folder)
        if ok:
            n = len(read_log(folder))
            print(f"OK    {folder} ({n} records)")
        else:
            n = len(read_log(folder))
            print(f"TAMPER {folder} ({n} records, {len(errors)} errors):")
            for e in errors:
                print(f"  - {e}")
            rc = 1
    return rc


def cmd_tail(args) -> int:
    folder = Path(args.path).resolve()
    records = read_log(folder)
    if not records:
        print(f"(no records at {folder / LOG_FILENAME})")
        return 0
    n = args.n
    tail = records[-n:]
    for rec in tail:
        print(
            f"seq={rec['seq']:5d} {rec['ts']}  {rec['operator']:30s}  "
            f"{rec['op']:14s}  {rec.get('file', '') or '-'}"
        )
    return 0


def cmd_export(args) -> int:
    folder = Path(args.path).resolve()
    records = read_log(folder)
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


def _format_cef(rec: dict) -> str:
    ts = rec.get("ts", "")
    operator = rec.get("operator", "-")
    op = rec.get("op", "-")
    file = rec.get("file", "-") or "-"
    seq = rec.get("seq", 0)
    chain = rec.get("chain_sha256", "")
    return (
        f"CEF:0|ILDAN|MemForge|0.3.0|{op}|memforge-audit|3|"
        f"rt={ts} suser={operator} fname={file} cs1={chain} "
        f"cs1Label=chainHash externalId={seq}"
    )


def default_paths() -> list[Path]:
    out: list[Path] = []
    home = Path.home()
    user = os.environ.get("USER", "")
    if user:
        per_cwd = home / ".claude" / "projects" / f"{user}-claude-projects" / "memory"
        if per_cwd.exists():
            out.append(per_cwd)
    glob = home / ".claude" / "global-memory"
    if glob.exists():
        out.append(glob)
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        prog="memory-audit-log",
        description="Tamper-evident hash-chain audit log for memory folders (Phase 1 T6.3).",
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

    pv = sub.add_parser("verify", help="Re-walk hash chain; exit 1 on tamper")
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

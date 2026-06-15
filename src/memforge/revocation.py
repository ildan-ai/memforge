"""Revocation event builder + revocation-set walker.

Spec ref: §"Key lifecycle + revocation (v0.5.0+)" and integrity invariant 22.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

from memforge import crypto, registry as registry_mod
from memforge.identity import generate_uuidv7, now_iso


REVOKE_PREFIX = "memforge: revoke "
SNAPSHOT_PREFIX = "memforge: revocation-snapshot "
FRESH_START_PREFIX = "memforge: fresh-start "
KEY_COMPROMISE_PREFIX = "memforge: key-compromise "
REGISTRY_PREFIX = "memforge: operator-registry"

# Per-memory-root config lives at <memory_root>/.memforge/config.yaml; the
# revocation walk caps are read from its `revocation.walk_max_commits` /
# `revocation.walk_max_bytes` keys (the same file the registry reads
# `identity.rotation_cooldown_hours` from).
CONFIG_DIRNAME = ".memforge"
CONFIG_FILENAME = "config.yaml"


# Walk-cap defaults. Operators with large histories can raise via
# `.memforge/config.yaml`: revocation.walk_max_commits / walk_max_bytes.
# When either cap is exceeded, walk_revocation_set fails closed with an
# explicit message pointing the operator at `memforge revocation-snapshot`
# to compress history.
DEFAULT_WALK_MAX_COMMITS = 100_000
DEFAULT_WALK_MAX_BYTES = 100 * 1024 * 1024  # 100 MB

# Commit hash format check used by walk_revocation_set. SHA-1 is 40 hex
# chars; git log --format=%H emits exactly this. Anything else means a
# git format / version surprise; fail closed.
_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")


class RevocationError(Exception):
    """Fail-closed revocation-layer error."""


def build_revoke_body(
    *,
    key_id: str,
    reason: str,
    revoked_by_uuid: str,
    signer_fingerprint: str,
    revoked_at: Optional[str] = None,
) -> tuple[str, dict]:
    """Build a signed revocation commit body. Returns (commit_message, body_dict).

    The commit message starts with `memforge: revoke <key_id>` (invariant 22).
    The body is a YAML mapping with `revocation_signature` covering the
    canonical envelope of the rest of the body.
    """
    if len(reason) < 8:
        raise RevocationError(
            f"revocation reason must be >= 8 characters; got {len(reason)} chars"
        )
    body = {
        "key_id": key_id,
        "revoked_at": revoked_at or now_iso(),
        "reason": reason,
        "revoked_by": revoked_by_uuid,
        "revocation_uid": generate_uuidv7(),
    }
    envelope = crypto.canonical_envelope(body)
    body["revocation_signature"] = crypto.gpg_sign_detached(envelope, fingerprint=signer_fingerprint)
    commit_message = f"{REVOKE_PREFIX}{key_id}\n\n{yaml.safe_dump(body, sort_keys=False)}"
    return commit_message, body


def verify_revoke_body(body: dict, *, expected_signer_fingerprint: str) -> bool:
    """Verify a revoke commit body's signature against the expected signer fingerprint.

    Returns True if the signature is valid. False otherwise (no raise).
    """
    sig = body.get("revocation_signature")
    if not sig:
        return False
    payload = {k: v for k, v in body.items() if k != "revocation_signature"}
    envelope = crypto.canonical_envelope(payload)
    return crypto.gpg_verify_detached(envelope, signature_b64=sig, expected_fingerprint=expected_signer_fingerprint)


def _key_was_active_at(key: dict, signer_op: dict, author_date_iso: str) -> bool:
    """Return True if `key` was the signer's ACTIVE signing key as of `author_date_iso`.

    revoke-signer-01: SPEC.md §"Signing-key-matches-revoked_by check (MUST)"
    requires the revocation be signed by the operator's CURRENTLY ACTIVE key.
    The deliberate carve-out is that an OLD legitimate revocation (signed while
    the key WAS active, then later rotated out) must still validate. We
    reconcile both by gating on the revoke commit's git author-date:

      - A currently-`active` key is always acceptable.
      - A `superseded` key is acceptable ONLY if it was still active when the
        revocation was AUTHORED, i.e. no successor key under the same operator
        was introduced at or before the revoke commit's author-date. The
        successor's `introduced_at` (/ `rotated_at`) is the supersession moment;
        a revocation authored before then was signed by the then-active key. A
        revocation authored AFTER the key was superseded (the attack: signing a
        NEW revocation with a now-superseded compromised key) is rejected.

    Fail closed: an unparseable / missing author-date, or an unparseable
    successor `introduced_at`, means we cannot establish the key was active at
    authoring time, so a superseded key is NOT accepted.
    """
    if key.get("status", "active") == "active":
        return True
    # Superseded key: require the revocation to have been authored before the
    # key was superseded. Establish the supersession boundary as the earliest
    # introduction time of any OTHER key under this operator that is newer than
    # this key (higher chain_index, or any fresh-start/rotated successor).
    author = _parse_iso(author_date_iso)
    if author is None:
        return False
    this_index = key.get("chain_index", 0)
    this_fpr = key.get("key_id")
    successor_times: list[datetime] = []
    for sibling in signer_op.get("public_keys", []):
        if sibling.get("key_id") == this_fpr:
            continue
        # A successor is any sibling introduced as a later/replacement key.
        if sibling.get("chain_index", 0) <= this_index and not sibling.get("fresh_start"):
            continue
        intro = _parse_iso(sibling.get("introduced_at", "") or sibling.get("rotated_at", ""))
        if intro is None:
            # Cannot establish when this successor took over: fail closed.
            return False
        successor_times.append(intro)
    if not successor_times:
        # Key is superseded but no successor introduction time is resolvable:
        # cannot prove it was active at authoring time. Fail closed.
        return False
    superseded_at = min(successor_times)
    return author < superseded_at


def verify_revoke_body_against_registry(body: dict, registry: dict) -> bool:
    """Verify a walked revoke body's signature against the operator-registry.

    Resolves the `revoked_by` operator from `registry` and attempts to verify
    the `revocation_signature` against each of that operator's listed public
    keys, using the REGISTERED public_material as the trust root (ephemeral
    keyring) when present rather than the ambient keyring.

    revoke-signer-01: a key only validates the revocation if it was the
    operator's ACTIVE signing key as of the revoke commit's git author-date
    (`_key_was_active_at`). A currently-active key always qualifies; a
    superseded key qualifies only for revocations AUTHORED before it was
    superseded (so old legitimate revocations still validate, but an attacker
    cannot sign a NEW revocation with a now-superseded compromised key). This
    enforces the spec MUST `revocation_signer_mismatch` fail-closed rather than
    the previous blanket membership-only acceptance.

    Returns True on the first qualifying key that validates; False if no
    qualifying key validates or the operator is unknown.

    This is the registry-aware signature check `walk_revocation_set_verified`
    runs so the only shipped consumer (revoke-cache-refresh) no longer caches
    unverified bodies (revocation-01).
    """
    sig = body.get("revocation_signature")
    if not sig:
        return False
    revoked_by = body.get("revoked_by")
    signer_op = None
    for op in registry.get("operators", []):
        if op.get("operator_uuid") == revoked_by:
            signer_op = op
            break
    if signer_op is None:
        return False
    author_date = body.get("_commit_author_date", "")
    payload = {k: v for k, v in body.items() if not (k == "revocation_signature" or k.startswith("_"))}
    envelope = crypto.canonical_envelope(payload)
    for key in signer_op.get("public_keys", []):
        fpr = key.get("key_id")
        if not fpr:
            continue
        # revoke-signer-01: only honor a key that was active when the revocation
        # was authored (active-status / author-date gate), then verify the sig.
        if not _key_was_active_at(key, signer_op, author_date):
            continue
        if crypto.gpg_verify_detached(
            envelope,
            signature_b64=sig,
            expected_fingerprint=fpr,
            registered_public_material_b64=key.get("public_material"),
        ):
            return True
    return False


def walk_revocation_set_verified(
    repo_path: Path,
    registry: dict,
    *,
    memory_root: Optional[Path] = None,
    max_commits: Optional[int] = None,
    max_bytes: Optional[int] = None,
) -> dict[str, dict]:
    """Snapshot-aware revocation walk that VERIFIES each entry before admitting it.

    This is the safe-by-default seam (revocation-01): it wraps
    `walk_revocation_set_from_snapshot` and, for every walked body, requires
    BOTH:
      1. a valid `revocation_signature` resolved against the operator-registry
         (`verify_revoke_body_against_registry`), AND
      2. the `revoked_at`-vs-author-date clock-skew guard
         (`is_revoked_at_within_skew`).
    Entries that fail either check are dropped (not cached / not honored). The
    raw `walk_revocation_set*` functions still exist for advanced callers, but
    every shipped consumer (and any adapter author copying the reference) should
    use THIS wrapper so the MUST-verify contract is enforced at the seam rather
    than pushed onto each caller (the trap the unverified-by-default API set).

    revoke-skew-decoupled-01: BOTH checks run at the CANDIDATE level (inside the
    walk, before the per-key_id earliest-wins dedup), via `candidate_filter`.
    Doing the filter pre-dedup prevents an unverified / out-of-skew candidate
    (e.g. an attacker-authored revoke for the same key_id with a far-past
    `revoked_at`) from winning the earliest-wins string-min and EVICTING a
    legitimate in-skew revocation before the filters narrow to one body. Only
    signature-valid, in-skew candidates ever compete for earliest-wins.
    """
    def _candidate_ok(body: dict) -> bool:
        if not verify_revoke_body_against_registry(body, registry):
            return False
        if not is_revoked_at_within_skew(
            body.get("revoked_at", ""), body.get("_commit_author_date", "")
        ):
            return False
        return True

    return walk_revocation_set_from_snapshot(
        repo_path,
        memory_root=memory_root,
        max_commits=max_commits,
        max_bytes=max_bytes,
        candidate_filter=_candidate_ok,
    )


def parse_revoke_commit_body(commit_msg: str) -> Optional[dict]:
    """Extract the YAML body from a `memforge: revoke ...` commit message.

    Returns the parsed dict, or None if the message doesn't match the
    expected shape (caller treats as Tier 2 BLOCKER per integrity invariant
    22 if it appears to modify revocation state).
    """
    if not commit_msg.startswith(REVOKE_PREFIX):
        return None
    parts = commit_msg.split("\n", 2)
    if len(parts) < 3:
        return None
    try:
        body = yaml.safe_load(parts[2])
    except yaml.YAMLError:
        return None
    if not isinstance(body, dict):
        return None
    for required in ("key_id", "revoked_at", "reason", "revoked_by", "revocation_uid"):
        if required not in body:
            return None
    return body


_WALK_CHUNK_BYTES = 65536


def _stream_hash_list(log_args: list[str], read_cap_bytes: int) -> list[str]:
    """Run `git log --format=%H` and return the hash list, bounded by `read_cap_bytes`.

    Streams stdout in fixed-size chunks and stops reading once the running byte
    total exceeds `read_cap_bytes` (then kills + waits the child), so a
    pathologically long history is count-capped without buffering the entire
    output into RAM (walk-01). Raises `RevocationError` on a non-zero git exit
    (after draining a bounded amount of stderr for the message).
    """
    proc = subprocess.Popen(
        log_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert proc.stdout is not None
    buf = bytearray()
    capped = False
    try:
        while True:
            chunk = proc.stdout.read(_WALK_CHUNK_BYTES)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > read_cap_bytes:
                capped = True
                break
    finally:
        if capped:
            proc.kill()
        proc.wait()
    if not capped and proc.returncode not in (0, None):
        stderr = b""
        if proc.stderr is not None:
            try:
                stderr = proc.stderr.read(4096)
            except OSError:
                stderr = b""
        raise RevocationError(
            f"git log exited {proc.returncode}: {stderr.decode('utf-8', 'replace')}"
        )
    text = buf.decode("utf-8", "replace")
    return [h.strip() for h in text.splitlines() if h.strip()]


def _stream_commit_body(
    repo_path: Path, commit_hash: str, read_limit_bytes: int, *, fmt: str = "%B"
) -> tuple[str, int, bool]:
    """Stream one commit field via `git log -1 --format=<fmt>`, bounded to `read_limit_bytes`.

    Returns `(message, bytes_consumed, capped)`. `capped` is True if the read hit
    the limit (the caller treats that as a byte-cap-exceeded abort). The child is
    killed + waited the moment the limit is hit so a multi-GB commit message
    cannot OOM the adapter (walk-01). `fmt` defaults to `%B` (full body); the
    author-date fetch passes `%aI` so it shares the same bounded-read hardening
    (walk-authdate-01).
    """
    proc = subprocess.Popen(
        ["git", "-C", str(repo_path), "log", "-1", f"--format={fmt}", commit_hash],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None
    buf = bytearray()
    capped = False
    try:
        while True:
            chunk = proc.stdout.read(_WALK_CHUNK_BYTES)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) >= read_limit_bytes:
                capped = True
                break
    finally:
        if capped:
            proc.kill()
        proc.wait()
    if not capped and proc.returncode not in (0, None):
        stderr = b""
        if proc.stderr is not None:
            try:
                stderr = proc.stderr.read(4096)
            except OSError:
                stderr = b""
        raise RevocationError(
            f"git log -1 {commit_hash} exited {proc.returncode}: {stderr.decode('utf-8', 'replace')}"
        )
    return buf.decode("utf-8", "replace"), len(buf), capped


def walk_revocation_set(
    repo_path: Path,
    *,
    since_commit: Optional[str] = None,
    max_commits: int = DEFAULT_WALK_MAX_COMMITS,
    max_bytes: int = DEFAULT_WALK_MAX_BYTES,
    candidate_filter=None,
) -> dict[str, dict]:
    """Stream git history collecting `memforge: revoke ...` commit bodies.

    Returns a dict keyed by `key_id` whose values are the parsed bodies.
    When the same key_id appears multiple times, the earliest revocation
    (by `revoked_at`) wins (most permissive for the receiver: once revoked,
    always revoked).

    Walk is bounded: aborts with `RevocationError` when EITHER the commit
    count exceeds `max_commits` OR total bytes read exceeds `max_bytes`.
    Closes the v0.5.2 threat-model MAJOR where a malicious repo with a very
    long history or very large commit messages could OOM any adapter walking
    revocation state at startup.

    Performance note: the walk materializes the full hash list (one 40-byte
    string per commit, count-capped) up front, then fetches each commit body in
    an isolated `git log -1 --format=...` subprocess. This is NOT the
    O(stream) ideal -- it spawns one subprocess per commit -- but it is the
    framing-injection-safe design and stays bounded by the caps. The hash list
    is the only thing held in bulk; bodies are processed and dropped one at a
    time.

    Each captured revoke body carries two walk-derived fields the caller needs
    for the spec's clock-skew guards: `_commit_hash` and `_commit_author_date`
    (the commit's git author-date, ISO-8601). Callers MUST run the
    `revoked_at`-vs-author-date skew check (`is_revoked_at_within_skew` /
    `assert_revoked_at_within_skew`) per SPEC.md §"`revoked_at` clock-skew
    guard (MUST)" to close the immortal-/future-revocation attack; the walk
    surfaces the author-date so that check is possible.

    Operators with legitimate large histories can raise the caps via
    `.memforge/config.yaml` under `revocation.walk_max_commits` /
    `revocation.walk_max_bytes`, OR publish a `memforge:
    revocation-snapshot` commit so the walk starts from the snapshot
    instead of repo root (see `walk_revocation_set_from_snapshot`, the
    reference cold-start path).

    Verification of `revocation_signature` is the caller's responsibility
    (signature verification needs the operator-registry to resolve signer
    pubkeys, which is a registry-layer call).
    """
    # Two-pass design (v0.5.3 BLOCKER closure): first pass fetches ONLY
    # commit hashes (40-hex-char strings; not attacker-controllable). Second
    # pass fetches each commit body in isolation via `git log -1
    # --format=%B <hash>`. This eliminates the framing-injection vector
    # where an attacker who can land a commit could craft a body containing
    # a record separator to spoof or hide revocations.
    rev_set: dict[str, dict] = {}

    log_args = ["git", "-C", str(repo_path), "log", "--format=%H"]
    if since_commit:
        log_args.append(f"{since_commit}..HEAD")

    # walk-01: STREAM the hash list with a bounded read rather than buffering the
    # whole `git log --format=%H` output via capture_output. The list is ~41
    # bytes/commit; we cap the read at (max_commits + 1) lines' worth so a
    # pathologically long history aborts on the commit cap WITHOUT first
    # buffering the entire stream into RAM.
    hash_read_cap = (max_commits + 1) * 64  # generous per-line budget; bounded
    try:
        hashes = _stream_hash_list(log_args, hash_read_cap)
    except OSError as exc:
        raise RevocationError(f"git log spawn failed: {exc}") from exc
    if len(hashes) > max_commits:
        raise RevocationError(
            f"revocation-walk commit cap exceeded ({len(hashes)} > {max_commits}). "
            "Either raise `revocation.walk_max_commits` in .memforge/config.yaml OR "
            "publish a `memforge revocation-snapshot` commit to compress history."
        )

    bytes_read = 0
    for commit_hash in hashes:
        if not _HEX40_RE.match(commit_hash):
            # git log --format=%H emits 40-char hex; anything else means
            # an unexpected upstream change. Fail closed.
            raise RevocationError(
                f"unexpected non-hex commit hash from git log: {commit_hash!r}"
            )
        # walk-01: STREAM the commit body with bounded chunked reads + a running
        # byte counter rather than capture_output (which fully buffers the body
        # into RAM BEFORE the cap is consulted). A single hostile commit with a
        # multi-GB message would OOM at the capture buffer before the byte cap
        # ever fired. We read in fixed-size chunks, abort the moment the running
        # total exceeds max_bytes, and kill+wait the child.
        remaining = max_bytes - bytes_read + 1  # +1 so the cap can be EXCEEDED, not just reached
        message, consumed, capped = _stream_commit_body(repo_path, commit_hash, remaining)
        bytes_read += consumed
        if capped or bytes_read > max_bytes:
            raise RevocationError(
                f"revocation-walk byte cap exceeded ({bytes_read} > {max_bytes}). "
                "Either raise `revocation.walk_max_bytes` in .memforge/config.yaml OR "
                "publish a `memforge revocation-snapshot` commit to compress history."
            )
        message = message.rstrip("\n")
        body = parse_revoke_commit_body(message)
        if body is None:
            continue
        body["_commit_hash"] = commit_hash
        # Capture the commit's git author-date in an isolated, fixed-format
        # call (%aI is a single ISO-8601 line; not body-controllable framing).
        # Callers run the revoked_at-vs-author-date skew guard against this.
        # walk-01 / walk-authdate-01: fetch through the same bounded-read helper
        # so this third subprocess call shares the OOM-hardening guarantee of the
        # hash-list + body fetches (a hostile repo / unexpected git output cannot
        # buffer unbounded stdout/stderr here). %aI is one short line, so the
        # 64 KiB cap is generous; exceeding it means a git surprise -> fail closed.
        date_str, _consumed, date_capped = _stream_commit_body(
            repo_path, commit_hash, _WALK_CHUNK_BYTES, fmt="%aI"
        )
        if date_capped:
            raise RevocationError(
                f"git log -1 author-date {commit_hash} produced unexpectedly large output "
                "(> read cap); fail-closed."
            )
        body["_commit_author_date"] = date_str.strip()
        # revoke-skew-decoupled-01: apply any candidate-level filter (signature +
        # skew) BEFORE the earliest-wins dedup, so an unverified / out-of-skew
        # candidate body cannot win the per-key_id selection and evict a
        # legitimate revocation. Only filter-passing candidates compete.
        if candidate_filter is not None and not candidate_filter(body):
            continue
        existing = rev_set.get(body["key_id"])
        if existing is None or body["revoked_at"] < existing["revoked_at"]:
            rev_set[body["key_id"]] = body
    return rev_set


# revoked_at clock-skew window (SPEC.md §"`revoked_at` clock-skew guard").
# Same defaults as the signing-time backdating guard: 10 min backdating, 1 min
# future skew, relative to the commit's git author-date.
REVOKED_AT_BACKDATING_MAX_SKEW = timedelta(minutes=10)
REVOKED_AT_FUTURE_MAX_SKEW = timedelta(minutes=1)


def _parse_iso(value: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (accepting a trailing Z) to an aware datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_revoked_at_within_skew(revoked_at_iso: str, author_date_iso: str) -> bool:
    """Return True if `revoked_at` is within the clock-skew window of the author-date.

    Per SPEC.md §"`revoked_at` clock-skew guard (MUST)": a revoke commit is only
    honored when `revoked_at` falls in
    `[author_date - backdating_max_skew, author_date + future_max_skew]`. This
    closes the immortal-revocation (far-past) / future-revocation (far-future,
    e.g. `revoked_at: 2099-01-01`) attacks where a compromised-key holder picks
    a `revoked_at` that defeats signing-time-aware verification. Returns False
    (fail-closed) when either timestamp is unparseable or the author-date is
    empty (the walk could not resolve it).
    """
    revoked = _parse_iso(revoked_at_iso)
    author = _parse_iso(author_date_iso)
    if revoked is None or author is None:
        return False
    lower = author - REVOKED_AT_BACKDATING_MAX_SKEW
    upper = author + REVOKED_AT_FUTURE_MAX_SKEW
    return lower <= revoked <= upper


def assert_revoked_at_within_skew(entry: dict) -> None:
    """Raise `RevocationError` if a walked revoke `entry` fails the skew guard.

    Operates on an entry from `walk_revocation_set` (which carries
    `_commit_author_date`). This is the helper a caller is contractually
    required to run per the spec MUST, so the skew guard is not silently skipped
    across consumers. Audit code: `revoked_at_skew_out_of_window`.
    """
    revoked_at = entry.get("revoked_at", "")
    author_date = entry.get("_commit_author_date", "")
    if not is_revoked_at_within_skew(revoked_at, author_date):
        raise RevocationError(
            f"revoked_at {revoked_at!r} is outside the clock-skew window of the "
            f"revoke commit's git author-date {author_date!r} "
            f"(audit BLOCKER revoked_at_skew_out_of_window). Reject this revocation."
        )


def is_key_revoked_at(rev_set: dict[str, dict], key_id: str, signing_time_iso: str) -> bool:
    """Return True if `key_id` is revoked as of `signing_time_iso`.

    Per §"Signing-time-aware verification" rule 3: the signing key was NOT
    revoked at `signature.signing_time`. So the function returns True only
    when `revoked_at <= signing_time`.

    registry-03: comparison is on parsed aware datetimes, not raw lexicographic
    string compare. A caller-supplied `+00:00`-form signing_time would otherwise
    sort below an equal-instant `Z`-form `revoked_at` and silently misjudge the
    revocation. Fail CLOSED (treat as revoked) on an unparseable input rather
    than honoring a write whose timestamps cannot be compared.
    """
    entry = rev_set.get(key_id)
    if entry is None:
        return False
    revoked = _parse_iso(entry.get("revoked_at", ""))
    signing = _parse_iso(signing_time_iso)
    if revoked is None or signing is None:
        # Unparseable timestamp: fail closed by treating the key as revoked at
        # this signing_time (refuse to honor a write we cannot adjudicate).
        return True
    return revoked <= signing


def build_revocation_snapshot_body(rev_set: dict[str, dict], *, signer_fingerprint: str) -> tuple[str, dict]:
    """Build a `memforge: revocation-snapshot <hash>` commit body.

    Returns (commit_message, body_dict) where body is YAML-serializable.
    """
    payload = {
        "snapshot_time": now_iso(),
        "revocations": [
            {
                "key_id": v["key_id"],
                "revoked_at": v["revoked_at"],
                "revoked_by": v["revoked_by"],
                "revocation_uid": v["revocation_uid"],
            }
            for v in rev_set.values()
        ],
    }
    envelope = crypto.canonical_envelope(payload)
    snap_hash = crypto.sha256_hex(envelope)
    body = dict(payload)
    body["snapshot_hash"] = snap_hash
    body["snapshot_signature"] = crypto.gpg_sign_detached(envelope, fingerprint=signer_fingerprint)
    commit_message = f"{SNAPSHOT_PREFIX}{snap_hash}\n\n{yaml.safe_dump(body, sort_keys=False)}"
    return commit_message, body


def find_revocation_snapshot_commit(repo_path: Path) -> Optional[str]:
    """Return the most recent revocation-snapshot commit hash, or None.

    SECURITY (revoke-snapshot-02): this selects a candidate by an UNSIGNED
    `git log --grep` match with ZERO signature verification of the matched
    commit. An attacker who can land a commit whose message begins
    `memforge: revocation-snapshot ` would be selected here. It is therefore
    NON-LOAD-BEARING for trust and MUST NOT be used to choose a walk boundary
    for any security-bearing revocation walk. `walk_revocation_set_from_snapshot`
    only consults it behind the off-by-default `use_snapshot_floor` opt-in,
    which is documented as experimental / unsound-for-trust; the verified walk
    never uses it. It remains only for diagnostics / the snapshot-emit tooling.
    """
    try:
        out = subprocess.check_output(
            [
                "git",
                "-C",
                str(repo_path),
                "log",
                "-n",
                "1",
                "--grep",
                "^memforge: revocation-snapshot",
                "--format=%H",
            ],
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        return None
    return out or None


def read_walk_caps(memory_root: Optional[Path]) -> tuple[int, int]:
    """Resolve `(max_commits, max_bytes)` from `<memory_root>/.memforge/config.yaml`.

    Reads `revocation.walk_max_commits` / `revocation.walk_max_bytes`. Returns
    the module defaults when the file / keys are absent. A misconfigured
    (non-integer or non-positive) value falls back to the corresponding default
    rather than crashing the walk, so a malformed config cannot brick a
    cold-start; an operator who legitimately needs a larger cap and sets a valid
    integer gets it honored. This is the knob the cap error messages instruct
    operators to edit, so it MUST actually be consulted by the walkers.
    """
    if memory_root is None:
        return DEFAULT_WALK_MAX_COMMITS, DEFAULT_WALK_MAX_BYTES
    cfg = Path(memory_root) / CONFIG_DIRNAME / CONFIG_FILENAME
    if not cfg.is_file():
        return DEFAULT_WALK_MAX_COMMITS, DEFAULT_WALK_MAX_BYTES
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError):
        return DEFAULT_WALK_MAX_COMMITS, DEFAULT_WALK_MAX_BYTES
    if not isinstance(data, dict):
        return DEFAULT_WALK_MAX_COMMITS, DEFAULT_WALK_MAX_BYTES
    rev_cfg = data.get("revocation") or {}
    if not isinstance(rev_cfg, dict):
        return DEFAULT_WALK_MAX_COMMITS, DEFAULT_WALK_MAX_BYTES

    def _coerce(raw, default: int) -> int:
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    max_commits = _coerce(rev_cfg.get("walk_max_commits"), DEFAULT_WALK_MAX_COMMITS)
    max_bytes = _coerce(rev_cfg.get("walk_max_bytes"), DEFAULT_WALK_MAX_BYTES)
    return max_commits, max_bytes


def walk_revocation_set_from_snapshot(
    repo_path: Path,
    *,
    memory_root: Optional[Path] = None,
    max_commits: Optional[int] = None,
    max_bytes: Optional[int] = None,
    use_snapshot_floor: bool = False,
    candidate_filter=None,
) -> dict[str, dict]:
    """Cold-start revocation walk that honors the config caps.

    SECURITY (snapshot-01 / revoke-snapshot-01 / revoke-snapshot-02): the
    snapshot-as-walk-FLOOR optimization is DISABLED by default and is unsound
    for trust. It is unsafe for three compounding reasons:

      1. `walk_revocation_set` with a `since_commit` builds the git range
         `since..HEAD`, which EXCLUDES the snapshot commit and everything before
         it. The revocations compressed INTO a snapshot live only in the
         `memforge: revocation-snapshot` commit body, which nothing in this
         module re-ingests (`parse_revoke_commit_body` only parses
         `memforge: revoke ` bodies). So flooring at the snapshot silently DROPS
         every pre-snapshot revocation: a revoked (compromised) key becomes valid
         again. This is a revocation-set history-truncation evasion, not the
         documented deferred snapshot-hash item.
      2. The snapshot start-commit was selected by an UNSIGNED `git log --grep`
         match (`find_revocation_snapshot_commit`), so any attacker who can land
         a commit whose message begins `memforge: revocation-snapshot ` controls
         the walk floor and can truncate the revocation set at will.
      3. The snapshot itself was built from the UNVERIFIED raw walk, so forged /
         backdated revoke bodies could be laundered into a signed snapshot.

    DECISION (approved): correctness over performance. A fresh / partner store
    has a tiny revocation history, so the full VERIFIED walk is cheap. This
    function ALWAYS walks the FULL history from repo root (no floor) unless the
    caller passes the explicit, OFF-by-default `use_snapshot_floor=True`
    opt-in, which is documented as EXPERIMENTAL and UNSOUND-FOR-TRUST and must
    never be used by a security-bearing consumer. `walk_revocation_set_verified`
    (the only seam shipped consumers use) never passes it, so the verified walk
    is always the full-history walk.

    Behavior:

    1. Resolve the walk caps from `<memory_root>/.memforge/config.yaml`
       (`revocation.walk_max_commits` / `walk_max_bytes`) so an operator who
       follows a cap-exceeded error message and raises the cap in config gets
       the larger cap honored. Explicit `max_commits` / `max_bytes` arguments
       win over config (used by tests). `memory_root` defaults to `repo_path`
       when not given (the common single-store layout).
    2. Walk the FULL revocation history from repo root (no snapshot floor) so no
       pre-snapshot revocation can be silently dropped. The
       `memforge revocation-snapshot` commit remains a non-load-bearing artifact
       (it is NOT consumed as a walk boundary by the security path).
    """
    config_root = memory_root if memory_root is not None else repo_path
    cfg_commits, cfg_bytes = read_walk_caps(config_root)
    effective_commits = cfg_commits if max_commits is None else max_commits
    effective_bytes = cfg_bytes if max_bytes is None else max_bytes
    # snapshot-01: do NOT floor the security-bearing walk at the snapshot. The
    # snapshot floor is gated behind an explicit, off-by-default, unsound-for-
    # trust opt-in; the default (and every shipped consumer) walks full history.
    since = find_revocation_snapshot_commit(repo_path) if use_snapshot_floor else None
    return walk_revocation_set(
        repo_path,
        since_commit=since,
        max_commits=effective_commits,
        max_bytes=effective_bytes,
        candidate_filter=candidate_filter,
    )

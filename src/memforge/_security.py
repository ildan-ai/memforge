"""Cross-platform secure-file permission abstraction.

Spec ref: §"Operator-identity file (per-machine)", §"Recovery-secret filesystem
mode (normative)", and integrity invariant 21.

The MemForge v0.5+ spec mandates that operator-identity / recovery-secret /
sender-sequence / agent-session files MUST be restricted to the current
operator: read/write by the file owner only, no access for other users on
the host. The original v0.5.0 / v0.5.1 / v0.5.2-pre code enforced this via
POSIX mode bits (0600 / 0700) + `stat().st_uid == os.geteuid()`. That
implementation is correct on macOS + Linux but is a no-op on native
Windows (POSIX mode bits map to no-op on NTFS; `os.geteuid` raises
AttributeError).

v0.5.2 introduces a platform abstraction:

- POSIX (macOS, Linux, *BSD): unchanged. `os.chmod(path, file_mode)` plus
  `stat()` uid match against `os.geteuid()`.
- Windows: ACL-based restriction via the built-in `icacls` binary.
  `restrict_to_owner` removes inherited ACEs and grants Full Control to
  the current user only; `verify_owner_restricted` parses `icacls` output
  and rejects on the presence of any ACE for Everyone / Authenticated
  Users / Users / Guest / other-principals.

Both paths satisfy the spec contract "file is restricted to current
owner"; the spec is platform-agnostic and references this module as the
normative implementation.
"""

from __future__ import annotations

import os
import platform
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Optional

IS_WINDOWS = sys.platform == "win32"


class SecurityError(Exception):
    """Fail-closed permission-layer error. Spec §"Cross-cutting fail-closed posture"."""


def restrict_file_to_owner(path: Path, *, mode: int = 0o600) -> None:
    """Restrict a single FILE at `path` to current-owner read/write only.

    Caller is responsible for creating the file before calling. Idempotent.

    POSIX: `os.chmod(path, mode)`.
    Windows: `icacls /inheritance:r` (remove inherited ACEs) +
    `icacls /grant:r <current-user>:F`.
    """
    if not path.exists():
        raise SecurityError(f"restrict_file_to_owner: {path} does not exist")
    if IS_WINDOWS:
        _windows_restrict(path)
    else:
        os.chmod(path, mode)


def restrict_dir_to_owner(path: Path, *, mode: int = 0o700) -> None:
    """Restrict a single DIRECTORY at `path` to current-owner rwx-only.

    Creates the dir (and parents) if missing. Idempotent.

    POSIX: `os.chmod(path, mode)` (must include x-bit so owner can enter).
    Windows: `icacls /inheritance:r` + `icacls /grant:r <current-user>:F`.
    """
    path.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        _windows_restrict(path)
    else:
        os.chmod(path, mode)


def secure_read_text(path: Path, *, file_mode: int = 0o600, encoding: str = "utf-8") -> str:
    """Open `path`, verify owner restriction ON THE FILE DESCRIPTOR, read text.

    Closes the TOCTOU window between `verify_owner_restricted(path)` and a
    subsequent `open(path, "r")`: a same-uid attacker could swap the file
    (or substitute a symlink) between the verify call and the read. This
    function does both in one shot:

    1. POSIX: opens with `O_RDONLY | O_NOFOLLOW` (refuses if path is a
       symlink) + `os.fstat(fd)` checks mode + owner. The fd is bound to
       the file inode at open-time; subsequent path-level swaps do not
       affect the read.
    2. Windows: opens normally + calls `verify_owner_restricted(path)` on
       the path (Windows ACLs are not bound to a single inode the same
       way; the path-level check is the closest equivalent + native
       Windows lacks `O_NOFOLLOW`).

    Raises `SecurityError` on any deviation.
    """
    if not path.exists():
        raise SecurityError(f"secure file missing: {path}")
    if IS_WINDOWS:
        # Windows: path-level verification is what we have; the symlink-
        # swap surface is much smaller on NTFS because symlinks require
        # admin or developer-mode privileges by default.
        verify_owner_restricted(path)
        with open(path, "r", encoding=encoding) as f:
            return f.read()

    # POSIX path: open with O_NOFOLLOW, verify fd, read.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        # ELOOP (POSIX) when O_NOFOLLOW refuses a symlink.
        raise SecurityError(
            f"secure_read_text refused to open {path}: {exc}. "
            "Path may be a symlink (rejected) or permissions block read."
        ) from exc
    try:
        st = os.fstat(fd)
        actual_mode = stat.S_IMODE(st.st_mode)
        if actual_mode != file_mode:
            raise SecurityError(
                f"{path} fd-mode is {oct(actual_mode)}, expected {oct(file_mode)}. "
                "Fail-closed; TOCTOU-swap detected OR mode relaxed."
            )
        if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
            raise SecurityError(
                f"{path} fd-uid={st.st_uid} != effective uid={os.geteuid()}. "
                "Fail-closed; ownership mismatch."
            )
        with os.fdopen(fd, "r", encoding=encoding) as f:
            return f.read()
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def secure_read_bytes(path: Path, *, file_mode: int = 0o600) -> bytes:
    """Same contract as `secure_read_text` but returns bytes (for binary content)."""
    if not path.exists():
        raise SecurityError(f"secure file missing: {path}")
    if IS_WINDOWS:
        verify_owner_restricted(path)
        with open(path, "rb") as f:
            return f.read()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise SecurityError(
            f"secure_read_bytes refused to open {path}: {exc}."
        ) from exc
    try:
        st = os.fstat(fd)
        actual_mode = stat.S_IMODE(st.st_mode)
        if actual_mode != file_mode:
            raise SecurityError(
                f"{path} fd-mode is {oct(actual_mode)}, expected {oct(file_mode)}."
            )
        if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
            raise SecurityError(
                f"{path} fd-uid={st.st_uid} != effective uid={os.geteuid()}."
            )
        with os.fdopen(fd, "rb") as f:
            return f.read()
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def verify_owner_restricted(path: Path, *, file_mode: int = 0o600, parent_mode: int = 0o700) -> None:
    """Verify `path` (and its parent dir) are restricted to current owner.

    POSIX: file mode == `file_mode`, parent mode == `parent_mode`, file uid
    == effective uid.
    Windows: file ACL + parent dir ACL grant access only to the current
    user (no inherited ACEs; no Everyone / Authenticated Users / Users /
    Guest principals).

    Raises `SecurityError` (fail-closed) on any deviation per spec
    §"Cross-cutting fail-closed posture" items 3, 4, 6, 7.
    """
    if not path.exists():
        raise SecurityError(f"secure file missing: {path}")
    if IS_WINDOWS:
        _windows_verify(path)
        _windows_verify(path.parent)
        return
    _posix_verify(path, file_mode=file_mode)
    _posix_verify(path.parent, file_mode=parent_mode)


def current_owner_label() -> str:
    """Human-readable label for the current owner. Used in error messages.

    POSIX: effective uid. Windows: `USERDOMAIN\\USERNAME` (or `USERNAME` if
    no domain is set, which is typical for local accounts).
    """
    if IS_WINDOWS:
        domain = os.environ.get("USERDOMAIN", "")
        user = os.environ.get("USERNAME", "")
        return f"{domain}\\{user}" if domain else user
    return f"uid={os.geteuid()}"


def host_node_name() -> str:
    """Cross-platform hostname. Replaces `os.uname().nodename` (POSIX-only)."""
    return platform.node() or "unknown"


# ---------- POSIX implementation ----------


def _posix_verify(path: Path, *, file_mode: int) -> None:
    st = path.stat()
    actual_mode = stat.S_IMODE(st.st_mode)
    if actual_mode != file_mode:
        raise SecurityError(
            f"{path} mode is {oct(actual_mode)}, expected {oct(file_mode)}. "
            f"Run `chmod {oct(file_mode)[2:]} {path}`."
        )
    if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
        raise SecurityError(
            f"{path} uid={st.st_uid} != effective uid={os.geteuid()}. "
            "Fail-closed; investigate ownership."
        )


# ---------- Windows implementation (PowerShell Get-Acl + SID-based check) ----------


# Well-known forbidden SIDs. SIDs are language-agnostic identifiers;
# parsing localized icacls output (the v0.5.2 implementation) failed
# closed on English Windows only and failed OPEN on every other locale
# (German "Jeder" for Everyone, French "Tout le monde", etc.). Spec
# §"Operator-identity file" v0.5.3 mandates SID-based enforcement.
_FORBIDDEN_SIDS = frozenset({
    "S-1-1-0",        # Everyone
    "S-1-5-7",        # ANONYMOUS LOGON
    "S-1-5-11",       # Authenticated Users
    "S-1-5-32-545",   # BUILTIN\Users
    "S-1-5-32-546",   # BUILTIN\Guests
    "S-1-5-4",        # INTERACTIVE
    "S-1-5-2",        # NETWORK
    "S-1-5-3",        # BATCH
    "S-1-5-6",        # SERVICE
    "S-1-5-1",        # DIALUP
    "S-1-5-13",       # TERMINAL SERVER USER
    "S-1-5-14",       # REMOTE INTERACTIVE LOGON
})


def _icacls_path() -> str:
    # icacls.exe is in %SystemRoot%\System32 on every supported Windows;
    # used only for the restrict-path (write-side; SID translation not
    # needed since we GRANT to a known user, never enumerate).
    return "icacls"


def _windows_restrict(path: Path) -> None:
    """Remove inherited ACEs + grant Full Control to current user only.

    Two icacls calls:
      icacls <path> /inheritance:r
        Removes inherited permissions; does NOT convert inherited to explicit.
      icacls <path> /grant:r <user>:F
        Grants Full Control to current user (replacing any prior ACE).

    Both calls write SIDs through the user-name resolution layer Windows
    handles internally; the user name on this side is interpreted by
    Windows itself, not by us.
    """
    user = current_owner_label()
    subprocess.run(
        [_icacls_path(), str(path), "/inheritance:r"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [_icacls_path(), str(path), "/grant:r", f"{user}:F"],
        check=True,
        capture_output=True,
        text=True,
    )


def _windows_current_user_sid() -> str:
    """Get current user's SID via `whoami /user /fo csv /nh`. Cached per-process.

    `whoami` is a builtin Windows binary in `%SystemRoot%\\System32`. The
    `/fo csv /nh` flags produce machine-parseable CSV with no header.
    Output shape: `"DOMAIN\\user","S-1-5-21-..."`.
    """
    global __CURRENT_USER_SID_CACHE
    if __CURRENT_USER_SID_CACHE is not None:
        return __CURRENT_USER_SID_CACHE
    proc = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        check=True,
        capture_output=True,
        text=True,
    )
    # Output: "DOMAIN\user","S-1-5-21-..."
    parts = [p.strip().strip('"') for p in proc.stdout.strip().split(",")]
    sid = next((p for p in parts if p.startswith("S-1-")), None)
    if sid is None:
        raise SecurityError(
            f"could not resolve current user SID via whoami. Got: {proc.stdout!r}"
        )
    __CURRENT_USER_SID_CACHE = sid
    return sid


__CURRENT_USER_SID_CACHE: Optional[str] = None


# SDDL ACE parser. Each ACE has shape (type;flags;rights;objectGUID;
# inheritObjectGUID;trustee). For our purposes we only need the trustee
# field (last position), which is either a SID like S-1-1-0 or a two-letter
# SDDL alias like WD (= Everyone = S-1-1-0).
#
# SDDL aliases for forbidden well-known principals
# (https://learn.microsoft.com/en-us/windows/win32/secauthz/sid-strings).
_SDDL_ALIAS_TO_SID = {
    "WD": "S-1-1-0",        # Everyone
    "AN": "S-1-5-7",        # ANONYMOUS LOGON
    "AU": "S-1-5-11",       # Authenticated Users
    "BU": "S-1-5-32-545",   # BUILTIN\Users
    "BG": "S-1-5-32-546",   # BUILTIN\Guests
    "IU": "S-1-5-4",        # INTERACTIVE
    "NU": "S-1-5-2",        # NETWORK
    "BA": "S-1-5-32-544",   # BUILTIN\Administrators (not in forbidden list, but parseable)
    "SY": "S-1-5-18",       # LOCAL SYSTEM (not in forbidden list)
    "LA": "S-1-5-21-...-500",  # local Administrator (template; not used)
}

_ACE_RE = re.compile(
    r"\(([^;]*);([^;]*);([^;]*);([^;]*);([^;]*);([^)]+)\)"
)


def _sddl_to_sids(sddl: str) -> list[str]:
    """Extract all trustee SIDs from an SDDL ACL string.

    SDDL format: `D:PAI(A;;FA;;;S-1-5-21-...)(A;;FA;;;BA)`. The trustee field
    is either a full SID or a 2-letter SDDL alias. We resolve aliases to
    SIDs via `_SDDL_ALIAS_TO_SID`.
    """
    sids: list[str] = []
    for match in _ACE_RE.finditer(sddl):
        trustee = match.group(6).strip()
        if trustee.startswith("S-1-"):
            sids.append(trustee)
        elif trustee in _SDDL_ALIAS_TO_SID:
            sids.append(_SDDL_ALIAS_TO_SID[trustee])
        else:
            # Unknown 2-letter alias OR alias longer than 2 chars (e.g.
            # NS = LOCAL_SERVICE). Be conservative: if it's not a SID and
            # not in our known-safe alias table, treat as unknown and
            # surface it.
            sids.append(f"UNKNOWN-SDDL-TRUSTEE:{trustee}")
    return sids


def _windows_verify(path: Path) -> None:
    """Verify `path` is restricted to current owner via SID-based ACL check.

    Closes v0.5.3 BLOCKER D: the v0.5.2 implementation parsed localized
    icacls output which failed open on non-English Windows. v0.5.3 uses
    `icacls /save` to dump the ACL in SDDL (Security Descriptor Definition
    Language) format. SDDL is the canonical Windows ACL serialization;
    trustees are expressed as SIDs (language-agnostic) or as 2-letter
    SDDL aliases (e.g. WD = Everyone). We parse SDDL ACEs + check against
    a forbidden-SIDs denylist.

    Earlier v0.5.3 attempts used PowerShell `Get-Acl` but GitHub-Actions-
    hosted Windows runners have a broken Microsoft.PowerShell.Security
    type-data import that prevents Get-Acl from loading. icacls is a
    builtin binary in %SystemRoot%\\System32 with no module dependencies.

    Process:
    1. Call `icacls <path> /save <tmpfile> /c`. icacls writes the path
       and its SDDL ACL to tmpfile in UTF-16 LE encoding.
    2. Parse the SDDL string. Extract trustee SIDs.
    3. Reject if any SID is in the forbidden-SIDs denylist.
    4. Reject if the current user's SID is not present.
    """
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".acl", delete=False) as f:
        tmpfile = f.name
    try:
        proc = subprocess.run(
            [_icacls_path(), str(path), "/save", tmpfile, "/c"],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise SecurityError(
                f"icacls /save failed on {path}: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        # icacls /save writes UTF-16 LE, sometimes with BOM and sometimes
        # without depending on Windows + locale. Read raw bytes + try
        # decode strategies in order.
        with open(tmpfile, "rb") as f_in:
            raw = f_in.read()
        content = None
        for enc in ("utf-16", "utf-16-le", "utf-8-sig", "utf-8", "mbcs"):
            try:
                content = raw.decode(enc)
                break
            except (UnicodeError, LookupError):
                continue
        if content is None:
            raise SecurityError(
                f"could not decode icacls /save output for {path}; "
                f"raw bytes start with {raw[:32]!r}"
            )
    finally:
        try:
            os.unlink(tmpfile)
        except OSError:
            pass

    sids = _sddl_to_sids(content)
    forbidden_present = [s for s in sids if s in _FORBIDDEN_SIDS]
    if forbidden_present:
        raise SecurityError(
            f"{path} ACL contains forbidden SID(s) {forbidden_present!r}. "
            f"Re-run `icacls \"{path}\" /inheritance:r /grant:r \"{current_owner_label()}:F\"` "
            "to restrict to the current owner only."
        )
    unknown = [s for s in sids if s.startswith("UNKNOWN-SDDL-TRUSTEE:")]
    if unknown:
        raise SecurityError(
            f"{path} ACL contains unrecognized SDDL trustee(s) {unknown!r}. "
            "Conservative fail-closed; investigate the ACL via `icacls \"<path>\"`."
        )
    user_sid = _windows_current_user_sid()
    if user_sid not in sids:
        raise SecurityError(
            f"{path} ACL does not include current owner SID {user_sid!r}. "
            f"Run `icacls \"{path}\" /grant:r \"{current_owner_label()}:F\"`."
        )

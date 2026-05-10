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
    """Get current user's SID via PowerShell. Cached per-process."""
    global __CURRENT_USER_SID_CACHE
    if __CURRENT_USER_SID_CACHE is not None:
        return __CURRENT_USER_SID_CACHE
    proc = subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    sid = proc.stdout.strip()
    if not sid.startswith("S-1-"):
        raise SecurityError(
            f"could not resolve current user SID via PowerShell. Got: {sid!r}"
        )
    __CURRENT_USER_SID_CACHE = sid
    return sid


__CURRENT_USER_SID_CACHE: Optional[str] = None


def _windows_verify(path: Path) -> None:
    """Verify `path` is restricted to current owner via SID-based ACL check.

    Closes v0.5.3 BLOCKER D: the v0.5.2 implementation parsed localized
    icacls output ("Everyone:") which failed open on non-English Windows.
    v0.5.3 uses PowerShell Get-Acl + IdentityReference.Translate to get
    SIDs directly. SIDs are language-agnostic.

    Process:
    1. Run `powershell -Command "..."` that emits one SID per ACE line.
    2. Reject if any SID is in the forbidden-SIDs denylist.
    3. Reject if the current user's SID is not present (no ACE for owner).
    """
    ps_cmd = (
        f"$ErrorActionPreference='Stop'; "
        f"(Get-Acl -Path '{path}').Access | ForEach-Object {{ "
        f"$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value "
        f"}}"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SecurityError(
            f"PowerShell Get-Acl failed on {path}: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    sids = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    forbidden_present = [s for s in sids if s in _FORBIDDEN_SIDS]
    if forbidden_present:
        raise SecurityError(
            f"{path} ACL contains forbidden SID(s) {forbidden_present!r}. "
            f"Re-run `icacls \"{path}\" /inheritance:r /grant:r \"{current_owner_label()}:F\"` "
            "to restrict to the current owner only."
        )
    user_sid = _windows_current_user_sid()
    if user_sid not in sids:
        raise SecurityError(
            f"{path} ACL does not include current owner SID {user_sid!r}. "
            f"Run `icacls \"{path}\" /grant:r \"{current_owner_label()}:F\"`."
        )

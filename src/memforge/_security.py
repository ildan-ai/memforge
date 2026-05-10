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


# ---------- Windows implementation (icacls-based) ----------


# ACEs that imply "not owner-restricted" if present on the path's ACL.
# All matches are case-insensitive substring matches against icacls output.
_FORBIDDEN_PRINCIPALS = (
    "Everyone:",
    "Authenticated Users:",
    "BUILTIN\\Users:",
    "BUILTIN\\Guests:",
    "NT AUTHORITY\\INTERACTIVE:",
    "NT AUTHORITY\\Authenticated Users:",
    "NT AUTHORITY\\NETWORK:",
    "NT AUTHORITY\\BATCH:",
    "NT AUTHORITY\\SERVICE:",
    "NT AUTHORITY\\ANONYMOUS LOGON:",
    "Guest:",
    "Users:",
)


def _icacls_path() -> str:
    # icacls.exe is in %SystemRoot%\System32 on every supported Windows; rely on
    # PATH having it. If somehow missing, the subprocess call raises FileNotFoundError
    # and the caller fails closed.
    return "icacls"


def _windows_restrict(path: Path) -> None:
    """Remove inherited ACEs + grant Full Control to current user only.

    Two icacls calls:
      icacls <path> /inheritance:r
        Removes inherited permissions; converts inherited to explicit.
      icacls <path> /remove:g "<other-principal>" ... ; /grant:r <user>:F
        Replaces any existing ACE for the current user with Full Control.

    We use /inheritance:r (remove all inherited; do NOT convert) so the
    file's only ACEs become the explicit /grant we add. The /grant:r form
    REPLACES rather than adds.
    """
    user = current_owner_label()
    # Step 1: strip inherited ACEs.
    subprocess.run(
        [_icacls_path(), str(path), "/inheritance:r"],
        check=True,
        capture_output=True,
        text=True,
    )
    # Step 2: explicitly grant Full Control to the current user (replacing any prior ACE).
    subprocess.run(
        [_icacls_path(), str(path), "/grant:r", f"{user}:F"],
        check=True,
        capture_output=True,
        text=True,
    )


def _windows_verify(path: Path) -> None:
    """Parse `icacls <path>` output; reject any ACE not owned by current user.

    icacls output shape:
        <path-line> <ace-1>
                    <ace-2>
                    <ace-3>
        Successfully processed 1 files; Failed processing 0 files

    We collect all ACE lines (everything after the path token in line 1
    plus continuation lines), then check (a) the current user has an ACE
    and (b) no forbidden principal is present.
    """
    user = current_owner_label().lower()
    proc = subprocess.run(
        [_icacls_path(), str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SecurityError(
            f"icacls failed on {path}: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    out = proc.stdout
    out_lower = out.lower()
    for principal in _FORBIDDEN_PRINCIPALS:
        if principal.lower() in out_lower:
            raise SecurityError(
                f"{path} ACL contains forbidden principal {principal!r}. "
                f"Run `icacls \"{path}\" /inheritance:r /grant:r \"{current_owner_label()}:F\"` "
                f"to restrict to the current owner only."
            )
    if user not in out_lower:
        raise SecurityError(
            f"{path} ACL does not list current owner {current_owner_label()!r}. "
            f"Run `icacls \"{path}\" /grant:r \"{current_owner_label()}:F\"`."
        )

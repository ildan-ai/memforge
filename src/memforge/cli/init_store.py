"""`memforge init-store`: create the `.memforge/` layout in a memory-root.

With `--mode`, also writes the signer list for that commit-trust mode. The list
holds the creator's own configured signing key when it can be read, otherwise a
comment-only file the creator must fill before the first `memforge sync`.
Nothing here creates, reads or writes a private key.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

LIST_FILES: dict[str, str] = {
    "ssh": "allowed_signers",
    "gpg": "allowed_gpg_fingerprints",
    "keyless": "allowed_identities",
}

_COMMENT_ONLY: dict[str, str] = {
    "ssh": (
        "# Signers allowed to change this store, one per line, in git's\n"
        "# allowed_signers format:\n"
        '#   <label> namespaces="git" <ssh public key>\n'
        "# Do not use valid-after, valid-before or cert-authority options.\n"
        "# Add at least one line before the first sync.\n"
    ),
    "gpg": (
        "# Signers allowed to change this store: one 40-hex primary key\n"
        "# fingerprint per line. Add at least one before the first sync.\n"
    ),
    "keyless": (
        "# Signers allowed to change this store, one per line:\n"
        "#   <certificate-identity> <oidc-issuer-url>\n"
        "# Both fields are matched exactly. Add at least one line before the\n"
        "# first sync.\n"
    ),
}

_SSH_KEY_RE = re.compile(r"^(ssh-[a-z0-9]+|ecdsa-sha2-\S+|sk-\S+) [A-Za-z0-9+/=]+")
_GPG_KEY_ID_RE = re.compile(r"^(0x)?[0-9A-Fa-f]{8,40}$")
_FPR_RE = re.compile(r"^[0-9A-Fa-f]{40}$")
_MAX_PUB_BYTES = 16384
_TIMEOUT_S = 10


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "init-store",
        help="Create the .memforge/ folder in a memory-root; with --mode, add the signer list.",
    )
    p.add_argument(
        "--memory-root",
        default=".",
        help="Memory-root directory (default: cwd). The .memforge/ subfolder is created here.",
    )
    p.add_argument(
        "--mode",
        choices=sorted(LIST_FILES),
        help="Commit-trust mode. Writes that mode's signer list; never overwrites one.",
    )
    p.set_defaults(func=cmd)


def _run(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def _git_config(root: Path, key: str) -> str | None:
    out = _run(["git", "config", "--get", key], root)
    value = out.strip() if out else ""
    return value or None


def _ssh_list_line(root: Path) -> str | None:
    """`<label> namespaces="git" <public key>` from user.signingkey, or None."""
    configured = _git_config(root, "user.signingkey")
    if not configured:
        return None
    if configured.startswith("key::"):
        material = configured[len("key::"):].strip()
    elif _SSH_KEY_RE.match(configured):
        material = configured
    else:
        # A path. Only the public half is read, never a private key file.
        path = Path(configured).expanduser()
        if path.suffix != ".pub":
            path = path.with_name(path.name + ".pub")
        try:
            if not path.is_file() or path.stat().st_size > _MAX_PUB_BYTES:
                return None
            material = path.read_text(encoding="utf-8").splitlines()[0].strip()
        except (OSError, UnicodeDecodeError, IndexError):
            return None
    if not _SSH_KEY_RE.match(material):
        return None
    key = " ".join(material.split()[:2])
    label = (_git_config(root, "user.email") or "creator").split()[0]
    return f'{label} namespaces="git" {key}'


def _gpg_list_line(root: Path) -> str | None:
    """Primary fingerprint of user.signingkey from the creator's keyring, or None."""
    key_id = _git_config(root, "user.signingkey")
    if not key_id or not _GPG_KEY_ID_RE.match(key_id):
        return None
    out = _run(["gpg", "--batch", "--with-colons", "--fingerprint", "--list-keys", key_id], root)
    if not out:
        return None
    records = [line.split(":") for line in out.splitlines()]
    # A short id can match several keys. Refuse rather than guess which one.
    if sum(1 for fields in records if fields[0] == "pub") != 1:
        return None
    # The first `fpr` record follows the `pub` record: the primary key.
    for fields in records:
        if fields[0] == "fpr" and len(fields) > 9:
            fpr = fields[9]
            return fpr.upper() if _FPR_RE.match(fpr) else None
    return None


def _write_new(path: Path, text: str) -> None:
    """Create `path` with `text`, atomically, failing if it already exists."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".init-store-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.link(tmp, path)
    finally:
        os.unlink(tmp)


def cmd(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root).resolve()
    if not memory_root.is_dir():
        print(f"memory-root {memory_root} is not a directory.", file=sys.stderr)
        return 2

    store_dir = memory_root / ".memforge"
    existing = [n for n in LIST_FILES.values() if (store_dir / n).exists()]
    if existing:
        print(
            f"signer list already present in {store_dir}: {', '.join(existing)}. "
            "Nothing was changed.",
            file=sys.stderr,
        )
        return 2

    store_dir.mkdir(exist_ok=True)
    if not args.mode:
        print(f"layout ready at {store_dir}.")
        print("To use commit trust, run `memforge init-store --mode <ssh|gpg|keyless>`.")
        return 0

    line = None
    if args.mode == "ssh":
        line = _ssh_list_line(memory_root)
    elif args.mode == "gpg":
        line = _gpg_list_line(memory_root)

    target = store_dir / LIST_FILES[args.mode]
    if line:
        _write_new(target, line + "\n")
        print(f"wrote {target} with your configured signing key.")
    else:
        _write_new(target, _COMMENT_ONLY[args.mode])
        print(f"wrote {target} with comments only: no signing key was added.")
        print("Fill the list before the first `memforge sync`; an empty list fails every commit.")
    print("Commit the list, signed, then run the first sync.")
    return 0

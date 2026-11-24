"""Signer lists and the per-commit verdict for `memforge sync`.

The mode of a commit is the mode of the single list file in its first parent,
so a commit that edits the list cannot authorize itself. No decision in the
`ssh` mode reads a clock.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from dataclasses import dataclass

from memforge._sync_git import TIMEOUT_SECONDS, Fail, Isolation, SyncError, Tool, run

LIST_DIR = ".memforge"
LIST_FILES = {
    "allowed_signers": "ssh",
    "allowed_gpg_fingerprints": "gpg",
    "allowed_identities": "keyless",
}

# The largest list sync reads. No entry in any mode needs more than a few KB
# (the longest is an ssh line with the largest RSA key OpenSSH accepts, 16384
# bits, under 3 KB), so 1 MiB holds hundreds of such lines and thousands of
# ordinary ones, while a listed signer can no longer make every receiver read
# a list of any size for each commit judged against it.
LIST_MAX_BYTES = 1024 * 1024

# The most of a commit's list file listing sync reads. Each `ls-tree` record
# for a list file is under 200 bytes and a well-formed tree has at most one per
# name, so only a tree that repeats a name can be longer, and any tree under
# `.memforge/`, however wide, is judged without reading its other entries.
LISTING_MAX_BYTES = 4096

# gpg and keyless stay unreleased until their real-tool proofs pass.
RELEASED_MODES = frozenset({"ssh"})

MODE_LINES = {
    "ssh": "verdict has no receiver-local input except the git and ssh-keygen executables",
    "gpg": "verdict depends on this receiver's keyring, configuration and clock",
    "keyless": "verdict depends on this receiver's keyring, configuration and clock",
}

_HEX = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_FINGERPRINT = re.compile(r"[0-9A-Fa-f]{40}")
_SSH_KEY_TYPE = re.compile(r"(?:ssh-|ecdsa-sha2-|sk-)\S*")
_SSH_FORBIDDEN = re.compile(r'(?:^|[\s,"])(?:cert-authority|valid-after=|valid-before=)', re.IGNORECASE)
# The one option value a list may quote. ssh-keygen ends a quoted field at its
# closing quote, so any other quote can join or split fields unseen.
_SSH_NAMESPACES_VALUE = re.compile(r'(?:^|(?<=[ \t,]))namespaces="[^"]*"', re.IGNORECASE)
# Every mode: an entry is printable ASCII, space and tab, so trimming and
# splitting fields can only ever mean space and tab.
_ENTRY_CHARS = re.compile(r"[\t\x20-\x7e]*")
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")

# Variables a keyless verification may read; gitsign's trust roots, config and network.
_KEYLESS_PREFIXES = ("GITSIGN_", "SIGSTORE_")
_KEYLESS_NAMES = ("HOME", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR")


@dataclass(frozen=True)
class CommitInfo:
    id: str
    parent: str | None
    signed: bool


def parse_commit(commit_id: str, raw: bytes) -> CommitInfo:
    """Read the parent and the signature header from the raw object.

    Only header lines count; the message cannot spoof a header, and
    continuation lines (leading space) are skipped.
    """
    parent = None
    signed = False
    for line in raw.decode("utf-8", "replace").split("\n"):
        if line == "":
            break
        if line.startswith(" "):
            continue
        key = line.split(" ", 1)[0]
        if key == "parent" and parent is None:
            parent = line[len("parent"):].strip()
        elif key in ("gpgsig", "gpgsig-sha256"):
            signed = True
    if parent is not None and not _HEX.fullmatch(parent):
        raise SyncError("bad_commit", f"{commit_id} has a malformed parent header")
    return CommitInfo(commit_id, parent, signed)


# ---- lists -----------------------------------------------------------------

def parse_list(mode: str, data: bytes) -> tuple:
    """Return the entries of a list, or raise Fail('list_invalid')."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Fail("list_invalid", "list is not valid UTF-8") from None
    entries = []
    # Lines, blanks and comments as ssh-keygen reads them: split on newline
    # only, and only space and tab before a `#`. A character either side would
    # read differently makes the list invalid.
    for number, raw in enumerate(text.split("\n"), 1):
        raw = raw.removesuffix("\r")
        if _CONTROL.search(raw):
            raise Fail("list_invalid", f"line {number}: control character")
        line = raw.strip(" \t")
        if not line or line.startswith("#"):
            continue
        entries.append(_parse_line(mode, line, number))
    if not entries:
        raise Fail("list_invalid", "list has no entries")
    return tuple(entries)


def _parse_line(mode: str, line: str, number: int) -> object:
    if not _ENTRY_CHARS.fullmatch(line):
        raise Fail("list_invalid", f"line {number}: only printable ASCII is allowed in an entry")
    if mode == "ssh":
        if '"' in _SSH_NAMESPACES_VALUE.sub("", line):
            raise Fail("list_invalid", f'line {number}: a quote is allowed only around a namespaces="..." value')
        if _SSH_FORBIDDEN.search(line):
            raise Fail("list_invalid", f"line {number}: valid-after, valid-before and cert-authority are not allowed")
        if not any(_SSH_KEY_TYPE.fullmatch(token) for token in line.split()):
            raise Fail("list_invalid", f"line {number}: no public key found")
        return line
    if mode == "gpg":
        field = line.split("#", 1)[0].strip(" \t")
        if not _FINGERPRINT.fullmatch(field):
            raise Fail("list_invalid", f"line {number}: expected one 40-hex primary fingerprint")
        return field.upper()
    parts = line.split()
    if len(parts) != 2:
        raise Fail("list_invalid", f"line {number}: expected '<certificate-identity> <oidc-issuer-url>'")
    return (parts[0], parts[1])


def _list_records(iso: Isolation, commit: str) -> bytes:
    """The `ls-tree -z` records of the list file names in a commit, read up to
    LISTING_MAX_BYTES; a longer listing is refused unread."""
    argv = [iso._git, f"--git-dir={iso.gitdir}", "ls-tree", "-z", commit, "--",
            *(f"{LIST_DIR}/{name}" for name in LIST_FILES)]
    try:
        proc = subprocess.Popen(argv, env=iso.env(), cwd=iso.root, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise SyncError("object_missing", f"cannot read tree of {commit}: {exc}") from None
    timer = threading.Timer(TIMEOUT_SECONDS, proc.kill)
    timer.start()
    try:
        out = proc.stdout.read(LISTING_MAX_BYTES + 1)
        if len(out) > LISTING_MAX_BYTES:
            # Close both pipes instead of draining them: on Windows the git on
            # the path is a launcher, the kill reaches only the launcher, and
            # draining would read the whole listing into memory.
            proc.kill()
            proc.stdout.close()
            proc.stderr.close()
            proc.wait()
            raise Fail("ambiguous_list",
                       f"the tree names a list file more than once (listing over {LISTING_MAX_BYTES} bytes)")
        _, err = proc.communicate()
    finally:
        timer.cancel()
    if proc.returncode != 0:
        detail = err.decode("utf-8", "replace").strip() or f"git ls-tree exited {proc.returncode}"
        raise SyncError("object_missing", f"cannot read tree of {commit}: {detail}")
    return out


def list_files(iso: Isolation, commit: str) -> dict[str, str]:
    """Map each list file name present in a commit's tree to its blob id."""
    found: dict[str, str] = {}
    for record in _list_records(iso, commit).split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        mode_bits, kind, blob = meta.decode("ascii", "replace").split()[:3]
        name = path.decode("utf-8", "replace").rsplit("/", 1)[-1]
        if kind == "blob" and name in LIST_FILES:
            if name in found:
                raise Fail("ambiguous_list", f"the tree names {name} more than once")
            found[name] = blob
    return found


def _blob_size(iso: Isolation, blob: str) -> int:
    # The tree entry's mode says blob, but it can name any object, and
    # `cat-file blob` follows a tag to its target: the size checked must be
    # the size of the object read.
    result = iso.git("cat-file", "-t", blob)
    kind = result.out.decode("ascii", "replace").strip()
    if result.rc != 0:
        raise SyncError("object_missing", f"cannot read the type of list {blob}: {result.err.strip()}")
    if kind != "blob":
        raise Fail("list_invalid", f"list entry names a {kind}, not a blob")
    result = iso.git("cat-file", "-s", blob)
    text = result.out.decode("ascii", "replace").strip()
    if result.rc != 0 or not text.isdigit():
        raise SyncError("object_missing", f"cannot read the size of list {blob}: {result.err.strip()}")
    return int(text)


def _blob(iso: Isolation, blob: str) -> bytes:
    result = iso.git("cat-file", "blob", blob)
    if result.rc != 0:
        raise SyncError("object_missing", f"cannot read list {blob}: {result.err.strip()}")
    return result.out


def _single_list(files: dict[str, str]) -> tuple[str, str]:
    if not files:
        raise Fail("list_missing", "no list file in the first parent")
    if len(files) > 1:
        raise Fail("ambiguous_list", "more than one list file: " + ", ".join(sorted(files)))
    name, blob = next(iter(files.items()))
    return LIST_FILES[name], blob


# ---- verdicts --------------------------------------------------------------

def classify_gpg_status(rc: int, lines: list[list[str]]) -> str:
    """Apply the ordered gpg rules to `[GNUPG:]` lines; return the primary fingerprint."""
    words = [line[0] for line in lines if line]

    def has(*names: str) -> bool:
        return any(name in words for name in names)

    if "BADSIG" in words or ("ERRSIG" in words and "NO_PUBKEY" not in words):
        raise Fail("bad_signature")
    if "ERRSIG" in words:
        raise Fail("no_key")
    if has("EXPSIG", "EXPKEYSIG", "KEYEXPIRED"):
        raise Fail("expired")
    if has("REVKEYSIG", "KEYREVOKED"):
        raise Fail("revoked")
    if not lines and rc != 0:
        raise Fail("verifier_failed", "gpg produced no status lines")
    if rc != 0 or words.count("GOODSIG") != 1 or words.count("VALIDSIG") != 1:
        raise Fail("bad_signature")
    args = next(line[1:] for line in lines if line and line[0] == "VALIDSIG")
    if len(args) == 9:
        return args[0].upper()
    if len(args) == 10:
        return args[9].upper()
    raise Fail("bad_signature", "VALIDSIG has an unexpected argument count")


def _gpg_status_lines(stderr: str) -> list[list[str]]:
    return [line[len("[GNUPG:] "):].split() for line in stderr.splitlines()
            if line.startswith("[GNUPG:] ")]


def _gpg_env() -> dict[str, str]:
    return {name: os.environ[name] for name in ("GNUPGHOME", "HOME") if name in os.environ}


def _keyless_env(iso: Isolation) -> dict[str, str]:
    env = iso.env({"GIT_DIR": iso.gitdir})
    for name, value in os.environ.items():
        if name in _KEYLESS_NAMES or name.startswith(_KEYLESS_PREFIXES):
            env[name] = value
    return env


def _verify_ssh(iso: Isolation, tools: dict[str, Tool], commit: str, data: bytes) -> None:
    allowed = iso.write_temp(data)
    result = iso.git("verify-commit", commit, config=(
        ("gpg.format", "ssh"),
        ("gpg.ssh.program", tools["ssh-keygen"].arg),
        ("gpg.ssh.allowedSignersFile", allowed),
    ))
    if result.rc != 0:
        raise Fail("ssh_not_verified",
                   result.err.strip() or f"git verify-commit exited {result.rc} and said nothing")


def _verify_gpg(iso: Isolation, tools: dict[str, Tool], commit: str, entries: tuple) -> None:
    result = iso.git("verify-commit", "--raw", commit, config=(
        ("gpg.format", "openpgp"),
        ("gpg.program", tools["gpg"].arg),
    ), extra_env=_gpg_env())
    fingerprint = classify_gpg_status(result.rc, _gpg_status_lines(result.err))
    if fingerprint not in entries:
        raise Fail("signer_not_listed", fingerprint)


def _verify_keyless(iso: Isolation, tools: dict[str, Tool], commit: str, entries: tuple) -> None:
    gitsign = tools["gitsign"]
    if gitsign.path is None:
        raise Fail("gitsign_missing", "gitsign was not found")
    stderr = ""
    for identity, issuer in entries:
        result = run([
            gitsign.path, "verify",
            f"--certificate-identity={identity}",
            f"--certificate-oidc-issuer={issuer}",
            commit,
        ], env=_keyless_env(iso), cwd=iso.root)
        if result.rc == 0:
            return
        stderr = result.err.strip()
    raise Fail("keyless_not_verified", stderr)


class Verifier:
    """Judges commits against the list in their first parent."""

    def __init__(self, iso: Isolation, tools: dict[str, Tool], released: frozenset[str]) -> None:
        self.iso = iso
        self.tools = tools
        self.released = released
        self.modes_seen: list[str] = []
        self._parsed: dict[tuple[str, str], tuple] = {}

    def _entries(self, mode: str, blob: str) -> tuple[bytes, tuple]:
        size = _blob_size(self.iso, blob)
        if size > LIST_MAX_BYTES:
            raise Fail("list_invalid", f"list is {size} bytes, over the {LIST_MAX_BYTES}-byte limit")
        data = _blob(self.iso, blob)
        key = (mode, blob)
        if key not in self._parsed:
            self._parsed[key] = parse_list(mode, data)
        return data, self._parsed[key]

    def verify(self, commit: CommitInfo) -> str:
        """Raise Fail on refusal; return the mode that judged the commit."""
        if commit.parent is None:
            raise Fail("list_missing", "commit has no first parent")
        mode, blob = _single_list(list_files(self.iso, commit.parent))
        if mode not in self.released:
            raise Fail("mode_unreleased", f"mode {mode} is not released")
        if mode not in self.modes_seen:
            self.modes_seen.append(mode)
        data, entries = self._entries(mode, blob)
        # A commit refused for its signature state alone is not worth reading
        # its own list for.
        if not commit.signed:
            raise Fail("unsigned", "commit has no signature header")
        try:
            own_mode, own_blob = _single_list(list_files(self.iso, commit.id))
            self._entries(own_mode, own_blob)
        except Fail as exc:
            raise Fail("list_removed", exc.reason + ": " + exc.detail) from None
        if mode == "ssh":
            _verify_ssh(self.iso, self.tools, commit.id, data)
        elif mode == "gpg":
            _verify_gpg(self.iso, self.tools, commit.id, entries)
        else:
            _verify_keyless(self.iso, self.tools, commit.id, entries)
        return mode

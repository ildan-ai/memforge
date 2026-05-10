"""GPG subprocess wrappers + canonical envelope serialization.

Spec refs: §"Cryptographic attribution (v0.5.0+)", §"Signed envelope scope
(normative)".

Backend: shells out to the system `gpg` binary (GnuPG >= 2.4 expected).
Detached, armored signatures are stored base64-encoded in YAML.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata
from typing import Any, Iterable, Optional


ACCEPTED_ALGOS = ("gpg-rsa4096", "gpg-ed25519")
DENYLIST_ALGOS = ("plaintext", "md5", "sha1", "sha-1", "rsa-2048")


class CryptoError(Exception):
    """Fail-closed crypto-layer error. Spec §"Cross-cutting fail-closed posture"."""


def _gpg_bin() -> str:
    gpg = shutil.which("gpg")
    if not gpg:
        raise CryptoError(
            "gpg binary not found on PATH. MemForge v0.5.1 requires GnuPG (>= 2.4). "
            "Install via `brew install gnupg` (macOS) or your distro's package manager."
        )
    return gpg


def gpg_version() -> str:
    """Return the first line of `gpg --version` output."""
    out = subprocess.check_output([_gpg_bin(), "--version"], text=True)
    return out.splitlines()[0].strip()


def gpg_list_secret_keys() -> list[dict]:
    """Return a list of secret-key dicts: fingerprint, algo, length, uid.

    Each entry maps to one usable signing key. Subkeys are flattened up to
    their primary so the returned `fingerprint` is the signing primary.
    """
    try:
        raw = subprocess.check_output(
            [_gpg_bin(), "--list-secret-keys", "--with-colons", "--with-fingerprint"],
            text=True,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as exc:
        raise CryptoError(f"gpg --list-secret-keys failed: {exc.output}") from exc

    entries: list[dict] = []
    current: Optional[dict] = None
    for line in raw.splitlines():
        fields = line.split(":")
        if not fields:
            continue
        rec = fields[0]
        if rec == "sec":
            # New primary secret key. fields: rec, validity, length, algo, keyid, creation, ...
            algo_num = fields[3] if len(fields) > 3 else ""
            length = int(fields[2]) if len(fields) > 2 and fields[2].isdigit() else 0
            current = {
                "algo_num": algo_num,
                "length": length,
                "fingerprint": None,
                "uid": None,
            }
            entries.append(current)
        elif rec == "fpr" and current is not None and current["fingerprint"] is None:
            current["fingerprint"] = fields[9] if len(fields) > 9 else None
        elif rec == "uid" and current is not None and current["uid"] is None:
            current["uid"] = fields[9] if len(fields) > 9 else None

    out: list[dict] = []
    for e in entries:
        if not e["fingerprint"]:
            continue
        # GnuPG algo numbers: 1=RSA, 22=Ed25519, 18=ECDH, 19=ECDSA.
        algo_num = e["algo_num"]
        if algo_num == "1" and e["length"] >= 4096:
            algo_label = "gpg-rsa4096"
        elif algo_num == "22":
            algo_label = "gpg-ed25519"
        elif algo_num == "1" and e["length"] >= 3072:
            algo_label = f"gpg-rsa{e['length']}"
        else:
            algo_label = f"gpg-algo{algo_num}"
        out.append(
            {
                "fingerprint": e["fingerprint"],
                "algo": algo_label,
                "length": e["length"],
                "uid": e["uid"],
            }
        )
    return out


def gpg_check_algo_accepted(algo: str) -> None:
    """Raise `CryptoError` if `algo` is on the built-in denylist (invariant per §"Security considerations" item 11).

    Note: `algo` is checked case-insensitively. Operator config can NARROW
    the accepted set but cannot WIDEN past this denylist.
    """
    lower = algo.lower()
    for bad in DENYLIST_ALGOS:
        if bad in lower:
            raise CryptoError(
                f"algorithm {algo!r} is on the v0.5.1 built-in denylist (matches {bad!r}). "
                "Use gpg-rsa4096 or gpg-ed25519."
            )


def gpg_export_public_key(fingerprint: str) -> str:
    """Return the armored public key block for `fingerprint`, base64-encoded."""
    try:
        out = subprocess.check_output(
            [_gpg_bin(), "--export", "--armor", fingerprint],
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as exc:
        raise CryptoError(f"gpg --export failed: {exc.output!r}") from exc
    return base64.b64encode(out).decode("ascii")


def gpg_import_public_key(b64_armored: str) -> list[str]:
    """Import an armored public key (base64-encoded form from `gpg_export_public_key`).

    Returns the list of fingerprints imported (typically 1).
    """
    armored = base64.b64decode(b64_armored)
    try:
        proc = subprocess.run(
            [_gpg_bin(), "--import", "--with-fingerprint", "--batch", "--no-tty"],
            input=armored,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise CryptoError(f"gpg --import failed: {exc.stderr!r}") from exc
    stderr = proc.stderr.decode("utf-8", "replace")
    fprs = re.findall(r"key ([0-9A-F]{16,})", stderr)
    return fprs


def gpg_sign_detached(data: bytes, *, fingerprint: str) -> str:
    """Produce a detached, ASCII-armored signature; return base64 of the armored bytes."""
    try:
        proc = subprocess.run(
            [
                _gpg_bin(),
                "--local-user",
                fingerprint,
                "--detach-sign",
                "--armor",
                "--batch",
                "--yes",
                "--pinentry-mode",
                "loopback",
                "-o",
                "-",
            ],
            input=data,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise CryptoError(f"gpg --detach-sign failed: {exc.stderr!r}") from exc
    return base64.b64encode(proc.stdout).decode("ascii")


def gpg_verify_detached(data: bytes, *, signature_b64: str, expected_fingerprint: Optional[str] = None) -> bool:
    """Verify `signature_b64` against `data`. If `expected_fingerprint` set, require match.

    Returns True on valid signature + (when set) fingerprint match. False
    otherwise. Does NOT raise on bad signatures (callers usually want to
    distinguish "bad sig" from "infrastructure failure"); raises only on
    gpg binary errors (truly unexpected).
    """
    armored = base64.b64decode(signature_b64)
    proc = subprocess.run(
        [
            _gpg_bin(),
            "--verify",
            "--status-fd",
            "1",
            "--batch",
            "--no-tty",
            "-",
            "-",
        ],
        input=armored + b"\n" + data,
        capture_output=True,
    )
    # Above doesn't pipe data + sig correctly; use --files mode via temp.
    # Re-do with a more reliable invocation.
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False) as sig_f, \
         tempfile.NamedTemporaryFile(delete=False) as data_f:
        sig_f.write(armored)
        sig_f.flush()
        data_f.write(data)
        data_f.flush()
        sig_path = sig_f.name
        data_path = data_f.name
    try:
        proc = subprocess.run(
            [
                _gpg_bin(),
                "--verify",
                "--status-fd",
                "1",
                "--batch",
                "--no-tty",
                sig_path,
                data_path,
            ],
            capture_output=True,
        )
    finally:
        try:
            os.unlink(sig_path)
            os.unlink(data_path)
        except OSError:
            pass
    if proc.returncode != 0:
        return False
    status = proc.stdout.decode("utf-8", "replace")
    if "GOODSIG" not in status and "VALIDSIG" not in status:
        return False
    if expected_fingerprint:
        fpr_match = re.search(r"VALIDSIG\s+([0-9A-F]+)", status)
        if not fpr_match:
            return False
        if not fpr_match.group(1).startswith(expected_fingerprint[-40:].upper()):
            # GnuPG VALIDSIG emits the full 40-char fingerprint; we accept
            # a suffix match for short-id callers.
            if expected_fingerprint.upper() not in fpr_match.group(1):
                return False
    return True


def gpg_gen_key_batch(*, name_real: str, name_email: str, expire: str = "0") -> str:
    """Generate an Ed25519 keypair non-interactively. Returns the fingerprint.

    Uses `gpg --quick-gen-key` (GnuPG 2.1.13+). The key is created without a
    passphrase (loopback pinentry / empty passphrase) so the reference CLI
    can sign without prompting. Operators in production SHOULD set a
    passphrase via `gpg --edit-key <fpr> passwd` after generation.
    """
    user_id = f"{name_real} <{name_email}>"
    try:
        proc = subprocess.run(
            [
                _gpg_bin(),
                "--quick-gen-key",
                "--batch",
                "--passphrase",
                "",
                "--pinentry-mode",
                "loopback",
                user_id,
                "ed25519",
                "sign",
                expire,
            ],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise CryptoError(f"gpg --quick-gen-key failed: {exc.stderr!r}") from exc
    stderr = proc.stderr.decode("utf-8", "replace")
    # Look for the new key's fingerprint in the marked output.
    m = re.search(r"key ([0-9A-F]{16,40})\s+marked as ultimately trusted", stderr)
    if not m:
        # Fall back: list secret keys + return the most recent matching uid.
        keys = gpg_list_secret_keys()
        for k in reversed(keys):
            if k.get("uid", "").startswith(name_real):
                return k["fingerprint"]
        raise CryptoError(f"could not resolve fingerprint after key generation. gpg stderr: {stderr}")
    short = m.group(1)
    # Resolve short id → full fingerprint.
    for k in gpg_list_secret_keys():
        if k["fingerprint"].endswith(short):
            return k["fingerprint"]
    return short


def _nfc_normalize(value: Any) -> Any:
    """Recursively NFC-normalize every string in a JSON-shaped structure.

    Closes the Unicode-normalization repudiation vector: two visually
    identical inputs in different normalization forms (NFC vs NFD) would
    otherwise produce different byte outputs from json.dumps and therefore
    different signatures. We canonicalize to NFC before serialization so
    the envelope is determined by visual identity, not codepoint sequence.
    """
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {_nfc_normalize(k): _nfc_normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_nfc_normalize(v) for v in value]
    return value


def canonical_envelope(fields: dict) -> bytes:
    """Serialize a dict deterministically for signing/verification.

    Spec §"Signed envelope scope (normative)" requires that the signature
    cover a canonical serialization of {memory_body, identity, sender_uid,
    sequence_number, signing_time}. The canonical form is:
    1. NFC-normalize every string (keys and values, recursively).
    2. JSON with sorted keys + no whitespace + ensure_ascii=False.
    3. UTF-8 encode.

    Step 1 closes a repudiation vector where a sender could ship one
    normalization form and later claim the verifier received a different
    one.
    """
    normalized = _nfc_normalize(fields)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

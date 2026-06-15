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
import sys
import tempfile
import unicodedata
import warnings
from typing import Any, Iterable, Optional


# Accepted signature-algorithm FAMILIES (spec rule 1 is an allowlist). The
# accepted set is Ed25519 plus RSA at or above the key-size floor. RSA labels
# carry the bit-length (`gpg-rsa3072`, `gpg-rsa4096`), so the allowlist is
# expressed as a family check + numeric floor rather than a frozen literal list:
# `gpg-rsa4096` is the canonical strong-RSA label, but a spec-compliant
# `gpg-rsa3072` key must also pass. `ACCEPTED_ALGO_FAMILIES` enumerates the
# non-RSA labels accepted verbatim; RSA is accepted iff its bit-length >= floor.
ACCEPTED_ALGO_FAMILIES = ("gpg-ed25519",)
# crypto-03: this module no longer references ACCEPTED_ALGOS internally (the
# authoritative gate is gpg_check_algo_accepted -- allowlist + numeric RSA
# floor). It is retained ONLY as a stable public constant for external adapters
# that imported the canonical strong-label tuple under the old name; it is NOT
# consulted by any gate here. Do not use it for acceptance decisions.
ACCEPTED_ALGOS = ("gpg-rsa4096", "gpg-ed25519")
# String denylist for hash-family / plaintext markers. The RSA key-size floor
# (spec invariant 11/24: RSA < 3072 refused regardless of operator config) is
# enforced NUMERICALLY in gpg_check_algo_accepted, not by a string token: a
# 2048-bit RSA key can surface under several labels (`gpg-algo1`, `gpg-rsa2048`)
# that no single substring catches.
DENYLIST_ALGOS = ("plaintext", "md5", "sha1", "sha-1")
MIN_RSA_BITS = 3072

# A canonical OpenPGP v4 fingerprint is exactly 40 hex characters. Any value
# returned as a "fingerprint" that is not 40 hex chars is a short key-id or a
# parse artifact and MUST NOT be stored as a verification pin (it can never
# satisfy the exact full-fingerprint comparison in gpg_verify_detached).
_FULL_FPR_RE = re.compile(r"^[0-9A-Fa-f]{40}$")


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
        if algo_num == "22":
            algo_label = "gpg-ed25519"
        elif algo_num == "1" and e["length"] >= 3072:
            # crypto-05: emit the TRUE length for every accepted RSA key (e.g.
            # gpg-rsa8192), not a flattened gpg-rsa4096 for anything >= 4096.
            # gpg_check_algo_accepted accepts any gpg-rsa<N> with N >= floor, so
            # this is a label-fidelity fix with no gate impact; the stored label
            # then actually describes the signing key (agent_session reads it).
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
    """Raise `CryptoError` unless `algo` is on the accepted allowlist (spec rule 1).

    Note: `algo` is checked case-insensitively. Operator config can NARROW
    the accepted set but cannot WIDEN past this gate.

    Three gates, all fail-closed:
      1. String denylist for plaintext / MD5 / SHA-1 hash-family markers.
      2. Numeric RSA key-size floor. Spec invariant 11/24 mandates refusing
         RSA keys smaller than 3072 bits "regardless of operator config". The
         old token-only check (`'rsa-2048' in algo`) missed every label this
         module actually emits for a weak RSA key (`gpg-algo1` for a 2048-bit
         key from gpg_list_secret_keys, or `gpg-rsa2048` without the hyphen).
         We parse any RSA bit-length out of the label and reject < 3072.
      3. ALLOWLIST (algo-01 fix). SPEC.md §"signing-time-aware verification"
         rule 1 requires `signature.algo` be on the adapter's accepted-algo
         list -- an allowlist, not merely "not denylisted". The accepted set is
         Ed25519 plus RSA at/above the floor. Any other label (`gpg-ed448`,
         `gpg-custom`, an unrecognized `gpg-algoN`) is REJECTED here rather than
         passing silently as unvalidated metadata callers might trust.
    """
    lower = algo.lower()
    for bad in DENYLIST_ALGOS:
        if bad in lower:
            raise CryptoError(
                f"algorithm {algo!r} is on the v0.5.1 built-in denylist (matches {bad!r}). "
                "Use gpg-rsa4096 or gpg-ed25519."
            )
    # Numeric RSA size floor + ANCHORED allowlist (crypto-01). The accepted RSA
    # label form is EXACTLY `gpg-rsa<digits>` (optionally a hyphen/underscore
    # before the digits). We anchor with re.fullmatch so an attacker-influenced
    # or corrupted label that merely CONTAINS an rsa<N>=3072 substring (e.g.
    # `malicious-rsa4096`, `rsa99999garbage`, `x-rsa4096-y`) cannot smuggle past
    # the ALLOW path. A bare `rsa<digits>` (no `gpg-` prefix) is also accepted
    # for back-compat with callers that pass the family token directly. Anything
    # that contains an rsa-size token but does NOT fullmatch the canonical form
    # is rejected as an unrecognized label rather than accepted.
    rsa_floor_match = re.fullmatch(r"(?:gpg-)?rsa[\-_]?(\d+)", lower)
    if rsa_floor_match:
        bits = int(rsa_floor_match.group(1))
        if bits < MIN_RSA_BITS:
            raise CryptoError(
                f"algorithm {algo!r} declares RSA-{bits}, below the v0.5.1 floor of "
                f"RSA-{MIN_RSA_BITS} (spec invariant 11/24). Use gpg-rsa4096 or gpg-ed25519."
            )
        # RSA at/above the floor, in the canonical label form, is on the allowlist.
        return
    # A label that contains an rsa-size token but does NOT fullmatch the
    # canonical `gpg-rsa<digits>` form is an unrecognized / smuggled label.
    # Reject it here rather than letting it fall through to the family-set
    # check (which it would also fail, but with a less specific message).
    if re.search(r"rsa[\-_]?\d+", lower):
        raise CryptoError(
            f"algorithm {algo!r} contains an RSA size token but is not the canonical "
            f"`gpg-rsa<bits>` label form; refusing to accept an unanchored / smuggled "
            "RSA label (crypto-01). Use gpg-rsa4096 or gpg-ed25519."
        )
    # GnuPG colon-format algo number 1 == RSA. A bare `gpg-algo1` label means
    # an RSA key whose length gpg_list_secret_keys could not classify as
    # >= 3072 (it would otherwise have produced a `gpg-rsa<length>` label), so
    # it is a sub-3072 RSA key and MUST be refused.
    if re.fullmatch(r"gpg-algo1", lower):
        raise CryptoError(
            f"algorithm {algo!r} is an unclassified RSA key (GnuPG algo 1) below the "
            f"RSA-{MIN_RSA_BITS} floor (spec invariant 11/24). Use gpg-rsa4096 or gpg-ed25519."
        )
    # Allowlist gate: non-RSA labels must be in the explicit accepted-family set.
    if lower not in ACCEPTED_ALGO_FAMILIES:
        raise CryptoError(
            f"algorithm {algo!r} is not on the accepted-algo allowlist "
            f"(spec rule 1). Accepted: gpg-ed25519 or gpg-rsa>= {MIN_RSA_BITS}."
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


def _gpg_env(gnupg_home: Optional[str]) -> Optional[dict]:
    """Build a subprocess env dict that points GnuPG at `gnupg_home`, or None.

    When `gnupg_home` is provided every gpg invocation in this call uses an
    isolated keyring instead of the ambient default. This is the isolation
    seam that lets verification trust ONLY a single imported key (the registry
    material) rather than whatever happens to live in the operator's keyring.
    """
    if gnupg_home is None:
        return None
    env = dict(os.environ)
    env["GNUPGHOME"] = gnupg_home
    return env


def gpg_import_public_key(b64_armored: str, *, gnupg_home: Optional[str] = None) -> list[str]:
    """Import an armored public key (base64-encoded form from `gpg_export_public_key`).

    Returns the list of full 40-char fingerprints imported (typically 1).
    `gnupg_home` selects an isolated keyring when set (see `_gpg_env`).
    """
    armored = base64.b64decode(b64_armored)
    try:
        proc = subprocess.run(
            [_gpg_bin(), "--import", "--import-options", "import-show",
             "--with-colons", "--with-fingerprint", "--batch", "--no-tty"],
            input=armored,
            capture_output=True,
            check=True,
            env=_gpg_env(gnupg_home),
        )
    except subprocess.CalledProcessError as exc:
        raise CryptoError(f"gpg --import failed: {exc.stderr!r}") from exc
    # Prefer the colon-format `fpr` records from import-show on stdout (full
    # 40-char fingerprints).
    fprs: list[str] = []
    stdout = proc.stdout.decode("utf-8", "replace")
    for line in stdout.splitlines():
        fields = line.split(":")
        if fields and fields[0] == "fpr" and len(fields) > 9 and fields[9]:
            fprs.append(fields[9])
    if not fprs:
        # sec-03: the human stderr `key <id>` form yields SHORT key-ids (16+
        # chars), not full 40-char fingerprints. Returning a short id silently
        # poisons the exact-match verification pin (gpg_verify_detached requires
        # a full-length match, so a short imported id can never validate a
        # legitimate key). Re-query each short id via `--with-colons
        # --fingerprint` to resolve the canonical 40-char fpr; fail closed if it
        # cannot be resolved rather than returning a value the pin will reject.
        stderr = proc.stderr.decode("utf-8", "replace")
        short_ids = re.findall(r"key ([0-9A-F]{16,})", stderr)
        for short in short_ids:
            full = _resolve_full_fingerprint(short, gnupg_home=gnupg_home)
            if full is None:
                raise CryptoError(
                    f"imported key {short!r} but could not resolve its full 40-char "
                    "fingerprint (import-show emitted no `fpr` record and the keyid "
                    "could not be expanded). Fail-closed: refusing to return a short "
                    "id that the verification pin would always reject."
                )
            fprs.append(full)
    return fprs


def _resolve_fpr_via_gpg(arg: str, *, gnupg_home: Optional[str] = None) -> Optional[str]:
    """Single resolver: `gpg --with-colons --fingerprint <arg>` -> first 40-hex `fpr`.

    crypto-04: the two public resolvers below were byte-identical; both delegate
    here. Returns the first full 40-char `fpr` record (case as gpg emits), or
    None when `arg` cannot be expanded to a full fingerprint (caller
    fail-closes). The first `fpr` is the PRIMARY key's fingerprint in gpg colon
    output.
    """
    try:
        out = subprocess.check_output(
            [_gpg_bin(), "--with-colons", "--fingerprint", arg],
            text=True,
            stderr=subprocess.STDOUT,
            env=_gpg_env(gnupg_home),
        )
    except subprocess.CalledProcessError:
        return None
    for line in out.splitlines():
        fields = line.split(":")
        if fields and fields[0] == "fpr" and len(fields) > 9 and _FULL_FPR_RE.match(fields[9] or ""):
            return fields[9]
    return None


def _resolve_full_fingerprint(keyid: str, *, gnupg_home: Optional[str] = None) -> Optional[str]:
    """Resolve a (possibly short) key-id to its canonical 40-char fingerprint.

    Thin wrapper over `_resolve_fpr_via_gpg` (crypto-04). Returns None when the
    keyid cannot be expanded to a full fingerprint (caller fail-closes).
    """
    return _resolve_fpr_via_gpg(keyid, gnupg_home=gnupg_home)


def gpg_resolve_public_fingerprint(value: str, *, gnupg_home: Optional[str] = None) -> Optional[str]:
    """Resolve `value` to its canonical 40-char PRIMARY fingerprint from the keyring.

    Thin wrapper over `_resolve_fpr_via_gpg` (crypto-04). Covers operator-B
    public keys an operator-A added (public+secret keyring), not just locally
    generated secret keys. Returns None when the key is not present or no full
    fingerprint can be resolved (caller fail-closes).

    This is the resolver `operator-registry add` / `fresh-start` use to apply
    the same canonical-fingerprint discipline init-operator applies (opreg-01),
    so a short id / typo / non-canonical-case value is never persisted as the
    registry `key_id` (which later signature lookups match on exactly).
    """
    return _resolve_fpr_via_gpg(value, gnupg_home=gnupg_home)


def _classify_gpg_algo(algo_num: str, length: int) -> Optional[str]:
    """Map a GnuPG colon-format (algo number, key length) to a label.

    Mirrors gpg_list_secret_keys' classification: 22 -> ed25519; 1 + length ->
    rsa<length> (length-accurate). Returns None when the algo/length cannot be
    classified into an accepted family (caller fail-closes); an RSA key below
    the floor is returned as `gpg-rsaN` so gpg_check_algo_accepted rejects it on
    the true size rather than a default-stamped label.
    """
    if algo_num == "22":
        return "gpg-ed25519"
    if algo_num == "1" and length > 0:
        return f"gpg-rsa{length}"
    return None


def gpg_resolve_public_algo(value: str, *, gnupg_home: Optional[str] = None) -> Optional[str]:
    """Resolve the algo label of a key from the PUBLIC+secret keyring.

    opreg-algo-01: `operator-registry add` / `fresh-start` register ANOTHER
    operator's PUBLIC key (imported via `gpg --import`), which is NOT in the
    local SECRET keyring, so a secret-keyring-only resolver (the old
    `_resolve_algo`) always fell through to a hardcoded `gpg-ed25519` and
    persisted the WRONG algo for e.g. an RSA-4096 operator-B key. This queries
    `gpg --with-colons --fingerprint <value>` (public+secret keyring) and
    classifies the PRIMARY key's `pub`/`sec` record algo number + length the
    same way gpg_list_secret_keys does. Returns the label, or None when the key
    is absent or its algo cannot be classified (caller fail-closes rather than
    default-stamping ed25519).
    """
    try:
        out = subprocess.check_output(
            [_gpg_bin(), "--with-colons", "--fingerprint", value],
            text=True,
            stderr=subprocess.STDOUT,
            env=_gpg_env(gnupg_home),
        )
    except subprocess.CalledProcessError:
        return None
    for line in out.splitlines():
        fields = line.split(":")
        if not fields:
            continue
        # The first `pub` (or `sec`) record is the PRIMARY key. fields:
        # rec, validity, length, algo, keyid, creation, ...
        if fields[0] in ("pub", "sec"):
            length = int(fields[2]) if len(fields) > 2 and fields[2].isdigit() else 0
            algo_num = fields[3] if len(fields) > 3 else ""
            return _classify_gpg_algo(algo_num, length)
    return None


def gpg_sign_detached(data: bytes, *, fingerprint: str, gnupg_home: Optional[str] = None) -> str:
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
            env=_gpg_env(gnupg_home),
        )
    except subprocess.CalledProcessError as exc:
        raise CryptoError(f"gpg --detach-sign failed: {exc.stderr!r}") from exc
    return base64.b64encode(proc.stdout).decode("ascii")


def _normalize_fpr(value: str) -> str:
    """Normalize a GPG fingerprint for comparison: strip spaces, upper-case."""
    return re.sub(r"\s+", "", value).upper()


def gpg_verify_detached(
    data: bytes,
    *,
    signature_b64: str,
    expected_fingerprint: str,
    registered_public_material_b64: Optional[str] = None,
    gnupg_home: Optional[str] = None,
) -> bool:
    """Verify `signature_b64` over `data`, pinned to `expected_fingerprint`.

    Trust model (v0.5.1+ hardening):

    - `expected_fingerprint` is REQUIRED (no default). A signature with no
      identity binding is not a meaningful verification in this fail-closed
      threat model. The VALIDSIG fingerprint from GnuPG MUST equal the
      expected fingerprint under EXACT, full-length, case-normalized
      comparison. No substring / suffix / startswith acceptance: those let a
      different key whose fingerprint merely contains the expected hex chars
      pass the pin.
    - When `registered_public_material_b64` is supplied, the trust ROOT is the
      registered key material, NOT the ambient local keyring. The material is
      imported into an ephemeral, throwaway GNUPGHOME (mode 0700) and the
      signature is verified against ONLY that keyring. We additionally assert
      that the registered material resolves to `expected_fingerprint`, so the
      registry's declared fingerprint and its declared public bytes cannot
      disagree. This closes the attack where an unrelated key in the operator's
      ambient keyring produces a VALIDSIG that the registry layer accepts.
    - When `registered_public_material_b64` is None (back-compat / callers that
      legitimately verify against an attestation-provided ephemeral key already
      imported into the keyring, e.g. agent writes), verification runs against
      `gnupg_home` (or the ambient keyring) and the exact-fingerprint pin is
      the sole binding.

    Returns True only on a valid signature whose signing key fingerprint
    EXACTLY equals `expected_fingerprint`. Returns False on bad signature /
    fingerprint mismatch / material mismatch. Raises `CryptoError` only on a
    gpg binary failure (truly unexpected infrastructure error).
    """
    if not expected_fingerprint:
        # Fail-closed: refuse to verify without an identity pin.
        return False

    expected_norm = _normalize_fpr(expected_fingerprint)

    # When the caller hands us the registered key material, build an ephemeral
    # single-key keyring so the trust root is that material, not the ambient
    # keyring. mkdtemp gives a 0700 dir; we tear it down in finally.
    ephemeral_home: Optional[str] = None
    effective_home = gnupg_home
    try:
        if registered_public_material_b64 is not None:
            ephemeral_home = tempfile.mkdtemp(prefix="mfg-verify-")
            os.chmod(ephemeral_home, 0o700)
            try:
                imported = gpg_import_public_key(
                    registered_public_material_b64, gnupg_home=ephemeral_home
                )
            except CryptoError:
                return False
            imported_norm = {_normalize_fpr(f) for f in imported}
            # The registered material MUST resolve to the registered
            # fingerprint, AND it must be a full 40-char match (exact). A
            # short imported id that is merely a suffix is not accepted.
            if not any(f == expected_norm for f in imported_norm):
                return False
            effective_home = ephemeral_home

        armored = base64.b64decode(signature_b64)
        tmpdir = tempfile.mkdtemp(prefix="mfg-sig-")
        os.chmod(tmpdir, 0o700)
        sig_path = os.path.join(tmpdir, "sig.asc")
        data_path = os.path.join(tmpdir, "data.bin")
        try:
            # 0600 temp files inside the 0700 dir; both unlinked + dir removed
            # in finally. Matches the package's secure-write discipline rather
            # than dropping plaintext into a world-default /tmp file.
            sig_fd = os.open(sig_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(sig_fd, "wb") as f:
                f.write(armored)
            data_fd = os.open(data_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(data_fd, "wb") as f:
                f.write(data)
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
                    env=_gpg_env(effective_home),
                )
            except OSError as exc:
                raise CryptoError(f"gpg --verify invocation failed: {exc}") from exc
        finally:
            for p in (sig_path, data_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass
            try:
                os.rmdir(tmpdir)
            except OSError:
                pass
    finally:
        if ephemeral_home is not None:
            shutil.rmtree(ephemeral_home, ignore_errors=True)

    if proc.returncode != 0:
        return False
    status = proc.stdout.decode("utf-8", "replace")
    # VALIDSIG means the signature cryptographically validated against a key in
    # the (now isolated) keyring. We require VALIDSIG specifically (GOODSIG alone
    # does not carry a fingerprint) AND an exact pin match.
    #
    # The GnuPG --status-fd VALIDSIG line is:
    #   VALIDSIG <signing-key-fpr> <date> <ts> <expire> <ver> <reserved>
    #            <pubkey-algo> <hash-algo> <sig-class> <primary-key-fpr>
    # The FIRST field is the fingerprint of the key that actually signed -- for
    # an RSA-4096 key with a separate signing subkey (the GnuPG default, and a
    # first-class spec algo) that is the SUBKEY fingerprint. The LAST field is
    # the PRIMARY-key fingerprint. The registry pins the PRIMARY fpr (because
    # gpg_list_secret_keys captures only the primary), so an exact match on the
    # first field alone fail-closes every RSA-4096-with-signing-subkey key.
    # verify-01 fix: accept an exact full-fingerprint match on EITHER the
    # signing-key (first) fpr OR the primary-key (last) fpr. Both fields come
    # from GnuPG's own validated output, so neither widens the trust root: the
    # signature already cryptographically validated against the imported key,
    # and we still require the expected fingerprint to equal one of the two
    # fingerprints GnuPG reports for that exact key.
    validsig_match = re.search(r"^.*VALIDSIG\s+(.+)$", status, re.MULTILINE)
    if not validsig_match:
        return False
    fields = validsig_match.group(1).split()
    if not fields:
        return False
    signing_key_fpr = _normalize_fpr(fields[0])
    primary_key_fpr = _normalize_fpr(fields[-1])
    if expected_norm != signing_key_fpr and expected_norm != primary_key_fpr:
        return False
    return True


class UnprotectedKeyWarning(UserWarning):
    """Emitted when a signing key is generated without a passphrase.

    File-read access to the keyring == signing capability for such a key. The
    spec calls for a persistent WARN until a hardware-backed install replaces
    the software-only reference posture; this warning category is that signal.
    """


def warn_unprotected_signing_key(fingerprint: str) -> None:
    """Emit the persistent unprotected-signing-key WARN (spec posture).

    Surfaces both as a Python warning (so test/log harnesses capture it) and on
    stderr (so an interactive operator sees it). The condition is the
    empty-passphrase reference keygen; on a partner deployment this is the
    signal to set a passphrase or move to a hardware-backed key.
    """
    msg = (
        f"signing key {fingerprint} was generated WITHOUT a passphrase. "
        "Anyone who can read this GNUPGHOME can mint valid v0.5 signatures. "
        "Set a passphrase (`gpg --edit-key <fpr> passwd`) or move to a "
        "hardware-backed key before a production / partner deployment."
    )
    warnings.warn(msg, UnprotectedKeyWarning, stacklevel=2)
    print(f"WARN: {msg}", file=sys.stderr)


def gpg_gen_key_batch(*, name_real: str, name_email: str, expire: str = "0") -> str:
    """Generate an Ed25519 keypair non-interactively. Returns the fingerprint.

    Uses `gpg --quick-gen-key` (GnuPG 2.1.13+). The key is created without a
    passphrase (loopback pinentry / empty passphrase) so the reference CLI
    can sign without prompting. Operators in production SHOULD set a
    passphrase via `gpg --edit-key <fpr> passwd` after generation. Because the
    key is unprotected, this function emits a persistent
    `UnprotectedKeyWarning` (spec §"persistent startup WARN until
    hardware-backed install").
    """
    user_id = f"{name_real} <{name_email}>"

    # crypto-02 / sec-02: resolve the fingerprint AUTHORITATIVELY by diffing the
    # secret-keyring fingerprint set BEFORE and AFTER --quick-gen-key. The single
    # new fingerprint is the just-generated key, unambiguously and version-/
    # locale-independently. This removes BOTH the fragile free-text stderr regex
    # (`key <id> marked as ultimately trusted` is GnuPG-version/locale-specific)
    # AND the uid-prefix + reversed()-list heuristic (`startswith` matched
    # adjacent-prefix uids, and reversed() assumed keyring order == creation
    # order, which GnuPG colon output does not guarantee). We still validate the
    # resolved value is exactly 40 hex chars and fail closed otherwise, never
    # returning a short id that would poison the verification pin.
    try:
        before = {
            (k.get("fingerprint") or "").upper()
            for k in gpg_list_secret_keys()
            if k.get("fingerprint")
        }
    except CryptoError as exc:
        raise CryptoError(
            f"could not snapshot the secret keyring before keygen: {exc}"
        ) from exc

    try:
        subprocess.run(
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

    try:
        after_keys = gpg_list_secret_keys()
    except CryptoError as exc:
        raise CryptoError(
            f"could not list secret keys to resolve the generated fingerprint: {exc}"
        ) from exc
    after = {
        (k.get("fingerprint") or "").upper()
        for k in after_keys
        if k.get("fingerprint")
    }

    new_fprs = after - before
    if len(new_fprs) != 1:
        # Zero -> keygen produced no new secret key we can see; >1 -> a
        # concurrent keygen raced us. Either way we cannot UNAMBIGUOUSLY identify
        # the key we just generated, so fail closed rather than guess a pin.
        raise CryptoError(
            f"could not unambiguously resolve the generated key (uid {user_id!r}): "
            f"expected exactly 1 new secret-key fingerprint, found {len(new_fprs)} "
            f"({sorted(new_fprs)}). Fail-closed: refusing to guess the verification pin "
            "(crypto-02)."
        )
    fingerprint = next(iter(new_fprs))
    if not _FULL_FPR_RE.match(fingerprint):
        raise CryptoError(
            f"resolved key fingerprint {fingerprint!r} is not a 40-character hex "
            "fingerprint; refusing to return a value that would poison the "
            "verification pin (sec-02 fail-closed)."
        )
    warn_unprotected_signing_key(fingerprint)
    return fingerprint


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
        # nfc-01 / sec-05: NFC-normalizing keys can collapse two distinct keys
        # that differ only by Unicode normalization form (e.g. precomposed vs
        # decomposed) into one, silently dropping a field from the signed
        # envelope (last-write-wins). Detect the collision and fail closed
        # rather than signing/verifying content the operator did not intend.
        normalized: dict = {}
        for k, v in value.items():
            nk = _nfc_normalize(k)
            if nk in normalized:
                raise CryptoError(
                    f"canonicalization key collision: two distinct keys normalize "
                    f"to the same NFC form {nk!r}. Refusing to silently drop a field "
                    "from the signed envelope (nfc-01 fail-closed)."
                )
            normalized[nk] = _nfc_normalize(v)
        return normalized
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

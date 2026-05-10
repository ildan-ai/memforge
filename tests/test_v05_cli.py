"""Smoke + happy-path tests for v0.5.1 reference CLI.

These tests exercise the pure-Python helper layer (identity, crypto envelope,
registry, revocation, agent_session, sender_sequence) without requiring a
real GPG keyring for every assertion. Tests that DO require gpg are gated on
the `MEMFORGE_TEST_GPG=1` env var so CI can keep running on machines without
a populated keyring.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from memforge import agent_session, crypto, identity, recovery, registry, revocation, sender_sequence
from memforge.cli._dispatch import build_parser


def test_dispatcher_help_smoke(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "init-operator" in out
    assert "attest-agent" in out
    assert "messaging-doctor" in out


def test_dispatcher_all_subcommands_registered():
    import argparse as _argparse

    parser = build_parser()
    sub_action = next(
        (a for a in parser._actions if isinstance(a, _argparse._SubParsersAction)),
        None,
    )
    assert sub_action is not None, "no subparsers found on dispatcher"
    cmd_names = set(sub_action.choices.keys())
    expected = {
        "init-operator",
        "init-store",
        "operator-registry",
        "rotate-key",
        "revoke",
        "revocation-snapshot",
        "memories-by-key",
        "revoke-memories",
        "upgrade-v04-memories",
        "revoke-cache-refresh",
        "messaging-doctor",
        "recovery-init",
        "recovery-backup-confirm",
        "attest-agent",
    }
    assert expected.issubset(cmd_names), f"missing: {expected - cmd_names}"


def test_uuidv7_format():
    uid = identity.generate_uuidv7()
    # UUID format: 8-4-4-4-12 hex; version nibble = 7; variant bits = 10xx.
    assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", uid)
    # Two consecutive calls should give monotonically increasing (or equal) values
    # because the timestamp prefix dominates ordering.
    uid2 = identity.generate_uuidv7()
    assert uid <= uid2 or abs(int(uid[:13].replace("-", ""), 16) - int(uid2[:13].replace("-", ""), 16)) < 100


def test_uuidv7_distinct():
    ids = {identity.generate_uuidv7() for _ in range(100)}
    assert len(ids) == 100, "UUIDv7 should be unique across 100 generations"


def test_now_iso_format():
    s = identity.now_iso()
    assert s.endswith("Z")
    assert "T" in s


def test_agent_session_id_format():
    sid = identity.mint_agent_session_id("cc")
    assert identity.AGENT_SESSION_ID_RE.match(sid)
    assert sid.startswith("cc-")


def test_agent_session_id_normalizes_prefix():
    sid = identity.mint_agent_session_id("CC-Cursor!")
    # Non-alphanumerics stripped, lowercased.
    assert sid.startswith("cccursor-")


def test_agent_session_id_empty_prefix_raises():
    with pytest.raises(identity.IdentityError):
        identity.mint_agent_session_id("")
    with pytest.raises(identity.IdentityError):
        identity.mint_agent_session_id("!!!")


def test_validate_agent_session_id_rejects_bad_format():
    with pytest.raises(identity.IdentityError):
        identity.validate_agent_session_id("BadCase-2026-05-10-abcdefgh")
    with pytest.raises(identity.IdentityError):
        identity.validate_agent_session_id("cc-26-5-10-abcdefgh")
    with pytest.raises(identity.IdentityError):
        identity.validate_agent_session_id("cc-2026-05-10-short")


def test_validate_agent_session_id_accepts_good_format():
    identity.validate_agent_session_id("cc-2026-05-10-abcdefgh")
    identity.validate_agent_session_id("cursor-2026-05-10-aaaa1234bbbb5678")


def test_parse_identity_operator():
    parsed = identity.parse_identity("operator:01923456-7890-7abc-9def-0123456789ab")
    assert parsed["class"] == "operator"
    assert parsed["operator_uuid"] == "01923456-7890-7abc-9def-0123456789ab"


def test_parse_identity_agent():
    parsed = identity.parse_identity(
        "agent:01923456-7890-7abc-9def-0123456789ab:cc-2026-05-10-abcdefgh"
    )
    assert parsed["class"] == "agent"
    assert parsed["agent_session_id"] == "cc-2026-05-10-abcdefgh"


def test_parse_identity_rejects_bad_class():
    with pytest.raises(identity.IdentityError):
        identity.parse_identity("nonsense:abc")


def test_canonical_envelope_deterministic():
    a = crypto.canonical_envelope({"b": 1, "a": 2})
    b = crypto.canonical_envelope({"a": 2, "b": 1})
    assert a == b


def test_canonical_envelope_distinguishes_values():
    a = crypto.canonical_envelope({"x": 1})
    b = crypto.canonical_envelope({"x": 2})
    assert a != b


def test_canonical_envelope_nfc_normalizes_strings():
    """v0.5.2: NFC normalization closes repudiation via Unicode form drift."""
    # "café" in NFC (precomposed é) vs NFD (e + combining acute).
    nfc = "café"
    nfd = "café"
    assert nfc != nfd  # different codepoint sequences
    assert crypto.canonical_envelope({"v": nfc}) == crypto.canonical_envelope({"v": nfd})


def test_canonical_envelope_nfc_normalizes_keys():
    nfc_key = "café"
    nfd_key = "café"
    assert crypto.canonical_envelope({nfc_key: 1}) == crypto.canonical_envelope({nfd_key: 1})


def test_canonical_envelope_nfc_normalizes_nested():
    nfc = "café"
    nfd = "café"
    a = crypto.canonical_envelope({"outer": {"inner": [nfc, {"k": nfc}]}})
    b = crypto.canonical_envelope({"outer": {"inner": [nfd, {"k": nfd}]}})
    assert a == b


def test_gpg_check_algo_accepted_passes():
    crypto.gpg_check_algo_accepted("gpg-rsa4096")
    crypto.gpg_check_algo_accepted("gpg-ed25519")


def test_gpg_check_algo_accepted_blocks_denylist():
    for bad in ("plaintext", "MD5", "sha1", "sha-1"):
        with pytest.raises(crypto.CryptoError):
            crypto.gpg_check_algo_accepted(bad)


def test_sha256_hex_known_vector():
    assert crypto.sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_sender_uid_format():
    op_uid = identity.generate_uuidv7()
    suid = sender_sequence.mint_sender_uid(op_uid)
    sender_sequence.validate_sender_uid(suid)


def test_sender_uid_validate_bad_raises():
    with pytest.raises(identity.IdentityError):
        sender_sequence.validate_sender_uid("not-a-sender-uid")


def test_revoke_body_requires_reason_length():
    with pytest.raises(revocation.RevocationError):
        revocation.build_revoke_body(
            key_id="AAA",
            reason="short",  # < 8 chars
            revoked_by_uuid="fake-uuid",
            signer_fingerprint="DEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEF",
        )


def test_parse_revoke_commit_body_rejects_wrong_prefix():
    msg = "not-a-revoke-commit\n\nkey_id: AAA\n"
    assert revocation.parse_revoke_commit_body(msg) is None


def test_write_secure_yaml_atomic_mode(tmp_path):
    """v0.5.2: write_secure_yaml must create file restricted to current owner atomically."""
    from memforge._security import IS_WINDOWS, verify_owner_restricted
    import stat as _stat

    target = tmp_path / "subdir" / "secret.yaml"
    identity.write_secure_yaml(target, {"k": "v"})
    assert target.is_file()
    # Platform-aware verification (POSIX mode bits on Unix; NTFS ACLs on Windows).
    verify_owner_restricted(target)
    if not IS_WINDOWS:
        # POSIX-specific assertion that the v0.5.2 implementation also
        # exposed the right mode bits (caught the regression that the
        # generic verify masked).
        assert _stat.S_IMODE(target.stat().st_mode) == 0o600
        assert _stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    # No leftover tmp files.
    leftovers = [p for p in target.parent.iterdir() if ".tmp-" in p.name]
    assert not leftovers, f"leftover tmp files: {leftovers}"


def test_write_secure_yaml_overwrite_preserves_mode(tmp_path):
    """v0.5.2: re-writing an existing secure file must keep restriction + leave no tmp."""
    from memforge._security import IS_WINDOWS, verify_owner_restricted
    import stat as _stat

    target = tmp_path / "secret.yaml"
    identity.write_secure_yaml(target, {"v": 1})
    identity.write_secure_yaml(target, {"v": 2})
    verify_owner_restricted(target)
    if not IS_WINDOWS:
        assert _stat.S_IMODE(target.stat().st_mode) == 0o600
    leftovers = [p for p in target.parent.iterdir() if ".tmp-" in p.name]
    assert not leftovers


def test_write_secure_bytes_atomic_mode(tmp_path):
    """v0.5.2: write_secure_bytes follows the same atomic restricted-file contract."""
    from memforge._security import IS_WINDOWS, verify_owner_restricted
    import stat as _stat

    target = tmp_path / "secret.bin"
    identity.write_secure_bytes(target, b"\x00\x01\x02")
    assert target.read_bytes() == b"\x00\x01\x02"
    verify_owner_restricted(target)
    if not IS_WINDOWS:
        assert _stat.S_IMODE(target.stat().st_mode) == 0o600
    leftovers = [p for p in target.parent.iterdir() if ".tmp-" in p.name]
    assert not leftovers


def test_security_host_node_name_returns_nonempty():
    from memforge._security import host_node_name
    assert host_node_name()
    assert isinstance(host_node_name(), str)


def test_security_current_owner_label_returns_nonempty():
    from memforge._security import current_owner_label
    label = current_owner_label()
    assert label  # POSIX: "uid=N"; Windows: "DOMAIN\\USER" or "USER"


def test_security_windows_acl_parse_rejects_everyone(monkeypatch):
    """Mocks an icacls output containing Everyone: ACE to exercise the Windows-path verify."""
    from memforge import _security as sec
    import subprocess as sp

    monkeypatch.setattr(sec, "IS_WINDOWS", True)
    monkeypatch.setattr(sec, "current_owner_label", lambda: "TESTDOMAIN\\testuser")

    class FakeProc:
        returncode = 0
        stderr = ""
        # icacls output with a forbidden Everyone ACE.
        stdout = (
            "C:\\Users\\testuser\\.memforge\\identity.yaml TESTDOMAIN\\testuser:(F)\n"
            "                                              Everyone:(R)\n"
            "Successfully processed 1 files; Failed processing 0 files\n"
        )

    def fake_run(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(sp, "run", fake_run)
    # Need a Path that exists. tmp_path-style would do, but we don't have one here.
    # Use Path("/dev/null") on POSIX which is_file()==False; verify_owner_restricted
    # raises early on non-existent. Instead, mock pathlib.Path.exists True for the
    # whole verify call.
    import pathlib
    monkeypatch.setattr(pathlib.Path, "exists", lambda self: True)
    with pytest.raises(sec.SecurityError, match=r"Everyone:"):
        sec.verify_owner_restricted(pathlib.Path("C:\\Users\\testuser\\.memforge\\identity.yaml"))


def test_security_windows_acl_parse_accepts_owner_only(monkeypatch):
    """Mocks an icacls output containing only the current-user ACE; passes verify."""
    from memforge import _security as sec
    import subprocess as sp

    monkeypatch.setattr(sec, "IS_WINDOWS", True)
    monkeypatch.setattr(sec, "current_owner_label", lambda: "TESTDOMAIN\\testuser")

    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = (
            "C:\\Users\\testuser\\.memforge\\identity.yaml TESTDOMAIN\\testuser:(F)\n"
            "Successfully processed 1 files; Failed processing 0 files\n"
        )

    monkeypatch.setattr(sp, "run", lambda *a, **kw: FakeProc())
    import pathlib
    monkeypatch.setattr(pathlib.Path, "exists", lambda self: True)
    # Should not raise.
    sec.verify_owner_restricted(pathlib.Path("C:\\Users\\testuser\\.memforge\\identity.yaml"))


def test_record_seen_nonce_gcs_expired(tmp_path, monkeypatch):
    """v0.5.2: record_seen_nonce GCs expired entries on every call."""
    from datetime import datetime, timedelta, timezone

    from memforge import agent_session

    op_uuid = identity.generate_uuidv7()
    memory_root = tmp_path
    # Pre-seed with an expired nonce (expires_at deep in the past).
    long_expired = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    agent_session.record_seen_nonce(memory_root, op_uuid, "stale-nonce", expires_at=long_expired)
    # Add a fresh nonce; the stale one should be GC'd in the same call.
    fresh_expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    agent_session.record_seen_nonce(memory_root, op_uuid, "fresh-nonce", expires_at=fresh_expires)
    assert agent_session.is_nonce_seen(memory_root, op_uuid, "fresh-nonce")
    assert not agent_session.is_nonce_seen(memory_root, op_uuid, "stale-nonce"), "expired nonce should have been GC'd"


# ---------- end-to-end happy path (requires gpg + writable HOME) ----------


def _gpg_available() -> bool:
    try:
        crypto.gpg_version()
        return True
    except crypto.CryptoError:
        return False


pytestmark_gpg = pytest.mark.skipif(
    not (_gpg_available() and os.environ.get("MEMFORGE_TEST_GPG") == "1"),
    reason="end-to-end gpg tests require MEMFORGE_TEST_GPG=1 (touches GPG keyring)",
)


def _short_sandbox():
    """macOS gpg-agent sockets have a 104-char path limit; pytest tmp_path is too long.

    Returns a freshly-created sandbox directory under /tmp with a short name.
    """
    import tempfile

    return Path(tempfile.mkdtemp(prefix="mfg", dir="/tmp"))


@pytestmark_gpg
def test_e2e_init_operator_then_init_store(monkeypatch):
    sandbox = _short_sandbox()
    sandbox_home = sandbox / "h"
    sandbox_home.mkdir()
    monkeypatch.setenv("GNUPGHOME", str(sandbox_home / "gpg"))
    (sandbox_home / "gpg").mkdir(mode=0o700)
    monkeypatch.setattr(identity, "OPERATOR_IDENTITY_PATH", sandbox_home / "mf" / "operator-identity.yaml")
    monkeypatch.setattr(identity, "RECOVERY_SECRET_PATH", sandbox_home / "mf" / "recovery-secret.bin")
    monkeypatch.setattr(identity, "PER_USER_CONFIG_PATH", sandbox_home / "mf" / "config.yaml")
    fpr = crypto.gpg_gen_key_batch(name_real="MemForge Test", name_email="test@memforge.test")
    op_uuid = identity.generate_uuidv7()
    identity.save_operator_identity(
        operator_uuid=op_uuid,
        operator_name="MemForge Test",
        key_fingerprint=fpr,
    )
    loaded = identity.load_operator_identity()
    assert loaded["operator_uuid"] == op_uuid

    # init-store happy path.
    memory_root = sandbox / "store"
    memory_root.mkdir()
    pub_b64 = crypto.gpg_export_public_key(fpr)
    reg = registry.init_registry(
        operator_uuid=op_uuid,
        operator_name="MemForge Test",
        key_id=fpr,
        algo="gpg-ed25519",
        public_material_b64=pub_b64,
    )
    registry.sign_and_save(reg, memory_root, signer_uuid=op_uuid, signer_fingerprint=fpr)

    # Verify round-trips.
    loaded_reg = registry.load_registry(memory_root, verify_signature=True)
    assert loaded_reg["operators"][0]["operator_uuid"] == op_uuid

    # Build + verify a revoke body.
    msg, body = revocation.build_revoke_body(
        key_id="AAA0000",
        reason="testing revocation",
        revoked_by_uuid=op_uuid,
        signer_fingerprint=fpr,
    )
    assert msg.startswith(revocation.REVOKE_PREFIX)
    assert revocation.verify_revoke_body(body, expected_signer_fingerprint=fpr)


@pytestmark_gpg
def test_e2e_attest_agent_round_trip(monkeypatch):
    sandbox = _short_sandbox()
    monkeypatch.setenv("GNUPGHOME", str(sandbox / "gpg"))
    (sandbox / "gpg").mkdir(mode=0o700)
    fpr = crypto.gpg_gen_key_batch(name_real="MemForge Op", name_email="op@memforge.test")
    agent_fpr = crypto.gpg_gen_key_batch(name_real="MemForge Agent", name_email="agent@memforge.test")
    op_uuid = identity.generate_uuidv7()
    memory_root = sandbox / "store"
    memory_root.mkdir()
    record = agent_session.build_attestation(
        operator_uuid=op_uuid,
        agent_pubkey_b64=crypto.gpg_export_public_key(agent_fpr),
        agent_pubkey_algo="gpg-ed25519",
        capability_memory_roots=[memory_root],
        adapter_prefix="cc",
        signer_fingerprint=fpr,
    )
    path = agent_session.save_attestation(memory_root, record)
    loaded = agent_session.load_attestation(memory_root, record["agent_session_id"])
    assert loaded["operator_uuid"] == op_uuid
    assert agent_session.verify_attestation(loaded, signer_fingerprint=fpr)
    agent_session.check_not_expired(loaded)
    # Scope check passes for the configured memory_root.
    agent_session.check_scope(loaded, write_path=memory_root / "memory.md", operation="write")
    with pytest.raises(agent_session.AttestationError):
        agent_session.check_scope(loaded, write_path=Path("/etc/passwd"), operation="write")

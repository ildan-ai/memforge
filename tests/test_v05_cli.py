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


def test_security_sddl_parser_extracts_sids():
    """v0.5.3: _sddl_to_sids handles raw SIDs + SDDL aliases."""
    from memforge._security import _sddl_to_sids

    # Mixed: raw SID + WD alias (Everyone) + AU alias (Authenticated Users).
    sddl = 'somepath "D:PAI(A;;FA;;;S-1-5-21-111-1000)(A;;FA;;;WD)(A;;FA;;;AU)"'
    sids = _sddl_to_sids(sddl)
    assert "S-1-5-21-111-1000" in sids
    assert "S-1-1-0" in sids  # WD resolved
    assert "S-1-5-11" in sids  # AU resolved


def test_security_windows_acl_rejects_forbidden_sid(monkeypatch):
    """v0.5.3: SID-based ACL check rejects forbidden well-known SIDs."""
    from memforge import _security as sec
    import subprocess as sp

    monkeypatch.setattr(sec, "IS_WINDOWS", True)
    sec.__CURRENT_USER_SID_CACHE = None
    monkeypatch.setattr(sec, "current_owner_label", lambda: "TESTDOMAIN\\testuser")

    OWNER_SID = "S-1-5-21-111-222-333-1000"

    # Simulate icacls /save writing an SDDL with Everyone (WD) ACE alongside the owner.
    fake_sddl = f'somepath "D:PAI(A;;FA;;;{OWNER_SID})(A;;FA;;;WD)"'

    def fake_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        class P:
            returncode = 0
            stdout = ""
            stderr = ""
        p = P()
        if cmd and cmd[0] == "whoami":
            p.stdout = f'"TESTDOMAIN\\\\testuser","{OWNER_SID}"\n'
        return p

    monkeypatch.setattr(sp, "run", fake_run)
    # Mock the tempfile-based icacls /save flow by short-circuiting the
    # file read: monkeypatch builtins.open inside _security to return the
    # fake SDDL when icacls would have written it.
    import builtins
    real_open = builtins.open
    def fake_open(path, *args, **kwargs):
        if str(path).endswith(".acl"):
            from io import BytesIO
            # icacls /save writes UTF-16 LE with BOM; mock matches.
            return BytesIO(fake_sddl.encode("utf-16"))
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", fake_open)
    import pathlib
    monkeypatch.setattr(pathlib.Path, "exists", lambda self: True)
    with pytest.raises(sec.SecurityError, match=r"S-1-1-0|forbidden SID"):
        sec.verify_owner_restricted(pathlib.Path("C:\\Users\\testuser\\.memforge\\identity.yaml"))


def test_security_windows_acl_accepts_owner_only(monkeypatch):
    """v0.5.3: SID-based ACL check accepts when only the owner's SID is present."""
    from memforge import _security as sec
    import subprocess as sp

    monkeypatch.setattr(sec, "IS_WINDOWS", True)
    sec.__CURRENT_USER_SID_CACHE = None
    monkeypatch.setattr(sec, "current_owner_label", lambda: "TESTDOMAIN\\testuser")

    OWNER_SID = "S-1-5-21-111-222-333-1000"
    fake_sddl = f'somepath "D:PAI(A;;FA;;;{OWNER_SID})"'

    def fake_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        class P:
            returncode = 0
            stdout = ""
            stderr = ""
        p = P()
        if cmd and cmd[0] == "whoami":
            p.stdout = f'"TESTDOMAIN\\\\testuser","{OWNER_SID}"\n'
        return p

    monkeypatch.setattr(sp, "run", fake_run)
    import builtins
    real_open = builtins.open
    def fake_open(path, *args, **kwargs):
        if str(path).endswith(".acl"):
            from io import BytesIO
            # icacls /save writes UTF-16 LE with BOM; mock matches.
            return BytesIO(fake_sddl.encode("utf-16"))
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", fake_open)
    import pathlib
    monkeypatch.setattr(pathlib.Path, "exists", lambda self: True)
    # Should not raise.
    sec.verify_owner_restricted(pathlib.Path("C:\\Users\\testuser\\.memforge\\identity.yaml"))


def test_security_windows_acl_rejects_inheritance_enabled(monkeypatch):
    """v0.5.3: SDDL without PAI flag (inheritance enabled) is rejected."""
    from memforge import _security as sec
    import subprocess as sp

    monkeypatch.setattr(sec, "IS_WINDOWS", True)
    sec.__CURRENT_USER_SID_CACHE = None
    monkeypatch.setattr(sec, "current_owner_label", lambda: "TESTDOMAIN\\testuser")

    OWNER_SID = "S-1-5-21-111-222-333-1000"
    # Note: SDDL omits PAI/P flag -> inheritance is enabled.
    fake_sddl = f'somepath "D:(A;;FA;;;{OWNER_SID})"'

    def fake_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        class P:
            returncode = 0
            stdout = ""
            stderr = ""
        p = P()
        if cmd and cmd[0] == "whoami":
            p.stdout = f'"TESTDOMAIN\\\\testuser","{OWNER_SID}"\n'
        return p

    monkeypatch.setattr(sp, "run", fake_run)
    import builtins
    real_open = builtins.open
    def fake_open(path, *args, **kwargs):
        if str(path).endswith(".acl"):
            from io import BytesIO
            return BytesIO(fake_sddl.encode("utf-16"))
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", fake_open)
    import pathlib
    monkeypatch.setattr(pathlib.Path, "exists", lambda self: True)
    with pytest.raises(sec.SecurityError, match=r"inheritance disabled|PAI"):
        sec.verify_owner_restricted(pathlib.Path("C:\\Users\\testuser\\.memforge\\identity.yaml"))


def test_revocation_walk_injection_safe(tmp_path):
    """v0.5.3 BLOCKER closure: malicious commit body cannot inject fake revocation records.

    The v0.5.2 parser used `git log --format=%H%x00%B%x00END%x00` which mixed
    framing bytes with attacker-controllable body content. Git itself rejects
    NUL bytes in commit messages (saving v0.5.2 from the specific NUL-injection
    attack), but the framing-confusion CLASS remained: any commit body that
    LOOKED like a `memforge: revoke <key>` prefix could be parsed as a record
    if it appeared after a framing boundary in another commit's body.

    The v0.5.3 two-pass design fetches each commit body in isolation, so a
    commit whose body MENTIONS `memforge: revoke FAKE-KEY` (but does NOT
    start with that prefix) is correctly ignored.
    """
    import subprocess as sp

    sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    # Land a commit whose body mentions a revoke prefix internally but does NOT
    # start with one. v0.5.3 parse_revoke_commit_body only matches commits
    # whose FIRST line starts with `memforge: revoke `, so this body is safely
    # ignored.
    malicious_msg = (
        "chore: refactor module\n"
        "\n"
        "This refactor incidentally exposes a parsing surface. Here is what a\n"
        "fake revoke commit body might look like if it were a real one:\n"
        "\n"
        "memforge: revoke BOGUS-KEY\n"
        "\n"
        "key_id: BOGUS-KEY\n"
        "revoked_at: 2020-01-01T00:00:00Z\n"
        "reason: this should not be honored\n"
        "revoked_by: attacker-uuid\n"
        "revocation_uid: fake-uuid\n"
    )
    sp.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", malicious_msg],
        check=True,
        capture_output=True,
    )
    rev_set = revocation.walk_revocation_set(tmp_path)
    assert "BOGUS-KEY" not in rev_set, (
        "v0.5.3 parser MUST NOT honor revoke-prefix text embedded in another commit; "
        f"got rev_set={rev_set}"
    )


def test_registry_key_is_in_cooldown_within_window():
    """v0.5.3: registry-layer cool-down recognizes future expiry timestamps."""
    from datetime import datetime, timedelta, timezone
    from memforge import registry as reg_mod
    future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat().replace("+00:00", "Z")
    reg = {
        "operators": [
            {
                "operator_uuid": "op-1",
                "public_keys": [
                    {"key_id": "FPR-A", "status": "active"},
                    {"key_id": "FPR-B", "status": "active", "rotation_cooldown_expires_at": future},
                ],
            }
        ]
    }
    assert reg_mod.key_is_in_cooldown(reg, "FPR-B") is True
    assert reg_mod.key_is_in_cooldown(reg, "FPR-A") is False


def test_registry_key_is_in_cooldown_after_expiry():
    """v0.5.3: keys outside cool-down are honored."""
    from datetime import datetime, timedelta, timezone
    from memforge import registry as reg_mod
    past = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat().replace("+00:00", "Z")
    reg = {
        "operators": [
            {
                "operator_uuid": "op-1",
                "public_keys": [
                    {"key_id": "FPR-B", "status": "active", "rotation_cooldown_expires_at": past},
                ],
            }
        ]
    }
    assert reg_mod.key_is_in_cooldown(reg, "FPR-B") is False


def test_registry_verify_signing_key_rejects_unlisted():
    """v0.5.3: signing key not in registry fails registry-layer check."""
    from memforge import registry as reg_mod
    reg = {"operators": [{"operator_uuid": "op-1", "public_keys": [{"key_id": "FPR-A"}]}]}
    with pytest.raises(reg_mod.RegistryError, match=r"not present"):
        reg_mod.verify_signing_key_acceptable(reg, "FPR-OTHER")


def test_registry_verify_signing_key_rejects_in_cooldown():
    """v0.5.3: signing key in cool-down rejected regardless of CLI caller."""
    from datetime import datetime, timedelta, timezone
    from memforge import registry as reg_mod
    future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat().replace("+00:00", "Z")
    reg = {
        "operators": [
            {
                "operator_uuid": "op-1",
                "public_keys": [{"key_id": "FPR-B", "rotation_cooldown_expires_at": future}],
            }
        ]
    }
    with pytest.raises(reg_mod.RegistryError, match=r"rotation cool-down"):
        reg_mod.verify_signing_key_acceptable(reg, "FPR-B")


def test_registry_verify_signing_key_rejects_superseded_key():
    """cooldown-active-01: a superseded KEY is not acceptable as a signing key.

    Membership + cool-down alone is insufficient: a key rotated/removed out of
    active status (no cool-down) must still be rejected by the natural seam an
    adapter calls before honoring a write signature.
    """
    from memforge import registry as reg_mod
    reg = {
        "operators": [
            {
                "operator_uuid": "op-1",
                "status": "active",
                "public_keys": [
                    {"key_id": "FPR-OLD", "status": "superseded"},
                    {"key_id": "FPR-NEW", "status": "active"},
                ],
            }
        ]
    }
    # Active key passes; superseded key is rejected even with no cool-down set.
    reg_mod.verify_signing_key_acceptable(reg, "FPR-NEW")
    with pytest.raises(reg_mod.RegistryError, match=r"not active"):
        reg_mod.verify_signing_key_acceptable(reg, "FPR-OLD")


def test_registry_verify_signing_key_rejects_superseded_operator():
    """cooldown-active-01: a key under a superseded OPERATOR is rejected."""
    from memforge import registry as reg_mod
    reg = {
        "operators": [
            {
                "operator_uuid": "op-1",
                "status": "superseded",
                "public_keys": [{"key_id": "FPR-A", "status": "active"}],
            }
        ]
    }
    with pytest.raises(reg_mod.RegistryError, match=r"not active"):
        reg_mod.verify_signing_key_acceptable(reg, "FPR-A")


def test_revoke_signer_active_at_authoring_time():
    """revoke-signer-01: a superseded key validates a revocation it signed WHILE
    active, but NOT a revocation authored after it was superseded.
    """
    from memforge import revocation as rev_mod

    signer_op = {
        "operator_uuid": "op-1",
        "public_keys": [
            {
                "key_id": "OLD",
                "status": "superseded",
                "chain_index": 0,
                "introduced_at": "2020-01-01T00:00:00Z",
            },
            {
                "key_id": "NEW",
                "status": "active",
                "chain_index": 1,
                "introduced_at": "2021-06-01T00:00:00Z",
            },
        ],
    }
    old_key = signer_op["public_keys"][0]
    new_key = signer_op["public_keys"][1]

    # Currently-active key is always acceptable.
    assert rev_mod._key_was_active_at(new_key, signer_op, "2021-06-02T00:00:00Z")

    # OLD key: a revocation authored BEFORE supersession (2021-06-01) is honored.
    assert rev_mod._key_was_active_at(old_key, signer_op, "2021-01-01T00:00:00Z")
    # OLD key: a revocation authored AFTER supersession is rejected (the attack).
    assert not rev_mod._key_was_active_at(old_key, signer_op, "2021-12-01T00:00:00Z")
    # Fail closed on an unparseable author-date for a superseded key.
    assert not rev_mod._key_was_active_at(old_key, signer_op, "")


def test_registry_cooldown_floor_enforced():
    """v0.5.3: cool-down hours below 1h floor raises."""
    from memforge import registry as reg_mod
    with pytest.raises(reg_mod.RegistryError, match=r"below floor"):
        reg_mod._compute_cooldown_expiry(hours=0.5)


def test_revocation_walk_bounded_by_max_commits(tmp_path):
    """v0.5.3: walk_revocation_set aborts when commit cap exceeded."""
    import subprocess as sp
    sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    # Create 10 empty commits with non-revoke prefixes so walk has work to do.
    for i in range(10):
        sp.run(
            ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", f"chore: noop {i}"],
            check=True,
            capture_output=True,
        )
    with pytest.raises(revocation.RevocationError, match=r"commit cap exceeded"):
        revocation.walk_revocation_set(tmp_path, max_commits=3)


def test_revocation_walk_bounded_by_max_bytes(tmp_path):
    """v0.5.3: walk_revocation_set aborts when byte cap exceeded."""
    import subprocess as sp
    sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    sp.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "chore: noop"],
        check=True,
        capture_output=True,
    )
    with pytest.raises(revocation.RevocationError, match=r"byte cap exceeded"):
        revocation.walk_revocation_set(tmp_path, max_bytes=10)


def test_revocation_walk_handles_empty_history(tmp_path):
    """v0.5.3: walk_revocation_set on a repo with no revoke commits returns empty dict."""
    import subprocess as sp
    sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    sp.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "chore: init"],
        check=True,
        capture_output=True,
    )
    assert revocation.walk_revocation_set(tmp_path) == {}


def test_secure_read_text_refuses_symlink(tmp_path):
    """v0.5.3: secure_read_text refuses to follow a symlink on POSIX."""
    from memforge._security import IS_WINDOWS, SecurityError, restrict_dir_to_owner, restrict_file_to_owner, secure_read_text
    if IS_WINDOWS:
        pytest.skip("O_NOFOLLOW is POSIX-specific")
    target = tmp_path / "real.yaml"
    target.write_text("data: ok\n", encoding="utf-8")
    restrict_dir_to_owner(target.parent)
    restrict_file_to_owner(target)
    link = tmp_path / "link.yaml"
    link.symlink_to(target)
    with pytest.raises(SecurityError):
        secure_read_text(link)


def test_secure_read_text_succeeds_on_proper_file(tmp_path):
    """v0.5.3: secure_read_text reads correctly when file is owner-restricted via the platform primitives."""
    from memforge._security import restrict_dir_to_owner, restrict_file_to_owner, secure_read_text
    target = tmp_path / "ok.yaml"
    target.write_text("hello\n", encoding="utf-8")
    restrict_dir_to_owner(target.parent)
    restrict_file_to_owner(target)
    assert secure_read_text(target) == "hello\n"


def test_secure_read_text_rejects_relaxed_mode(tmp_path):
    """v0.5.3: secure_read_text fails closed when mode != expected."""
    from memforge._security import IS_WINDOWS, SecurityError, restrict_dir_to_owner, secure_read_text
    if IS_WINDOWS:
        pytest.skip("POSIX mode check; Windows uses ACLs (covered by SDDL parser tests)")
    target = tmp_path / "loose.yaml"
    target.write_text("data\n", encoding="utf-8")
    restrict_dir_to_owner(target.parent)
    target.chmod(0o644)  # group + others readable
    with pytest.raises(SecurityError, match=r"fd-mode"):
        secure_read_text(target)


def test_secure_read_text_rejects_relaxed_parent_dir(tmp_path):
    """sec-01 (regression): secure_read_text fails closed when the PARENT dir is
    more permissive than 0700, even when the file itself is correctly 0600.

    Root cause of the regression: the POSIX read path verified only the FILE fd
    mode/uid and never the parent directory, so an attestation / sender-sequence
    file that was 0600 inside a 0750/0770/0777 parent passed verification --
    defeating the spec's parent-0700 fail-closed requirement (the Windows branch
    already checked the parent). The fix adds a POSIX parent-dir 0700 + uid check.
    """
    from memforge._security import (
        IS_WINDOWS,
        SecurityError,
        restrict_dir_to_owner,
        restrict_file_to_owner,
        secure_read_text,
    )

    if IS_WINDOWS:
        pytest.skip("POSIX parent-mode check; Windows uses ACLs (parent already checked)")
    parent = tmp_path / "sub"
    target = parent / "secret.yaml"
    restrict_dir_to_owner(parent)  # 0700
    target.write_text("data: ok\n", encoding="utf-8")
    restrict_file_to_owner(target)  # 0600
    # Sanity: with a correct 0700 parent + 0600 file, the read succeeds.
    assert secure_read_text(target) == "data: ok\n"
    # Relax ONLY the parent to group/world-traversable; file stays 0600.
    parent.chmod(0o755)
    with pytest.raises(SecurityError, match=r"parent of|0o700|more permissive"):
        secure_read_text(target)


def test_secure_read_bytes_rejects_relaxed_parent_dir(tmp_path):
    """sec-01 (regression): same POSIX parent-dir 0700 check for secure_read_bytes."""
    from memforge._security import (
        IS_WINDOWS,
        SecurityError,
        restrict_dir_to_owner,
        restrict_file_to_owner,
        secure_read_bytes,
    )

    if IS_WINDOWS:
        pytest.skip("POSIX parent-mode check")
    parent = tmp_path / "sub"
    target = parent / "secret.bin"
    restrict_dir_to_owner(parent)
    target.write_bytes(b"\x00\x01")
    restrict_file_to_owner(target)
    assert secure_read_bytes(target) == b"\x00\x01"
    parent.chmod(0o777)
    with pytest.raises(SecurityError):
        secure_read_bytes(target)


def test_gpg_check_algo_allowlist_rejects_unknown_family():
    """algo-01: the gate is an allowlist (spec rule 1), not denylist-only.

    A non-denylisted, non-RSA, non-Ed25519 label (gpg-ed448, gpg-custom) must be
    REJECTED rather than passing silently as unvalidated metadata.
    """
    for bad in ("gpg-ed448", "gpg-custom", "ed448", "gpg-secp256k1"):
        with pytest.raises(crypto.CryptoError, match=r"allowlist|accepted-algo"):
            crypto.gpg_check_algo_accepted(bad)
    # Allowlisted labels still pass (including spec-floor RSA-3072).
    crypto.gpg_check_algo_accepted("gpg-ed25519")
    crypto.gpg_check_algo_accepted("gpg-rsa3072")
    crypto.gpg_check_algo_accepted("gpg-rsa4096")


def test_canonical_envelope_rejects_nfc_key_collision():
    """nfc-01 / sec-05: two distinct keys that normalize to the same NFC form must
    fail closed (CryptoError) rather than silently collapsing (last-write-wins),
    which would change what gets signed vs what the operator believes they signed.
    """
    # Precomposed "Å" (U+00C5) vs decomposed "A" + combining ring (U+030A).
    precomposed = "Åfield"
    decomposed = "Åfield"
    assert precomposed != decomposed
    with pytest.raises(crypto.CryptoError, match=r"key collision|collapse|NFC"):
        crypto.canonical_envelope({precomposed: 1, decomposed: 2})
    # A nested mapping collision is also caught.
    with pytest.raises(crypto.CryptoError):
        crypto.canonical_envelope({"outer": {precomposed: 1, decomposed: 2}})


def test_key_is_in_cooldown_rejects_unparseable_timestamp():
    """registry-03: cool-down comparator parses timestamps; fails closed on garbage."""
    from memforge import registry as reg_mod

    reg = {
        "operators": [
            {
                "operator_uuid": "op-1",
                "public_keys": [
                    {"key_id": "K", "status": "active", "rotation_cooldown_expires_at": "future"},
                ],
            }
        ]
    }
    with pytest.raises(reg_mod.RegistryError, match=r"unparseable"):
        reg_mod.key_is_in_cooldown(reg, "K")


def test_key_is_in_cooldown_timezone_form_insensitive():
    """registry-03: a +00:00-form at_time is compared correctly against a Z-form
    expiry (raw lexicographic compare would mis-sort '+' below 'Z').
    """
    from datetime import datetime, timedelta, timezone
    from memforge import registry as reg_mod

    expiry_z = (datetime.now(timezone.utc) + timedelta(hours=12)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    reg = {
        "operators": [
            {"operator_uuid": "op-1", "public_keys": [
                {"key_id": "K", "status": "active", "rotation_cooldown_expires_at": expiry_z}]},
        ]
    }
    # +00:00 form now, well before the expiry -> still in cool-down (True).
    now_offset = datetime.now(timezone.utc).replace(microsecond=0).isoformat()  # ...+00:00
    assert "+00:00" in now_offset
    assert reg_mod.key_is_in_cooldown(reg, "K", at_time=now_offset) is True


def test_key_is_in_cooldown_rederives_from_commit_author_date():
    """registry-02: when commit_author_date is supplied, the window is re-derived
    from author-date + rotation_cooldown_hours (spec anchor), not the build-time-
    frozen rotation_cooldown_expires_at.
    """
    from memforge import registry as reg_mod

    # Stored expiry is in the far past (as if built long before commit), but the
    # commit author-date is recent and the duration is 24h, so re-derivation
    # places us INSIDE the window.
    reg = {
        "operators": [
            {"operator_uuid": "op-1", "public_keys": [
                {
                    "key_id": "K",
                    "status": "active",
                    "rotation_cooldown_hours": 24,
                    "rotation_cooldown_expires_at": "2000-01-01T00:00:00Z",
                }
            ]},
        ]
    }
    # at_time just after author-date; author-date + 24h is well in the future.
    assert reg_mod.key_is_in_cooldown(
        reg, "K", at_time="2026-06-14T12:30:00Z", commit_author_date="2026-06-14T12:00:00Z"
    ) is True
    # at_time past author-date + 24h -> out of window.
    assert reg_mod.key_is_in_cooldown(
        reg, "K", at_time="2026-06-16T12:00:01Z", commit_author_date="2026-06-14T12:00:00Z"
    ) is False


def test_is_key_revoked_at_timezone_form_insensitive():
    """registry-03: is_key_revoked_at parses both operands; +00:00 vs Z compares right."""
    rev_set = {"K": {"revoked_at": "2026-06-14T12:00:00Z"}}
    # signing_time in +00:00 form, AFTER revoked_at -> revoked (True).
    assert revocation.is_key_revoked_at(rev_set, "K", "2026-06-14T12:00:01+00:00") is True
    # signing_time BEFORE revoked_at -> not revoked yet.
    assert revocation.is_key_revoked_at(rev_set, "K", "2026-06-14T11:59:59+00:00") is False
    # Unparseable signing_time -> fail closed (treat as revoked).
    assert revocation.is_key_revoked_at(rev_set, "K", "garbage") is True


def test_check_not_expired_timezone_form_insensitive():
    """registry-03: check_not_expired parses operands; +00:00 within window passes."""
    record = {
        "issued_at": "2026-06-14T12:00:00Z",
        "expires_at": "2026-06-15T12:00:00Z",
    }
    # signing_time in +00:00 form inside the window -> no raise.
    agent_session.check_not_expired(record, signing_time_iso="2026-06-14T18:00:00+00:00")
    # Before issued -> raise.
    with pytest.raises(agent_session.AttestationError):
        agent_session.check_not_expired(record, signing_time_iso="2026-06-14T11:00:00Z")
    # Unparseable -> fail closed.
    with pytest.raises(agent_session.AttestationError, match=r"unparseable"):
        agent_session.check_not_expired(record, signing_time_iso="not-a-time")


def test_should_publish_checkpoint_fails_closed_on_malformed_timestamp():
    """sender-seq-02: a malformed last-checkpoint timestamp fails closed (raise),
    instead of silently returning True (which masked tampering of signed state).
    """
    data = {
        "current_sequence": 5,
        "checkpoints": [{"sequence": 4, "timestamp": "not-an-iso-timestamp"}],
    }
    with pytest.raises(identity.IdentityError, match=r"corrupt or tampered|not parseable"):
        sender_sequence.should_publish_checkpoint(data)
    # Missing timestamp key also fails closed.
    data2 = {"current_sequence": 5, "checkpoints": [{"sequence": 4}]}
    with pytest.raises(identity.IdentityError):
        sender_sequence.should_publish_checkpoint(data2)


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


def test_claim_nonce_atomic_check_and_record(tmp_path):
    """nonce-replay-01: claim_nonce is the atomic check-AND-record replay defense.

    The first claim of a nonce returns True (admit); a second claim of the SAME
    nonce returns False (reject the replay). The check and the record happen
    under one lock, closing the TOCTOU gap that the lock-free is_nonce_seen read
    left open in a check-then-record-across-the-lock-gap caller.
    """
    from datetime import datetime, timedelta, timezone

    from memforge import agent_session

    op_uuid = identity.generate_uuidv7()
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    # First claim succeeds (newly recorded).
    assert agent_session.claim_nonce(tmp_path, op_uuid, "n1", expires_at=expires) is True
    # The nonce is now recorded.
    assert agent_session.is_nonce_seen(tmp_path, op_uuid, "n1")
    # A second claim of the same nonce is rejected as a replay.
    assert agent_session.claim_nonce(tmp_path, op_uuid, "n1", expires_at=expires) is False
    # A different nonce still claims fine.
    assert agent_session.claim_nonce(tmp_path, op_uuid, "n2", expires_at=expires) is True


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


# ---------- crypto trust-anchor hardening (pure-Python, no gpg) ----------


def test_gpg_check_algo_rejects_rsa_2048_labels():
    """crypto-03: numeric RSA floor catches every label a weak RSA key surfaces under."""
    # Both the bare GnuPG-algo-number label and the hyphenless rsa label.
    for bad in ("gpg-algo1", "gpg-rsa2048", "rsa-2048", "RSA2048", "gpg-rsa1024"):
        with pytest.raises(crypto.CryptoError):
            crypto.gpg_check_algo_accepted(bad)


def test_gpg_check_algo_accepts_strong_rsa_and_ed25519():
    """crypto-03: 3072+ RSA and Ed25519 pass the floor."""
    crypto.gpg_check_algo_accepted("gpg-rsa3072")
    crypto.gpg_check_algo_accepted("gpg-rsa4096")
    crypto.gpg_check_algo_accepted("gpg-ed25519")


def test_opreg_resolve_algo_uses_public_keyring_not_default(monkeypatch):
    """opreg-algo-01: operator-registry _resolve_algo resolves the REAL algo of an
    imported PUBLIC key (not in the secret keyring) instead of hardcoding
    gpg-ed25519, and fails closed when the algo cannot be classified.
    """
    from memforge import crypto
    from memforge.cli.v05 import operator_registry as opreg

    # Public RSA-4096 operator-B key: absent from secret keyring, resolvable via
    # the public keyring. Must record gpg-rsa4096, NOT a default gpg-ed25519.
    monkeypatch.setattr(crypto, "gpg_list_secret_keys", lambda: [])
    monkeypatch.setattr(crypto, "gpg_resolve_public_algo", lambda *a, **k: "gpg-rsa4096")
    assert opreg._resolve_algo("B" * 40) == "gpg-rsa4096"

    # Unclassifiable key -> fail closed (no default-stamp).
    monkeypatch.setattr(crypto, "gpg_resolve_public_algo", lambda *a, **k: None)
    with pytest.raises(opreg._FingerprintError, match=r"could not classify"):
        opreg._resolve_algo("C" * 40)


def test_resolve_signer_algo_fails_closed_when_unresolvable(monkeypatch):
    """agent-algo-fallback-01: _resolve_signer_algo raises AttestationError when
    the key cannot be resolved to a concrete algo (no silent gpg-ed25519 stamp).
    """
    from memforge import agent_session, crypto

    monkeypatch.setattr(crypto, "gpg_list_secret_keys", lambda: [])
    monkeypatch.setattr(crypto, "gpg_resolve_public_algo", lambda *a, **k: None)
    with pytest.raises(agent_session.AttestationError, match=r"could not resolve"):
        agent_session._resolve_signer_algo("F" * 40)


def test_resolve_signer_algo_uses_public_keyring_fallback(monkeypatch):
    """agent-algo-fallback-01: a key absent from the SECRET keyring but resolvable
    in the public keyring records its REAL algo, not a default.
    """
    from memforge import agent_session, crypto

    monkeypatch.setattr(crypto, "gpg_list_secret_keys", lambda: [])
    monkeypatch.setattr(crypto, "gpg_resolve_public_algo", lambda *a, **k: "gpg-rsa4096")
    assert agent_session._resolve_signer_algo("A" * 40) == "gpg-rsa4096"


def test_rotate_next_chain_index_absent_operator_returns_zero():
    """rotate-chainidx-01: absent operator -> 0 (mirrors add_rotated_key's
    max(..., default=-1)+1), so the uid suffix never disagrees with the stamped
    chain_index.
    """
    from memforge.cli.v05 import rotate_key
    reg = {"operators": [{"operator_uuid": "op-1", "public_keys": [{"chain_index": 0}]}]}
    # Present operator with one chain-0 key -> next index 1.
    assert rotate_key._next_chain_index(reg, "op-1") == 1
    # Absent operator -> 0 (was 1).
    assert rotate_key._next_chain_index(reg, "op-missing") == 0


def test_assert_recovery_preconditions_composes_both_checks(monkeypatch):
    """recovery-startup-01: the composed gate fails closed unless BOTH the
    secret-integrity check AND the backup-ack check pass.
    """
    from memforge import recovery as rec_mod

    calls = []

    def _ok_integrity(registry, *, operator_uuid):
        calls.append("integrity")

    def _ok_ack():
        calls.append("ack")

    monkeypatch.setattr(rec_mod, "verify_recovery_secret_integrity", _ok_integrity)
    monkeypatch.setattr(rec_mod, "check_backup_acknowledged", _ok_ack)
    rec_mod.assert_recovery_preconditions({"operators": []}, operator_uuid="op-1")
    assert calls == ["integrity", "ack"]

    # If integrity fails, the gate raises and never reaches the ack check.
    def _bad_integrity(registry, *, operator_uuid):
        raise rec_mod.RecoveryError("tampered")

    monkeypatch.setattr(rec_mod, "verify_recovery_secret_integrity", _bad_integrity)
    with pytest.raises(rec_mod.RecoveryError, match=r"tampered"):
        rec_mod.assert_recovery_preconditions({"operators": []}, operator_uuid="op-1")


def test_gpg_check_algo_allow_path_is_anchored():
    """crypto-01: the RSA ALLOW path is anchored; a label that merely CONTAINS an
    rsa<N>=3072 substring must NOT pass (the old re.search let it through).
    """
    for smuggled in (
        "malicious-rsa4096",
        "rsa99999garbage",
        "x-rsa4096-y",
        "gpg-rsa4096-backdoor",
        "rsa4096 ; rm -rf",
    ):
        with pytest.raises(crypto.CryptoError):
            crypto.gpg_check_algo_accepted(smuggled)
    # The canonical forms still pass.
    crypto.gpg_check_algo_accepted("gpg-rsa4096")
    crypto.gpg_check_algo_accepted("rsa4096")


def test_gpg_check_algo_rejects_oversized_label_below_floor_substring():
    """crypto-01: a sub-floor RSA size that is a substring of an allowed label
    (e.g. embedded) is rejected rather than accepted via a >=3072 substring.
    """
    with pytest.raises(crypto.CryptoError):
        crypto.gpg_check_algo_accepted("gpg-rsa2048-but-mentions-rsa4096")


def test_gpg_verify_detached_requires_fingerprint_pin():
    """crypto-04: verification with no identity pin fails closed (no GOODSIG-only accept)."""
    # Empty / falsy expected_fingerprint must return False without touching gpg.
    assert crypto.gpg_verify_detached(b"data", signature_b64="", expected_fingerprint="") is False
    assert crypto.gpg_verify_detached(b"data", signature_b64="", expected_fingerprint=None) is False


def test_normalize_fpr_exact_match_semantics():
    """crypto-01 / verify-02: exact full-fingerprint equality, no substring acceptance."""
    full = "DEADBEEF1234567890ABCDEF1234567890ABCDEF"
    short = "1234567890ABCDEF"  # a substring of `full`
    # The new comparator normalizes (strip spaces, upper) but does NOT do
    # substring/startswith: a short id is NOT equal to a full fingerprint.
    assert crypto._normalize_fpr(full) == crypto._normalize_fpr(full.lower())
    assert crypto._normalize_fpr("DEAD BEEF 1234") == "DEADBEEF1234"
    assert crypto._normalize_fpr(short) != crypto._normalize_fpr(full)


def test_mint_agent_session_id_suffix_len_envelope():
    """id-02: suffix_len knob honored + bounded to the validated 8-16 envelope."""
    sid8 = identity.mint_agent_session_id("cc", suffix_len=8)
    sid16 = identity.mint_agent_session_id("cc", suffix_len=16)
    identity.validate_agent_session_id(sid8)
    identity.validate_agent_session_id(sid16)
    assert len(sid8.rsplit("-", 1)[1]) == 8
    assert len(sid16.rsplit("-", 1)[1]) == 16
    for bad in (7, 17, 0):
        with pytest.raises(identity.IdentityError):
            identity.mint_agent_session_id("cc", suffix_len=bad)


def test_registry_read_rotation_cooldown_hours_from_config(tmp_path):
    """registry-02: cool-down hours read from .memforge/config.yaml; default when absent."""
    from memforge import registry as reg_mod

    # No config -> default.
    assert reg_mod.read_rotation_cooldown_hours(tmp_path) == float(reg_mod.DEFAULT_ROTATION_COOLDOWN_HOURS)
    # Configured value honored.
    cfg = tmp_path / reg_mod.REGISTRY_DIRNAME / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("identity:\n  rotation_cooldown_hours: 72\n", encoding="utf-8")
    assert reg_mod.read_rotation_cooldown_hours(tmp_path) == 72.0
    # Malformed value falls back to default rather than crashing.
    cfg.write_text("identity:\n  rotation_cooldown_hours: not-a-number\n", encoding="utf-8")
    assert reg_mod.read_rotation_cooldown_hours(tmp_path) == float(reg_mod.DEFAULT_ROTATION_COOLDOWN_HOURS)


def test_add_rotated_key_uses_configured_cooldown(tmp_path):
    """registry-02: add_rotated_key stamps the configured cool-down duration + expiry."""
    from datetime import datetime, timezone
    from memforge import registry as reg_mod

    cfg = tmp_path / reg_mod.REGISTRY_DIRNAME / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("identity:\n  rotation_cooldown_hours: 48\n", encoding="utf-8")
    reg = {
        "operators": [
            {
                "operator_uuid": "op-1",
                "status": "active",
                "public_keys": [
                    {"key_id": "OLD", "algo": "gpg-ed25519", "chain_index": 0, "status": "active"}
                ],
            }
        ]
    }
    reg = reg_mod.add_rotated_key(
        reg,
        operator_uuid="op-1",
        new_key_id="NEW",
        new_algo="gpg-ed25519",
        new_public_material_b64="",
        cross_signature_by_old="",
        cross_signature_by_new="",
        memory_root=tmp_path,
    )
    new_key = reg["operators"][0]["public_keys"][1]
    assert new_key["rotation_cooldown_hours"] == 48.0
    # Expiry is in the future (sanity: well past the 24h default).
    expiry = new_key["rotation_cooldown_expires_at"]
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    assert expiry > now
    assert reg_mod.key_is_in_cooldown(reg, "NEW") is True


# ---------- BLOCKER closures (require gpg) ----------


def _seed_registry_for(memory_root, op_uuid, fpr, name="MemForge Test"):
    pub_b64 = crypto.gpg_export_public_key(fpr)
    return registry.init_registry(
        operator_uuid=op_uuid,
        operator_name=name,
        key_id=fpr,
        algo="gpg-ed25519",
        public_material_b64=pub_b64,
    )


@pytestmark_gpg
def test_registry_loads_after_rotation_signed_by_new_key(monkeypatch):
    """BLOCKER registry-01: a registry signed by the NEW key during a two-active-key
    cool-down still load-verifies, because the signature block records
    signing_key_id and the loader resolves the exact signing key.
    """
    sandbox = _short_sandbox()
    monkeypatch.setenv("GNUPGHOME", str(sandbox / "gpg"))
    (sandbox / "gpg").mkdir(mode=0o700)
    old_fpr = crypto.gpg_gen_key_batch(name_real="MemForge Test", name_email="old@memforge.test")
    new_fpr = crypto.gpg_gen_key_batch(name_real="MemForge Test", name_email="new@memforge.test")
    op_uuid = identity.generate_uuidv7()
    memory_root = sandbox / "store"
    memory_root.mkdir()

    reg = _seed_registry_for(memory_root, op_uuid, old_fpr)
    # Rotate: append the new key (both old + new now active = cool-down state).
    reg = registry.add_rotated_key(
        reg,
        operator_uuid=op_uuid,
        new_key_id=new_fpr,
        new_algo="gpg-ed25519",
        new_public_material_b64=crypto.gpg_export_public_key(new_fpr),
        cross_signature_by_old="",
        cross_signature_by_new="",
    )
    active = [k for k in reg["operators"][0]["public_keys"] if k.get("status", "active") == "active"]
    assert len(active) == 2, "rotation cool-down should leave two active keys"

    # Sign the registry with the NEW key (simulates operator-registry add /
    # recovery-init during the cool-down, which sign with identity.key_fingerprint
    # = the new key). Pre-fix this bricked the store: the loader pinned the
    # first-active (old) key as the expected verifier.
    registry.sign_and_save(reg, memory_root, signer_uuid=op_uuid, signer_fingerprint=new_fpr)
    assert reg["registry_signature"]["signing_key_id"] == new_fpr

    loaded = registry.load_registry(memory_root, verify_signature=True)
    assert loaded["operators"][0]["operator_uuid"] == op_uuid


@pytestmark_gpg
def test_sign_and_save_refuses_superseded_signer_key(monkeypatch):
    """registry-01 (regression): sign_and_save MUST fail closed BEFORE writing if
    the resolved signer key entry is 'superseded'.

    Root cause: remove_operator supersedes the operator AND its keys; the CLI
    then signed with that now-superseded key (sign_and_save matched by key_id
    only, not status), and the next load_registry failed closed forever (bricked
    store). The fix makes sign_and_save mirror the loader's superseded-key check
    before producing the signature / writing.
    """
    sandbox = _short_sandbox()
    monkeypatch.setenv("GNUPGHOME", str(sandbox / "gpg"))
    (sandbox / "gpg").mkdir(mode=0o700)
    fpr = crypto.gpg_gen_key_batch(name_real="Brick", name_email="brick@memforge.test")
    op_uuid = identity.generate_uuidv7()
    memory_root = sandbox / "store"
    memory_root.mkdir()
    reg = _seed_registry_for(memory_root, op_uuid, fpr)
    # Supersede the operator + its key (what remove_operator does).
    registry.remove_operator(reg, operator_uuid=op_uuid)
    with pytest.raises(registry.RegistryError, match=r"superseded"):
        registry.sign_and_save(reg, memory_root, signer_uuid=op_uuid, signer_fingerprint=fpr)
    # The bricking write never happened: the registry file is absent (or, if a
    # prior good copy existed, unchanged). Here it was never written.
    assert not registry.registry_path(memory_root).exists()


@pytestmark_gpg
def test_registry_backward_compat_loads_without_signing_key_id(monkeypatch):
    """registry-01 back-compat: a registry written before signing_key_id existed
    (single active key) still loads via the first-active fallback.
    """
    sandbox = _short_sandbox()
    monkeypatch.setenv("GNUPGHOME", str(sandbox / "gpg"))
    (sandbox / "gpg").mkdir(mode=0o700)
    fpr = crypto.gpg_gen_key_batch(name_real="MemForge Test", name_email="bc@memforge.test")
    op_uuid = identity.generate_uuidv7()
    memory_root = sandbox / "store"
    memory_root.mkdir()
    reg = _seed_registry_for(memory_root, op_uuid, fpr)
    registry.sign_and_save(reg, memory_root, signer_uuid=op_uuid, signer_fingerprint=fpr)

    # Simulate a legacy registry: strip signing_key_id from the persisted file.
    path = registry.registry_path(memory_root)
    import yaml as _yaml
    with open(path, "r", encoding="utf-8") as f:
        data = _yaml.safe_load(f)
    data["registry_signature"].pop("signing_key_id", None)
    with open(path, "w", encoding="utf-8") as f:
        _yaml.safe_dump(data, f, sort_keys=False)

    loaded = registry.load_registry(memory_root, verify_signature=True)
    assert loaded["operators"][0]["operator_uuid"] == op_uuid


@pytestmark_gpg
def test_verify_rejects_ambient_keyring_only_signature(monkeypatch):
    """BLOCKER verify-01: a signature that would verify only because an unrelated
    key sits in the ambient keyring is REJECTED, because the trust root is the
    REGISTERED public_material, not the ambient keyring.

    Construction: registry declares operator A's key + material, but the
    registry is actually signed by key B (an unrelated key that lives in the
    ambient keyring). Pre-fix, gpg would VALIDSIG against B from the ambient
    keyring and the only guard was a fingerprint string check. With the
    ephemeral-keyring trust root, B's signature cannot validate against A's
    imported material, so load fails closed.
    """
    sandbox = _short_sandbox()
    monkeypatch.setenv("GNUPGHOME", str(sandbox / "gpg"))
    (sandbox / "gpg").mkdir(mode=0o700)
    fpr_a = crypto.gpg_gen_key_batch(name_real="Operator A", name_email="a@memforge.test")
    fpr_b = crypto.gpg_gen_key_batch(name_real="Unrelated B", name_email="b@memforge.test")
    op_uuid = identity.generate_uuidv7()
    memory_root = sandbox / "store"
    memory_root.mkdir()

    # Registry declares A's key + A's material, but we sign with B and hand-set
    # the signature block to claim A's fingerprint as the signer.
    reg = _seed_registry_for(memory_root, op_uuid, fpr_a, name="Operator A")
    payload = registry._canonical_for_signature(reg)
    sig_by_b = crypto.gpg_sign_detached(payload, fingerprint=fpr_b)
    reg["registry_signature"] = {
        "algo": "gpg-ed25519",
        "signing_uuid": op_uuid,
        "signing_key_id": fpr_a,  # claims A signed...
        "signing_time": identity.now_iso(),
        "value": sig_by_b,        # ...but B actually signed
    }
    path = registry.registry_path(memory_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    import yaml as _yaml
    with open(path, "w", encoding="utf-8") as f:
        _yaml.safe_dump(reg, f, sort_keys=False)

    # B is in the ambient keyring (we just generated it), so the OLD code would
    # have accepted. The hardened loader imports A's material into an ephemeral
    # keyring and B's signature does not validate against A's key.
    with pytest.raises(registry.RegistryError, match=r"did not verify"):
        registry.load_registry(memory_root, verify_signature=True)


@pytestmark_gpg
def test_verify_material_binding_mismatch_rejected(monkeypatch):
    """verify-01 binding: if the registered material does not resolve to the
    registered fingerprint, verification fails closed (material/fingerprint
    disagreement is rejected even before signature validation).
    """
    sandbox = _short_sandbox()
    monkeypatch.setenv("GNUPGHOME", str(sandbox / "gpg"))
    (sandbox / "gpg").mkdir(mode=0o700)
    fpr_a = crypto.gpg_gen_key_batch(name_real="Operator A", name_email="a2@memforge.test")
    fpr_b = crypto.gpg_gen_key_batch(name_real="Operator B", name_email="b2@memforge.test")
    data = b"trust-anchor binding payload"
    sig_by_a = crypto.gpg_sign_detached(data, fingerprint=fpr_a)
    # Pin to A's fingerprint but hand over B's material: binding assertion fails.
    assert crypto.gpg_verify_detached(
        data,
        signature_b64=sig_by_a,
        expected_fingerprint=fpr_a,
        registered_public_material_b64=crypto.gpg_export_public_key(fpr_b),
    ) is False
    # Sanity: correct material + correct signer verifies True.
    assert crypto.gpg_verify_detached(
        data,
        signature_b64=sig_by_a,
        expected_fingerprint=fpr_a,
        registered_public_material_b64=crypto.gpg_export_public_key(fpr_a),
    ) is True


@pytestmark_gpg
def test_verify_exact_fingerprint_pin_rejects_different_key(monkeypatch):
    """crypto-01 / verify-02: a valid signature from key A is rejected when the
    pin is key B, under exact full-fingerprint comparison (no substring accept).
    """
    sandbox = _short_sandbox()
    monkeypatch.setenv("GNUPGHOME", str(sandbox / "gpg"))
    (sandbox / "gpg").mkdir(mode=0o700)
    fpr_a = crypto.gpg_gen_key_batch(name_real="Key A", name_email="ka@memforge.test")
    fpr_b = crypto.gpg_gen_key_batch(name_real="Key B", name_email="kb@memforge.test")
    data = b"exact pin payload"
    sig_by_a = crypto.gpg_sign_detached(data, fingerprint=fpr_a)
    # Correct pin (A) verifies; wrong pin (B) is rejected.
    assert crypto.gpg_verify_detached(data, signature_b64=sig_by_a, expected_fingerprint=fpr_a) is True
    assert crypto.gpg_verify_detached(data, signature_b64=sig_by_a, expected_fingerprint=fpr_b) is False
    # A short suffix of A's fingerprint must NOT validate (no substring accept).
    assert crypto.gpg_verify_detached(data, signature_b64=sig_by_a, expected_fingerprint=fpr_a[-16:]) is False


def _gen_rsa4096_with_signing_subkey(name_email: str) -> str:
    """Generate an RSA-4096 key with a SEPARATE signing subkey; return PRIMARY fpr.

    This is the common GnuPG default shape for RSA keys (primary is cert-only,
    a signing subkey actually signs). `gpg_list_secret_keys` records only the
    PRIMARY fpr, so this is exactly the verify-01 case: VALIDSIG's first field
    is the SUBKEY fpr but the registry pins the PRIMARY.
    """
    import subprocess as sp

    gpg = crypto._gpg_bin()
    # Primary: RSA-4096 cert-only key.
    sp.run(
        [gpg, "--batch", "--passphrase", "", "--pinentry-mode", "loopback",
         "--quick-gen-key", f"verify01 <{name_email}>", "rsa4096", "cert", "0"],
        check=True, capture_output=True,
    )
    # Resolve the primary fingerprint for this uid from the secret keyring.
    primary = None
    for k in crypto.gpg_list_secret_keys():
        if name_email in (k.get("uid") or ""):
            primary = k["fingerprint"]
            break
    assert primary, "could not resolve primary fingerprint for the RSA-4096 key"
    # Add an RSA-4096 SIGNING subkey to that primary.
    sp.run(
        [gpg, "--batch", "--passphrase", "", "--pinentry-mode", "loopback",
         "--quick-add-key", primary, "rsa4096", "sign", "0"],
        check=True, capture_output=True,
    )
    return primary


@pytestmark_gpg
def test_verify_rsa4096_signing_subkey_against_primary_pin(monkeypatch):
    """verify-01 (regression): an RSA-4096 key with a separate signing SUBKEY
    verifies when pinned on the PRIMARY fingerprint (which is what
    gpg_list_secret_keys / the registry stores).

    Root cause of the regression: gpg_verify_detached pinned on the FIRST
    VALIDSIG field (the signing-subkey fpr), but the registry stores the PRIMARY
    fpr, so every RSA-4096-with-signing-subkey key (the GnuPG default, a
    first-class spec algo) failed verification. The fix accepts an exact match on
    EITHER the VALIDSIG signing-key (first) fpr OR the primary-key (last) fpr.
    """
    sandbox = _short_sandbox()
    monkeypatch.setenv("GNUPGHOME", str(sandbox / "gpg"))
    (sandbox / "gpg").mkdir(mode=0o700)
    primary_fpr = _gen_rsa4096_with_signing_subkey("rsa4096sub@memforge.test")
    data = b"rsa-4096 signing-subkey payload"
    # Sign with the PRIMARY identity (gpg routes to the signing subkey).
    sig = crypto.gpg_sign_detached(data, fingerprint=primary_fpr)
    # Verify pinned on the PRIMARY fpr (the registry's expected_fingerprint).
    assert crypto.gpg_verify_detached(
        data, signature_b64=sig, expected_fingerprint=primary_fpr
    ) is True
    # A wrong primary pin still fails closed.
    other = crypto.gpg_gen_key_batch(name_real="Other", name_email="other-rsa@memforge.test")
    assert crypto.gpg_verify_detached(
        data, signature_b64=sig, expected_fingerprint=other
    ) is False


@pytestmark_gpg
def test_gpg_gen_key_batch_returns_full_40char_fingerprint(monkeypatch):
    """sec-02 (regression): gen-key NEVER returns a short (<40) key-id; the
    returned value is always a canonical 40-char hex fingerprint (a short pin
    would silently fail-close every later verification of that key).
    """
    sandbox = _short_sandbox()
    monkeypatch.setenv("GNUPGHOME", str(sandbox / "gpg"))
    (sandbox / "gpg").mkdir(mode=0o700)
    fpr = crypto.gpg_gen_key_batch(name_real="Sec02", name_email="sec02@memforge.test")
    assert len(fpr) == 40
    assert all(c in "0123456789ABCDEF" for c in fpr.upper())
    # And it round-trips through verification as a real pin.
    data = b"sec02 payload"
    sig = crypto.gpg_sign_detached(data, fingerprint=fpr)
    assert crypto.gpg_verify_detached(data, signature_b64=sig, expected_fingerprint=fpr) is True


@pytestmark_gpg
def test_gpg_gen_key_batch_warns_unprotected(monkeypatch, recwarn):
    """keygen-01: empty-passphrase keygen emits the persistent UnprotectedKeyWarning."""
    sandbox = _short_sandbox()
    monkeypatch.setenv("GNUPGHOME", str(sandbox / "gpg"))
    (sandbox / "gpg").mkdir(mode=0o700)
    crypto.gpg_gen_key_batch(name_real="Warn Key", name_email="warn@memforge.test")
    assert any(issubclass(w.category, crypto.UnprotectedKeyWarning) for w in recwarn.list)

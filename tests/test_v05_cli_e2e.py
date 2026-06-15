"""End-to-end tests for the v0.5 reference CLI `cmd()` handlers.

test_v05_cli.py covers the helper layer; this file exercises the actual
`cmd()` handlers shipped to a design partner, driven through
`cli._dispatch.build_parser` (so the argparse wiring + defaults are exercised
too, not just the handler bodies). Each handler runs against a throwaway
GNUPGHOME with a real Ed25519 key and a tmp memory-root.

These tests require gpg + the MEMFORGE_TEST_GPG=1 opt-in, using the same
skip-guard pattern as the existing v0.5 e2e tests (gpg touches the keyring).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from memforge import crypto, identity, registry, revocation
from memforge.cli import _dispatch
from memforge.cli.v05 import attest_agent, init_operator
from memforge.cli._dispatch import build_parser


def _gpg_available() -> bool:
    try:
        crypto.gpg_version()
        return True
    except crypto.CryptoError:
        return False


pytestmark = pytest.mark.skipif(
    not (_gpg_available() and os.environ.get("MEMFORGE_TEST_GPG") == "1"),
    reason="v0.5 cmd() e2e tests require MEMFORGE_TEST_GPG=1 (real gpg keyring)",
)


def _short_sandbox() -> Path:
    """macOS gpg-agent sockets have a 104-char path limit; pytest tmp_path is too long."""
    return Path(tempfile.mkdtemp(prefix="mfg", dir="/tmp"))


def _gpg_env(monkeypatch, sandbox: Path) -> None:
    gpg_home = sandbox / "gpg"
    gpg_home.mkdir(mode=0o700)
    monkeypatch.setenv("GNUPGHOME", str(gpg_home))


def _patch_identity_paths(monkeypatch, sandbox: Path) -> Path:
    """Redirect the per-machine identity / recovery / config paths into the sandbox.

    init_operator imports OPERATOR_IDENTITY_PATH by value, so its module binding
    is patched separately from the identity module binding (the latter is read
    at call time by load/save_operator_identity).
    """
    mf = sandbox / "mf"
    monkeypatch.setattr(identity, "OPERATOR_IDENTITY_PATH", mf / "operator-identity.yaml")
    monkeypatch.setattr(identity, "RECOVERY_SECRET_PATH", mf / "recovery-secret.bin")
    monkeypatch.setattr(identity, "PER_USER_CONFIG_PATH", mf / "config.yaml")
    monkeypatch.setattr(init_operator, "OPERATOR_IDENTITY_PATH", mf / "operator-identity.yaml")
    return mf


def _run(argv: list[str]) -> int:
    """Drive a subcommand through the real dispatcher parser -> handler."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


def _make_store(sandbox: Path) -> Path:
    store = sandbox / "store"
    store.mkdir()
    return store


# --------------------------------------------------------------------------
# init-operator
# --------------------------------------------------------------------------


def test_e2e_init_operator_gen_key(monkeypatch):
    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)

    rc = _run(["init-operator", "--name", "Partner Op", "--gen-key"])
    assert rc == 0
    loaded = identity.load_operator_identity()
    assert loaded["operator_name"] == "Partner Op"
    fpr = loaded["key_fingerprint"]
    # initop-04: a real 40-char hex fingerprint is stored.
    assert len(fpr) == 40
    assert all(c in "0123456789ABCDEF" for c in fpr.upper())


def test_e2e_init_operator_next_steps_order(monkeypatch, capsys):
    """initop-nextsteps-01: the printed next-steps put init-store BEFORE
    recovery-init (recovery-init hard-requires an already-signed registry).
    """
    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)
    assert _run(["init-operator", "--name", "Op", "--gen-key"]) == 0
    out = capsys.readouterr().out
    assert "init-store" in out and "recovery-init" in out
    assert out.index("init-store") < out.index("recovery-init"), (
        "next-steps must run init-store before recovery-init"
    )


def test_e2e_init_operator_rejects_short_fingerprint(monkeypatch, capsys):
    """initop-04: a 16-char short id is rejected (was previously accepted by suffix match)."""
    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)
    # Generate a key so a suffix match WOULD have succeeded pre-fix.
    fpr = crypto.gpg_gen_key_batch(name_real="Short", name_email="short@memforge.test")
    short = fpr[-16:]
    rc = _run(["init-operator", "--name", "Short", "--gpg-fingerprint", short])
    assert rc == 2
    assert "40-character hex" in capsys.readouterr().err


def test_e2e_init_operator_full_fingerprint_stores_canonical(monkeypatch):
    """initop-04: full-fingerprint path stores the keyring's canonical value."""
    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)
    fpr = crypto.gpg_gen_key_batch(name_real="Full", name_email="full@memforge.test")
    # Supply the fingerprint lower-cased + spaced; canonical (upper, no-space) stored.
    spaced = " ".join(fpr[i : i + 4] for i in range(0, 40, 4)).lower()
    rc = _run(["init-operator", "--name", "Full", "--gpg-fingerprint", spaced])
    assert rc == 0
    assert identity.load_operator_identity()["key_fingerprint"] == fpr


# --------------------------------------------------------------------------
# init-store + operator-registry verify
# --------------------------------------------------------------------------


def test_e2e_init_store_then_registry_verify(monkeypatch):
    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)
    store = _make_store(sandbox)

    assert _run(["init-operator", "--name", "Op", "--gen-key"]) == 0
    assert _run(["init-store", "--memory-root", str(store)]) == 0
    # operator-registry verify drives load_registry(verify_signature=True).
    assert _run(["operator-registry", "verify", "--memory-root", str(store)]) == 0
    # Registry actually load-verifies + records signing_key_id.
    reg = registry.load_registry(store, verify_signature=True)
    op = reg["operators"][0]
    assert op["operator_uuid"] == identity.load_operator_identity()["operator_uuid"]
    assert reg["registry_signature"]["signing_key_id"] == op["public_keys"][0]["key_id"]


# --------------------------------------------------------------------------
# attest-agent
# --------------------------------------------------------------------------


def test_e2e_attest_agent_default_scope(monkeypatch):
    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)
    store = _make_store(sandbox)
    assert _run(["init-operator", "--name", "Op", "--gen-key"]) == 0
    assert _run(["init-store", "--memory-root", str(store)]) == 0

    rc = _run(["attest-agent", "--memory-root", str(store)])
    assert rc == 0
    # Exactly one attestation written; default scope is least-privilege.
    sess_dir = store / registry.REGISTRY_DIRNAME / registry.AGENT_SESSIONS_SUBDIR
    files = list(sess_dir.glob("*.yaml"))
    assert len(files) == 1
    rec = agent_session_load(store, files[0])
    assert rec["capability_scope"]["allowed_operations"] == ["write", "resolve"]
    # attest-08: gen-key path records the real ed25519 algo on the agent key.
    assert rec["agent_pubkey_algo"] == "gpg-ed25519"
    # agentsession-01: operator_signature.algo is the real signer algo, not a
    # hardcoded literal disconnected from the key (here ed25519, and it passes
    # the accepted-algo gate verify_attestation enforces).
    crypto.gpg_check_algo_accepted(rec["operator_signature"]["algo"])


def test_e2e_attest_agent_elevated_requires_confirmation(monkeypatch, capsys):
    """attest-02: elevated capability without confirmation is refused (non-interactive)."""
    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)
    store = _make_store(sandbox)
    assert _run(["init-operator", "--name", "Op", "--gen-key"]) == 0
    assert _run(["init-store", "--memory-root", str(store)]) == 0

    # Force non-interactive: stdin not a TTY in the test runner, so no flag = refuse.
    # dispatch-01: elevated-not-confirmed is a precondition/usage-class failure -> exit 2.
    rc = _run(["attest-agent", "--memory-root", str(store), "--capability", "revoke"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "ELEVATED" in err
    # No attestation file was written (gate runs before any side effect).
    sess_dir = store / registry.REGISTRY_DIRNAME / registry.AGENT_SESSIONS_SUBDIR
    assert not list(sess_dir.glob("*.yaml")) if sess_dir.exists() else True


def test_e2e_attest_agent_elevated_with_yes_flag(monkeypatch):
    """attest-02: --yes-i-understand-elevated permits elevated scope non-interactively."""
    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)
    store = _make_store(sandbox)
    assert _run(["init-operator", "--name", "Op", "--gen-key"]) == 0
    assert _run(["init-store", "--memory-root", str(store)]) == 0

    rc = _run(
        [
            "attest-agent",
            "--memory-root",
            str(store),
            "--capability",
            "registry-edit",
            "--yes-i-understand-elevated",
        ]
    )
    assert rc == 0
    sess_dir = store / registry.REGISTRY_DIRNAME / registry.AGENT_SESSIONS_SUBDIR
    files = list(sess_dir.glob("*.yaml"))
    assert len(files) == 1
    rec = agent_session_load(store, files[0])
    assert "registry-edit" in rec["capability_scope"]["allowed_operations"]


# --------------------------------------------------------------------------
# rotate-key (rotate-03 + rotate-06)
# --------------------------------------------------------------------------


def test_e2e_rotate_key_cooldown_and_provenance(monkeypatch):
    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    mf = _patch_identity_paths(monkeypatch, sandbox)
    store = _make_store(sandbox)
    # Make the store a git repo so rotate-key's auto-commit (rotate-03) works.
    import subprocess as sp

    sp.run(["git", "init", "-q", str(store)], check=True)
    sp.run(["git", "-C", str(store), "config", "user.email", "t@t.com"], check=True)
    sp.run(["git", "-C", str(store), "config", "user.name", "t"], check=True)
    sp.run(["git", "-C", str(store), "config", "commit.gpgsign", "false"], check=True)

    assert _run(["init-operator", "--name", "Op", "--gen-key"]) == 0
    assert _run(["init-store", "--memory-root", str(store)]) == 0

    before = identity.load_operator_identity()
    old_fpr = before["key_fingerprint"]
    original_created = before["created"]
    original_origin = before["machine_origin"]

    # Distinct email so gpg --quick-gen-key does not refuse a duplicate uid
    # (the init-operator key already used <operator-uuid>@memforge.local).
    assert _run(["rotate-key", "--memory-root", str(store), "--email", "rotated@memforge.test"]) == 0

    after = identity.load_operator_identity()
    new_fpr = after["key_fingerprint"]
    assert new_fpr != old_fpr
    # rotate-06: install provenance preserved across the rotation.
    assert after["created"] == original_created
    assert after["machine_origin"] == original_origin

    # rotate-03: the new key is in cool-down, anchored to the rotation commit
    # that rotate-key made itself (build-time == commit author-date).
    reg = registry.load_registry(store, verify_signature=True)
    assert registry.key_is_in_cooldown(reg, new_fpr) is True
    assert registry.key_is_in_cooldown(reg, old_fpr) is False
    # The rotation was committed automatically (rotate-03 anchor fix).
    log = sp.run(
        ["git", "-C", str(store), "log", "--format=%s", "-1"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "memforge: operator-registry rotate" in log.stdout


def test_e2e_rotate_key_default_email_no_collision(monkeypatch):
    """rotate-01 (regression): `rotate-key` with NO --email succeeds (rc==0).

    Root cause: the default rotated-key uid was `<operator-uuid>@memforge.local`,
    the SAME uid init-operator --gen-key used, so `gpg --quick-gen-key` refused
    the duplicate uid and the DEFAULT rotation path raised CryptoError for every
    operator who bootstrapped via the primary onboarding path. The old e2e test
    masked it by passing a distinct --email. The fix makes the rotated uid unique
    by default (`<uuid>+rot<N>-<nonce>@memforge.local`). This test exercises the
    no---email default end to end.
    """
    import subprocess as sp

    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)
    store = _make_store(sandbox)
    sp.run(["git", "init", "-q", str(store)], check=True)
    sp.run(["git", "-C", str(store), "config", "user.email", "t@t.com"], check=True)
    sp.run(["git", "-C", str(store), "config", "user.name", "t"], check=True)
    sp.run(["git", "-C", str(store), "config", "commit.gpgsign", "false"], check=True)

    # Bootstrap via the PRIMARY onboarding path: init-operator --gen-key, which
    # uses <operator-uuid>@memforge.local as the gen-key uid.
    assert _run(["init-operator", "--name", "Op", "--gen-key"]) == 0
    assert _run(["init-store", "--memory-root", str(store)]) == 0
    before = identity.load_operator_identity()
    old_fpr = before["key_fingerprint"]

    # rotate-key with NO --email must NOT collide and must succeed.
    rc = _run(["rotate-key", "--memory-root", str(store)])
    assert rc == 0
    after = identity.load_operator_identity()
    assert after["key_fingerprint"] != old_fpr
    reg = registry.load_registry(store, verify_signature=True)
    assert registry.key_is_in_cooldown(reg, after["key_fingerprint"]) is True


def test_e2e_operator_registry_add_rejects_short_fingerprint(monkeypatch, capsys):
    """opreg-01 (regression): operator-registry `add` rejects a 16-char short id
    (must require the full 40-char fingerprint, like init-operator does).

    Root cause: add/fresh-start passed the user-supplied fingerprint straight to
    gpg_export_public_key + persisted it verbatim as key_id (no 40-hex check, no
    canonical-fingerprint resolution), so a short id / typo was accepted and
    silently broke later signature lookups (exact key_id match). The fix factors
    the init-operator validation into a shared helper applied here.
    """
    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)
    store = _make_store(sandbox)
    assert _run(["init-operator", "--name", "Op", "--gen-key"]) == 0
    assert _run(["init-store", "--memory-root", str(store)]) == 0
    # A second operator's key, supplied as a 16-char short id.
    bkey = crypto.gpg_gen_key_batch(name_real="OpB", name_email="opb@memforge.test")
    short = bkey[-16:]
    other_uuid = identity.generate_uuidv7()
    rc = _run(
        [
            "operator-registry", "add", "--memory-root", str(store),
            "--operator-uuid", other_uuid, "--operator-name", "OpB",
            "--pubkey-fingerprint", short,
        ]
    )
    assert rc == 2
    assert "40-character hex" in capsys.readouterr().err


def test_e2e_operator_registry_add_stores_canonical_fingerprint(monkeypatch):
    """opreg-01 (regression): the full-fingerprint path persists the keyring's
    canonical value (upper, no-space, primary) as key_id, even when supplied
    spaced + lower-cased.
    """
    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)
    store = _make_store(sandbox)
    assert _run(["init-operator", "--name", "Op", "--gen-key"]) == 0
    assert _run(["init-store", "--memory-root", str(store)]) == 0
    bkey = crypto.gpg_gen_key_batch(name_real="OpB", name_email="opb2@memforge.test")
    spaced = " ".join(bkey[i : i + 4] for i in range(0, 40, 4)).lower()
    other_uuid = identity.generate_uuidv7()
    rc = _run(
        [
            "operator-registry", "add", "--memory-root", str(store),
            "--operator-uuid", other_uuid, "--operator-name", "OpB",
            "--pubkey-fingerprint", spaced,
        ]
    )
    assert rc == 0
    reg = registry.load_registry(store, verify_signature=True)
    added = next(op for op in reg["operators"] if op["operator_uuid"] == other_uuid)
    assert added["public_keys"][0]["key_id"] == bkey


def test_e2e_operator_registry_remove_refuses_self_supersede(monkeypatch, capsys):
    """registry-01 (regression): operator-registry `remove` refuses to remove the
    operator whose key signs the resulting registry (would brick the store).

    Root cause: remove_operator supersedes the operator + its keys, then
    sign_and_save signs with this machine's identity key. If the removed operator
    IS the signer, the freshly-superseded key signs the registry and the next
    load fails closed forever. The CLI guard refuses the self-supersede path.
    """
    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)
    store = _make_store(sandbox)
    assert _run(["init-operator", "--name", "Op", "--gen-key"]) == 0
    assert _run(["init-store", "--memory-root", str(store)]) == 0
    self_uuid = identity.load_operator_identity()["operator_uuid"]
    rc = _run(
        [
            "operator-registry", "remove", "--memory-root", str(store),
            "--operator-uuid", self_uuid,
        ]
    )
    assert rc == 2
    assert "cannot remove the operator whose key signs" in capsys.readouterr().err
    # The store still load-verifies (was not bricked).
    registry.load_registry(store, verify_signature=True)


# --------------------------------------------------------------------------
# revoke (revoke-07)
# --------------------------------------------------------------------------


def test_e2e_revoke_writes_body(monkeypatch, tmp_path):
    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)
    assert _run(["init-operator", "--name", "Op", "--gen-key"]) == 0
    target_key = "A" * 40
    out = sandbox / "revoke-body.txt"
    rc = _run(
        [
            "revoke",
            target_key,
            "--reason",
            "compromised in test",
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    body = out.read_text(encoding="utf-8")
    assert body.startswith(revocation.REVOKE_PREFIX + target_key)
    # Body verifies under the operator's key.
    parsed = revocation.parse_revoke_commit_body(body)
    assert parsed is not None
    fpr = identity.load_operator_identity()["key_fingerprint"]
    assert revocation.verify_revoke_body(parsed, expected_signer_fingerprint=fpr)


def test_e2e_revoke_commit_signs_and_lands(monkeypatch):
    """revoke-07: --commit produces a signed empty commit atomically."""
    import subprocess as sp

    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)
    repo = sandbox / "repo"
    repo.mkdir()
    sp.run(["git", "init", "-q", str(repo)], check=True)
    sp.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True)
    sp.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    assert _run(["init-operator", "--name", "Op", "--gen-key"]) == 0
    fpr = identity.load_operator_identity()["key_fingerprint"]
    sp.run(["git", "-C", str(repo), "config", "user.signingkey", fpr], check=True)

    rc = _run(
        [
            "revoke",
            "B" * 40,
            "--reason",
            "atomic commit test",
            "--commit",
            "--repo-root",
            str(repo),
        ]
    )
    assert rc == 0
    # A signed commit with the revoke prefix is now HEAD.
    subj = sp.run(
        ["git", "-C", str(repo), "log", "--format=%s", "-1"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert subj.startswith("memforge: revoke " + "B" * 40)
    # Walk picks it up + it passes the revoked_at skew guard (revoke-02).
    rev_set = revocation.walk_revocation_set(repo)
    assert ("B" * 40) in rev_set
    revocation.assert_revoked_at_within_skew(rev_set["B" * 40])


# --------------------------------------------------------------------------
# revocation-01 / revocation-02: snapshot + config-cap wiring through the walker
# --------------------------------------------------------------------------


def _build_pre_post_snapshot_repo(tmp_path):
    """Build a git repo: revoke PREKEY, a snapshot commit, then revoke POSTKEY."""
    import subprocess as sp

    sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)

    def _commit(msg: str) -> None:
        sp.run(
            ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", msg],
            check=True,
            capture_output=True,
        )

    # A revoke BEFORE the snapshot.
    _commit(
        "memforge: revoke PREKEY\n\n"
        "key_id: PREKEY\nrevoked_at: 2020-01-01T00:00:00Z\n"
        "reason: pre-snapshot\nrevoked_by: op\nrevocation_uid: u1\n"
    )
    # The snapshot commit itself.
    _commit("memforge: revocation-snapshot deadbeef\n\nsnapshot_hash: deadbeef\n")
    # A revoke AFTER the snapshot.
    _commit(
        "memforge: revoke POSTKEY\n\n"
        "key_id: POSTKEY\nrevoked_at: 2021-01-01T00:00:00Z\n"
        "reason: post-snapshot\nrevoked_by: op\nrevocation_uid: u2\n"
    )


def test_snapshot_floor_disabled_pre_snapshot_revocation_still_enforced(tmp_path):
    """snapshot-01 / revoke-snapshot-01: a pre-snapshot revocation is STILL
    enforced after a snapshot exists.

    The snapshot-as-walk-floor optimization is unsound (it truncates pre-snapshot
    revocations, silently un-revoking compromised keys). The default walk MUST
    walk full history so PREKEY (revoked BEFORE the snapshot) is never dropped.
    """
    _build_pre_post_snapshot_repo(tmp_path)

    # Raw walk from repo root sees BOTH.
    full = revocation.walk_revocation_set(tmp_path)
    assert {"PREKEY", "POSTKEY"} <= set(full)

    # The shipped (default) snapshot-aware walk MUST also see BOTH: the snapshot
    # is NOT used as a floor, so the pre-snapshot revocation survives.
    default = revocation.walk_revocation_set_from_snapshot(tmp_path)
    assert "POSTKEY" in default
    assert "PREKEY" in default, (
        "pre-snapshot revocation was dropped: the snapshot floor must be disabled"
    )


def test_snapshot_floor_optin_is_unsound_and_off_by_default(tmp_path):
    """revoke-snapshot-02: the snapshot floor is an explicit, off-by-default,
    unsound opt-in; the default path never truncates at the snapshot.

    This documents (and pins) that the floor behavior only happens when a caller
    explicitly opts in, demonstrating why no security-bearing consumer should.
    """
    _build_pre_post_snapshot_repo(tmp_path)

    # Default: full history (both keys).
    default = revocation.walk_revocation_set_from_snapshot(tmp_path)
    assert {"PREKEY", "POSTKEY"} <= set(default)

    # Explicit opt-in to the UNSOUND floor: it truncates at the snapshot and
    # drops PREKEY. This is exactly the un-revocation bug; the opt-in exists only
    # to keep the floor reachable for non-trust diagnostics, never the security
    # path.
    floored = revocation.walk_revocation_set_from_snapshot(
        tmp_path, use_snapshot_floor=True
    )
    assert "POSTKEY" in floored
    assert "PREKEY" not in floored


def test_verified_walk_enforces_pre_snapshot_revocation(monkeypatch):
    """snapshot-01 / revoke-snapshot-01 (security regression): a registry-VERIFIED
    revocation authored BEFORE a snapshot is STILL enforced after the snapshot.

    Drives the real security seam (`walk_revocation_set_verified`) end to end:
    real operator key + signed registry + a real signed revoke commit landed
    before a snapshot commit. With the snapshot floor disabled, the verified walk
    must still return the pre-snapshot revocation (the compromised key stays
    revoked).
    """
    import subprocess as sp

    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)
    store = _make_store(sandbox)
    sp.run(["git", "init", "-q", str(store)], check=True)
    sp.run(["git", "-C", str(store), "config", "user.email", "t@t.com"], check=True)
    sp.run(["git", "-C", str(store), "config", "user.name", "t"], check=True)

    assert _run(["init-operator", "--name", "Op", "--gen-key"]) == 0
    assert _run(["init-store", "--memory-root", str(store)]) == 0
    ident = identity.load_operator_identity()
    fpr = ident["key_fingerprint"]
    op_uuid = ident["operator_uuid"]
    reg = registry.load_registry(store, verify_signature=True)

    # A real, signed revocation of PREKEY authored by the operator.
    target_key = "C" * 40
    commit_msg, _body = revocation.build_revoke_body(
        key_id=target_key,
        reason="compromised before snapshot",
        revoked_by_uuid=op_uuid,
        signer_fingerprint=fpr,
    )
    sp.run(
        ["git", "-C", str(store), "commit", "--allow-empty", "-m", commit_msg],
        check=True,
        capture_output=True,
    )
    # A snapshot commit lands AFTER the revocation.
    sp.run(
        ["git", "-C", str(store), "commit", "--allow-empty", "-m",
         "memforge: revocation-snapshot deadbeef\n\nsnapshot_hash: deadbeef\n"],
        check=True,
        capture_output=True,
    )

    # The verified walk (the shipped seam) MUST still carry PREKEY's revocation
    # despite the later snapshot: the floor is disabled, so it is not truncated.
    verified = revocation.walk_revocation_set_verified(store, reg, memory_root=store)
    assert target_key in verified, (
        "pre-snapshot, registry-verified revocation was dropped after a snapshot "
        "landed: the un-revocation bug is back"
    )
    # And the key reads as revoked at a signing-time after the revoke.
    assert revocation.is_key_revoked_at(verified, target_key, "2099-01-01T00:00:00Z")


def test_snapshot_cli_builds_from_verified_walk(monkeypatch, tmp_path):
    """snapshot-verify-02 (regression): `revocation-snapshot` compresses ONLY
    registry-verified, in-skew revocations into the signed snapshot.

    A forged (unsigned) revoke body landed in history must NOT be laundered into
    the operator-signed snapshot; a real signed revoke must be carried.
    """
    import subprocess as sp

    from memforge.cli.v05 import revocation_snapshot

    sandbox = _short_sandbox()
    _gpg_env(monkeypatch, sandbox)
    _patch_identity_paths(monkeypatch, sandbox)
    store = _make_store(sandbox)
    sp.run(["git", "init", "-q", str(store)], check=True)
    sp.run(["git", "-C", str(store), "config", "user.email", "t@t.com"], check=True)
    sp.run(["git", "-C", str(store), "config", "user.name", "t"], check=True)

    assert _run(["init-operator", "--name", "Op", "--gen-key"]) == 0
    assert _run(["init-store", "--memory-root", str(store)]) == 0
    ident = identity.load_operator_identity()

    # A REAL signed revoke (carried into the snapshot).
    good_key = "D" * 40
    good_msg, _ = revocation.build_revoke_body(
        key_id=good_key,
        reason="legit revoke for snapshot",
        revoked_by_uuid=ident["operator_uuid"],
        signer_fingerprint=ident["key_fingerprint"],
    )
    sp.run(["git", "-C", str(store), "commit", "--allow-empty", "-m", good_msg],
           check=True, capture_output=True)
    # A FORGED, unsigned revoke body (must be dropped by the verified walk).
    forged_key = "E" * 40
    forged_msg = (
        f"memforge: revoke {forged_key}\n\n"
        f"key_id: {forged_key}\nrevoked_at: 2021-01-01T00:00:00Z\n"
        f"reason: forged unsigned body\nrevoked_by: {ident['operator_uuid']}\n"
        "revocation_uid: forged-uid\n"
    )
    sp.run(["git", "-C", str(store), "commit", "--allow-empty", "-m", forged_msg],
           check=True, capture_output=True)

    out = sandbox / "snap.txt"
    rc = _run(["revocation-snapshot", "--repo-root", str(store),
               "--memory-root", str(store), "--output", str(out)])
    assert rc == 0
    body_text = out.read_text(encoding="utf-8")
    assert good_key in body_text, "verified revoke must be carried into the snapshot"
    assert forged_key not in body_text, (
        "forged unsigned revoke was laundered into the signed snapshot"
    )


def test_candidate_filter_runs_before_earliest_wins_dedup(tmp_path):
    """revoke-skew-decoupled-01: an out-of-skew candidate must NOT win the
    per-key_id earliest-wins selection and evict a legitimate in-skew revocation.

    No gpg: drives the walk's candidate_filter directly. Two revoke commits for
    the SAME key_id: a legitimate in-window one and an attacker far-past one with
    a smaller revoked_at (which would win earliest-wins). With a candidate filter
    that drops the far-past body BEFORE dedup, the legitimate body survives.
    """
    import subprocess as sp

    sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)

    def _commit(msg: str) -> None:
        sp.run(["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", msg],
               check=True, capture_output=True)

    # Attacker far-past body (smaller revoked_at -> would win earliest-wins).
    _commit(
        "memforge: revoke SHARED\n\n"
        "key_id: SHARED\nrevoked_at: 1970-01-01T00:00:00Z\n"
        "reason: attacker far past\nrevoked_by: op\nrevocation_uid: bad\n"
    )
    # Legitimate body (later revoked_at).
    _commit(
        "memforge: revoke SHARED\n\n"
        "key_id: SHARED\nrevoked_at: 2021-01-01T00:00:00Z\n"
        "reason: legitimate revoke\nrevoked_by: op\nrevocation_uid: good\n"
    )

    # Without a filter, the far-past attacker body wins earliest-wins.
    raw = revocation.walk_revocation_set(tmp_path)
    assert raw["SHARED"]["revocation_uid"] == "bad"

    # With a candidate filter dropping the 1970 body BEFORE dedup, the legitimate
    # revocation survives (it is not evicted by the bogus earliest candidate).
    # (yaml may parse revoked_at as a datetime; compare via str().)
    def _drop_far_past(body):
        return str(body.get("revoked_at", "")) >= "2000-01-01"

    filtered = revocation.walk_revocation_set(tmp_path, candidate_filter=_drop_far_past)
    assert filtered["SHARED"]["revocation_uid"] == "good", (
        "out-of-window candidate evicted the legitimate revocation before filtering"
    )


def test_config_caps_read_by_snapshot_walker(tmp_path):
    """revocation-02: walk caps come from .memforge/config.yaml when present."""
    import subprocess as sp

    cfg = tmp_path / revocation.CONFIG_DIRNAME / revocation.CONFIG_FILENAME
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        "revocation:\n  walk_max_commits: 2\n  walk_max_bytes: 5000000\n",
        encoding="utf-8",
    )
    assert revocation.read_walk_caps(tmp_path) == (2, 5_000_000)

    sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    for i in range(4):
        sp.run(
            ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", f"chore: {i}"],
            check=True,
            capture_output=True,
        )
    # The configured 2-commit cap is honored by the snapshot-aware walker
    # (no snapshot present -> walks from root -> 4 commits > cap -> fail closed).
    with pytest.raises(revocation.RevocationError, match=r"commit cap exceeded"):
        revocation.walk_revocation_set_from_snapshot(tmp_path)


def test_config_caps_default_when_absent(tmp_path):
    """revocation-02: missing config falls back to module defaults."""
    assert revocation.read_walk_caps(tmp_path) == (
        revocation.DEFAULT_WALK_MAX_COMMITS,
        revocation.DEFAULT_WALK_MAX_BYTES,
    )
    # Malformed values fall back rather than crash.
    cfg = tmp_path / revocation.CONFIG_DIRNAME / revocation.CONFIG_FILENAME
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("revocation:\n  walk_max_commits: not-a-number\n", encoding="utf-8")
    assert revocation.read_walk_caps(tmp_path)[0] == revocation.DEFAULT_WALK_MAX_COMMITS


# --------------------------------------------------------------------------
# revoke-02: revoked_at clock-skew guard helper
# --------------------------------------------------------------------------


def test_revoked_at_skew_guard_rejects_future_and_far_past():
    author = "2026-06-14T12:00:00Z"
    # Far-future (immortal-revocation defeat): rejected.
    assert revocation.is_revoked_at_within_skew("2099-01-01T00:00:00Z", author) is False
    # Far-past: rejected.
    assert revocation.is_revoked_at_within_skew("2000-01-01T00:00:00Z", author) is False
    # Within +1 min future skew: accepted.
    assert revocation.is_revoked_at_within_skew("2026-06-14T12:00:30Z", author) is True
    # Within -10 min backdating skew: accepted.
    assert revocation.is_revoked_at_within_skew("2026-06-14T11:55:00Z", author) is True
    # Unparseable / empty: fail-closed False.
    assert revocation.is_revoked_at_within_skew("", author) is False
    assert revocation.is_revoked_at_within_skew(author, "") is False


def test_assert_revoked_at_within_skew_raises_on_out_of_window():
    entry = {
        "revoked_at": "2099-01-01T00:00:00Z",
        "_commit_author_date": "2026-06-14T12:00:00Z",
    }
    with pytest.raises(revocation.RevocationError, match=r"revoked_at_skew_out_of_window"):
        revocation.assert_revoked_at_within_skew(entry)


# --------------------------------------------------------------------------
# dispatch-09: top-level exception guard
# --------------------------------------------------------------------------


def test_dispatch_top_level_guard_clean_exit(monkeypatch, capsys):
    """dispatch-09: an unhandled exception in a handler becomes `memforge: ...` + exit 1."""

    def boom(_args):
        raise PermissionError("cannot write 0600 file")

    parser = build_parser()
    sub = next(
        a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"
    )
    # Point an existing subcommand's func at a raising stub.
    sub.choices["operator-registry"].set_defaults(func=boom)
    monkeypatch.setattr(_dispatch, "build_parser", lambda: parser)

    rc = _dispatch.main(["operator-registry", "verify"])
    assert rc == 1
    assert "memforge: cannot write 0600 file" in capsys.readouterr().err


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def agent_session_load(store: Path, path: Path) -> dict:
    """Load an attestation YAML directly (avoids re-deriving the session id)."""
    import yaml

    from memforge._security import secure_read_text

    return yaml.safe_load(secure_read_text(path))

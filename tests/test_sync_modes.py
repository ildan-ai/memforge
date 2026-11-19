"""`memforge sync` for the keyless mode (against a stub gitsign), the release
gate, mode migration and the gpg status rules (against fixtures).

The stub proves the arguments and the exit-code contract only. A real
gitsign job against a Fulcio and Rekor instance has not been run.
"""

from __future__ import annotations

import shutil

import pytest

from memforge._sync_git import Fail
from memforge._sync_git import SyncError
from memforge._sync_verify import classify_gpg_status, parse_commit
from tests.sync_support import (  # noqa: F401  (lab is a fixture)
    ALL_MODES, git, install_stub, lab, make_ssh_key, needs_ssh, run_sync,
    signers_text, verified_head,
)

ISSUER = "https://issuer.example"
IDS = "alice@example.com {i}\nbob@example.com {i}\ncarol@example.com {i}\n".format(i=ISSUER)


@pytest.fixture
def key(tmp_path):
    return make_ssh_key(tmp_path, "any")     # only supplies a signature header; the stub decides


def head(path):
    return git(path, "rev-parse", "HEAD")


# ---- the release gate ---------------------------------------------

@needs_ssh
@pytest.mark.parametrize("mode,text", [("keyless", IDS), ("gpg", "A" * 40 + "\n")])
def test_unreleased_mode_is_refused_without_verifying(lab, key, tmp_path, monkeypatch, capsys, mode, text):
    stub = install_stub(tmp_path, monkeypatch)
    base = lab.start(mode, text)
    recv = lab.receiver()
    bad = lab.commit({"a.md": "a\n"}, key=key)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv, trust_head=base) == 2
    err = capsys.readouterr().err
    assert "(mode_unreleased)" in err and bad in err
    assert stub.logged() == []
    assert head(recv) == base and verified_head(recv) == ""


# ---- keyless against the stub -------------------------------------

@needs_ssh
def test_keyless_calls_gitsign_with_exact_arguments_in_order_and_stops_at_the_first_pass(
        lab, key, tmp_path, monkeypatch):
    stub = install_stub(tmp_path, monkeypatch)
    base = lab.start("keyless", IDS)
    recv = lab.receiver()
    one = lab.commit({"a.md": "a\n"}, key=key)
    stub.allow(one, "bob@example.com")
    lab.push()
    assert run_sync(recv, trust_head=base, released=ALL_MODES) == 0
    assert stub.logged() == [
        f"verify --certificate-identity=alice@example.com --certificate-oidc-issuer={ISSUER} {one}",
        f"verify --certificate-identity=bob@example.com --certificate-oidc-issuer={ISSUER} {one}",
    ]
    assert verified_head(recv) == one


@needs_ssh
@pytest.mark.parametrize("body", [None, "#!/bin/sh\nexit 3\n"])
def test_keyless_refusals(lab, key, tmp_path, monkeypatch, capsys, body):
    stub = install_stub(tmp_path, monkeypatch, body)
    base = lab.start("keyless", IDS)
    recv = lab.receiver()
    lab.commit({"a.md": "a\n"}, key=key)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv, trust_head=base, released=ALL_MODES) == 2
    assert "(keyless_not_verified)" in capsys.readouterr().err
    assert head(recv) == base


@needs_ssh
def test_keyless_unknown_subcommand_is_keyless_not_verified(lab, key, tmp_path, monkeypatch, capsys):
    install_stub(tmp_path, monkeypatch, "#!/bin/sh\necho 'unknown command' >&2\nexit 2\n")
    base = lab.start("keyless", IDS)
    recv = lab.receiver()
    lab.commit({"a.md": "a\n"}, key=key)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv, trust_head=base, released=ALL_MODES) == 2
    err = capsys.readouterr().err
    assert "(keyless_not_verified)" in err and "unknown command" in err


@needs_ssh
def test_keyless_unsigned_commit_never_calls_gitsign(lab, tmp_path, monkeypatch, capsys):
    stub = install_stub(tmp_path, monkeypatch)
    base = lab.start("keyless", IDS)
    recv = lab.receiver()
    lab.commit({"a.md": "a\n"})
    lab.push()
    capsys.readouterr()
    assert run_sync(recv, trust_head=base, released=ALL_MODES) == 2
    assert "(unsigned)" in capsys.readouterr().err
    assert stub.logged() == []


@needs_ssh
def test_gitsign_missing_only_when_the_executable_is_not_found(lab, key, tmp_path, monkeypatch, capsys):
    if shutil.which("gitsign"):
        pytest.skip("a real gitsign is installed")
    base = lab.start("keyless", IDS)
    recv = lab.receiver()
    lab.commit({"a.md": "a\n"}, key=key)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv, trust_head=base, released=ALL_MODES) == 2
    assert "(gitsign_missing)" in capsys.readouterr().err


@needs_ssh
def test_keyless_self_authorizing_edit_is_refused(lab, key, tmp_path, monkeypatch, capsys):
    stub = install_stub(tmp_path, monkeypatch)
    base = lab.start("keyless", "alice@example.com " + ISSUER + "\n")
    recv = lab.receiver()
    edit = lab.commit({".memforge/allowed_identities": IDS}, key=key)
    stub.allow(edit, "bob@example.com")          # signed by bob, who is only listed by this very commit
    lab.push()
    capsys.readouterr()
    assert run_sync(recv, trust_head=base, released=ALL_MODES) == 2
    assert "(keyless_not_verified)" in capsys.readouterr().err


@needs_ssh
def test_keyless_list_edit_by_a_listed_identity_passes_and_the_new_identity_follows(
        lab, key, tmp_path, monkeypatch):
    stub = install_stub(tmp_path, monkeypatch)
    base = lab.start("keyless", "alice@example.com " + ISSUER + "\n")
    recv = lab.receiver()
    edit = lab.commit({".memforge/allowed_identities": IDS}, key=key)
    stub.allow(edit, "alice@example.com")
    later = lab.commit({"b.md": "b\n"}, key=key)
    stub.allow(later, "bob@example.com")
    lab.push()
    assert run_sync(recv, trust_head=base, released=ALL_MODES) == 0
    assert verified_head(recv) == later


@needs_ssh
@pytest.mark.parametrize("text", ["alice@example.com\n", "a b c\n", "# only a comment\n"])
def test_keyless_malformed_list_is_invalid(lab, key, tmp_path, monkeypatch, capsys, text):
    install_stub(tmp_path, monkeypatch)
    base = lab.start("keyless", text)
    recv = lab.receiver()
    lab.commit({"a.md": "a\n"}, key=key)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv, trust_head=base, released=ALL_MODES) == 2
    assert "(list_invalid)" in capsys.readouterr().err


# ---- mode migration ----------------------------------------------

@needs_ssh
def test_mode_migration_is_judged_under_the_old_mode_and_descendants_under_the_new(
        lab, tmp_path, monkeypatch):
    alice, bob = make_ssh_key(tmp_path, "alice"), make_ssh_key(tmp_path, "bob")
    stub = install_stub(tmp_path, monkeypatch)
    base = lab.start("ssh", signers_text(alice))
    recv = lab.receiver()
    lab.commit({".memforge/allowed_signers": None,
                ".memforge/allowed_identities": "alice@example.com " + ISSUER + "\n"}, key=alice)
    # bob is not on any ssh list, so only the keyless mode and its stub can pass this commit.
    after = lab.commit({"b.md": "b\n"}, key=bob)
    stub.allow(after, "alice@example.com")
    lab.push()
    assert run_sync(recv, trust_head=base, released=ALL_MODES) == 0
    assert verified_head(recv) == after
    assert len(stub.logged()) == 1


@needs_ssh
def test_migration_commit_signed_by_an_unlisted_signer_is_refused(lab, tmp_path, monkeypatch, capsys):
    alice, bob = make_ssh_key(tmp_path, "alice"), make_ssh_key(tmp_path, "bob")
    install_stub(tmp_path, monkeypatch)
    base = lab.start("ssh", signers_text(alice))
    recv = lab.receiver()
    lab.commit({".memforge/allowed_signers": None,
                ".memforge/allowed_identities": "bob@example.com " + ISSUER + "\n"}, key=bob)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv, trust_head=base, released=ALL_MODES) == 2
    assert "(ssh_not_verified)" in capsys.readouterr().err


# ---- the gpg status rules ------------------------------------

FPR = "A" * 40
PRIMARY = "B" * 40


def _line(keyword, *args):
    return [keyword, *args]


def _valid(n_args):
    args = [FPR, "1700000000", "1", "0", "4", "0", "22", "10", "00"]
    if n_args >= 10:
        args.append(PRIMARY)
    if n_args == 11:
        args.append("extra")
    return _line("VALIDSIG", *args[:n_args])


GOOD = [_line("GOODSIG", "ID", "name")]


def test_nine_arguments_mean_the_signer_is_the_primary():
    assert classify_gpg_status(0, GOOD + [_valid(9)]) == FPR


def test_ten_arguments_use_the_tenth_as_the_primary():
    assert classify_gpg_status(0, GOOD + [_valid(10)]) == PRIMARY


@pytest.mark.parametrize("count", [8, 11])
def test_other_argument_counts_are_bad_signature(count):
    with pytest.raises(Fail) as raised:
        classify_gpg_status(0, GOOD + [_valid(count)])
    assert raised.value.reason == "bad_signature"


@pytest.mark.parametrize("extra,reason", [
    ([_line("BADSIG", "ID", "n")], "bad_signature"),
    ([_line("ERRSIG", "ID")], "bad_signature"),
    ([_line("ERRSIG", "ID"), _line("NO_PUBKEY", "ID")], "no_key"),
    ([_line("EXPSIG", "ID", "n")], "expired"),
    ([_line("EXPKEYSIG", "ID", "n")], "expired"),
    ([_line("KEYEXPIRED", "1")], "expired"),
    ([_line("REVKEYSIG", "ID", "n")], "revoked"),
    ([_line("KEYREVOKED")], "revoked"),
])
def test_failure_lines_win_over_success_lines(extra, reason):
    with pytest.raises(Fail) as raised:
        classify_gpg_status(0, GOOD + [_valid(10)] + extra)
    assert raised.value.reason == reason


def test_nonzero_exit_with_only_success_lines_is_bad_signature():
    with pytest.raises(Fail) as raised:
        classify_gpg_status(1, GOOD + [_valid(10)])
    assert raised.value.reason == "bad_signature"


def test_no_status_lines_and_a_failed_verifier():
    with pytest.raises(Fail) as raised:
        classify_gpg_status(1, [])
    assert raised.value.reason == "verifier_failed"


def test_two_good_signatures_are_bad_signature():
    with pytest.raises(Fail) as raised:
        classify_gpg_status(0, GOOD + GOOD + [_valid(10)])
    assert raised.value.reason == "bad_signature"


def test_trust_lines_are_ignored():
    lines = GOOD + [_valid(10), _line("TRUST_UNDEFINED", "0", "pgp")]
    assert classify_gpg_status(0, lines) == PRIMARY


@pytest.mark.parametrize("header", [b"parent\n", b"parent \n", b"parent nothex\n"])
def test_malformed_parent_header_is_a_controlled_error(header):
    raw = b"tree " + b"0" * 40 + b"\n" + header + b"\nmessage\n"
    with pytest.raises(SyncError) as raised:
        parse_commit("1" * 40, raw)
    assert raised.value.reason == "bad_commit"

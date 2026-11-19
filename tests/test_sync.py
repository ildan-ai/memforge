"""`memforge sync` against real git repositories and real ssh signatures.

Each test names the behaviour it proves. Every rule here must fail on code
without the rule.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from memforge import sync as sync_mod
from memforge._sync_git import SyncError
from memforge.cli._dispatch import main
from tests.sync_support import (  # noqa: F401  (lab is a fixture)
    git, lab, make_ssh_key, needs_ssh, run_sync, signers_text, verified_head,
)

pytestmark = needs_ssh


@pytest.fixture
def keys(tmp_path):
    return (make_ssh_key(tmp_path, "alice"), make_ssh_key(tmp_path, "bob"),
            make_ssh_key(tmp_path, "carol"))


def pinned(lab, keys, list_text=None):
    """A receiver already synced to the base commit."""
    alice, _bob, _carol = keys
    base = lab.start("ssh", list_text if list_text is not None else signers_text(alice))
    recv = lab.receiver()
    assert run_sync(recv, trust_head=base) == 0
    return base, recv


def _rc(path, *args):
    return subprocess.run(["git", *args], cwd=path, capture_output=True,
                          stdin=subprocess.DEVNULL).returncode


def head(path):
    return git(path, "rev-parse", "HEAD")


# ---- refusals and the principal label ------------------------------

@pytest.mark.parametrize("case,reason", [
    ("unlisted", "ssh_not_verified"),
    ("unsigned", "unsigned"),
    ("badsig", "ssh_not_verified"),
])
def test_refusals_advance_nothing(lab, keys, capsys, case, reason):
    alice, bob, _ = keys
    base, recv = pinned(lab, keys)
    lab.commit({"a.md": "a\n"}, key={"unlisted": bob, "unsigned": None, "badsig": alice}[case])
    if case == "badsig":
        lab.tamper()
    lab.push()
    capsys.readouterr()
    assert run_sync(recv) == 2
    assert f"({reason})" in capsys.readouterr().err
    assert head(recv) == base
    assert verified_head(recv) == base
    assert not (recv / "a.md").exists()


def test_empty_list_gives_list_invalid(lab, keys, capsys):
    alice, _, _ = keys
    base = lab.start("ssh", "# nothing yet\n")
    recv = lab.receiver()
    assert run_sync(recv, trust_head=base) == 0
    lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv) == 2
    assert "(list_invalid)" in capsys.readouterr().err
    assert head(recv) == base


def test_author_identity_does_not_matter_only_the_key(lab, keys, monkeypatch):
    alice, _, _ = keys
    base = lab.start("ssh", alice.signers_line("someone-else@x") + "\n")
    recv = lab.receiver()
    assert run_sync(recv, trust_head=base) == 0
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "a@x")
    new = lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    assert run_sync(recv) == 0
    assert head(recv) == new and verified_head(recv) == new


# ---- a commit cannot authorize itself ------------------------------

def test_self_authorizing_list_edit_is_refused(lab, keys, capsys):
    alice, bob, _ = keys
    base, recv = pinned(lab, keys)
    lab.commit({".memforge/allowed_signers": signers_text(alice, bob)}, key=bob)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv) == 2
    assert "(ssh_not_verified)" in capsys.readouterr().err
    assert head(recv) == base


def test_list_edit_by_listed_signer_passes_and_new_signer_follows(lab, keys):
    alice, bob, _ = keys
    base, recv = pinned(lab, keys)
    lab.commit({".memforge/allowed_signers": signers_text(alice, bob)}, key=alice)
    last = lab.commit({"b.md": "b\n"}, key=bob)
    lab.push()
    assert run_sync(recv) == 0
    assert head(recv) == last and verified_head(recv) == last


# ---- the middle of a range ------------------------------------------

def test_unsigned_commit_in_the_middle_is_refused(lab, keys, capsys):
    alice, _, _ = keys
    base, recv = pinned(lab, keys)
    lab.commit({"1.md": "1\n"}, key=alice)
    middle = lab.commit({"2.md": "2\n"})
    lab.commit({"3.md": "3\n"}, key=alice)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv) == 2
    err = capsys.readouterr().err
    assert "(unsigned)" in err and middle in err
    assert head(recv) == base and verified_head(recv) == base


# ---- isolation ------------------------------------------------------

def test_store_and_global_config_cannot_change_the_verdict(lab, keys, tmp_path, monkeypatch):
    alice, _, _ = keys
    base, recv = pinned(lab, keys)
    new = lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    git(recv, "fetch", "-q")
    revoked = tmp_path / "revoked"
    revoked.write_text(alice.public + "\n")
    allowed = tmp_path / "allowed"
    allowed.write_text(signers_text(alice))

    # Companion: the same git, run in the store's own directory, is changed by the revocation file.
    pins = ["-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={allowed}"]
    assert _rc(recv, *pins, "verify-commit", new) == 0
    assert _rc(recv, *pins, "-c", f"gpg.ssh.revocationFile={revoked}", "verify-commit", new) != 0

    git(recv, "config", "gpg.ssh.revocationFile", str(revoked))
    git(recv, "config", "gpg.ssh.program", "/bin/false")
    git(recv, "config", "gpg.format", "openpgp")
    hostile = tmp_path / "hostile.gitconfig"
    hostile.write_text("[gpg]\n\tformat = openpgp\n[gpg \"ssh\"]\n\tprogram = /bin/false\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(hostile))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "gpg.ssh.program")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "/bin/false")
    assert run_sync(recv) == 0
    assert head(recv) == new


# ---- merges ---------------------------------------------------------

def _merge(lab, key):
    git(lab.writer, "checkout", "-q", "-b", "side")
    lab.commit({"side.md": "s\n"})
    git(lab.writer, "checkout", "-q", "main")
    lab.commit({"main.md": "m\n"}, key=key)
    git(lab.writer, *key.sign_config(), "merge", "-q", "--no-ff", "-S", "-m", "merge", "side")
    lab.push()


def test_listed_merge_vouches_for_an_unsigned_second_parent(lab, keys):
    alice, _, _ = keys
    base, recv = pinned(lab, keys)
    _merge(lab, alice)
    assert run_sync(recv) == 0
    assert (recv / "side.md").exists()


def test_unlisted_merge_is_refused(lab, keys, capsys):
    alice, bob, _ = keys
    base, recv = pinned(lab, keys)
    git(lab.writer, "checkout", "-q", "-b", "side")
    lab.commit({"side.md": "s\n"})
    git(lab.writer, "checkout", "-q", "main")
    lab.commit({"main.md": "m\n"}, key=alice)
    git(lab.writer, *bob.sign_config(), "merge", "-q", "--no-ff", "-S", "-m", "merge", "side")
    lab.push()
    capsys.readouterr()
    assert run_sync(recv) == 2
    assert "(ssh_not_verified)" in capsys.readouterr().err
    assert head(recv) == base


# ---- first sync, state and step order -------------------------------

def test_first_sync_without_a_pin_changes_nothing(lab, keys, capsys):
    base = lab.start("ssh", signers_text(keys[0]))
    recv = lab.receiver()
    before = head(recv)
    assert run_sync(recv) == 1
    assert "no_pin" in capsys.readouterr().err
    assert verified_head(recv) == "" and head(recv) == before


def test_trust_head_and_adopt_local_pin_and_skip_earlier_commits(lab, keys):
    alice, _, _ = keys
    lab.commit({"early.md": "unsigned history\n"})
    base = lab.start("ssh", signers_text(alice))
    recv = lab.receiver()
    assert run_sync(recv, trust_head=base) == 0
    assert verified_head(recv) == base
    lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    assert run_sync(lab.writer, adopt_local=True) == 0
    assert verified_head(lab.writer) == head(lab.writer)


def test_trust_head_needs_the_full_id(lab, keys, capsys):
    base = lab.start("ssh", signers_text(keys[0]))
    assert run_sync(lab.receiver(), trust_head=base[:10]) == 1
    assert "pin_invalid" in capsys.readouterr().err


def test_failed_first_sync_leaves_the_key_unset_and_branch_unmoved(lab, keys, capsys):
    base = lab.start("ssh", signers_text(keys[0]))
    recv = lab.receiver()
    lab.commit({"a.md": "a\n"}, key=keys[1])
    lab.push()
    git(recv, "fetch", "-q")
    capsys.readouterr()
    assert run_sync(recv, trust_head=base) == 2
    assert verified_head(recv) == "" and head(recv) == base


@pytest.mark.parametrize("first_sync", [True, False])
def test_blocked_fast_forward_gives_ff_failed(lab, keys, capsys, first_sync):
    alice, _, _ = keys
    base = lab.start("ssh", signers_text(alice))
    recv = lab.receiver()
    if not first_sync:
        assert run_sync(recv, trust_head=base) == 0
    lab.commit({"seed.md": "changed upstream\n"}, key=alice)
    lab.push()
    (recv / "seed.md").write_text("local edit\n")
    capsys.readouterr()
    kwargs = {"trust_head": base} if first_sync else {}
    assert run_sync(recv, **kwargs) == 1
    assert "ff_failed" in capsys.readouterr().err
    assert head(recv) == base
    assert verified_head(recv) == ("" if first_sync else base)


def test_state_write_failure_after_fast_forward_then_next_run_succeeds(lab, keys, monkeypatch, capsys):
    alice, _, _ = keys
    base, recv = pinned(lab, keys)
    new = lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()

    def broken(store, value):
        raise SyncError("state_write_failed", "forced")

    real = sync_mod._write_verified_head
    monkeypatch.setattr(sync_mod, "_write_verified_head", broken)
    capsys.readouterr()
    assert run_sync(recv) == 1
    assert "state_write_failed" in capsys.readouterr().err
    assert head(recv) == new and verified_head(recv) == base
    monkeypatch.setattr(sync_mod, "_write_verified_head", real)
    assert run_sync(recv) == 0
    assert verified_head(recv) == new


def test_state_is_written_only_after_the_fast_forward(lab, keys, monkeypatch):
    alice, _, _ = keys
    base, recv = pinned(lab, keys)
    new = lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    real = sync_mod._write_verified_head
    seen = []

    def spy(store, value):
        seen.append(head(recv))
        real(store, value)

    monkeypatch.setattr(sync_mod, "_write_verified_head", spy)
    assert run_sync(recv) == 0
    assert seen == [new]

    lab.commit({"seed.md": "changed\n"}, key=alice)
    lab.push()
    (recv / "seed.md").write_text("local edit\n")
    seen.clear()
    assert run_sync(recv) == 1
    assert seen == []


def test_only_one_code_path_writes_the_state_key():
    source = Path(sync_mod.__file__).read_text()
    assert source.count("_write_verified_head(") == 2          # the definition and its one call
    state_lines = [line for line in source.splitlines() if "STATE_KEY" in line and "store.git(" in line]
    assert len(state_lines) == 2                               # the reader and the writer


def test_pin_refused_with_the_key_set_and_nothing_changes(lab, keys, capsys):
    base, recv = pinned(lab, keys)
    for kwargs in ({"trust_head": base}, {"adopt_local": True}):
        capsys.readouterr()
        assert run_sync(recv, **kwargs) == 1
        assert "pin_refused" in capsys.readouterr().err
    assert verified_head(recv) == base


def test_diverged_changes_nothing(lab, keys, capsys):
    alice, _, _ = keys
    base, recv = pinned(lab, keys)
    lab.commit({"remote.md": "r\n"}, key=alice)
    lab.push()
    local = lab.commit({"local.md": "l\n"}, key=alice, repo=recv)
    capsys.readouterr()
    assert run_sync(recv) == 1
    assert "diverged" in capsys.readouterr().err
    assert head(recv) == local and verified_head(recv) == base


def test_local_commits_ahead_of_the_remote_are_verified_and_recorded(lab, keys):
    alice, _, _ = keys
    base, recv = pinned(lab, keys)
    local = lab.commit({"local.md": "l\n"}, key=alice, repo=recv)
    assert run_sync(recv) == 0
    assert verified_head(recv) == local


def test_check_changes_no_local_state_but_reports_head(lab, keys, capsys):
    alice, _, _ = keys
    base, recv = pinned(lab, keys)
    new = lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv, check=True) == 0
    out = capsys.readouterr().out
    assert "HEAD equals verifiedHead: yes" in out
    assert head(recv) == base and verified_head(recv) == base
    assert git(recv, "rev-parse", "origin/main") == new      # the fetch did update remote-tracking refs
    git(recv, "merge", "-q", "--ff-only", new)
    assert run_sync(recv, check=True) == 0
    assert "HEAD equals verifiedHead: no" in capsys.readouterr().out


# ---- rewrite and shallow ---------------------------------------------

def test_rewrite_is_refused_and_the_work_tree_is_untouched(lab, keys, capsys):
    alice, _, _ = keys
    base = lab.start("ssh", signers_text(alice))
    git(lab.writer, "checkout", "-q", "-b", "side")
    side = lab.commit({"side.md": "s\n"}, key=alice)
    git(lab.writer, "checkout", "-q", "main")
    lab.commit({"main.md": "m\n"}, key=alice)
    git(lab.writer, *alice.sign_config(), "merge", "-q", "--no-ff", "-S", "-m", "merge", "side")
    lab.push()
    recv = lab.receiver()
    git(recv, "config", "memforge.verifiedHead", side)      # verified head not on the first-parent chain
    before = head(recv)
    capsys.readouterr()
    assert run_sync(recv) == 2
    err = capsys.readouterr().err
    assert "(history_rewrite)" in err and side in err and before in err
    assert head(recv) == before and verified_head(recv) == side


def test_unknown_verified_head_is_a_rewrite(lab, keys, capsys):
    base, recv = pinned(lab, keys)
    git(recv, "config", "memforge.verifiedHead", "1" * 40)
    capsys.readouterr()
    assert run_sync(recv) == 2
    assert "(history_rewrite)" in capsys.readouterr().err


def test_shallow_clone_crossing_the_boundary_is_refused(lab, keys, capsys):
    alice, _, _ = keys
    base = lab.start("ssh", signers_text(alice))
    for i in range(5):
        lab.commit({f"{i}.md": "x\n"}, key=alice)
    lab.push()
    recv = lab.root / "shallow"
    git(lab.root, "clone", "-q", "--depth", "2", f"file://{lab.origin}", str(recv))
    capsys.readouterr()
    assert run_sync(recv, trust_head=base) == 2
    assert "(shallow_range)" in capsys.readouterr().err
    assert verified_head(recv) == ""


# ---- list rules -----------------------------------------------------

def test_two_lists_in_the_first_parent_is_ambiguous(lab, keys, capsys):
    alice, _, _ = keys
    lab.commit({".memforge/allowed_identities": "me@x https://issuer.example\n"})
    base = lab.start("ssh", signers_text(alice))
    recv = lab.receiver()
    assert run_sync(recv, trust_head=base) == 0
    lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv) == 2
    assert "(ambiguous_list)" in capsys.readouterr().err


def test_no_list_in_the_first_parent_is_missing(lab, keys, capsys):
    alice, _, _ = keys
    lab.commit({"seed.md": "s\n"})
    base = lab.commit({"b.md": "b\n"})
    lab.push()
    recv = lab.receiver()
    lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv, trust_head=base) == 2
    assert "(list_missing)" in capsys.readouterr().err


@pytest.mark.parametrize("edit", ["delete", "empty", "corrupt"])
def test_a_commit_that_removes_the_list_is_refused(lab, keys, capsys, edit):
    alice, _, _ = keys
    base, recv = pinned(lab, keys)
    text = {"delete": None, "empty": "", "corrupt": "this is not a key\n"}[edit]
    lab.commit({".memforge/allowed_signers": text}, key=alice)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv) == 2
    assert "(list_removed)" in capsys.readouterr().err
    assert head(recv) == base


@pytest.mark.parametrize("line", [
    'valid-before="20300101" ', 'valid-after="20200101" ', "cert-authority ",
])
def test_time_and_ca_options_make_the_list_invalid(lab, keys, capsys, line):
    alice, _, _ = keys
    text = f'{alice.label} {line}namespaces="git" {alice.public}\n'
    base = lab.start("ssh", text)
    recv = lab.receiver()
    lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv, trust_head=base) == 2
    assert "(list_invalid)" in capsys.readouterr().err


# ---- ssh determinism -------------------------------------------------

def test_verdict_is_the_same_whole_or_step_by_step_and_across_config(lab, keys, tmp_path, monkeypatch):
    alice, _, _ = keys
    base, whole = pinned(lab, keys)
    step = lab.receiver("step")
    assert run_sync(step, trust_head=base) == 0
    ids = []
    for i in range(3):
        ids.append(lab.commit({f"{i}.md": "x\n"}, key=alice,
                              date="2001-01-01T00:00:00" if i == 1 else "2099-01-01T00:00:00"))
        lab.push()
        assert run_sync(step) == 0
    odd = tmp_path / "odd.gitconfig"
    odd.write_text("[user]\n\tname = Someone Else\n[commit]\n\tgpgsign = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(odd))
    assert run_sync(whole) == 0
    assert verified_head(whole) == verified_head(step) == ids[-1]


def test_sync_reads_no_clock_and_imports_no_removed_module():
    import importlib
    removed = "crypto|registry|revocation|identity|agent_session|sender_sequence|recovery|_security"
    for name in ("sync", "_sync_git", "_sync_verify"):
        source = Path(importlib.import_module(f"memforge.{name}").__file__).read_text()
        assert not re.search(r"\bimport (time|datetime)\b|from (time|datetime) import|time\.time\(", source)
        assert not re.search(rf"from memforge(\.| import )({removed})\b|import memforge\.({removed})\b", source)


# ---- legacy key-system data is ignored ---------------

def test_registry_revocation_and_signature_frontmatter_are_ignored(lab, keys):
    alice, _, _ = keys
    base, recv = pinned(lab, keys)
    lab.commit({
        ".memforge/operator-registry.yaml": "operators: []\n",
        ".memforge/revocations/old.md": "revoked: nothing\n",
        "m.md": "---\nname: x\ndescription: y\ntype: user\nidentity: someone\nsignature: abc\n---\nbody\n",
    }, key=alice)
    lab.push()
    assert run_sync(recv) == 0


# ---- tools resolved once, printed, and used by absolute path -----------

def _fake_ssh_keygen(tmp_path, monkeypatch, body):
    directory = tmp_path / "fakebin"
    directory.mkdir()
    script = directory / "ssh-keygen"
    script.write_text(body)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return directory, script


def test_verdict_comes_from_the_printed_absolute_path(lab, keys, tmp_path, monkeypatch, capsys):
    alice, _, _ = keys
    base, recv = pinned(lab, keys)
    lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    directory, script = _fake_ssh_keygen(tmp_path, monkeypatch, "#!/bin/sh\necho fake >&2\nexit 1\n")
    capsys.readouterr()
    assert run_sync(recv) == 2
    captured = capsys.readouterr()
    assert f"verifier ssh-keygen: path={script}" in captured.out
    assert "(ssh_not_verified)" in captured.err and "fake" in captured.err


def test_tool_paths_are_pinned_into_the_git_call(lab, keys, tmp_path, monkeypatch):
    alice, _, _ = keys
    base, recv = pinned(lab, keys)
    lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    log = tmp_path / "args.log"
    real = os.popen("command -v ssh-keygen").read().strip()
    _fake_ssh_keygen(tmp_path, monkeypatch, f'#!/bin/sh\necho "$0" >> "{log}"\nexec "{real}" "$@"\n')
    assert run_sync(recv) == 0
    assert str(tmp_path / "fakebin" / "ssh-keygen") in log.read_text()


def test_each_run_prints_the_mode_line_and_verifier_hashes(lab, keys, capsys):
    alice, _, _ = keys
    base, recv = pinned(lab, keys)
    lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv) == 0
    out = capsys.readouterr().out
    assert "mode ssh: verdict has no receiver-local input except the git and ssh-keygen executables" in out
    assert re.search(r"verifier git: path=/\S+ version=git version \S+ sha256=[0-9a-f]{64}", out)
    assert re.search(r"verifier ssh-keygen: path=/\S+ .* sha256=[0-9a-f]{64}", out)


# ---- misc -----------------------------------------------------------------------

def test_memory_root_below_the_repository_top_is_refused(lab, keys, capsys):
    alice, _, _ = keys
    base = lab.start("ssh", signers_text(alice))
    recv = lab.receiver()
    (recv / "mem").mkdir()
    capsys.readouterr()
    assert run_sync(recv / "mem", trust_head=base) == 1
    assert "root_not_toplevel" in capsys.readouterr().err
    assert verified_head(recv) == ""


def test_memory_root_is_compared_through_symlinks(lab, keys, tmp_path):
    alice, _, _ = keys
    base = lab.start("ssh", signers_text(alice))
    recv = lab.receiver()
    link = tmp_path / "link"
    link.symlink_to(recv)
    assert run_sync(link, trust_head=base) == 0
    assert verified_head(recv) == base


def test_cli_is_registered_and_runs(lab, keys, monkeypatch, capsys):
    alice, _, _ = keys
    base = lab.start("ssh", signers_text(alice))
    recv = lab.receiver()
    assert main(["sync", "--memory-root", str(recv), "--trust-head", base]) == 0
    assert verified_head(recv) == base


def test_throwaway_directory_is_removed_on_success_failure_and_crash(lab, keys, tmp_path, monkeypatch):
    alice, bob, _ = keys
    scratch = tmp_path / "tmpdir"
    scratch.mkdir()
    monkeypatch.setenv("TMPDIR", str(scratch))
    base, recv = pinned(lab, keys)
    lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    assert run_sync(recv) == 0
    assert list(scratch.iterdir()) == []
    lab.commit({"b.md": "b\n"}, key=bob)
    lab.push()
    assert run_sync(recv) == 2
    assert list(scratch.iterdir()) == []

    def crash(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(sync_mod, "_verify_range", crash)
    with pytest.raises(RuntimeError):
        run_sync(recv)
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("check", [False, True])
def test_only_the_upstream_branch_is_fetched(lab, keys, check):
    alice, _, _ = keys
    base, recv = pinned(lab, keys)
    git(lab.writer, "push", "-q", "origin", "main:other")
    git(recv, "fetch", "-q")
    other_before = git(recv, "rev-parse", "origin/other")
    git(lab.writer, "checkout", "-q", "-b", "other")
    lab.commit({"o.md": "o\n"}, key=alice)
    git(lab.writer, "push", "-q", "origin", "other")
    git(lab.writer, "checkout", "-q", "main")
    new = lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    assert run_sync(recv, check=check) == 0
    assert git(recv, "rev-parse", "origin/main") == new
    assert git(recv, "rev-parse", "origin/other") == other_before

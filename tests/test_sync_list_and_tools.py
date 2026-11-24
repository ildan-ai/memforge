"""The signer-list grammar is narrower than ssh-keygen's, and sync's tools are
never found in the store's work tree.

Every rule here must fail on code without the rule.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys

import pytest

from memforge._sync_git import Fail
from memforge._sync_verify import parse_list
from tests.sync_support import (  # noqa: F401  (lab is a fixture)
    lab, make_ssh_key, needs_sh, needs_ssh, run_sync, signers_text,
)

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterialForTestsOnly000000000000"
OTHER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOtherKeyMaterialForTestsOnly00000000000"
GOOD = f'alice@example.org namespaces="git" {KEY}\n'


# ---- list grammar ----------------------------------------------------------------

@pytest.mark.parametrize("hostile", [
    # Comments to Python's strip(), live lines to ssh-keygen.
    f"\x0b# {OTHER}\n",
    f"\x0c# {OTHER}\n",
    f"\u00a0# {OTHER}\n",
    # Line breaks to Python's splitlines(), not to ssh-keygen.
    f"# note\x1cmallory {OTHER}\n",
    f"# note\rmallory {OTHER}\n",
    # A quote ends a field with no space, hiding an option from the word check.
    f"*\"\"cert-authority {OTHER}\n",
    f'alice""valid-before="20200101" {OTHER}\n',
    f'alice namespaces="git"cert-authority {OTHER}\n',
    # A quoted principal that shows one key while another is trusted.
    f'"bob {KEY}"namespaces="git" {OTHER}\n',
    # Non-ASCII in an entry.
    f'al\u0456ce namespaces="git" {OTHER}\n',
])
def test_a_line_ssh_keygen_reads_differently_makes_the_list_invalid(hostile):
    with pytest.raises(Fail) as exc:
        parse_list("ssh", (GOOD + hostile).encode())
    assert exc.value.reason == "list_invalid"


@pytest.mark.parametrize("text", [
    GOOD,
    GOOD.replace("\n", "\r\n"),
    f"# signers\n\n  \t# indented comment\n{GOOD}\n",
    f"# Zo\u00eb's laptop\n{GOOD}",
    f"# note\u2028mallory {OTHER}\n{GOOD}",
    f'alice@example.org,bob@example.org NAMESPACES="git,file" {KEY} alice laptop\n',
    f"\talice@example.org {KEY}  \n",
])
def test_well_formed_lists_still_parse(text):
    assert len(parse_list("ssh", text.encode())) == 1


@needs_ssh
def test_a_signer_hidden_behind_a_vertical_tab_is_not_trusted(lab, tmp_path, capsys):
    alice, mallory = make_ssh_key(tmp_path, "alice"), make_ssh_key(tmp_path, "mallory")
    base = lab.start("ssh", signers_text(alice))
    recv = lab.receiver()
    assert run_sync(recv, trust_head=base) == 0
    # A diff shows the added line as a comment.
    lab.commit({".memforge/allowed_signers": signers_text(alice) + f"\x0b# {mallory.public}\n"},
               key=alice)
    lab.commit({"evil.md": "injected\n"}, key=mallory)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv) == 2
    assert "list_invalid: line 2: control character" in capsys.readouterr().err
    assert not (recv / "evil.md").exists()


# ---- tool lookup -----------------------------------------------------------------

def _planted(folder, name, marker):
    script = folder / name
    script.write_text(f'#!/bin/sh\necho ran >> "{marker}"\nexit 0\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "store"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True, timeout=30)
    monkeypatch.chdir(root)
    return root


@needs_sh
def test_a_dot_or_empty_path_entry_never_finds_a_tool_in_the_store(store, tmp_path, monkeypatch, capsys):
    marker = tmp_path / "ran"
    for name in ("git", "ssh-keygen", "gpg", "gitsign"):
        _planted(store, name, marker)
    monkeypatch.setenv("PATH", f".{os.pathsep}{os.environ['PATH']}{os.pathsep}")
    run_sync(store)
    assert not marker.exists()
    assert f"path={store}" not in capsys.readouterr().out


@needs_sh
def test_windows_current_folder_lookup_is_not_used(store, tmp_path, monkeypatch):
    marker = tmp_path / "ran"
    _planted(store, "gpg.CMD", marker)
    monkeypatch.setenv("PATHEXT", os.pathsep.join([".CMD", "."]))
    monkeypatch.setattr(shutil, "_win_path_needs_curdir", lambda cmd, mode: True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    run_sync(store)
    monkeypatch.undo()
    assert not marker.exists()


@needs_sh
def test_a_tool_inside_the_store_is_refused_before_it_runs(store, tmp_path, monkeypatch, capsys):
    marker = tmp_path / "ran"
    folder = store / "bin"
    folder.mkdir()
    _planted(folder, "ssh-keygen", marker)
    _planted(folder, "ssh", marker)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    assert run_sync(store) == 1
    assert "tool_in_store" in capsys.readouterr().err
    assert not marker.exists()


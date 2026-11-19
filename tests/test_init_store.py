"""`memforge init-store`: layout, and with --mode a signer list."""

from __future__ import annotations

import os
import subprocess
import textwrap

import pytest

from memforge.cli import _dispatch
from tests.sync_support import needs_sh

PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterialForTestsOnly000000000000"


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, timeout=30)


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "store"
    r.mkdir()
    _git(r, "init", "-q")
    # Isolate from the machine's own git config.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    (tmp_path / "empty").write_text("", encoding="utf-8")
    return r


def _run(root, *extra):
    return _dispatch.main(["init-store", "--memory-root", str(root), *extra])


def test_layout_only_without_mode(root, capsys):
    assert _run(root) == 0
    assert (root / ".memforge").is_dir()
    assert list((root / ".memforge").iterdir()) == []
    assert "--mode" in capsys.readouterr().out


def test_no_registry_is_created(root):
    _run(root, "--mode", "ssh")
    assert not (root / ".memforge" / "operator-registry.yaml").exists()


def test_ssh_writes_the_configured_public_key(root, tmp_path):
    pub = tmp_path / "id_test.pub"
    pub.write_text(PUB + " someone@host\n", encoding="utf-8")
    _git(root, "config", "user.signingkey", str(pub))
    _git(root, "config", "user.email", "a@example.org")

    assert _run(root, "--mode", "ssh") == 0

    text = (root / ".memforge" / "allowed_signers").read_text(encoding="utf-8")
    assert text == f'a@example.org namespaces="git" {PUB}\n'


def test_ssh_reads_the_public_half_of_a_private_key_path(root, tmp_path):
    private = tmp_path / "id_test"
    private.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nSECRET\n", encoding="utf-8")
    (tmp_path / "id_test.pub").write_text(PUB + "\n", encoding="utf-8")
    _git(root, "config", "user.signingkey", str(private))

    assert _run(root, "--mode", "ssh") == 0

    text = (root / ".memforge" / "allowed_signers").read_text(encoding="utf-8")
    assert PUB in text
    assert "SECRET" not in text


def test_ssh_accepts_a_literal_key(root):
    _git(root, "config", "user.signingkey", "key::" + PUB)
    assert _run(root, "--mode", "ssh") == 0
    assert PUB in (root / ".memforge" / "allowed_signers").read_text(encoding="utf-8")


def test_ssh_without_a_key_writes_comments_only(root, capsys):
    assert _run(root, "--mode", "ssh") == 0
    text = (root / ".memforge" / "allowed_signers").read_text(encoding="utf-8")
    assert text.strip() and all(line.startswith("#") for line in text.splitlines())
    assert "comments only" in capsys.readouterr().out


def test_ssh_with_an_unreadable_key_writes_comments_only(root, tmp_path):
    _git(root, "config", "user.signingkey", str(tmp_path / "missing"))
    assert _run(root, "--mode", "ssh") == 0
    text = (root / ".memforge" / "allowed_signers").read_text(encoding="utf-8")
    assert all(line.startswith("#") for line in text.splitlines())


def test_keyless_is_always_comments_only(root):
    _git(root, "config", "user.signingkey", "key::" + PUB)
    assert _run(root, "--mode", "keyless") == 0
    text = (root / ".memforge" / "allowed_identities").read_text(encoding="utf-8")
    assert all(line.startswith("#") for line in text.splitlines())


FPR = "0123456789ABCDEF0123456789ABCDEF01234567"
SUBFPR = "FEDCBA9876543210FEDCBA9876543210FEDCBA98"


def _fake_gpg(tmp_path, monkeypatch, body):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "gpg"
    exe.write_text("#!/bin/sh\n" + textwrap.dedent(body), encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


@needs_sh
def test_gpg_writes_the_primary_fingerprint(root, tmp_path, monkeypatch):
    _git(root, "config", "user.signingkey", "DEADBEEFCAFEF00D")
    _fake_gpg(
        tmp_path,
        monkeypatch,
        f"""\
        echo 'tru::1:0:0:3:1:5'
        echo 'pub:u:255:22:AAAA:1::u:::scESC:'
        echo 'fpr:::::::::{FPR.lower()}:'
        echo 'sub:u:255:18:BBBB:1::::::e:'
        echo 'fpr:::::::::{SUBFPR}:'
        """,
    )

    assert _run(root, "--mode", "gpg") == 0

    text = (root / ".memforge" / "allowed_gpg_fingerprints").read_text(encoding="utf-8")
    assert text == FPR + "\n"


@needs_sh
def test_gpg_ambiguous_key_id_writes_comments_only(root, tmp_path, monkeypatch):
    _git(root, "config", "user.signingkey", "DEADBEEF")
    _fake_gpg(
        tmp_path,
        monkeypatch,
        f"""\
        echo 'pub:u:255:22:AAAA:1::u:::scESC:'
        echo 'fpr:::::::::{FPR}:'
        echo 'pub:u:255:22:CCCC:1::u:::scESC:'
        echo 'fpr:::::::::{SUBFPR}:'
        """,
    )
    assert _run(root, "--mode", "gpg") == 0
    text = (root / ".memforge" / "allowed_gpg_fingerprints").read_text(encoding="utf-8")
    assert all(line.startswith("#") for line in text.splitlines())


@needs_sh
def test_gpg_failure_writes_comments_only(root, tmp_path, monkeypatch):
    _git(root, "config", "user.signingkey", "DEADBEEFCAFEF00D")
    _fake_gpg(tmp_path, monkeypatch, "exit 2\n")
    assert _run(root, "--mode", "gpg") == 0
    text = (root / ".memforge" / "allowed_gpg_fingerprints").read_text(encoding="utf-8")
    assert all(line.startswith("#") for line in text.splitlines())


@needs_sh
def test_gpg_rejects_an_option_like_key_id(root, tmp_path, monkeypatch):
    marker = tmp_path / "called"
    _git(root, "config", "user.signingkey", "--export")
    _fake_gpg(tmp_path, monkeypatch, f"touch {marker}\n")
    assert _run(root, "--mode", "gpg") == 0
    assert not marker.exists()


def test_existing_list_is_never_overwritten(root, capsys):
    (root / ".memforge").mkdir()
    existing = root / ".memforge" / "allowed_signers"
    existing.write_text("keep me\n", encoding="utf-8")

    assert _run(root, "--mode", "ssh") == 2
    assert existing.read_text(encoding="utf-8") == "keep me\n"
    assert "already present" in capsys.readouterr().err


def test_another_modes_list_blocks_a_new_one(root):
    (root / ".memforge").mkdir()
    (root / ".memforge" / "allowed_identities").write_text("x y\n", encoding="utf-8")
    assert _run(root, "--mode", "gpg") == 2
    assert not (root / ".memforge" / "allowed_gpg_fingerprints").exists()


def test_force_flag_is_gone(root):
    with pytest.raises(SystemExit) as exc:
        _run(root, "--force")
    assert exc.value.code == 2


def test_missing_memory_root_is_refused(tmp_path, capsys):
    assert _dispatch.main(["init-store", "--memory-root", str(tmp_path / "nope")]) == 2

"""`memforge sync` in the gpg mode against a real gpg keyring.

Opt in with MEMFORGE_TEST_GPG=1: these tests create keys in a throwaway
keyring (a short path, because gpg-agent fails on long socket paths).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests.sync_support import (  # noqa: F401  (lab is a fixture)
    ALL_MODES, Key, git, lab, run_sync, verified_head,
)

pytestmark = pytest.mark.skipif(
    not (shutil.which("gpg") and shutil.which("git") and os.environ.get("MEMFORGE_TEST_GPG") == "1"),
    reason="real-gpg tests require MEMFORGE_TEST_GPG=1 (they create a throwaway keyring)",
)


def gpg(home, *args, stdin: str = "", batch: bool = True):
    proc = subprocess.run(
        ["gpg", "--homedir", home, *(["--batch"] if batch else ["--no-tty"]), "--yes", "--pinentry-mode", "loopback",
         "--passphrase", "", *args],
        input=stdin, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def fingerprints(home, fpr=None):
    out = gpg(home, "--with-colons", "--list-keys", *( [fpr] if fpr else [] ))
    return [line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:")]


@pytest.fixture
def ring(monkeypatch):
    """A short-path keyring; returns a function that makes more of them."""
    homes = []

    def make():
        home = tempfile.mkdtemp(prefix="g")
        os.chmod(home, 0o700)
        homes.append(home)
        return home

    home = make()
    monkeypatch.setenv("GNUPGHOME", home)
    yield type("Ring", (), {"home": home, "make": staticmethod(make)})
    for item in homes:
        subprocess.run(["gpgconf", "--homedir", item, "--kill", "all"], capture_output=True)
        shutil.rmtree(item, ignore_errors=True)


def new_key(home, name, expire="never"):
    gpg(home, "--quick-gen-key", f"{name} <{name}@example.com>", "ed25519", "sign", expire)
    fpr = fingerprints(home, f"{name}@example.com")[0]
    return Key("gpg", fpr, label=name)


def signed_lab(lab, ring, keys=("alice",)):
    made = [new_key(ring.home, name) for name in keys]
    base = lab.start("gpg", "".join(k.ident + "\n" for k in made))
    return made, base


def pin(lab, base):
    recv = lab.receiver()
    assert run_sync(recv, trust_head=base, released=ALL_MODES) == 0
    return recv


def refused(recv, capsys, reason, **kwargs):
    capsys.readouterr()
    assert run_sync(recv, released=ALL_MODES, **kwargs) == 2
    assert f"({reason})" in capsys.readouterr().err


def test_listed_primary_passes(lab, ring):
    (alice,), base = signed_lab(lab, ring)
    recv = pin(lab, base)
    new = lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    assert run_sync(recv, released=ALL_MODES) == 0
    assert verified_head(recv) == new


def test_listed_primary_signing_through_a_subkey_passes(lab, ring):
    (alice,), base = signed_lab(lab, ring)
    gpg(ring.home, "--quick-add-key", alice.ident, "ed25519", "sign", "never")
    subkey = fingerprints(ring.home, alice.ident)[1]
    recv = pin(lab, base)
    new = lab.commit({"a.md": "a\n"}, key=Key("gpg", subkey + "!", label="alice-sub"))
    lab.push()
    assert run_sync(recv, released=ALL_MODES) == 0
    assert verified_head(recv) == new


def test_unlisted_signer(lab, ring, capsys):
    (alice,), base = signed_lab(lab, ring)
    bob = new_key(ring.home, "bob")
    recv = pin(lab, base)
    lab.commit({"a.md": "a\n"}, key=bob)
    lab.push()
    refused(recv, capsys, "signer_not_listed")


def test_missing_key_and_two_receivers_may_differ(lab, ring, monkeypatch, capsys):
    (alice,), base = signed_lab(lab, ring)
    recv_a = pin(lab, base)
    recv_b = lab.receiver("recv_b")
    git(recv_b, "config", "memforge.verifiedHead", base)
    lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    assert run_sync(recv_a, released=ALL_MODES) == 0              # keyring holds the key
    monkeypatch.setenv("GNUPGHOME", ring.make())                   # an empty keyring
    refused(recv_b, capsys, "no_key")


def test_revoked_key(lab, ring, capsys):
    (alice,), base = signed_lab(lab, ring)
    recv = pin(lab, base)
    lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    cert = Path(ring.home) / "rev.asc"
    gpg(ring.home, "--command-fd", "0", "--output", str(cert), "--gen-revoke", alice.ident,
        stdin="y\n0\n\ny\n", batch=False)
    gpg(ring.home, "--import", str(cert))
    refused(recv, capsys, "revoked")


def test_expired_key(lab, ring, tmp_path, monkeypatch, capsys):
    alice = new_key(ring.home, "alice", expire="1d")
    base = lab.start("gpg", alice.ident + "\n")
    recv = pin(lab, base)
    lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    real = shutil.which("gpg")
    wrapper = tmp_path / "timebin"
    wrapper.mkdir()
    script = wrapper / "gpg"
    script.write_text(f'#!/bin/sh\nexec "{real}" --faked-system-time 20991231T000000 "$@"\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{wrapper}{os.pathsep}{os.environ['PATH']}")
    refused(recv, capsys, "expired")


def test_unsigned_commit(lab, ring, capsys):
    (alice,), base = signed_lab(lab, ring)
    recv = pin(lab, base)
    lab.commit({"a.md": "a\n"})
    lab.push()
    refused(recv, capsys, "unsigned")


def test_tampered_commit(lab, ring, capsys):
    (alice,), base = signed_lab(lab, ring)
    recv = pin(lab, base)
    lab.commit({"a.md": "a\n"}, key=alice)
    lab.tamper()
    lab.push()
    refused(recv, capsys, "bad_signature")


def test_message_text_that_looks_like_a_status_line_changes_nothing(lab, ring, capsys):
    (alice,), base = signed_lab(lab, ring)
    bob = new_key(ring.home, "bob")
    recv = pin(lab, base)
    fake = f"x\n\n[GNUPG:] GOODSIG 0 bob\n[GNUPG:] VALIDSIG {alice.ident} 1700000000 1 0 4 0 22 10 00\n"
    lab.commit({"a.md": "a\n"}, key=bob, message=fake)
    lab.push()
    refused(recv, capsys, "signer_not_listed")


def test_self_authorizing_edit_is_refused_and_a_listed_edit_passes(lab, ring, capsys):
    (alice,), base = signed_lab(lab, ring)
    bob = new_key(ring.home, "bob")
    both = f"{alice.ident}\n{bob.ident}\n"
    recv = pin(lab, base)
    lab.commit({".memforge/allowed_gpg_fingerprints": both}, key=bob)
    lab.push()
    refused(recv, capsys, "signer_not_listed")
    git(lab.writer, "reset", "-q", "--hard", base)
    lab.commit({".memforge/allowed_gpg_fingerprints": both}, key=alice)
    last = lab.commit({"b.md": "b\n"}, key=bob)
    lab.push(force=True)
    git(recv, "fetch", "-q")
    assert run_sync(recv, released=ALL_MODES) == 0
    assert verified_head(recv) == last

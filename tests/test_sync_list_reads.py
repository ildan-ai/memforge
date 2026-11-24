"""Sync reads only bounded, plain objects from a fetched tree.

A remote needs no key to push a commit whose tree entries name any object, and
git's default fetch checks neither the object an entry names nor the size of a
tree. These commits carry a junk signature header so they reach the reads of
their own list, and every rule here must fail on code without the rule.
"""

from __future__ import annotations

import subprocess
import tracemalloc

import pytest

from memforge._sync_git import Fail
from memforge._sync_verify import parse_list
from tests.sync_support import (  # noqa: F401  (lab is a fixture)
    git, lab, make_ssh_key, needs_ssh, run_sync, signers_text, verified_head,
)

# The largest list a conforming sync reads, as the spec states it.
LIST_LIMIT = 1024 * 1024
# Entries in the wide trees below: their listing is megabytes, so reading it
# whole shows far above the bound the tests hold sync to.
WIDE = 200_000
# What sync may allocate judging one wide commit; the listing alone is over 10 MiB.
MEMORY_BOUND = 4 * 1024 * 1024


def _object(repo, kind: str, data: bytes) -> str:
    proc = subprocess.run(["git", "hash-object", "-t", kind, "-w", "--literally", "--stdin"],
                          cwd=repo, input=data, capture_output=True, check=True)
    return proc.stdout.decode().strip()


def _entry(mode: str, name: str, oid: str) -> bytes:
    return mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(oid)


def _tag(repo, target: str) -> str:
    body = (f"object {target}\ntype blob\ntag wrap\n"
            "tagger t <t@example.invalid> 1700000000 +0000\n\nwrap\n")
    proc = subprocess.run(["git", "mktag"], cwd=repo, input=body.encode(),
                          capture_output=True, check=True)
    return proc.stdout.decode().strip()


def _push_hostile(lab, parent: str, memforge_tree: bytes) -> str:
    """Push a commit with a junk signature header whose `.memforge` tree is the given bytes."""
    repo = lab.writer
    inner = _object(repo, "tree", memforge_tree)
    root = _object(repo, "tree", _entry("40000", ".memforge", inner))
    header = (f"tree {root}\nparent {parent}\n"
              "author t <t@example.invalid> 1700000000 +0000\n"
              "committer t <t@example.invalid> 1700000000 +0000\n"
              "gpgsig junk\n\nhostile\n")
    commit = _object(repo, "commit", header.encode())
    git(repo, "push", "-q", "origin", f"{commit}:refs/heads/main")
    return commit


@pytest.fixture
def pinned(lab, tmp_path):
    """A base with a valid list and a receiver pinned to it."""
    alice = make_ssh_key(tmp_path, "alice")
    base = lab.start("ssh", signers_text(alice))
    recv = lab.receiver()
    assert run_sync(recv, trust_head=base) == 0
    return alice, base, recv


@pytest.fixture
def blobs_read(monkeypatch):
    """The object ids sync reads in full as a list."""
    from memforge import _sync_verify as sync_verify
    read: list[str] = []
    real = sync_verify._blob

    def recording(iso, blob):
        read.append(blob)
        return real(iso, blob)

    monkeypatch.setattr(sync_verify, "_blob", recording)
    return read


# ---- the object a list entry names -------------------------------------------

@needs_ssh
def test_a_list_entry_naming_a_tag_of_a_large_blob_is_list_invalid_and_never_read(
        lab, pinned, capsys, blobs_read):
    _, base, recv = pinned
    big = _object(lab.writer, "blob", b"a" * (LIST_LIMIT + 1))
    tag = _tag(lab.writer, big)
    _push_hostile(lab, base, _entry("100644", "allowed_signers", tag) + _entry("100644", "pad", big))
    capsys.readouterr()
    assert run_sync(recv) == 2
    err = capsys.readouterr().err
    assert "(list_removed)" in err
    assert "list_invalid: list entry names a tag, not a blob" in err
    assert tag not in blobs_read
    assert verified_head(recv) == base


@needs_ssh
def test_a_list_entry_naming_a_tag_of_a_valid_list_is_list_invalid(lab, pinned, capsys):
    alice, base, recv = pinned
    tag = _tag(lab.writer, _object(lab.writer, "blob", signers_text(alice).encode()))
    _push_hostile(lab, base, _entry("100644", "allowed_signers", tag))
    capsys.readouterr()
    assert run_sync(recv) == 2
    err = capsys.readouterr().err
    assert "(list_removed)" in err
    assert "names a tag, not a blob" in err
    assert verified_head(recv) == base


@needs_ssh
def test_a_list_entry_naming_a_tag_whose_target_was_never_sent_is_refused(lab, pinned, capsys):
    _, base, recv = pinned
    # The target exists in the writer but nothing reachable names it as a
    # blob, so the push does not send it.
    tag = _tag(lab.writer, _object(lab.writer, "blob", b"never sent\n"))
    _push_hostile(lab, base, _entry("100644", "allowed_signers", tag))
    capsys.readouterr()
    assert run_sync(recv) == 2
    err = capsys.readouterr().err
    assert "(list_removed)" in err
    assert "names a tag, not a blob" in err
    assert verified_head(recv) == base


# ---- the size of the listing --------------------------------------------------

def _peak_of_sync(recv) -> tuple[int, int]:
    tracemalloc.start()
    try:
        code = run_sync(recv)
        return code, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


@needs_ssh
def test_a_memforge_folder_with_many_entries_is_judged_in_bounded_memory(lab, pinned, capsys):
    _, base, recv = pinned
    blob = _object(lab.writer, "blob", b"x\n")
    _push_hostile(lab, base, b"".join(_entry("100644", f"f{i:09d}", blob) for i in range(WIDE)))
    capsys.readouterr()
    code, peak = _peak_of_sync(recv)
    assert code == 2
    assert "(list_removed)" in capsys.readouterr().err
    assert peak < MEMORY_BOUND, f"sync allocated {peak} bytes"
    assert verified_head(recv) == base


@needs_ssh
def test_a_list_name_repeated_many_times_is_ambiguous_and_read_in_bounded_memory(
        lab, pinned, capsys):
    alice, base, recv = pinned
    blob = _object(lab.writer, "blob", signers_text(alice).encode())
    _push_hostile(lab, base, _entry("100644", "allowed_signers", blob) * WIDE)
    capsys.readouterr()
    code, peak = _peak_of_sync(recv)
    assert code == 2
    err = capsys.readouterr().err
    assert "(list_removed)" in err
    assert "ambiguous_list" in err
    assert peak < MEMORY_BOUND, f"sync allocated {peak} bytes"
    assert verified_head(recv) == base


@needs_ssh
def test_a_list_name_given_twice_is_ambiguous(lab, pinned, capsys):
    alice, base, recv = pinned
    blob = _object(lab.writer, "blob", signers_text(alice).encode())
    _push_hostile(lab, base, _entry("100644", "allowed_signers", blob) * 2)
    capsys.readouterr()
    assert run_sync(recv) == 2
    err = capsys.readouterr().err
    assert "(list_removed)" in err
    assert "ambiguous_list" in err
    assert verified_head(recv) == base


# ---- gpg and keyless lines use the ssh line rules ------------------------------

FINGERPRINT = "A" * 40


@pytest.mark.parametrize(("mode", "line"), [
    ("gpg", f"{FINGERPRINT} "),
    ("gpg", f" {FINGERPRINT}"),
    ("gpg", f"{FINGERPRINT} # Zoë"),
    ("keyless", "id@example.org https://issuer.example"),
    ("keyless", "id@example.org https://issuer.example "),
    ("keyless", "é@example.org https://issuer.example"),
])
def test_a_gpg_or_keyless_line_with_non_ascii_is_list_invalid(mode, line):
    with pytest.raises(Fail) as exc:
        parse_list(mode, (line + "\n").encode())
    assert exc.value.reason == "list_invalid"


@pytest.mark.parametrize(("mode", "line", "entry"), [
    ("gpg", f"{FINGERPRINT}", FINGERPRINT),
    ("gpg", f"\t{FINGERPRINT.lower()} \t# laptop key", FINGERPRINT),
    ("keyless", "id@example.org https://issuer.example",
     ("id@example.org", "https://issuer.example")),
    ("keyless", " id@example.org\t \thttps://issuer.example\t",
     ("id@example.org", "https://issuer.example")),
])
def test_a_gpg_or_keyless_line_split_on_space_and_tab_still_parses(mode, line, entry):
    assert parse_list(mode, (line + "\n").encode()) == (entry,)

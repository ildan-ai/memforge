"""A signer list is read only when it is small enough, and an unsigned commit's
own list is never read.

A listed signer can commit a valid list padded with comments to any size, and
every receiver would read it whole for each commit judged against it. An
unsigned commit is refused for its signature state alone, so its own list is
not worth reading. Every rule here must fail on code without the rule.
"""

from __future__ import annotations

import pytest

from memforge import _sync_verify as sync_verify
from tests.sync_support import (  # noqa: F401  (lab is a fixture)
    git, lab, make_ssh_key, needs_ssh, run_sync, signers_text, verified_head,
)

pytestmark = needs_ssh

LIST = ".memforge/allowed_signers"
# The largest list a conforming sync reads, as the spec states it.
LIST_LIMIT = 1024 * 1024


def _padded(text: str, size: int) -> str:
    """`text` followed by one comment line, `size` bytes in all."""
    filler = size - len(text.encode()) - len("# \n")
    return text + "# " + "x" * filler + "\n"


@pytest.fixture
def blobs_read(monkeypatch):
    """The blob ids sync reads in full."""
    read: list[str] = []
    real = sync_verify._blob

    def recording(iso, blob):
        read.append(blob)
        return real(iso, blob)

    monkeypatch.setattr(sync_verify, "_blob", recording)
    return read


def _blob_id(repo, commit: str) -> str:
    return git(repo, "rev-parse", f"{commit}:{LIST}")


def test_an_oversized_parent_list_is_list_invalid_and_never_read(lab, tmp_path, capsys, blobs_read):
    alice = make_ssh_key(tmp_path, "alice")
    base = lab.start("ssh", _padded(signers_text(alice), LIST_LIMIT + 1))
    recv = lab.receiver()
    assert run_sync(recv, trust_head=base) == 0
    lab.commit({LIST: signers_text(alice)}, key=alice)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv) == 2
    err = capsys.readouterr().err
    assert "(list_invalid)" in err
    assert f"over the {LIST_LIMIT}-byte limit" in err
    assert _blob_id(lab.writer, base) not in blobs_read
    assert verified_head(recv) == base


def test_an_oversized_own_list_is_list_removed_and_never_read(lab, tmp_path, capsys, blobs_read):
    alice = make_ssh_key(tmp_path, "alice")
    base = lab.start("ssh", signers_text(alice))
    recv = lab.receiver()
    assert run_sync(recv, trust_head=base) == 0
    big = lab.commit({LIST: _padded(signers_text(alice), LIST_LIMIT + 1)}, key=alice)
    lab.push()
    capsys.readouterr()
    assert run_sync(recv) == 2
    err = capsys.readouterr().err
    assert "(list_removed)" in err
    assert "list_invalid" in err
    assert _blob_id(lab.writer, big) not in blobs_read
    assert verified_head(recv) == base


def test_a_list_at_the_limit_is_still_read(lab, tmp_path, capsys):
    alice = make_ssh_key(tmp_path, "alice")
    base = lab.start("ssh", _padded(signers_text(alice), LIST_LIMIT))
    recv = lab.receiver()
    assert run_sync(recv, trust_head=base) == 0
    child = lab.commit({"a.md": "a\n"}, key=alice)
    lab.push()
    assert run_sync(recv) == 0
    assert verified_head(recv) == child


def test_an_unsigned_commit_is_refused_before_its_own_list_is_read(lab, tmp_path, capsys, blobs_read):
    alice = make_ssh_key(tmp_path, "alice")
    base = lab.start("ssh", signers_text(alice))
    recv = lab.receiver()
    assert run_sync(recv, trust_head=base) == 0
    unsigned = lab.commit({LIST: "this is not a key\n"})
    lab.push()
    capsys.readouterr()
    assert run_sync(recv) == 2
    assert "(unsigned)" in capsys.readouterr().err
    assert _blob_id(lab.writer, unsigned) not in blobs_read
    assert verified_head(recv) == base

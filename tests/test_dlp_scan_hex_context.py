"""When a bare hex string on a git-context line is, and is not, exempt.

Memory files quote git hashes, and entropy cannot tell a hex hash from a hex
key, so a bare hex string is not reported on a line that names a git object.
That exemption is narrow: a secret word on the line overrides it, and it only
covers the two lengths a git object id can have.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memforge.cli.dlp_scan import scan_text

# Fixture values shaped like digests; none is a live secret.
_HEX_40 = "2fd4e1c67a2d28fced849ee1bb76e7391b93eb12"
_HEX_64 = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
_HEX_48 = _HEX_64[:48]
_HEX_128 = _HEX_64 + _HEX_64[::-1]


def _bare_hex_findings(line: str) -> list[str]:
    return [
        f.severity
        for f in scan_text(line, Path("note.md"))
        if f.pattern == "aws_secret_access_key_bare"
    ]


@pytest.mark.parametrize(
    "line",
    [
        f"secret hash: {_HEX_64}",
        f"token sha256 {_HEX_64}",
        f"the api key digest is {_HEX_64}",
        f"bearer credential, see commit {_HEX_40}",
    ],
)
def test_secret_word_overrides_the_git_context_exemption(line):
    assert _bare_hex_findings(line) == ["BLOCKER"]


@pytest.mark.parametrize("token", [_HEX_48, _HEX_128])
def test_hex_of_a_length_git_never_produces_is_reported_next_to_hash(token):
    assert _bare_hex_findings(f"hash {token}") == ["BLOCKER"]


@pytest.mark.parametrize("line", [f"commit {_HEX_40}", f"tree {_HEX_64}"])
def test_git_object_id_without_a_secret_word_stays_unreported(line):
    assert _bare_hex_findings(line) == []


def test_git_hash_on_a_line_with_a_secret_word_is_reported():
    """The accepted cost of letting the secret word win."""
    assert _bare_hex_findings(f"token rotation landed in commit {_HEX_40}") == ["BLOCKER"]

"""No finding prints the secret it found, whatever the line's whitespace, and
no finding prints another secret found on the same line."""

from __future__ import annotations

from pathlib import Path

import pytest

from memforge.cli import dlp_scan

AWS_KEY = "AKIAABCDEFGHIJKLMNOP"
# A JWT-shaped fixture made of fixed filler, not a real token.
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlc2lnbmF0dXJlc2ln"

LINES = {
    "indented": f"                    key {AWS_KEY} end",
    "tab-indented": f"\t\tkey {AWS_KEY} end",
    "trailing-space": f"key {AWS_KEY}   ",
    "indented-and-trailing": f"    key {AWS_KEY} \t ",
}


@pytest.mark.parametrize("shape", sorted(LINES))
def test_no_excerpt_holds_the_matched_secret(tmp_path: Path, shape: str):
    findings = dlp_scan.scan_text(f"intro\n{LINES[shape]}\n", tmp_path / "k.md")

    expected = LINES[shape].strip().replace(AWS_KEY, "[REDACTED]")
    assert [f.excerpt for f in findings if f.pattern == "aws_access_key_id"] == [expected]
    for finding in findings:
        assert AWS_KEY not in finding.excerpt, finding


@pytest.mark.parametrize("shape", sorted(LINES))
def test_printed_report_never_holds_the_secret(tmp_path: Path, monkeypatch, capsys, shape: str):
    note = tmp_path / "k.md"
    note.write_text(f"intro\n{LINES[shape]}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["memory-dlp-scan", "--paths", str(note), "--strict",
                                     "--no-detect-secrets"])

    assert dlp_scan.main() == 1

    captured = capsys.readouterr()
    assert "[aws_access_key_id]" in captured.out
    assert AWS_KEY not in captured.out + captured.err


def test_a_finding_does_not_print_another_secret_on_its_line(tmp_path: Path):
    line = f"  password = {JWT} and {AWS_KEY}"

    findings = dlp_scan.scan_text(line + "\n", tmp_path / "k.md")

    assert {"aws_access_key_id", "jwt_token"} <= {f.pattern for f in findings}
    for finding in findings:
        assert AWS_KEY not in finding.excerpt, finding
        assert JWT not in finding.excerpt, finding
        for piece in JWT.split("."):
            assert piece not in finding.excerpt, finding


def test_an_unindented_single_secret_excerpt_is_unchanged(tmp_path: Path):
    findings = dlp_scan.scan_text(f"key {AWS_KEY} end\n", tmp_path / "k.md")

    assert [f.excerpt for f in findings if f.pattern == "aws_access_key_id"] == ["key [REDACTED] end"]

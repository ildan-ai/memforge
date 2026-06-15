"""DLP scanner coverage regressions (cluster-D dlp-* findings).

These assert that memory-dlp-scan (the pre-commit gate that actually blocks
commits) catches the secret/PII shapes a human author hand-types, and is not
weaker than lint's advisory pre-scan.
"""

from __future__ import annotations

from pathlib import Path

import secrets

from memforge.cli.dlp_scan import PATTERN_TIER_BY_NAME, main, scan_text


def _patterns(text: str) -> list[str]:
    return [f.pattern for f in scan_text(text, Path("dummy.md"))]


# ---------- dlp-aws-secret-01: bare 40-char AWS secret access key ----------


def test_bare_aws_secret_access_key_is_caught():
    """A bare 40-char AWS secret (no keyword, no assignment) is the part that
    actually grants access; the keyword-anchored aws_secret_access_key pattern
    missed it entirely."""
    text = "I stored the value wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY for later"
    assert "aws_secret_access_key_bare" in _patterns(text)


def test_bare_aws_secret_on_its_own_line():
    """The canonical 'creds across two lines' authoring form: AKIA id on one
    line, the 40-char secret on the next."""
    text = "aws backup creds:\nAKIAIOSFODNN7EXAMPLE\nwJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n"
    pats = _patterns(text)
    assert "aws_access_key_id" in pats
    assert "aws_secret_access_key_bare" in pats


def test_bare_aws_secret_with_base64_padding_is_caught():
    """dlp-01: an AWS secret access key is 40-char base64 and routinely ends in
    '=' padding. The prior bare pattern excluded '=', so a padded key evaded
    both the keyword and bare rules and was committed to a public-history repo.
    The token below ends in a single '=' pad and must now be flagged."""
    text = "AbCdEf01/GhIjKl23+MnOpQr45StUvWx67Yz0K9j=\n"
    assert "aws_secret_access_key_bare" in _patterns(text)


def test_bare_aws_secret_with_double_padding_is_caught():
    text = "AbCdEf01/GhIjKl23+MnOpQr45StUvWx67Yz09jQ==\n"
    assert "aws_secret_access_key_bare" in _patterns(text)


def test_bare_aws_secret_pattern_is_restricted_tier():
    assert PATTERN_TIER_BY_NAME.get("aws_secret_access_key_bare") == "restricted"


def test_low_entropy_40_char_run_not_flagged_as_secret():
    """A low-entropy 40-char run (repeated chars) must NOT trip the bare-secret
    rule; the entropy gate keeps prose false-positives down."""
    text = "a" * 40
    assert "aws_secret_access_key_bare" not in _patterns(text)


# ---------- dlp-bare-secret-03: 64-hex / >40-base64 bare secrets ----------


def test_bare_64_char_hex_secret_is_caught():
    """dlp-bare-secret-03: a 64-char hex API token (Mailgun / SendGrid-legacy /
    Square / generic HMAC) on its own line with no co-located keyword evaded the
    fixed-length-40 bare rule and committed clean. It must now be flagged."""
    token = secrets.token_hex(32)  # 64 hex chars, high entropy
    assert "aws_secret_access_key_bare" in _patterns(token), (
        f"64-hex secret {token!r} must be caught"
    )


def test_bare_over_40_char_base64_secret_is_caught():
    """A >40-char base64 token on its own line (the old rule was FIXED-40) must
    now fire."""
    token = "".join(
        secrets.choice(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        )
        for _ in range(50)
    )
    assert "aws_secret_access_key_bare" in _patterns(token)


def test_git_commit_hash_in_context_not_flagged():
    """A 40/64-hex git commit hash that MemForge legitimately stores in a memory
    body (always keyword-adjacent: 'Commit `<hash>`') must NOT trip the bare-hex
    rule; entropy cannot separate it from a hex secret, so the git-object context
    keyword is the precision lever."""
    assert "aws_secret_access_key_bare" not in _patterns(
        "Commit `e24f9ef5ef71358b05f61c29bbcf989bcb7e4150` and 0 forks"
    )
    assert "aws_secret_access_key_bare" not in _patterns(
        "git anchor tag points at " + secrets.token_hex(32)
    )


def test_low_entropy_64_hex_run_not_flagged():
    """A degenerate low-entropy 64-char hex run (repeated chars) stays below the
    entropy floor and is not flagged."""
    assert "aws_secret_access_key_bare" not in _patterns("a" * 64)


# ---------- dlp-strict-exit: --strict is BLOCKER-only; --strict-major is any ----------


def test_strict_does_not_exit_on_major_only(tmp_path, monkeypatch):
    """dlp-strict-exit: a file with ONLY a MAJOR finding (e.g. an AWS ARN) must
    pass --strict (exit 0); --strict is documented and implemented as
    BLOCKER-only."""
    f = tmp_path / "m.md"
    f.write_text("role arn:aws:iam::123456789012:user/Dev referenced here\n", encoding="utf-8")
    # Sanity: the only finding is MAJOR (aws_arn), no BLOCKER.
    sevs = {fd.severity for fd in scan_text(f.read_text(), f)}
    assert "MAJOR" in sevs and "BLOCKER" not in sevs
    monkeypatch.setattr(
        "sys.argv",
        ["memory-dlp-scan", "--paths", str(f), "--strict", "--no-detect-secrets"],
    )
    assert main() == 0


def test_strict_major_exits_on_major(tmp_path, monkeypatch):
    """--strict-major is the any-finding gate: a MAJOR-only file exits 1."""
    f = tmp_path / "m.md"
    f.write_text("role arn:aws:iam::123456789012:user/Dev referenced here\n", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["memory-dlp-scan", "--paths", str(f), "--strict-major", "--no-detect-secrets"],
    )
    assert main() == 1


def test_strict_exits_on_blocker(tmp_path, monkeypatch):
    """--strict still exits 1 on a BLOCKER (regression guard)."""
    f = tmp_path / "m.md"
    f.write_text("AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["memory-dlp-scan", "--paths", str(f), "--strict", "--no-detect-secrets"],
    )
    assert main() == 1


# ---------- dlp-unquoted-cred-01: unquoted creds + space-delimited SSN ----------


def test_unquoted_password_assignment_is_caught():
    assert "generic_password_assignment" in _patterns("password=hunter2supersecret")


def test_quoted_password_assignment_still_caught():
    """The pre-existing quoted form must NOT regress."""
    assert "generic_password_assignment" in _patterns('password="hunter2supersecret"')


def test_unquoted_password_with_punctuation_is_caught():
    """A password containing `$` (which split the entropy token) is still
    caught by the broadened assignment pattern."""
    assert "generic_password_assignment" in _patterns("password=Xq7$mK9pLwZ2vN4rT8yB")


def test_space_delimited_ssn_is_caught():
    assert "ssn_us_spaced" in _patterns("SSN 123 45 6789")


def test_dash_delimited_ssn_still_caught():
    """The original dash-separated SSN pattern must NOT regress."""
    assert "ssn_us" in _patterns("SSN 123-45-6789")


def test_invalid_ssn_area_not_flagged():
    """SSN validity guards (000/666/9xx area) still apply to the spaced
    variant."""
    assert "ssn_us_spaced" not in _patterns("000 45 6789")


# ---------- dlp-multiline-01: non-OPENSSH PEM bodies ----------


def test_rsa_private_key_body_multiline_caught():
    text = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        + "MIIEowIBAAKCAQEA" + ("A" * 120) + "\n"
        + "-----END RSA PRIVATE KEY-----\n"
    )
    pats = _patterns(text)
    # Header still fires AND the multiline body now fires too.
    assert "private_key_pem" in pats
    assert "private_key_pem_body" in pats


def test_openssh_private_key_body_still_caught():
    text = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        + ("b3BlbnNzaC1rZXktdjEA" * 6) + "\n"
        + "-----END OPENSSH PRIVATE KEY-----\n"
    )
    assert "ssh_private_key_body" in _patterns(text)

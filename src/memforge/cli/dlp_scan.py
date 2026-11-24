# memory-dlp-scan is the pre-commit DLP scanner for memory folders (Phase 1 T6.4).
#
# ADR: 0001 §Phase 1 T6 (DLP scanning sub-deliverable)
# ADR: 0002 §Track 5 (CI scrubber for OSS publication)
#
# Scans memory files (or any markdown) for sensitive content patterns:
# AWS ARNs / access keys, SSH/PEM private keys, API tokens (GitHub, Slack,
# OpenAI, Anthropic, xAI, Google), SSN, credit-card-like, base-64 PEM
# bodies, JWT tokens, generic high-entropy strings >40 chars in plain text.
#
# Modes:
#   --paths <files>        Scan specific files (default: stdin file list)
#   --staged                Scan the staged content of files staged for commit
#                           (the index copy, not the working-tree copy)
#   --memory-folders        Scan default memory folders recursively
#   --strict                Exit 1 on any BLOCKER finding (credentials, private
#                           keys, SSN, etc.). MAJOR findings (ARNs, JWTs,
#                           generic password assignments) do NOT trip --strict.
#   --strict-major          Exit 1 on any BLOCKER OR MAJOR finding. Use this in a
#                           CI/pre-commit gate that must block on MAJOR-severity
#                           hits too (dlp-strict-exit: header now matches the
#                           BLOCKER-only --strict behavior; --strict-major is the
#                           any-finding gate).
#
# Pure-Python, no external deps. Default-on regex patterns. Pattern
# customization currently requires editing PATTERNS in this module; there is no
# --rules loader yet (dlp-01: header claim corrected to match the CLI).

from __future__ import annotations

import argparse
import json
import math
import re
import os
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

from memforge._terminal import terminal_safe, visible
from memforge._programs import ProgramInFolder, find_program_outside
from memforge.discovery import LinkReadRefused, access_error, read_bytes_no_follow


@dataclass
class Pattern:
    name: str
    regex: re.Pattern
    severity: str
    implied_tier: str = "internal"
    examples: str = ""


# `implied_tier` drives the v0.4 sensitivity_label_mismatch cross-check.
# Secret-class patterns are tagged `restricted` because if a secret slips into
# memory it must at minimum be contained at restricted-tier. The secret-class
# BLOCKER finding still fires on its own; the cross-check is additional signal.
PATTERNS: list[Pattern] = [
    Pattern("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "BLOCKER", "restricted"),
    Pattern("aws_secret_access_key", re.compile(r"(?i)aws(.{0,20})?secret(.{0,20})?[:=]\s*['\"]?[A-Za-z0-9/+=]{40}\b"), "BLOCKER", "restricted"),
    Pattern("aws_arn", re.compile(r"\barn:aws:[a-z0-9\-]+:[a-z0-9\-]*:[0-9]+:[A-Za-z0-9_\-/.:*]+"), "MAJOR", "restricted"),
    Pattern("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "BLOCKER", "restricted"),
    Pattern("github_oauth", re.compile(r"\bgho_[A-Za-z0-9]{36}\b"), "BLOCKER", "restricted"),
    Pattern("github_app_token", re.compile(r"\b(ghu|ghs)_[A-Za-z0-9]{36}\b"), "BLOCKER", "restricted"),
    Pattern("github_refresh_token", re.compile(r"\bghr_[A-Za-z0-9]{76}\b"), "BLOCKER", "restricted"),
    Pattern("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), "BLOCKER", "restricted"),
    Pattern("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{40,}\b"), "BLOCKER", "restricted"),
    Pattern("xai_api_key", re.compile(r"\bxai-[A-Za-z0-9]{40,}\b"), "BLOCKER", "restricted"),
    Pattern("google_api_key", re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b"), "BLOCKER", "restricted"),
    Pattern("slack_bot_token", re.compile(r"\bxox[bpars]-[0-9A-Za-z\-]{10,}\b"), "BLOCKER", "restricted"),
    Pattern("slack_webhook", re.compile(r"https://hooks\.slack\.com/services/T[A-Za-z0-9]+/B[A-Za-z0-9]+/[A-Za-z0-9]{20,}"), "BLOCKER", "restricted"),
    Pattern(
        "private_key_pem",
        re.compile(
            r"-----BEGIN ((RSA|DSA|EC|OPENSSH|PGP|ENCRYPTED) )?PRIVATE KEY-----"
        ),
        "BLOCKER",
        "restricted",
    ),
    Pattern(
        "ssh_private_key_body",
        re.compile(r"-----BEGIN OPENSSH PRIVATE KEY-----[\s\S]{100,}?-----END OPENSSH PRIVATE KEY-----"),
        "BLOCKER",
        "restricted",
    ),
    # General PEM private-key BODY (RSA|DSA|EC|PGP|ENCRYPTED|OPENSSH), so the
    # multiline body of a non-OPENSSH key is detected and redacted as a block,
    # not only its single-line BEGIN header (dlp-multiline-01). The `_body`
    # suffix routes it through the multiline pass below.
    Pattern(
        "private_key_pem_body",
        re.compile(
            r"-----BEGIN ((RSA|DSA|EC|PGP|ENCRYPTED) )?PRIVATE KEY-----"
            r"[\s\S]{100,}?"
            r"-----END ((RSA|DSA|EC|PGP|ENCRYPTED) )?PRIVATE KEY-----"
        ),
        "BLOCKER",
        "restricted",
    ),
    Pattern("jwt_token", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), "MAJOR", "restricted"),
    Pattern("ssn_us", re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"), "BLOCKER", "restricted"),
    Pattern("credit_card_visa_mc", re.compile(r"\b(?:4\d{3}|5[1-5]\d{2})[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"), "BLOCKER", "restricted"),
    Pattern("docusign_api_account", re.compile(r"\bdocusign[A-Za-z0-9._-]{0,30}[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"), "MAJOR", "restricted"),
    Pattern("stripe_secret_key", re.compile(r"\b(sk_live|rk_live)_[A-Za-z0-9]{24,}\b"), "BLOCKER", "restricted"),
    Pattern("twilio_auth_token", re.compile(r"\bSK[a-f0-9]{32}\b"), "MAJOR", "restricted"),
    # Unquoted form accepted in addition to quoted: a hand-typed
    # `password=hunter2supersecret` (no quotes) is the case a human author
    # actually produces, exactly where DLP should be strongest (dlp-unquoted-
    # cred-01). The value run stops at whitespace/quote so it stays one token.
    Pattern("generic_password_assignment", re.compile(r"(?i)(password|passwd|pwd|secret|api[_\-]?key|token)\s*[:=]\s*['\"]?[^\s'\"]{8,}['\"]?"), "MAJOR", "restricted"),
    # Space-OR-dash separated SSN: prose commonly writes "123 45 6789".
    # ssn_us (dash-only) stays unchanged; this adds the space variant without
    # weakening it (dlp-unquoted-cred-01).
    Pattern("ssn_us_spaced", re.compile(r"\b(?!000|666|9\d{2})\d{3}\s(?!00)\d{2}\s(?!0000)\d{4}\b"), "BLOCKER", "restricted"),
]


PATTERN_TIER_BY_NAME: dict[str, str] = {p.name: p.implied_tier for p in PATTERNS}
# high_entropy_near_keyword is generated dynamically (not in PATTERNS); tier internal.
PATTERN_TIER_BY_NAME["high_entropy_near_keyword"] = "internal"
# aws_secret_access_key_bare is generated dynamically in scan_text (entropy-gated
# bare 40-char secret); secret-class, so restricted like the other AWS patterns.
PATTERN_TIER_BY_NAME["aws_secret_access_key_bare"] = "restricted"


@dataclass
class Finding:
    file: Path
    line_no: int
    pattern: str
    severity: str
    excerpt: str


_SECRET_KEYWORD_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api[_\-\s]?key|token|auth|credential|bearer)\b"
)
_HIGH_ENTROPY_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_\-\.]{20,}")

# Bare secret access key: a long high-entropy token with NO co-located keyword
# or assignment (e.g. authored as
# "aws backup creds: AKIA...EXAMPLE / wJalr...KEY" across two lines, so the
# secret line stands alone). The keyword-adjacent aws_secret_access_key pattern
# misses it entirely.
#
# dlp-bare-secret-03: the bare rule was previously FIXED-length-40 base64 only,
# so a 64-char hex token (Mailgun / SendGrid-legacy / Square / generic HMAC keys)
# or a >40-char base64 token on its own line evaded the commit-blocking gate.
# Two generalized, entropy-gated bare rules now close that window:
#
#   BASE64 ({40,}, 0-2 '=' padding): floor 4.2. A real base64 secret runs ~4.6+
#   bits; a 40-char hex git anchor (~3.95) and a long CamelCase identifier
#   (~4.16) sit below 4.2 and are NOT flagged. The negative lookbehind/lookahead
#   bound the token AFTER optional padding so a padded key cannot evade (dlp-01).
#
#   HEX ({40,}): floor 3.5 (a 16-symbol hex alphabet tops out near 4.0 bits, so
#   4.2 would never fire on hex). Entropy alone cannot separate a 64-char hex
#   SECRET from a 64-char hex git commit hash (both ~3.8). MemForge legitimately
#   stores git commit/anchor hashes in memory bodies, ALWAYS keyword-adjacent
#   ("Commit `<hash>`", "anchor tag <hash>"), so the hex rule SKIPS a token whose
#   line carries a git-anchor context keyword (commit/anchor/sha/hash/revision/
#   blob/tree). A standalone 64-hex API token on its own line (no such context)
#   still fires. This is the precision lever entropy cannot provide.
#
# Keyword-adjacent and PEM/AKIA/provider-prefixed secrets remain caught by their
# own unconditional PATTERNS.
_BARE_SECRET_BASE64_RE = re.compile(
    r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+]{40,}={0,2}(?![A-Za-z0-9/+=])"
)
_BARE_SECRET_HEX_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40,}(?![0-9a-fA-F])")
_BARE_SECRET_BASE64_ENTROPY = 4.2
_BARE_SECRET_HEX_ENTROPY = 3.5
# Git-object context: a bare hex run on a line mentioning any of these is a
# commit/anchor hash, not a credential. Skips the hex rule (not the base64 rule;
# base64 git anchors do not exist).
#
# The exemption is narrow on purpose, because it trades a missed secret for a
# quieter scan: a secret word on the line (_SECRET_KEYWORD_RE, the same list
# the keyword-entropy rule uses) overrides it, and it covers only the lengths a
# git object id can have. A hex run of any other length is not a git hash.
_GIT_OBJECT_ID_LENGTHS = (40, 64)
_GIT_HEX_CONTEXT_RE = re.compile(
    r"(?i)\b(commit|anchor|sha[0-9]*|hash|revision|rev|blob|tree|checksum|digest|"
    r"object[\s_-]?id|git)\b"
)


def shannon_entropy(s: str) -> float:
    """Bits-per-character Shannon entropy. Empty string returns 0.0."""
    if not s:
        return 0.0
    counts = Counter(s)
    total = len(s)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def scan_text(text: str, file: Path, entropy_threshold: float = 4.5) -> list[Finding]:
    out: list[Finding] = []
    seen: set[tuple[int, str, int, int]] = set()
    for line_no, line in enumerate(text.splitlines(), 1):
        # (pattern, severity, start, end) for each match on this line. Every
        # excerpt is cut after all of them are known, so no finding prints a
        # secret another finding on the same line matched.
        hits: list[tuple[str, str, int, int]] = []
        for pat in PATTERNS:
            for m in pat.regex.finditer(line):
                key = (line_no, pat.name, m.start(), m.end())
                if key in seen:
                    continue
                seen.add(key)
                hits.append((pat.name, pat.severity, m.start(), m.end()))

        # Bare secret access key (no keyword, no assignment): {40,} base64 OR
        # {40,} hex, entropy-gated so prose runs / git anchors do not false-
        # positive (dlp-aws-secret-01, dlp-bare-secret-03). A hex run of git
        # object-id length is additionally suppressed on a line carrying git-
        # object context (commit/anchor/sha/...) and no secret word, where it
        # is a commit hash, not a secret.
        line_is_git_context = bool(
            _GIT_HEX_CONTEXT_RE.search(line) and not _SECRET_KEYWORD_RE.search(line)
        )
        _bare_rules = [
            (_BARE_SECRET_BASE64_RE, _BARE_SECRET_BASE64_ENTROPY, False),
            (_BARE_SECRET_HEX_RE, _BARE_SECRET_HEX_ENTROPY, True),
        ]
        for rx, floor, is_hex in _bare_rules:
            for m in rx.finditer(line):
                token = m.group(0)
                if is_hex and line_is_git_context and len(token) in _GIT_OBJECT_ID_LENGTHS:
                    continue
                if shannon_entropy(token) < floor:
                    continue
                key = (line_no, "aws_secret_access_key_bare", m.start(), m.end())
                if key in seen:
                    continue
                seen.add(key)
                hits.append(("aws_secret_access_key_bare", "BLOCKER", m.start(), m.end()))

        # Entropy: flag tokens that are 20+ chars AND have entropy > threshold
        # AND co-occur with a secret-keyword on the same line. Catches base64-
        # wrapped tokens, custom-scheme keys, anything the regex set misses.
        if _SECRET_KEYWORD_RE.search(line):
            for m in _HIGH_ENTROPY_TOKEN_RE.finditer(line):
                token = m.group(0)
                if shannon_entropy(token) < entropy_threshold:
                    continue
                key = (line_no, "high_entropy_near_keyword", m.start(), m.end())
                if key in seen:
                    continue
                seen.add(key)
                hits.append(("high_entropy_near_keyword", "MAJOR", m.start(), m.end()))

        excerpt = _redact(line, [(start, end) for _, _, start, end in hits])
        out.extend(
            Finding(file=file, line_no=line_no, pattern=name, severity=severity, excerpt=excerpt)
            for name, severity, _, _ in hits
        )

    # Any pattern whose name ends in `_body` is a multiline (PEM-style) body
    # scan run over the whole text, not line-by-line (dlp-multiline-01).
    multiline_patterns = [pat for pat in PATTERNS if pat.name.endswith("_body")]
    for pat in multiline_patterns:
        for m in pat.regex.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            key = (line_no, pat.name, m.start(), m.end())
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Finding(
                    file=file,
                    line_no=line_no,
                    pattern=pat.name,
                    severity=pat.severity,
                    excerpt="[redacted multiline private key]",
                )
            )
    return out


def _detect_secrets_program(*folders: Path) -> Optional[str]:
    """The detect-secrets to run, or None after a warning when there is none.

    Raises ProgramInFolder when it lies inside one of `folders`, the folders
    being scanned: anyone who can commit there could have placed it.
    """
    program = find_program_outside("detect-secrets", *folders)
    if program is not None:
        return program
    # rel-03: surface reduced coverage rather than returning [] silently. A
    # green DLP scan that quietly ran only the regex/entropy floor (skipping
    # the split-key/base64-obfuscation cases detect-secrets is here to catch)
    # is a false sense of safety on a partner box where the console script is
    # not on PATH.
    sys.stderr.write(
        "warn: detect-secrets not on PATH; deep DLP layer skipped, "
        "regex/entropy floor only (install the 'dlp' extra to enable)\n"
    )
    return None


def run_detect_secrets(files: list[Path], stores: Sequence[Path] = ()) -> list[Finding]:
    """Optional supplement using detect-secrets if installed.

    Catches obfuscation patterns the regex set misses (split keys, base64
    bodies, custom secret formats). Skipped with a warning when not installed.

    detect-secrets scans only files under the folder it runs in and reports
    nothing for the rest, so it is run once per parent folder, in that folder,
    on base names. Findings carry the paths the caller passed. A detect-secrets
    inside one of `stores` is refused.
    """
    if not files:
        return []
    try:
        program = _detect_secrets_program(*stores)
    except ProgramInFolder as exc:
        return [_deep_scan_failed(exc.folder, f"refused: {exc}")]
    if program is None:
        return []
    files_by_folder: dict[Path, dict[Path, Path]] = {}
    for f in files:
        files_by_folder.setdefault(f.parent, {})[Path(f.name)] = f
    out: list[Finding] = []
    for folder, file_by_name in files_by_folder.items():
        try:
            findings = _detect_secrets_in(program, folder, list(file_by_name))
        except _DeepScanFailed as exc:
            out.append(_deep_scan_failed(folder, str(exc)))
            continue
        for finding in findings:
            finding.file = file_by_name.get(finding.file, finding.file)
            out.append(finding)
    return out


class _DeepScanFailed(Exception):
    """detect-secrets was found but produced no usable report. The message
    says why."""


def _deep_scan_failed(where: Path, reason: str) -> Finding:
    """Warn, and return the BLOCKER that keeps --strict from passing a run
    that asked for the deep layer and did not get it."""
    _warn_deep_layer_failed(reason)
    return Finding(where, 0, "deep_scan_failed", "BLOCKER",
                   f"detect-secrets failed ({reason}); the deep DLP layer did not scan here")


def _detect_secrets_in(program: str, folder: Path, files: list[Path]) -> list[Finding]:
    """Run detect-secrets in `folder` on `files`, which are relative to it.
    Findings carry the relative paths. Raises _DeepScanFailed when it gives
    no usable report."""
    try:
        # "--" ends option parsing: a file name that starts with a dash must
        # reach detect-secrets as a path, never as one of its options.
        proc = subprocess.run(
            [program, "scan", "--all-files", "--"] + [str(f) for f in files],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=folder,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise _DeepScanFailed(str(exc)) from exc
    # A nonzero return WITH stdout still proceeds to parse (detect-secrets exits
    # nonzero in some scan modes). Guard the parse and normalize emitted paths so
    # a partial/legacy-format report cannot emit findings with a path form that
    # disagrees with the caller-supplied (possibly absolute) paths (dlp-
    # detectsecrets-01); path-mismatched findings slow pre-commit triage.
    if proc.returncode != 0 and not proc.stdout:
        raise _DeepScanFailed(f"exit {proc.returncode}: {proc.stderr.strip()[-200:]}")
    try:
        report = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        raise _DeepScanFailed("output was not JSON") from None
    results = report.get("results") if isinstance(report, dict) else None
    if not isinstance(results, dict):
        raise _DeepScanFailed("output was not a detect-secrets report")

    # Map every plausible string form (absolute, as-passed, resolved, name) of
    # each scanned file back to the caller's canonical Path, so a finding keyed
    # on a repo-relative name is reported with the same path form as the regex
    # findings for that file.
    canon_by_str: dict[str, Path] = {}
    for f in files:
        for key in {str(f), f.as_posix(), f.name}:
            canon_by_str.setdefault(key, f)
        try:
            canon_by_str.setdefault(str(f.resolve()), f)
        except OSError:
            pass

    out: list[Finding] = []
    for fname, items in results.items():
        # Normalize the detect-secrets path against the scanned set; fall back to
        # the verbatim path only when no scanned file matches.
        path = canon_by_str.get(fname) or canon_by_str.get(Path(fname).name) or Path(fname)
        if not isinstance(items, list):
            raise _DeepScanFailed(f"report entry for {fname!r} was not a list")
        for item in items:
            if not isinstance(item, dict):
                raise _DeepScanFailed(f"report entry for {fname!r} held a non-object")
            line_no = item.get("line_number", 0)
            kind = item.get("type", "detect-secrets-finding")
            # bool is an int subclass; true is not a line number.
            if type(line_no) is not int or not isinstance(kind, str):
                raise _DeepScanFailed(
                    f"report entry for {fname!r} had a line number or type of the wrong kind"
                )
            # NOTE (dlp-detectsecrets-01): detect-secrets findings are emitted as
            # BLOCKER by design (a pre-commit gate treats any flagged secret as
            # blocking). detect-secrets plugin confidence is not mapped to a
            # lower severity; operators who find the heuristic too aggressive can
            # pass --no-detect-secrets to drop the supplementary scan entirely.
            out.append(
                Finding(
                    file=path,
                    line_no=line_no,
                    pattern=f"detect-secrets:{kind}",
                    severity="BLOCKER",
                    excerpt="[redacted by detect-secrets]",
                )
            )
    return out


def _warn_deep_layer_failed(reason: str) -> None:
    """Say that detect-secrets ran and produced nothing usable, so a green
    result is not read as covering the deep layer."""
    sys.stderr.write(
        f"warn: detect-secrets failed ({reason}); deep DLP layer skipped, "
        "regex/entropy floor only\n"
    )


def _redact(line: str, spans: list[tuple[int, int]]) -> str:
    """The line with every span replaced, stripped, and cut to 160 characters.

    The spans are offsets into the line as matched, so they are cut before the
    line is stripped; stripping first would shift every span on an indented
    line and print part of the secret.
    """
    parts: list[str] = []
    done = 0
    for start, end in sorted(spans):
        start = max(start, done)
        if end <= start:
            continue
        parts += [line[done:start], "[REDACTED]"]
        done = end
    parts.append(line[done:])
    shown = "".join(parts).strip()
    out = shown[:160]
    return out if len(line.strip()) <= 160 else out + "..."


def _read_for_scan(
    path: Path, root: Optional[Path] = None
) -> tuple[Optional[bytes], list[Finding]]:
    """Read a file's bytes, or report why it was not read.

    A path that is not scanned is always a finding: a file dropped without a
    line of output reads as "clean". Give ``root`` for a file found by walking
    a memory folder, so a folder on the way swapped for a link is not
    followed; a file named with ``--paths`` is read as named.
    """
    try:
        return read_bytes_no_follow(path, root=root), []
    except LinkReadRefused as exc:
        # The refusal also covers a path that is not a regular file. Only a
        # path that is a link now takes the lower severity; anything else
        # stays a BLOCKER.
        if not os.path.islink(path):
            return None, [
                Finding(path, 0, "unreadable_file", "BLOCKER", f"not scanned: {exc}")
            ]
        # The link target can be anywhere on disk; reading it would scan, and
        # print excerpts of, a file outside the folder being checked.
        return None, [
            Finding(path, 0, "symlink_not_scanned", "MAJOR",
                    "symbolic link; its target was not read")
        ]
    except OSError as exc:
        return None, [
            Finding(path, 0, "unreadable_file", "BLOCKER",
                    f"not scanned: {exc.strerror or exc}")
        ]


def _decode_for_scan(raw: bytes, path: Path) -> tuple[str, list[Finding]]:
    """Decode everything that can be decoded. Bytes that are not UTF-8 are
    replaced so the rest of the file is still scanned, and the file is
    reported, because one stray byte must not turn a file with a secret into
    a clean one."""
    try:
        return raw.decode("utf-8"), []
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), [
            Finding(path, 0, "invalid_utf8", "MAJOR",
                    "not valid UTF-8; scanned with the undecodable bytes replaced")
        ]


def _scan_decoded(
    text: str, path: Path, entropy_threshold: float = 4.5
) -> tuple[list[Finding], list[Finding]]:
    """Scan decoded text; return (findings about the file, content findings).

    UTF-16 without a byte-order mark is valid UTF-8 with a NUL beside every
    ASCII character, so no pattern matches it. Text holding NUL is scanned a
    second time with the NULs removed, which keeps every line break and so
    every line number, and the file is reported.
    """
    findings = scan_text(text, path, entropy_threshold=entropy_threshold)
    if "\0" not in text:
        return [], findings
    reported = {(f.line_no, f.pattern) for f in findings}
    findings += [
        f for f in scan_text(text.replace("\0", ""), path, entropy_threshold=entropy_threshold)
        if (f.line_no, f.pattern) not in reported
    ]
    return [
        Finding(path, 0, "nul_bytes", "MAJOR",
                "holds NUL bytes (UTF-16 or binary); also scanned with them removed")
    ], findings


def scan_file(path: Path) -> list[Finding]:
    raw, findings = _read_for_scan(path)
    if raw is None:
        return findings
    text, undecodable = _decode_for_scan(raw, path)
    notes, content = _scan_decoded(text, path)
    return findings + undecodable + notes + content


def check_sensitivity_mismatch(
    text: str, file: Path, findings: list[Finding]
) -> Optional[Finding]:
    """Compare declared `sensitivity` against the highest implied tier across
    findings. Emit a BLOCKER `sensitivity_label_mismatch` when declared is below.

    Returns None for files that do not parse as MemForge memory (no
    frontmatter with name+type) and for files where the declared label
    already covers the implied content tier.
    """
    from memforge.cli._config import (
        TIER_ORDER,
        parse_frontmatter_sensitivity,
        tier_rank,
    )

    is_memforge, declared = parse_frontmatter_sensitivity(text)
    if not is_memforge:
        return None
    if not findings:
        return None

    max_tier = "public"
    max_rank = tier_rank("public")
    for f in findings:
        implied = PATTERN_TIER_BY_NAME.get(f.pattern)
        if implied is None:
            continue
        r = tier_rank(implied)
        if r > max_rank:
            max_rank = r
            max_tier = implied

    declared_rank = tier_rank(declared)
    if declared_rank >= max_rank:
        return None

    return Finding(
        file=file,
        line_no=1,
        pattern="sensitivity_label_mismatch",
        severity="BLOCKER",
        excerpt=(
            f"declared sensitivity '{declared}' is below implied "
            f"'{max_tier}' from body content (tier order: "
            f"{' < '.join(TIER_ORDER)})"
        ),
    )


def _cross_check_enabled(cli_disabled: bool, config_enabled: bool) -> bool:
    """Cross-check enable resolution: CLI override beats config; both must
    explicitly disable for the check to skip. Hard-floor at the privileged
    tier is enforced inside the check itself, not here.
    """
    if cli_disabled:
        return False
    return config_enabled


def _privileged_floor_engaged(findings: list[Finding]) -> bool:
    """Return True if any finding implies privileged tier. The cross-check
    cannot be disabled in that case (spec hard floor).

    NOTE (dlp-config-floor-01): this floor is intentionally DORMANT today. No
    entry in PATTERNS implies `privileged` (all are `restricted` or
    `internal`), matching SPEC §"Label / content cross-check" which reserves
    the privileged backstop "for future privileged-tier markers". It cannot
    fire until a privileged-class pattern exists; it is not active enforcement.
    """
    from memforge.cli._config import tier_rank

    privileged_rank = tier_rank("privileged")
    for f in findings:
        implied = PATTERN_TIER_BY_NAME.get(f.pattern)
        if implied is None:
            continue
        if tier_rank(implied) >= privileged_rank:
            return True
    return False


class StagedScanError(Exception):
    """Raised when the staged file list cannot be read from git."""


_GITLINK_MODE = b"160000"


def staged_blobs() -> list[tuple[Path, Optional[bytes], list[Finding]]]:
    """Return (path, staged content, problems) for each staged file.

    The content is read from the index, because that is what the commit will
    contain; the working-tree copy can differ from it or be gone. Names are
    listed NUL-separated: the default listing quotes any name with a newline,
    a quote or a non-ASCII character, and a quoted name matches no file.
    Paths are relative to the repository root, which is the current folder.
    """
    try:
        git = find_program_outside("git", Path.cwd())
    except ProgramInFolder as exc:
        raise StagedScanError(str(exc)) from None
    if git is None:
        raise StagedScanError("git was not found on PATH")
    try:
        listing = subprocess.run(
            # T (type change) is listed too: a link that became a regular
            # file is reported as T, not M, and its new content is staged.
            [git, "diff", "--cached", "--raw", "-z", "--no-renames",
             "--diff-filter=ACMT"],
            capture_output=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, OSError) as exc:
        raise StagedScanError(str(exc)) from exc

    fields = listing.split(b"\0")
    out: list[tuple[Path, Optional[bytes], list[Finding]]] = []
    for meta, raw_name in zip(fields[0::2], fields[1::2]):
        if meta.split()[1] == _GITLINK_MODE:
            # A submodule pointer has no file content in this repository.
            continue
        name = os.fsdecode(raw_name)
        path = Path(name)
        try:
            blob = subprocess.run(
                [git, "cat-file", "blob", f":0:{name}"],
                capture_output=True,
                check=True,
            ).stdout
        except (subprocess.CalledProcessError, OSError) as exc:
            out.append((path, None, [
                Finding(path, 0, "unreadable_file", "BLOCKER",
                        f"not scanned: staged content could not be read ({exc})")
            ]))
            continue
        out.append((path, blob, []))
    return out


def _detect_secrets_on_staged(
    staged: list[tuple[Path, bytes]], repository: Path,
) -> list[Finding]:
    """Run detect-secrets on the staged content.

    detect-secrets reads files from disk and only scans files under the folder
    it runs in. Each staged blob is written to a private temporary folder under
    its own base name (several detect-secrets filters key on the name),
    detect-secrets runs in that folder, and findings are reported against the
    staged path. A detect-secrets inside `repository` is refused.
    """
    if not staged:
        return []
    try:
        program = _detect_secrets_program(repository)
    except ProgramInFolder as exc:
        return [_deep_scan_failed(Path("."), f"refused: {exc}")]
    if program is None:
        return []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            staged_path_by_copy: dict[Path, Path] = {}
            for index, (path, blob) in enumerate(staged):
                copy = Path(str(index)) / path.name
                (Path(tmp) / copy).parent.mkdir()
                (Path(tmp) / copy).write_bytes(blob)
                staged_path_by_copy[copy] = path
            findings = _detect_secrets_in(program, Path(tmp), list(staged_path_by_copy))
    except _DeepScanFailed as exc:
        return [_deep_scan_failed(Path("."), f"on the staged content: {exc}")]
    except OSError as exc:
        # No temporary copy means no deep scan; the regex and entropy findings
        # already collected must still be printed.
        return [_deep_scan_failed(Path("."), f"could not stage content for it: {exc}")]
    for finding in findings:
        finding.file = staged_path_by_copy.get(finding.file, finding.file)
    return findings


def memory_folder_files() -> list[tuple[Path, Path]]:
    """Every file to scan in the default memory folders, with its folder."""
    from memforge.paths import default_memory_paths

    out: list[tuple[Path, Path]] = []
    folders: list[Path] = list(default_memory_paths())
    skip = {"archive", ".git", "__pycache__"}
    for folder in folders:
        if not os.path.exists(folder) and access_error(folder) is None:
            continue
        # os.walk does not descend into a linked directory. Links are listed
        # (never followed) so the scan reports each one.
        # A directory that cannot be listed is added too, so the scan reports
        # it as unreadable; os.walk would otherwise pass over it in silence.
        unlistable: list[Path] = []
        for root, dirs, names in os.walk(
            folder, onerror=lambda err: unlistable.append(Path(err.filename))
        ):
            dirs[:] = [d for d in dirs if d not in skip]
            here = Path(root)
            out.extend((here / d, folder) for d in dirs if os.path.islink(here / d))
            out.extend((here / n, folder) for n in names if n.endswith(".md"))
        out.extend((u, folder) for u in unlistable)
    return out


def emit_text(findings: list[Finding]) -> None:
    if not findings:
        print("DLP: clean (0 findings)")
        return
    by_sev: dict[str, list[Finding]] = {}
    for f in findings:
        by_sev.setdefault(f.severity, []).append(f)
    for sev in ("BLOCKER", "MAJOR", "MINOR"):
        items = by_sev.get(sev, [])
        if not items:
            continue
        print(f"\n{sev} ({len(items)}):")
        for f in items:
            print(f"  {visible(f.file)}:{f.line_no}  [{f.pattern}]  {visible(f.excerpt)}")
    print(f"\nTotal findings: {len(findings)}  (BLOCKER={len(by_sev.get('BLOCKER', []))}, "
          f"MAJOR={len(by_sev.get('MAJOR', []))}, MINOR={len(by_sev.get('MINOR', []))})")


@terminal_safe
def main() -> int:
    p = argparse.ArgumentParser(
        prog="memory-dlp-scan",
        description="Pre-commit DLP scanner for MemForge folders.",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--paths", nargs="+", type=Path, help="Specific files to scan")
    src.add_argument("--staged", action="store_true",
                     help="Scan the staged content of git-staged files")
    src.add_argument("--memory-folders", action="store_true", help="Scan default memory folders")
    p.add_argument("--strict", action="store_true", help="Exit 1 on any BLOCKER finding")
    p.add_argument("--strict-major", action="store_true", help="Exit 1 on any BLOCKER or MAJOR finding")
    p.add_argument("--no-detect-secrets", action="store_true",
                   help="Skip detect-secrets supplementary scan even when installed")
    p.add_argument("--no-entropy", action="store_true",
                   help="Disable ONLY the generic high-entropy-near-keyword "
                        "heuristic. The bare-secret entropy gate (base64/hex "
                        "credential shapes) stays ON: it is high-precision and "
                        "is the credential-shape backstop on the commit gate "
                        "(dlp-entropy-noentropy-bypass-01).")
    p.add_argument("--entropy-threshold", type=float, default=4.5,
                   help="Bits-per-char entropy threshold for high-entropy heuristic (default 4.5)")
    p.add_argument("--no-sensitivity-cross-check", action="store_true",
                   help="Skip the v0.4 sensitivity_label_mismatch check. "
                        "Hard-floor: cannot disable when implied tier is privileged.")
    args = p.parse_args()

    from memforge.cli._config import load_config
    cfg = load_config()
    config_xcheck = bool(cfg.get("dlp", {}).get("enforce_sensitivity_cross_check", True))
    cross_check_active = _cross_check_enabled(args.no_sensitivity_cross_check, config_xcheck)

    loaded: list[tuple[Path, Optional[bytes], list[Finding]]]
    if args.staged:
        try:
            loaded = staged_blobs()
        except StagedScanError as exc:
            sys.stderr.write(f"error: could not list staged files: {exc}\n")
            return 2
        if not loaded:
            print("DLP: no staged files")
            return 0
    elif args.paths or args.memory_folders:
        named = [(Path(f), None) for f in args.paths] if args.paths else memory_folder_files()
        loaded = [(f, *_read_for_scan(f, root)) for f, root in named]
        # A file named on the command line is the operator's own choice; the
        # memory folders are stores anyone with commit access can write to.
        stores = sorted({root for _, root in named if root is not None})
    else:
        sys.stderr.write("error: specify --paths, --staged, or --memory-folders\n")
        return 2

    findings: list[Finding] = []
    scanned: list[tuple[Path, bytes]] = []
    entropy_threshold = math.inf if args.no_entropy else args.entropy_threshold
    for f, raw, problems in loaded:
        findings.extend(problems)
        if raw is None:
            continue
        scanned.append((f, raw))
        text, undecodable = _decode_for_scan(raw, f)
        findings.extend(undecodable)
        notes, per_file = _scan_decoded(text, f, entropy_threshold)
        findings.extend(notes)
        findings.extend(per_file)

        if cross_check_active or _privileged_floor_engaged(per_file):
            mismatch = check_sensitivity_mismatch(text, f, per_file)
            if mismatch is not None:
                findings.append(mismatch)

    if not args.no_detect_secrets:
        if args.staged:
            findings.extend(_detect_secrets_on_staged(scanned, Path.cwd()))
        else:
            findings.extend(run_detect_secrets([f for f, _ in scanned], stores))

    emit_text(findings)

    if not findings:
        return 0
    blockers = sum(1 for f in findings if f.severity == "BLOCKER")
    majors = sum(1 for f in findings if f.severity == "MAJOR")
    if args.strict and blockers > 0:
        return 1
    if args.strict_major and (blockers + majors) > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

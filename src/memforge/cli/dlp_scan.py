# memory-dlp-scan — pre-commit DLP scanner for memory folders (Phase 1 T6.4).
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
#   --staged                Scan files staged for commit (uses git diff --cached)
#   --memory-folders        Scan default memory folders recursively
#   --strict                Exit 1 on any finding
#
# Pure-Python, no external deps. Default-on regex patterns. Customize via
# --rules <yaml> if needed (future).

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass
class Pattern:
    name: str
    regex: re.Pattern
    severity: str
    examples: str = ""


PATTERNS: list[Pattern] = [
    Pattern("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "BLOCKER"),
    Pattern("aws_secret_access_key", re.compile(r"(?i)aws(.{0,20})?secret(.{0,20})?[:=]\s*['\"]?[A-Za-z0-9/+=]{40}\b"), "BLOCKER"),
    Pattern("aws_arn", re.compile(r"\barn:aws:[a-z0-9\-]+:[a-z0-9\-]*:[0-9]+:[A-Za-z0-9_\-/.:*]+"), "MAJOR"),
    Pattern("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "BLOCKER"),
    Pattern("github_oauth", re.compile(r"\bgho_[A-Za-z0-9]{36}\b"), "BLOCKER"),
    Pattern("github_app_token", re.compile(r"\b(ghu|ghs)_[A-Za-z0-9]{36}\b"), "BLOCKER"),
    Pattern("github_refresh_token", re.compile(r"\bghr_[A-Za-z0-9]{76}\b"), "BLOCKER"),
    Pattern("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), "BLOCKER"),
    Pattern("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{40,}\b"), "BLOCKER"),
    Pattern("xai_api_key", re.compile(r"\bxai-[A-Za-z0-9]{40,}\b"), "BLOCKER"),
    Pattern("google_api_key", re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b"), "BLOCKER"),
    Pattern("slack_bot_token", re.compile(r"\bxox[bpars]-[0-9A-Za-z\-]{10,}\b"), "BLOCKER"),
    Pattern("slack_webhook", re.compile(r"https://hooks\.slack\.com/services/T[A-Za-z0-9]+/B[A-Za-z0-9]+/[A-Za-z0-9]{20,}"), "BLOCKER"),
    Pattern(
        "private_key_pem",
        re.compile(
            r"-----BEGIN ((RSA|DSA|EC|OPENSSH|PGP|ENCRYPTED) )?PRIVATE KEY-----"
        ),
        "BLOCKER",
    ),
    Pattern(
        "ssh_private_key_body",
        re.compile(r"-----BEGIN OPENSSH PRIVATE KEY-----[\s\S]{100,}?-----END OPENSSH PRIVATE KEY-----"),
        "BLOCKER",
    ),
    Pattern("jwt_token", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), "MAJOR"),
    Pattern("ssn_us", re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"), "BLOCKER"),
    Pattern("credit_card_visa_mc", re.compile(r"\b(?:4\d{3}|5[1-5]\d{2})[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"), "BLOCKER"),
    Pattern("docusign_api_account", re.compile(r"\bdocusign[A-Za-z0-9._-]{0,30}[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"), "MAJOR"),
    Pattern("stripe_secret_key", re.compile(r"\b(sk_live|rk_live)_[A-Za-z0-9]{24,}\b"), "BLOCKER"),
    Pattern("twilio_auth_token", re.compile(r"\bSK[a-f0-9]{32}\b"), "MAJOR"),
    Pattern("generic_password_assignment", re.compile(r"(?i)(password|passwd|pwd|secret|api[_\-]?key|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]"), "MAJOR"),
]


@dataclass
class Finding:
    file: Path
    line_no: int
    pattern: str
    severity: str
    excerpt: str


_SECRET_KEYWORD_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api[_\-]?key|token|auth|credential|bearer)\b"
)
_HIGH_ENTROPY_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_\-\.]{20,}")


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
        for pat in PATTERNS:
            for m in pat.regex.finditer(line):
                key = (line_no, pat.name, m.start(), m.end())
                if key in seen:
                    continue
                seen.add(key)
                excerpt = _redact(line.strip(), m.start(), m.end())
                out.append(
                    Finding(
                        file=file,
                        line_no=line_no,
                        pattern=pat.name,
                        severity=pat.severity,
                        excerpt=excerpt,
                    )
                )

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
                excerpt = _redact(line.strip(), m.start(), m.end())
                out.append(
                    Finding(
                        file=file,
                        line_no=line_no,
                        pattern="high_entropy_near_keyword",
                        severity="MAJOR",
                        excerpt=excerpt,
                    )
                )

    multiline_patterns = [pat for pat in PATTERNS if pat.name == "ssh_private_key_body"]
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


def run_detect_secrets(files: list[Path]) -> list[Finding]:
    """Optional supplement using detect-secrets if installed.

    Catches obfuscation patterns the regex set misses (split keys, base64
    bodies, custom secret formats). Silently skipped when not installed.
    """
    if not files:
        return []
    if shutil.which("detect-secrets") is None:
        return []
    try:
        proc = subprocess.run(
            ["detect-secrets", "scan", "--all-files"] + [str(f) for f in files],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0 and not proc.stdout:
        return []
    try:
        report = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    out: list[Finding] = []
    for fname, items in (report.get("results") or {}).items():
        path = Path(fname)
        for item in items:
            line_no = int(item.get("line_number", 0) or 0)
            kind = str(item.get("type", "detect-secrets-finding"))
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


def _redact(line: str, start: int, end: int) -> str:
    if end <= start:
        return line
    head = line[: start]
    tail = line[end:]
    redacted = "[REDACTED]"
    out = (head + redacted + tail)[:160]
    return out if len(line) <= 160 else out + "..."


def scan_file(path: Path) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return scan_text(text, path)


def staged_files() -> list[Path]:
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    files: list[Path] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        p = Path(line)
        if p.exists() and p.is_file():
            files.append(p)
    return files


def memory_folder_files() -> list[Path]:
    out: list[Path] = []
    home = Path.home()
    user = os.environ.get("USER", "")
    folders: list[Path] = []
    if user:
        folders.append(home / ".claude" / "projects" / f"{user}-claude-projects" / "memory")
    folders.append(home / ".claude" / "global-memory")
    skip = {"archive", ".git", "__pycache__"}
    for folder in folders:
        if not folder.exists():
            continue
        for path in folder.rglob("*.md"):
            parts = path.relative_to(folder).parts
            if any(p in skip for p in parts):
                continue
            out.append(path)
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
            print(f"  {f.file}:{f.line_no}  [{f.pattern}]  {f.excerpt}")
    print(f"\nTotal findings: {len(findings)}  (BLOCKER={len(by_sev.get('BLOCKER', []))}, "
          f"MAJOR={len(by_sev.get('MAJOR', []))}, MINOR={len(by_sev.get('MINOR', []))})")


def main() -> int:
    p = argparse.ArgumentParser(
        prog="memory-dlp-scan",
        description="Pre-commit DLP scanner for MemForge folders.",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--paths", nargs="+", type=Path, help="Specific files to scan")
    src.add_argument("--staged", action="store_true", help="Scan git-staged files")
    src.add_argument("--memory-folders", action="store_true", help="Scan default memory folders")
    p.add_argument("--strict", action="store_true", help="Exit 1 on any BLOCKER finding")
    p.add_argument("--strict-major", action="store_true", help="Exit 1 on any BLOCKER or MAJOR finding")
    p.add_argument("--no-detect-secrets", action="store_true",
                   help="Skip detect-secrets supplementary scan even when installed")
    p.add_argument("--no-entropy", action="store_true",
                   help="Skip Shannon-entropy heuristic")
    p.add_argument("--entropy-threshold", type=float, default=4.5,
                   help="Bits-per-char entropy threshold for high-entropy heuristic (default 4.5)")
    args = p.parse_args()

    files: list[Path]
    if args.paths:
        files = [Path(f) for f in args.paths]
    elif args.staged:
        files = staged_files()
        if not files:
            print("DLP: no staged files")
            return 0
    elif args.memory_folders:
        files = memory_folder_files()
    else:
        sys.stderr.write("error: specify --paths, --staged, or --memory-folders\n")
        return 2

    findings: list[Finding] = []
    entropy_threshold = math.inf if args.no_entropy else args.entropy_threshold
    for f in files:
        if not f.exists() or not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(scan_text(text, f, entropy_threshold=entropy_threshold))

    if not args.no_detect_secrets:
        findings.extend(run_detect_secrets([f for f in files if f.exists() and f.is_file()]))

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

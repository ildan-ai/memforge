# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities **privately**. Do not open a public GitHub issue for a vulnerability.

Two channels are accepted:

1. **GitHub private security advisory** (preferred). Open an advisory at
   https://github.com/ildan-ai/memforge/security/advisories/new. GitHub
   notifies maintainers privately and provides a coordination workspace.
2. **Email**: `security@ildan.ai` with the prefix `[memforge-security]` in
   the subject line. Encrypted reports welcome (PGP key fingerprint TBD;
   contact the address for current key).

Include:

- Affected component (tool name, file path, line range when applicable).
- Reproduction steps or proof-of-concept.
- Your assessment of impact (data leak, RBAC bypass, supply-chain, etc).
- Whether you've coordinated disclosure with any other party.

## Response timeline

- **Acknowledgment**: 7 calendar days.
- **Triage + initial assessment**: 14 calendar days.
- **Fix or compensating control**: 90 calendar days for high-severity reports
  (memory data exposure, RBAC bypass, audit-log integrity break, secret-scan
  evasion). Lower-severity reports may take longer; the maintainer commits
  to a target date in the acknowledgment.

These targets reflect a small-team-maintainer reality. They are commitments,
not legal guarantees.

## Disclosure policy

This project follows **coordinated disclosure**. Once a fix lands, the
reporter and maintainer agree on a disclosure date (default: same day as
the patched release). Public credit to the reporter unless you ask
otherwise.

CVEs are requested for confirmed high-severity vulnerabilities via the
GitHub Security Advisory CVE flow.

## Supported versions

| Version | Status |
| --- | --- |
| 0.3.x | Supported (current) |
| < 0.3 | Not supported |

Once 0.4.x lands, 0.3.x will receive security fixes only for 90 days.

## Scope

In-scope for security reports:

- Code execution / arbitrary file write outside the memory folder.
- Path traversal in `memory-link-rewriter`, `memory-rollup`, or any tool
  that resolves user-supplied paths.
- RBAC bypass in `memory-index-gen` (file leaks across access tiers).
- Audit-log integrity defects (`memory-audit-log` chain forge / replay).
- DLP scanner evasion (`memory-dlp-scan`) where a known-bad pattern is missed.
- Frontmatter parser exploits causing tool misclassification (`memforge.frontmatter`).
- Supply-chain risks: dependency vulnerabilities exposed by memforge's pin set.

Out-of-scope:

- Bugs that require operator-supplied malicious memory folders AND the
  operator already has full filesystem access. The threat model assumes
  hostile contributors and hostile content, not a hostile operator.
- Denial-of-service via large memory folders (a real-world operator-side
  ergonomics concern, but not a security bug).
- Third-party adapter behavior (Cursor, Aider, Codex, VS Code Copilot);
  report those to the respective adapter's authors.

## Hall of fame

Security researchers who report valid vulnerabilities are listed below
unless they request anonymity.

(empty for now)

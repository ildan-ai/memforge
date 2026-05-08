# Changelog

All notable changes to MemForge are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The version number tracked here is the **package / tooling** version. The on-disk **spec version** (used in memory frontmatter and folder-index files) is tracked separately at `spec/VERSION` and follows its own SemVer track. A package release MAY include a spec bump; a spec bump MAY ship in a patch release.

## [Unreleased]

The Contributor License Agreement infrastructure is counsel-blocked; external pull requests are paused until the CLA flow lands.

## [spec 0.4.0-draft] - 2026-05-08

Spec-only draft on the `v0.4-draft` branch. v0.4 is a major bump per SemVer applied to spec semantics. Tooling stays at 0.3.x; the package release carrying this spec will land separately once v0.4 stabilizes.

### Added (spec)

- **§"Multi-agent concurrency: competing claims" section** in SPEC.md. Five new frontmatter keys (`decision_topic`, `replaces`, `superseded_by`, `topic_aliases`, `ever_multi_member`), a snooze record at `.memforge/snoozes/<topic>.yaml`, a config file at `.memforge/config.yaml` with hard-floor + edit-gate protection, the resolve operation contract (tool-neutral; CLI reference + Claude Code skill + Cursor / Continue / Aider / shell wrappers), the canonical reader-side competing-claim YAML block (byte-match CI), and a layered Tier 1 (HEAD-pure) + Tier 2 (commit-log) audit rule set. Closes the v0.3.x "Multi-user concurrency semantics (Phase 2+ concern)" deferral.
- **Status enumeration BLOCKER**: any value outside `{active, proposed, gated, superseded, dropped, archived}` is a HEAD-pure audit BLOCKER.
- **Status transition gating**: transitions to `superseded` MUST occur in a `memforge: resolve <decision_topic>` commit (Tier 2 BLOCKER if violated). Transitions to `archived` go through the generator (existing Phase-1 contract).
- **Secure-mode adapter conformance** (informative): adapters MAY claim secure-mode by detecting branch protection + required signed commits at startup; informative startup notice required when not in secure-mode.
- **Reserved-name slug denylist** for `decision_topic` (Windows device names: `con`, `aux`, `nul`, `prn`, `com[0-9]`, `lpt[0-9]`).

### Changed (spec)

- **Required frontmatter fields expanded.** `uid`, `tier`, `tags`, `owner`, `status`, `created` are now required (formerly optional in v0.3.x). A v0.4-conformant memory must carry: `name`, `description`, `type`, `uid`, `tier`, `tags`, `owner`, `status`, `created`. The `sensitivity` field remains independent.
- **v0.3.x backward compatibility: degraded mode.** Files written under v0.3.x that lack the newly-required fields load in degraded mode. Adapters MUST accept them, MAY warn, and SHOULD prompt the operator to backfill. Degraded-mode memories appear in `MEMORY.md` but cannot participate in rollup contracts, status-driven archival, or any v0.4 reader-side contract that depends on a v0.4-required field. Backfill is one-shot per file.
- **Integrity invariants** extended (rules 11-15) to cover the asymmetric-supersession contract, exactly-one-active per `ever_multi_member: true` group, `ever_multi_member` monotonicity, status enumeration, and the layered audit rule set.
- **§"Not in scope"** retitled to v0.4.0; multi-user concurrency removed (now in scope), v0.5.0 deferrals enumerated (centralized taxonomy, per-decision ledger, DAG cycle rejection, UUIDv7, vector-clock tie-breaker, CRDT, cryptographic provenance).

## [0.3.1] - 2026-05-07

Patch release wiring proper console scripts so `pip install ildan-memforge`
actually ships the CLI. Same on-disk format as 0.3.0; no schema or behavior
changes.

### Distribution name

The PyPI distribution is published under **`ildan-memforge`** because the
shorter `memforge` name on PyPI is already held by an unrelated project.
The Python import path is still `memforge`, and the CLI command names are
still `memory-audit`, `memory-watch`, etc. Only the install command differs.

### Added

- **`[project.scripts]` entry points for all 15 CLI tools.** After
  `pip install ildan-memforge`, the following commands land on `$PATH`:
  `memory-audit`, `memory-audit-deep`, `memory-audit-log`,
  `memory-cluster-suggest`, `memory-dedup`, `memory-dlp-scan`,
  `memory-frontmatter-backfill`, `memory-index-gen`, `memory-link-rewriter`,
  `memory-preamble-extract`, `memory-promote`, `memory-query`,
  `memory-rollup`, `memory-watch`, `agents-md-gen`. Previously the package
  was importable but none of the tools were on `$PATH`.

### Changed

- **Tool source moved into `src/memforge/cli/<name>.py` modules.** Each tool
  is now an importable module with a `main()` function; the corresponding
  `tools/<name>` script is now a thin shim that re-enters through the
  module's `main()`. Both invocation paths share the same code.
- **`memory-audit` rewritten in Python** (was bash). Cross-platform, uses
  `memforge.frontmatter.parse` for YAML parsing instead of awk, and gives a
  clearer error when frontmatter YAML fails to parse (single
  `"frontmatter YAML failed to parse"` violation instead of a misleading
  trio of `missing name/description/type`).
- **`memory-promote` rewritten in Python** (was bash). Cross-platform git
  operations via `subprocess`; same flag surface (`--source`, `--target`,
  `--dry-run`, `--no-commit`, `--yes`).

### Removed

- Bash sys.path / `_HERE` shims at the top of each tool (no longer needed
  now that tools live inside the installed package).

[Unreleased]: https://github.com/ildan-ai/memforge/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/ildan-ai/memforge/releases/tag/v0.3.1

## [0.3.0] - 2026-05-07

First public release.

### Added

- Project published under the `memforge` name. Tools, package imports (`memforge.frontmatter`, `memforge.discovery`, etc.), and CLI binaries all use the `memforge` namespace.
- **Spec 0.3.0** at `spec/SPEC.md`. Codifies:
  - The four memory types: `user`, `feedback`, `project`, `reference`.
  - Required `**Why:**` and `**How to apply:**` lines for `feedback` and `project` bodies (so future readers can judge edge cases instead of pattern-matching).
  - Rollup-with-subfolder pattern for topic clusters of five or more memories.
  - Optional UID frontmatter field (stable identifier for cross-folder linking via `mem:uid`).
  - Optional `access:` label for sensitivity-and-redaction scoping.
- **Reference adapter for Claude Code** at `adapters/claude-code/`. Includes auto-commit PostToolUse hook and the index-loading instruction that ships in `~/.claude/CLAUDE.md`.
- **Production adapters for Aider, Codex (OpenAI), Cursor, and GitHub Copilot Chat (VS Code)** under `adapters/`. Each adapter documents the agent-specific surface (rules file, instructions file, etc.) it bridges to the shared MemForge folder.
- **Cross-platform `memory-watch`** (Linux/macOS via watchdog).
- **Shared tooling at `tools/`:**
  - `memory-index-gen` (RBAC-aware folder index generator with `access:` filtering).
  - `memory-audit` and `memory-audit-deep` (integrity + health checks; `--strict` exits nonzero on violations).
  - `memory-cluster-suggest` (rollup candidate detection; O(n²) avoidance via inverted topic + token indexes).
  - `memory-query` (read-only structured query over a folder).
  - `memory-rollup` (rollup primitive with batch link rewriting).
  - `memory-link-rewriter` (UID-link integrity; `check`, `rename`, `rename-batch`, `upgrade` subcommands).
  - `memory-frontmatter-backfill` (Phase 1 migration helper).
  - `memory-preamble-extract` (Phase 1 migration helper).
  - `memory-dlp-scan` (detect-secrets + Shannon-entropy heuristic for memory bodies).
  - `memory-audit-log` (append-only JSONL with chained hash; `tail_record` for append without full reread).
  - `memory-dedup` (LLM-assisted near-duplicate detection; `--local-only` and `--redact-descriptions` are defaults).
- **Controlled topic taxonomy** at `spec/taxonomy.yaml`. Pinned vocabulary for the `topic:` axis to prevent label sprawl. Version 0.3.1 of the taxonomy ships alongside spec 0.3.0.
- **Repository-level `SECURITY.md`** with the `security@ildan.ai` reporting channel and the disclosure SLA.

### Changed

- **All shared tooling now imports `memforge.frontmatter.parse`** for YAML-frontmatter parsing. Eliminates seven prior copies of the same parser across the tooling tree.
- **`memory-dedup` rewritten in Python.** Defaults to local-only mode (no remote LLM call) and to redacted descriptions when remote dispatch is opted in.
- **`memory-cluster-suggest` clustering pre-filter** uses inverted topic and token indexes; eliminates the O(n²) pairwise pass on large folders.
- **Bash portability fixes** in `memory-audit`. Replaced macOS-specific `stat -f` and `date -r` with python3 inline calls so the tool runs the same on Linux + macOS.

### Fixed

- `memory-audit-log` no longer reads the full JSONL on append; uses `tail_record()` (seek-from-EOF) to read the prior chain hash in O(1).
- `memory-index-gen` preserves `access_labels` as a list (was being scalar-coerced); tier-and-team composition now applied correctly.

### Security

- DLP pass is now part of `memory-audit` health checks rather than a separate manual step.
- `memory-dedup` redacts memory descriptions before any optional remote LLM dispatch.

[0.3.0]: https://github.com/ildan-ai/memforge/releases/tag/v0.3.0

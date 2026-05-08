# Changelog

All notable changes to MemForge are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The version number tracked here is the **package / tooling** version. The on-disk **spec version** (used in memory frontmatter and folder-index files) is tracked separately at `spec/VERSION` and follows its own SemVer track. A package release MAY include a spec bump; a spec bump MAY ship in a patch release.

## [Unreleased]

The Contributor License Agreement infrastructure is counsel-blocked; external pull requests are paused until the CLA flow lands.

## [0.4.3] - 2026-05-08

Patch release: closes the duplicate-keys / growing-frontmatter regression in `memory-frontmatter-backfill` that surfaced when a memory file's `description:` value contained an unquoted colon-space. No spec changes (spec stays at 0.4.0).

### Fixed

- `memory-frontmatter-backfill` no longer corrupts files whose YAML frontmatter fails to parse. Previously, when `yaml.safe_load` raised on a value like `description: A: B description with embedded colon-space`, the parser collapsed the failure to an empty dict, every required field appeared "missing", and `apply_change` line-appended a fresh set of fields. The `memory-auto-commit.sh` PostToolUse hook then re-ran backfill on every Write/Edit, producing growing blocks of duplicate keys. Two-layer fix: (1) `plan_change` detects "frontmatter present but YAML parse failed" via the new `_frontmatter_present_but_unparseable` helper and skips with a stderr warning; (2) `apply_change` now does a dict-merge round trip via `memforge.frontmatter.render` instead of line-level appending, so duplicate keys are structurally impossible in the output even when called repeatedly.

### Tests

- New `tests/test_frontmatter_backfill.py` covers the colon-space detection path, the duplicate-key edge case (PyYAML silently last-wins, helper correctly returns False), the round-trip render of valid YAML, idempotency under repeated `apply_change` calls, preservation of existing operator-set fields, and the defense-in-depth skip in `apply_change` when fed broken YAML directly.

## [0.4.2] - 2026-05-08

Patch release: completes the `memory-audit` recursive-validation gap surfaced after v0.4.1, and bumps GitHub Actions to Node.js 24-compatible versions. No spec changes (spec stays at 0.4.0).

### Fixed

- `memory-audit` per-file frontmatter validation now recurses into rollup subfolders (excluding `archive/`), as required by spec §"Rollup subfolders" ("Audit tools MUST recurse into rollup subfolders to validate frontmatter, but MUST NOT generate parent-MEMORY.md pointers for detail files"). Previously the per-file audit reused the pointer-comparable file set, so detail-tier files inside rollups were silently skipped — YAML parse failures, missing frontmatter, invalid types, sensitivity issues, and staleness in those files all went unreported. v0.4.1 closed the orphan-pointer half of this gap; v0.4.2 closes the per-file half.

### Changed

- New helper `_files_to_audit()` in `memforge.cli.audit` returns the recursive audit set (top-level `.md` plus every `.md` inside any first-level subfolder, excluding `archive/`). The orphan-pointer comparison still runs against `_disk_md_files()` (top-level + rollup READMEs only), so detail-tier files do NOT generate spurious `Orphan file (no pointer)` violations. The per-file audit at `audit_target` line ~193 now iterates `_files_to_audit()`.
- `.github/workflows/release.yml`: bumped pinned action SHAs to Node.js 24-compatible major versions:
  - `actions/checkout` v4 -> v6.0.2
  - `actions/setup-python` v5 -> v6.2.0
  - `actions/upload-artifact` v4 -> v7.0.1
  - `actions/download-artifact` v4 -> v8.0.1
  - `pypa/gh-action-pypi-publish` `release/v1` -> v1.14.0

  Closes the v0.4.1 publish-workflow annotation about Node.js 20 deprecation (forced default June 2, 2026; runner removal September 16, 2026).

### Added

- `tests/test_audit.py` grows from 5 to 9 cases. New: `_files_to_audit` semantics (top-level only / recurses into rollups / excludes archive recursively) plus an end-to-end test asserting that a YAML parse failure in a rollup detail file is correctly reported (regression coverage for the silent-skip behavior pre-v0.4.2).

## [0.4.1] - 2026-05-08

Patch release: audit fix, adapter improvement, install docs. No spec changes (spec stays at 0.4.0).

### Fixed

- `memory-audit` no longer fires false-positive `Orphan pointer (no file)` for rollup-subfolder README files. `_disk_md_files` now returns top-level `.md` files plus `<topic>/README.md` for each first-level subfolder (excluding `archive/`), matching the spec's §"Rollup subfolders" rule that rollup READMEs are tier:index files surfacing in the parent `MEMORY.md`. Per-file frontmatter validation also extends to those READMEs as a side effect, narrowing the audit-vs-spec gap on subfolder coverage. Closes #8.

### Changed

- Adapter `adapters/claude-code/hooks/memory-auto-commit.sh` now runs `memory-frontmatter-backfill --apply` before committing on every Write/Edit inside a memory folder. Auto-normalizes v0.3-shaped frontmatter (the Claude Code harness auto-memory instruction emits 3 fields) to v0.4 shape on write. Portable: uses `command -v` to find the backfill CLI; silently skips if absent (works on machines without the package installed).

### Documentation

- README adds a "macOS / Homebrew Python (PEP 668)" subsection covering pipx, dedicated venv, and `--user --break-system-packages` install variants. Same guidance applies to most current Linux distributions that mark their system Python `EXTERNALLY-MANAGED`.
- `tools/README.md` clarifies that the `.py` shim scripts are reference implementations, not the canonical install path. `pip install ildan-memforge` installs all CLI commands as `console_scripts` entry points on `$PATH`, which is the supported install path.
- Tool-count references updated from "15 CLI commands" / "15+ commands" to "17 commands" (v0.4.0 added `memforge-resolve` and `memforge-migrate-claim-block`).

## [0.4.0] - 2026-05-08

Package release carrying spec v0.4.0.

### Added

- `memforge-resolve` CLI: walk the operator through reconciling competing claims for a `decision_topic`. Mutates winner / loser frontmatter, deletes any active snooze, and commits with `memforge: resolve <topic>` prefix.
- `memforge-migrate-claim-block` CLI: idempotent depth-aware fixer for the canonical reader-side competing-claim block in `MEMORY.md`.
- `memory-audit --export-tier=<level>` flag: v0.4 sensitivity export-tier gate. Reads `audit.default_export_tier` from `.memforge/config.yaml` when the flag is absent. Privileged-tier files always block when the gate runs, regardless of `audit.enforce_sensitivity_export_gate` config.
- `memory-dlp-scan --no-sensitivity-cross-check` flag: per-invocation override for the v0.4 `sensitivity_label_mismatch` BLOCKER. Cannot disable when implied tier is `privileged` (hard floor).
- `memforge.cli._config` module: shared loader for `.memforge/config.yaml` with auto-discovery, defaults merge, frontmatter sensitivity parser, and canonical `TIER_ORDER` constant.
- `memforge.cli._concurrency_audit` module: layered Tier 1 (HEAD-pure) and Tier 2 (commit-log) audit invariants for the v0.4 multi-agent concurrency surface.
- `tests/conformance/sensitivity/`: five conformance scenarios covering export-tier-{public, internal, restricted, privileged} and label-mismatch-blocked.
- `.gitleaks.toml` allowlist for the DLP scanner files (their own regex patterns are detected by gitleaks otherwise).

### Changed

- `memory-dlp-scan`: `Pattern` dataclass gains `implied_tier` field; PATTERNS table maps every detector to a tier (secret-class -> restricted, PII -> restricted, high-entropy heuristic -> internal). Cross-check fires by default; emits BLOCKER `sensitivity_label_mismatch` when declared sensitivity is below the highest implied tier across findings.
- `memory-audit`: integrates the v0.4 concurrency audit (`run_concurrency_audit`) and the export-tier gate. Surfaces `[v0.4]` and `[v0.4 MAJOR]` / `[v0.4 WARN]` prefixes in the violation list.
- `memory-index-gen`: `render_competing_claims_block(folder_root)` emits the canonical fenced YAML block per the v0.4 reader-side contract; byte-match CI on the rendered block.

### Test coverage

- 163 tests pass on Python 3.10 / 3.11 / 3.12 (39 new in this release).

## [spec 0.4.0] - 2026-05-08

v0.4 is a major bump per SemVer applied to spec semantics. The package release carrying this spec is `[0.4.0]` (above).

### Added (spec)

- **§"Multi-agent concurrency: competing claims" section** in SPEC.md. Five new frontmatter keys (`decision_topic`, `replaces`, `superseded_by`, `topic_aliases`, `ever_multi_member`), a snooze record at `.memforge/snoozes/<topic>.yaml`, a config file at `.memforge/config.yaml` with hard-floor + edit-gate protection, the resolve operation contract (tool-neutral; CLI reference + Claude Code skill + Cursor / Continue / Aider / shell wrappers), the canonical reader-side competing-claim YAML block (byte-match CI), and a layered Tier 1 (HEAD-pure) + Tier 2 (commit-log) audit rule set. Closes the v0.3.x "Multi-user concurrency semantics (Phase 2+ concern)" deferral.
- **Status enumeration BLOCKER**: any value outside `{active, proposed, gated, superseded, dropped, archived}` is a HEAD-pure audit BLOCKER.
- **Status transition gating**: transitions to `superseded` MUST occur in a `memforge: resolve <decision_topic>` commit (Tier 2 BLOCKER if violated). Transitions to `archived` go through the generator (existing Phase-1 contract).
- **Secure-mode adapter conformance** (informative): adapters MAY claim secure-mode by detecting branch protection + required signed commits at startup; informative startup notice required when not in secure-mode.
- **Reserved-name slug denylist** for `decision_topic` (Windows device names: `con`, `aux`, `nul`, `prn`, `com[0-9]`, `lpt[0-9]`).
- **§"Sensitivity enforcement (v0.4.0+)"** in SPEC.md. Three default-on, operator-disable-able checks tied to a hard floor at the `privileged` tier: an audit-side **export-tier gate** (`memory-audit --export-tier=<level>` or `audit.default_export_tier` config), a DLP-side **label/content cross-check** that emits BLOCKER `sensitivity_label_mismatch` when declared sensitivity is below the implied tier of body content, and a **conformance fixture set** (`tests/conformance/sensitivity/`) covering five scenarios that secure-mode adapters MUST pass. Adds three config keys (`audit.default_export_tier`, `audit.enforce_sensitivity_export_gate`, `dlp.enforce_sensitivity_cross_check`); a `--no-sensitivity-cross-check` CLI flag on `memory-dlp-scan`; and a `--export-tier` flag on `memory-audit`. Privileged-tier enforcement cannot be disabled by config.

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

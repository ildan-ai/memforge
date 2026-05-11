# MemForge spec

Version 0.5.0.

## Goal

Define a portable, agent-neutral format for persistent memory that any coding agent can consume through a thin adapter. The format describes a folder of typed markdown files with a single index, optionally organized into rollup subfolders. Loading semantics (how an agent reads the folder into a session) are the adapter's responsibility, not this spec's.

## Folder layout

A MemForge folder contains:

```
MEMORY.md                   # Required. Index of all top-level (tier:index) memory files.
<name>.md                   # Top-level memory files. tier:index files appear in MEMORY.md.
<topic>/                    # Optional rollup subfolders (Phase 0+).
  README.md                 # Required when subfolder exists. Acts as the rollup parent (tier:index).
  <name>.md                 # Detail-tier (tier:detail) memory files.
archive/                    # Reserved subfolder. Contains superseded files; excluded from audit recursion.
```

Rollup subfolders are first-class in v0.3.0. Detail-tier files inside them are not pointed to from MEMORY.md (which would defeat compression); they are surfaced via the rollup README.md, which IS pointed to from MEMORY.md as a tier:index file.

## File format

Each memory file is a markdown file with a YAML frontmatter block at the top.

```markdown
---
name: <short human-readable name>
description: <one-line description, under ~200 characters>
type: <user | feedback | project | reference>
sensitivity: <public | internal | restricted | privileged>
uid: <stable permalink, format: mem-YYYY-MM-DD-slug>
tier: <index | detail>
tags: [topic:<name>, ...]
owner: <accountable maintainer (role or person identifier)>
created: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
last_reviewed: <YYYY-MM-DD>
status: <active | proposed | gated | superseded | dropped | archived>
supersedes: [<uid>, ...]
superseded_by: [<uid>, ...]
aliases: [<uid>, ...]
pinned: <true | false>
dynamic_supplement: [<query string>, ...]
references_global: [<uid>, ...]
referenced_by_global: [<uid>, ...]
access: <label, e.g., public | internal | counsel | team:security>
decision_topic: <slug>                          # v0.4.0+: identifies a multi-claim decision (see §"Multi-agent concurrency")
replaces: [<uid>, ...]                          # v0.4.0+: advisory, proposes that listed UIDs be superseded
superseded_by: [<uid>]                          # v0.4.0+: set ONLY by the resolve operation; length exactly 1
topic_aliases: [<slug>, ...]                    # v0.4.0+: mutual aliases on the canonical anchor memory
ever_multi_member: <true | false>               # v0.4.0+: monotonic anchor flag, set by resolve, never cleared
identity: <class:operator-uuid[:agent-session]> # v0.5.0+: REQUIRED on v0.5+ writes (see §"Multi-identity primitives")
signature:                                      # v0.5.0+: REQUIRED on v0.5+ writes (see §"Cryptographic attribution")
  algo: <gpg-rsa4096 | gpg-ed25519>
  signing_time: <ISO-8601 UTC>
  value: <base64 detached signature>
---

<body in markdown>
```

Required in v0.4.0: `name`, `description`, `type`, `uid`, `tier`, `tags`, `owner`, `status`, `created`. The `sensitivity` field remains independent (separate from `access`); when absent, consumers MUST treat the memory as `internal`.

**v0.3.x compatibility.** Files written under v0.3.x that lack the newly-required fields (`uid`, `tier`, `tags`, `owner`, `status`, `created`) load in **degraded mode**: adapters MUST accept them, MAY emit a warning, and SHOULD prompt the operator to backfill. A degraded-mode memory still appears in `MEMORY.md` but cannot participate in rollup contracts (no `owner` / `last_reviewed`), status-driven archival (no `status`), or any v0.4 reader-side contract that depends on a v0.4-required field. Backfill is one-shot per file: once the required fields are present, the file is fully v0.4-conformant.

`tier: index` is the default when absent (any top-level file is treated as index unless it explicitly declares `tier: detail`).

Filenames SHOULD be lowercase kebab-case and SHOULD start with the `type:` value (for example: `feedback_no_em_dashes.md`, `project_auth_migration.md`). Tools MAY warn on deviation; adapters MUST NOT reject non-conforming filenames.

### Sensitivity and the description field

`description` is treated as **public-classification metadata** by tooling that surfaces memory catalogs to LLMs (deduplication, semantic search, agent-side recall). Operators MUST NOT place PII, credentials, internal-project codenames, or other sensitive data in description fields, regardless of the file's `sensitivity` label.

The body of a memory file may contain content matching the `sensitivity` label; the description must not. Reference-by-uid from elsewhere in the body is the safe pattern when a sensitive subject must be named: the uid resolves to the file, but the description never reveals it.

Tools that ship descriptions to external services (e.g., `memory-dedup`) default to redacting descriptions and to refusing cloud dispatchers; operators must explicitly opt-in to either. See `tools/memory-dedup --help`.

## Type taxonomy

Every memory has exactly one type. The type determines how agents and tools treat the memory.

### `user`

Facts about the user: role, responsibilities, preferences, domain expertise, working style. Durable across projects. Used to frame responses to the user's perspective.

### `feedback`

Explicit guidance the user has given about how to approach work: what to do and what to avoid. Durable across sessions. Feedback memories SHOULD include:

- The rule, stated directly in the body's first line.
- A `**Why:**` line explaining the reasoning (past incident, strong preference, cost, risk).
- A `**How to apply:**` line describing when and where the rule activates, including negative scope ("does NOT apply to X").

The Why + How to apply structure lets an agent judge edge cases instead of blindly pattern-matching the rule.

### `project`

Context about ongoing work, goals, initiatives, bugs, or incidents that is not derivable from the code or git history. Project memories change faster than other types and SHOULD include:

- The fact or decision.
- A `**Why:**` line with the motivation (constraint, deadline, stakeholder ask).
- A `**How to apply:**` line describing how it should shape agent suggestions.

Absolute dates MUST be used for any time reference; relative dates ("Thursday", "next week") lose meaning once the memory ages.

### `reference`

Pointers to information in external systems (ticket trackers, dashboards, shared docs, monitoring boards). Used so the agent knows where to look rather than what to recall. Reference memories SHOULD name the system and its purpose, and MAY include a URL or path.

## Tier semantics

The `tier` field distinguishes memories by their role in the index:

- **`index`** (default): Surfaces in MEMORY.md. Loaded at session start. Includes top-level memories AND rollup README.md files inside subfolders. Should be concise; the body of an index file should contain the synthesized rollup, not deep detail.
- **`detail`**: Lives inside a rollup subfolder. NOT surfaced in MEMORY.md. Read on-demand when an agent needs deep detail beyond what the rollup README.md exposes. Carries the original conception trail, panel reviews, per-decision history, etc.

Adapters MUST surface only `tier: index` files in the session-start MEMORY.md hotlist. Adapters MAY load `tier: detail` files on demand (for example, when the index rollup body references a specific detail UID).

## Status semantics

The `status` field is the single source of truth for memory lifecycle. v0.3.0 introduces the following values:

- **`active`** (default when absent): The memory reflects current state and applies. Surfaces normally.
- **`proposed`**: A new memory under review. Surfaces normally but adapters MAY tag it as draft.
- **`gated`**: The memory is contingent on an external decision (counsel signoff, vendor signature, etc.). Surfaces normally but tools MAY watch the gate.
- **`superseded`**: Replaced by another memory. The `superseded_by` field MUST point to the replacement. Generators MUST archive these files (move to `archive/`) within the time-to-archive SLO.
- **`dropped`**: The memory's premise no longer applies; not replaced. Generators MUST archive.
- **`archived`** (terminal): The memory has been moved to `archive/`. Should not appear outside `archive/`.

Pointer-hook status flags ("DROPPED", "PENDING", etc. in MEMORY.md hook lines) are deprecated in v0.3.0 in favor of frontmatter-driven status. The generator (when implemented) is the only sanctioned mover; manual archive moves are discouraged.

**v0.4.0 status enumeration enforcement.** The valid `status` values are exactly the six listed above: `active`, `proposed`, `gated`, `superseded`, `dropped`, `archived`. Any other value (including empty string, typos, or unratified future-extension values) is a HEAD-pure audit BLOCKER. Adapters MUST reject writes with invalid status at write-time.

**Live set for multi-agent concurrency.** For the purposes of competing-claim grouping (see §"Multi-agent concurrency"), memories with `status ∈ {active, proposed, gated}` are *live*. Memories with `status ∈ {superseded, dropped, archived}` exit the live set. The live-set partition is normative and used by the reader-side competing-claim contract.

**Status transition gating (v0.4.0+).** Transitions to `status: superseded` MUST occur inside a `memforge: resolve <decision_topic>` commit; any other commit setting a memory's status to `superseded` is a Tier 2 audit BLOCKER. Transitions to `status: archived` MUST occur via the generator (consistent with the existing Phase-1 archive contract). Transitions to `status: dropped` are operator actions and SHOULD use a `memforge: drop <reason>` commit prefix (advisory, not BLOCKER in v0.4.0).

## Rollup subfolders

A rollup subfolder is a topic-coherent grouping that compresses the MEMORY.md index. The pattern:

1. The subfolder is named for the topic anchor (e.g., `auth/`, `infra/`, `monitoring/`).
2. The subfolder contains a `README.md` file with `tier: index` and frontmatter describing the rollup. This README.md is the only file in the subfolder that surfaces in the parent MEMORY.md.
3. Detail-tier files (`tier: detail`) inside the subfolder are deep-dive lookups, not session-start context.
4. Audit tools MUST recurse into rollup subfolders (excluding `archive/`) to validate frontmatter, but MUST NOT generate parent-MEMORY.md pointers for detail files.

When a topic accumulates 5+ topic-coherent memories, tooling SHOULD suggest creating a rollup subfolder. The `memory-rollup` tool (Phase 1) automates the move; the spec defines the target shape.

## Sensitivity classification

The optional `sensitivity` frontmatter field labels each memory with one of four levels. Its purpose is to let downstream consumers (adapters, exporters, cross-surface bridges) make filtering decisions without having to inspect content.

Sensitivity is metadata, not access control. Adapters MAY honor it (for example by excluding `restricted` or `privileged` memories from an export) or ignore it. Consumers that ignore sensitivity MUST NOT treat all memories as `public`; the safe default when the field is absent is `internal`.

### Levels

- **`public`**: Safe to publish externally.
- **`internal`** (default when absent): Safe across the user's own tools and surfaces. Not for external publication.
- **`restricted`**: Sensitive content that must be contained. MUST be excluded from cross-perimeter exports unless the consumer is configured with a higher sensitivity ceiling.
- **`privileged`**: Attorney-client privileged or equivalent. MUST NOT cross to any network-bound destination unless the operator has explicitly marked the destination as privileged-eligible (typically local-only models).

Sensitivity is the memory author's declaration at the time of writing. Tooling MAY suggest upgrades but MUST NOT downgrade automatically.

### Sensitivity enforcement (v0.4.0+)

Three layered checks enforce sensitivity labels by default. Each is operator-disable-able through `.memforge/config.yaml`. A hard floor protects `privileged`-labeled content: no config knob can disable enforcement at the privileged tier.

Tier ordering (used by all three checks):

```
public < internal < restricted < privileged
```

Absent `sensitivity` field is treated as `internal` for ordering comparisons (consistent with §"Sensitivity classification").

#### Export-tier gate (audit-side)

`memory-audit --export-tier=<level>` MUST exit BLOCKER when any file's declared `sensitivity` exceeds `<level>`. Adapter export paths that filter by tier MUST run this audit before export. The check is operator-disable for `restricted` and below via `enforce_sensitivity_export_gate: false`; `privileged` is always enforced regardless of config.

When `--export-tier` is not supplied, the audit reads `audit.default_export_tier` from config (default: not set, meaning the gate is no-op). Operators who want CI to gate every plain `memory-audit` invocation set `audit.default_export_tier: public` (or another level).

#### Label / content cross-check (DLP-side)

`memory-dlp-scan` MUST flag a BLOCKER `sensitivity_label_mismatch` finding when a memory file's body contains content whose implied sensitivity tier exceeds its declared `sensitivity`. Pattern-to-tier mapping:

| Content class | Implied tier |
| --- | --- |
| Secret material (API keys, private keys, tokens, passwords) | flagged regardless of label; secrets must not be in memory at all |
| Identifiers (AWS ARNs, JWT tokens, account numbers, DocuSign account IDs) | `restricted` |
| PII (SSN, credit-card-like) | `restricted` |
| Generic high-entropy near a secret-keyword (entropy heuristic) | `internal` |

Disable for `restricted` and below via `dlp.enforce_sensitivity_cross_check: false`; cannot disable for `privileged` (reserved for future privileged-tier markers). The CLI flag `--no-sensitivity-cross-check` provides a per-invocation override at the same scope.

The cross-check runs only on files that parse as MemForge memory (frontmatter present with `name`, `type`). Plain markdown without frontmatter is exempt (no false positives on docs).

#### Conformance fixtures (adapter-side)

The repository ships `tests/conformance/sensitivity/` with documented scenarios that secure-mode adapters MUST pass. Each scenario is a directory containing `input/` (a synthetic memory folder) and `expected.json` (assertions about what the adapter should expose). Required scenarios:

- `export-tier-public`: only `public`-labeled files appear in the export.
- `export-tier-internal`: `public` + `internal` only.
- `export-tier-restricted`: `public` + `internal` + `restricted`.
- `export-tier-privileged`: all four levels.
- `label-mismatch-blocked`: a file claiming `sensitivity: public` with restricted-tier body content; the export tooling MUST refuse the file (BLOCKER from DLP cross-check before export).

The conformance harness (`pytest tests/conformance/sensitivity/`) is enabled by default. Adapter authors who deliberately target a non-secure-mode profile MAY skip via `pytest.skip("non-secure-mode")` with the rationale documented in their adapter README; spec-conformance claims are forfeit for the skipped scenarios.

#### Config keys (added to `.memforge/config.yaml`)

```yaml
audit:
  default_export_tier: null            # null | public | internal | restricted | privileged
  enforce_sensitivity_export_gate: true # cannot disable for privileged
dlp:
  enforce_sensitivity_cross_check: true # cannot disable for privileged
conformance:
  enforce_sensitivity_fixtures: true    # advisory; CI integration is adapter-side
```

Validation rules (HEAD-pure): `audit.default_export_tier` MUST be `null` or one of the four levels. `enforce_sensitivity_export_gate` and `dlp.enforce_sensitivity_cross_check` MUST be boolean. Any attempt to set either to `false` while a `privileged`-labeled memory exists in the folder is BLOCKER (the floor takes precedence over the disable).

## Access labels

The `access` field (v0.3.0+) is independent of `sensitivity`. Where sensitivity describes containment-by-content-type, access describes who can read the memory in a multi-tenant context. Adapters MAY enforce access labels via RBAC.

Initial vocabulary (extensible per deployment):

- `public` , anyone can read
- `internal` (default) , workspace members
- `counsel` , privileged legal-advisory role
- `team:<name>` , team-scoped (e.g., `team:security`, `team:platform`)

Access enforcement is an adapter responsibility. The spec defines only the field shape and the controlled-vocabulary expectation.

## Tag taxonomy

The `tags` field carries a list of taxonomy entries. Tags follow `<namespace>:<value>` format:

- `topic:<name>` , primary topic anchor (e.g., `topic:auth`, `topic:infra`). Drives rollup clustering.
- `area:<name>` , broader area for cross-cutting concerns (e.g., `area:ops`, `area:governance`).
- `priority:<value>` , operational priority (e.g., `priority:critical`).

The controlled vocabulary lives in `spec/taxonomy.yaml`. The vocabulary is versioned with the spec. Adapters MAY warn on tags outside the vocabulary; tools SHOULD provide a synonym map (e.g., `oauth` → `topic:auth`).

## Index format (`MEMORY.md`)

`MEMORY.md` is an index, not a memory. It MUST NOT carry frontmatter. It SHOULD be kept under 150 total lines.

In v0.3.0+, MEMORY.md is treated as a generated build artifact. Manual edits remain valid (current state) but the long-term direction is generator-driven via the `memory-index-gen` tool (Phase 1).

Each `tier: index` memory file MUST appear exactly once in the index (top-level files + rollup README.md files), as a pointer line in this shape:

```
- [<title>](<path>) , <one-line hook describing relevance>
```

For top-level files: `<path>` is the filename. For rollup README.md files: `<path>` is `<topic>/README.md`.

The hook line SHOULD stay under 150 characters (and 150 bytes; em-dashes are 3 UTF-8 bytes; prefer colons). Tools SHOULD flag overruns as warnings, not errors.

Section headers (`##`, `###`) MAY group pointers by theme. An introductory paragraph at the top is permitted.

## Multi-agent concurrency: competing claims

(v0.4.0+. Closes the v0.3.x §"Not in scope" deferral on multi-user concurrency.)

Two writers (two CC sessions, two human operators, an agent and an operator, two collaborators on the same repo) may make conflicting claims about the same decision. v0.3.x has no opinion: the data layer is git plus filesystem, last writer wins on disk. v0.4.0 adds five frontmatter keys, a snooze record, a config file, a reader-side contract, and an audit rule set that together implement **surface, do not resolve**: competing claims are detected mechanically and surfaced to the operator; readers refuse to silently pick a winner; resolution is operator-mediated through a single canonical operation.

### Frontmatter additions

- **`decision_topic`** (string slug): identifies the decision. Two memories with the same `decision_topic` and different bodies, both in the live set, are a competing-claim pair.

  Slug pipeline (normative). Adapters MUST apply this transformation in order, then verify:
  1. Unicode NFKD normalize.
  2. Strip Unicode combining marks (category Mn).
  3. ASCII transliterate (best-effort: `é` → `e`, `ß` → `ss`).
  4. Lowercase.
  5. Replace whitespace and underscores with single hyphens; collapse runs of hyphens.
  6. Trim leading/trailing hyphens.
  7. Verify length ≤ 64 (UTF-8 bytes, post-normalization).
  8. Verify regex `^[a-z0-9]+(-[a-z0-9]+)*$`.
  9. Verify slug is NOT in the reserved-name denylist: `con`, `aux`, `nul`, `prn`, `com1` through `com9`, `lpt1` through `lpt9`, `.`, `..`, plus `(con|aux|nul|prn|com[0-9]|lpt[0-9])(\..*)?$`. (Slugs become filenames under `.memforge/snoozes/`; Windows reserved device names break filesystem operations.)

  Adapters MUST apply the transformation and accept the result; they MUST NOT reject the writer's input outright unless verification fails at steps 7, 8, or 9.

- **`replaces`** (list of UIDs, advisory only): a writer setting `replaces:` is *proposing* the listed UIDs are no longer authoritative. Readers MUST NOT drop UIDs based on `replaces:` alone; only `status: superseded` on the target memory is the authority signal. Cardinality cap: 20. Every UID in `replaces:` MUST share the proposer's `decision_topic`; cross-topic `replaces:` is rejected at write-time and is a HEAD-pure audit BLOCKER if it slips through.

- **`superseded_by`** (list of UIDs, length exactly 1 when present): set ONLY by the resolve operation as part of an atomic operator-confirmed resolution. Writers MUST NOT hand-edit. Adapters MUST NOT mirror-write as a side effect.

- **`topic_aliases`** (optional, on the canonical anchor): list of alias slugs for cross-slug grouping. Anchor selection is HEAD-pure: the canonical anchor of a `decision_topic` group is the unique `status: active` member of the group (well-defined whenever the group carries `ever_multi_member: true`, since exactly-one-active is enforced). Aliases take effect only when bidirectional (mutual): anchor A lists B AND anchor B lists A. One-sided aliases are inactive (audit WARN). Cardinality cap: ≤ 10 aliases per anchor (audit MAJOR if exceeded). Adapters and `memory-audit` MUST detect cycles over the entire active mutual alias graph (transitive closure); cycles are HEAD-pure BLOCKER. `topic_aliases` mutations on a current anchor MUST occur in a `memforge: resolve <topic>` or `memforge: alias <topic>` commit (single-file scope).

- **`ever_multi_member`** (anchor-only, monotonic boolean): set to `true` by the resolve operation when the group has had two or more total members at any point. Once set, it is never cleared. Adapters MUST NOT clear it; `memory-audit` BLOCKER catches `true` → `false` transitions on HEAD via diff against the prior commit. The exactly-one-active invariant keys off this flag (HEAD-pure marker, no history walk).

### The resolve operation

The **resolve operation** is the canonical operator-mediated reconciliation flow. The spec defines it by post-conditions and commit shape, NOT by user-interface idiom. Any implementation that produces a conforming commit is a valid resolve operation.

Implementation surface (informative):

| Surface | How the resolve operation appears |
| --- | --- |
| CLI reference (load-bearing) | `memforge resolve <topic>` binary, ships in the canonical reference package. |
| Claude Code | Skill at `~/.claude/skills/consolidate-memory/SKILL.md` wrapping the CLI or implementing natively. |
| Cursor / Continue.dev / Aider | Slash command or rule that runs the CLI. |
| Plain shell / vim / emacs | Direct CLI invocation. |
| Web UI | Button POSTing to a server-side handler invoking the CLI. |

Spec conformance is verified by audit on the resulting commit, not by mode of invocation.

**Post-conditions.** After any successful `memforge: resolve <decision_topic>` commit:

1. Exactly one member of the affected group has `status: active`.
2. Every other member has `status: superseded` AND `superseded_by: [<active-member-UID>]` (length exactly 1, pointing to the unique active winner).
3. The active member's `replaces:` lists exactly the set of superseded members' UIDs in the group (no fewer, no more).
4. **Resolve-commit scope** (audit BLOCKER if violated): the commit touches ONLY (a) memory files whose `decision_topic == <topic>`, and (b) at most one snooze file at `.memforge/snoozes/<topic>.yaml` (deleted as part of resolution if previously present). Any cross-topic mutation or unrelated file in the same commit is BLOCKER.
5. The commit message MUST start with the exact prefix `memforge: resolve <decision_topic>`.
6. `memory-audit` running on HEAD after the commit produces zero findings against the resolved group.

If the operator chooses to write a new memory as the resolution (rather than picking an existing member), the new memory MUST inherit the same `decision_topic` and MUST list every prior member's UID in its `replaces:`. A "rename topic" action is out of scope for v0.4.0.

### Snooze record

Operators may legitimately defer reconciliation. The spec defines a per-group snooze record at `<memory-root>/.memforge/snoozes/<decision_topic>.yaml`:

```yaml
decision_topic: <slug>
snoozed_until: <YYYY-MM-DD>
snooze_reason: <free-form, ≥ 8 characters>
assignee: <owner>
created: <YYYY-MM-DD>
created_by: <owner>
```

Lifecycle:

- Snooze creation, edits, and deletion (outside resolve flow) MUST occur in commits whose message starts with `memforge: snooze <topic>` and that touch ONLY the single snooze file. Audit MAJOR if violated.
- Snooze deletion inside a `memforge: resolve <topic>` commit is permitted (deletion is part of the atomic resolution).
- Audit suppresses live-collision findings for any topic whose snooze is in effect (today ≤ `snoozed_until`), but emits a separate "snoozed" line per group so the queue is observable.
- Audit MAJOR `long-snoozed` once `today - created > horizon` (default 14 days, configurable). BLOCKER once `today - created > 2× horizon`.
- Audit MAJOR `orphaned snooze` if the snooze file exists for a topic with fewer than two live members.
- Audit MAJOR `date-forgery` if `created` is more than 24 hours prior to the commit timestamp that introduced the file.

**Snooze preemption** (collision-recency primary):

- Audit MAJOR if a snooze is created while the topic group has at least one live competing-claim pair younger than `audit.stale_collision_days` (default 7) since either member's `updated`. Operator override via `--ack-preemption` flag downgrades to WARN and records intent in commit metadata.
- Audit WARN (supplementary heuristic) if a snooze is created within 24 hours of a write by any author affecting any current member.
- Audit MAJOR back-to-back-renewal if the same `decision_topic` has had a snooze created within 24 hours of an expiring or recently-expired snooze. Operator override via `--allow-renewal`.
- Audit MAJOR per-author cap if total open snoozes per `created_by` exceed configured limit (default 10).
- Audit MAJOR per-topic cap if total open snoozes per `decision_topic` exceed 1 (no parallel snoozes per topic).

`created_by` is best-effort provenance, not unforgeable claim. Pure git allows amend; adapters MAY enforce stricter provenance via signed commits.

### Config file (`.memforge/config.yaml`)

Single-source-of-truth adapter config. Optional file; defaults apply if absent.

```yaml
spec_version: 0.4.0
audit:
  stale_collision_days: 7
  snooze_horizon_days: 14
  snooze_cap_per_author: 10
  decision_bearing_tags: []   # tags that mechanically require decision_topic
  audit_window_days: 30        # commit-log layer lookback (FLOORED at 30)
```

**Validation rules (HEAD-pure).** `audit.audit_window_days` MUST be ≥ 30 (BLOCKER if lower; the audit refuses to run on a sub-floor config). `audit.snooze_horizon_days` MUST be in `[1, 90]` (MAJOR if out of range). `audit.stale_collision_days` MUST be in `[1, 30]` (MAJOR). `audit.snooze_cap_per_author` MUST be in `[1, 100]` (MAJOR). `audit.decision_bearing_tags` MUST be a list of strings.

**Edit gate (Tier 2).** All edits to `.memforge/config.yaml` MUST occur in commits whose message starts with `memforge: config` AND that touch ONLY the config file. Multi-file or wrong-prefix commits modifying config are BLOCKER.

**Two-phase cutover for `decision_bearing_tags` shrinks.** A config change that shrinks `decision_bearing_tags` does NOT take effect immediately. For one full `audit.audit_window_days` after the shrink commit, the effective set is `union(old_tags, new_tags)` and audit emits MAJOR for the duration. At window end, the shrink takes effect. Operators may override via `memforge: config-major` prefix with a manifest of affected UIDs in the commit body; audit BLOCKER until each manifested UID is reconciled.

### Reader-side competing-claim contract

A consumer (CC adapter, memory-query, MEMORY.md generator) MUST:

1. Group memories by `decision_topic`, including topic-aliases-mapped groups (mutual aliases only). Memories with `status ∈ {superseded, dropped, archived}` are excluded; memories with `status ∈ {active, proposed, gated}` are included. Memories with any other `status:` value are excluded from grouping AND trigger the status-enumeration BLOCKER in audit.
2. For any group with two or more live members AND no active snooze, surface the set as a competing-claim block per the canonical serialization below.
3. **Snooze interaction**: when a snooze is in effect, the competing-claim block is SUPPRESSED but the canonical serialization includes a separate "snoozed" line listing the topic, snooze creator, `snoozed_until`, and `snooze_reason`. Operators are NOT blinded.
4. Refuse to silently pick a winner. Refuse to drop members based on any unratified `replaces:` claim.

The competing-claim block in `MEMORY.md` is a fenced YAML region with **canonical serialization**:

```yaml
# memforge:competing-claims:begin
- decision_topic: <slug>
  state: <competing|snoozed>
  snoozed_until: <YYYY-MM-DD>          # only present when state is snoozed
  snooze_reason: <free-form>           # only present when state is snoozed
  members:
    - uid: <mem-uid>
      owner: <owner>
      status: <active|proposed|gated>
      updated: <YYYY-MM-DD>
      first_line: <NFKD-normalized first non-empty body line, truncated to 117 UTF-8 bytes + literal "..." if truncation occurred>
      file_path: <relative path from memory root>
    - uid: <mem-uid>
      ...
# memforge:competing-claims:end
```

Canonical serialization rules (mandatory for byte-match CI):

- 2-space indent, no tabs.
- Key emission order MUST match the example exactly: `decision_topic`, `state`, conditionally `snoozed_until` and `snooze_reason`, then `members`; per-member `uid`, `owner`, `status`, `updated`, `first_line`, `file_path`.
- Block style only; no flow style.
- String values single-quoted only when they contain reserved YAML characters; otherwise plain.
- `first_line` truncation: NFKD normalize first, then take the first 117 UTF-8 bytes (split safely on UTF-8 boundary), then append the literal three-character string `...` only if any truncation occurred.
- Stable sort: groups by `decision_topic` ascending; within each group, members by `updated` descending then `uid` ascending.
- Empty groups (zero or one live member after exclusion AND no active snooze) are omitted.

**Migration story for the per-group field rename (v4 `status:` → v5+ `state:`).** For the entire v0.4.x train, parsers MUST accept BOTH `state:` (canonical) and `status:` (legacy) in the per-group block. Generators MUST emit `state:` only. CI byte-match tolerance MAY treat both forms as equivalent during v0.4.x; at v0.5.0, byte-match becomes strict and only `state:` is accepted. Adapters SHOULD provide a one-shot fixer (e.g., `memforge migrate-claim-block`) to rewrite legacy blocks.

### Audit invariants for competing claims

`memory-audit` v0.4.0+ enforces the layered rule set below. Tier 1 invariants are evaluated against HEAD alone (history-independent); Tier 2 invariants walk git log over the audit window (default 30 days, floored at 30 by config validation).

**Tier 1 (HEAD-pure) BLOCKERs**: asymmetric supersession (every `superseded` has `superseded_by: [exactly 1 UID]` pointing to sole active winner of same group; symmetric `replaces:` on winner); exactly-one-active for any group whose anchor or any member carries `ever_multi_member: true`; status enumeration; cross-topic replaces; dangling replaces; alias cycle; resolve-commit producing zero or two-plus active in resolved group; `ever_multi_member: true` → `false` transition; sub-floor `audit.audit_window_days` in config.

**Tier 1 MAJORs**: replaces cardinality > 20; alias cap > 10.

**Tier 1 WARNs**: alias non-mutual; near-duplicate slugs (edit distance ≤ 2 lacking mutual alias); adapter horizon misconfiguration; snooze author-correlation heuristic.

**Tier 2 (commit-log) BLOCKERs**: status transition to `superseded` outside resolve commit; `superseded_by:` written outside resolve commit; `decision_topic` mutation on existing memory outside resolve commit; memory deletion of group member outside resolve commit; resolve-commit scope violation; config edit outside `memforge: config` commit.

**Tier 2 MAJORs**: pending resolution (scoped to same-topic, at least one other live member); live collision past stale threshold; long-snoozed past horizon; orphaned snooze; snooze preemption (collision-recency primary); snooze cap (per-author and per-topic); snooze date-forgery; alias edit outside resolve or `memforge: alias`; snooze edit outside `memforge: snooze`.

**Tier 2 BLOCKER (escalated)**: long-snoozed past 2× horizon.

### Residual git-layer threat (informative)

Tier 2 invariants can be partially evaded by a writer with force-push privileges across a long enough rewrite that erases the entire commit-log audit window. Tier 1 invariants still catch any inconsistent residue, but a sophisticated writer who rewrites all history into a fabricated-clean state (including the `ever_multi_member` flag's introduction, which is its own BLOCKER on HEAD) can evade the Tier 2 layer entirely.

This is a **git-layer threat**, not a MemForge-layer threat. The mitigation is at the git provider:

- **Branch protection** on the canonical branch (no force-push; require pull-request review).
- **Required signed commits** so author identity is harder to forge.
- **Required-status-checks** that include `memory-audit` running on the resulting HEAD.

**Secure-mode adapter conformance (informative).** Adapters MAY claim secure-mode. Secure-mode adapters MUST: (1) at startup, detect (via the git provider's API where available) whether the canonical branch has branch protection with no force-push, required pull-request review, and required signed commits; (2) emit a startup MAJOR if any is missing; (3) refuse to perform any resolve operation if branch protection is absent (operator may override with `--insecure`, recorded in commit metadata).

Adapters that do NOT claim secure-mode MUST emit an informative startup notice that the deployment is operating without git-layer protection and that Tier 2 audit guarantees are reduced. Solo-operator deployments running without branch protection are explicitly NOT in secure-mode; the operator accepts the residual force-push threat as part of running solo.

## v0.5.0 surface map

v0.5.0 extends the format with five coupled elements. The dependencies between the sections that follow:

```
+----------------------+      +-------------------------+      +----------------------+
| Multi-identity (E1)  |----->| Cryptographic           |<-----| Operator identity +  |
| identity + signature |      | attribution (E2)        |      | cross-store refs (E3)|
|  in frontmatter      |      | GPG, signing-time-aware |      | UUIDv7 + registry    |
+----------------------+      | verification, rotation  |      +----------------------+
                              +-------------------------+               |
                                       |                                |
                                       v                                v
                              +-------------------------+      +----------------------+
                              | Key lifecycle +         |      | Messaging adapter    |
                              | revocation (E5)         |<-----| (E4)                 |
                              | revoke commits, fallback|      | WebSocket, sender_uid|
                              | recovery-secret         |      | sequence, checkpoints|
                              +-------------------------+      +----------------------+
```

Shared envelope (signed across all paths): `{memory_body, identity, sender_uid, sequence_number, signing_time}`. The same envelope applies whether the memory arrives via git pull (E3 + E5) or via WebSocket (E4). Revocation events (E5) propagate via either path. The sections below specify each element in normative terms.

## Multi-identity primitives (v0.5.0+)

(Closes the v0.4.x §"Out of scope" deferral on multi-operator team-scale memory.)

The v0.4.0 format treats `owner` as a free-form string. v0.5.0 introduces two cryptographically-anchored identity classes and makes signed attribution REQUIRED on v0.5+ writes.

### Identity classes (normative)

- **Operator identity.** One per human. Long-lived GPG keypair (RSA-4096 or Ed25519). Stable operator-UUID (UUIDv7) generated at first MemForge install; survives key rotation, machine replacement, OS reinstall. Public key + identity record live in the operator-registry (§"Operator identity + cross-store references").
- **Agent identity.** One per CC / Cursor / Aider / Continue.dev / etc. session. Ephemeral GPG keypair generated at session start, deleted at session end. Cryptographically bound to the operator-UUID via a session-attestation record signed by the operator's long-lived key.

Adapters MUST refuse to accept a v0.5+ write whose `identity` does not resolve to either a current operator in the operator-registry OR an agent whose session-attestation is signed by a current operator.

### Frontmatter additions (v0.5.0+)

- **`identity`** (REQUIRED on v0.5+ writes). String. Format `<class>:<operator-uuid>[:<agent-session-id>]`. Examples: `operator:01HXY7Z8...` (operator write); `agent:01HXY7Z8...:cc-2026-05-10-aaaa1234` (agent write under that operator).
- **`signature`** (REQUIRED on v0.5+ writes). Object with three subfields: `algo` (v0.5.0: `gpg-rsa4096` or `gpg-ed25519`), `signing_time` (ISO-8601 UTC), `value` (base64 of detached signature bytes).

**Agent-session-id format (MUST; v0.5.1+).** The `<agent-session-id>` slot of `identity` MUST match the regex `^[a-z0-9]+-\d{4}-\d{2}-\d{2}-[a-z0-9]{8,16}$`. Adapter prefix (`cc`, `cursor`, `aider`, `continue`, `windsurf`, etc.) is operator-facing and informational; date is the session start date (YYYY-MM-DD UTC); suffix is 8-16 lowercase base32 characters from a CSPRNG. Adapters MUST reject non-matching agent-session-ids on read (audit MAJOR `agent_session_id_format_invalid`) and refuse to mint non-matching values on write.

`owner` is RETAINED in v0.5 frontmatter as advisory. v0.5 readers prefer `identity` for trust decisions; `owner` is informational.

### Signed envelope scope (normative)

The signature MUST cover the canonical serialization of:

```
{memory_body, identity, sender_uid, sequence_number, signing_time}
```

Substitution at any layer breaks the signature.

**Canonical-form Unicode normalization (MUST; v0.5.2+).** Before serialization, every string value in the envelope (keys + values, recursively) MUST be normalized to Unicode NFC (Canonical Composition). Implementations MUST canonicalize to NFC; serialization MUST be JSON with sorted keys + no whitespace + non-ASCII escape disabled (`ensure_ascii=False`); the result MUST be UTF-8 encoded. Closes the canonicalization repudiation vector where two visually-identical inputs in different normalization forms (NFC vs NFD) would produce different signed envelopes and therefore different signatures, letting a sender repudiate a signature by claiming the verifier received the "other" form.

### Mixed v0.4 / v0.5 deployment posture

v0.4 memories loaded by v0.5 readers are treated as **read-only-untrusted**. They appear in MEMORY.md but the reader explicitly tags them `(v0.4: unsigned)` in the prompt-injection layer. A v0.5 resolve commit MAY include v0.4 members; if so, the resolve commit MUST satisfy one of:

- **Upgrade path:** the v0.4 memory is rewritten in the same commit to include valid v0.5 `identity` + `signature`, with the signature verified against the current operator-registry.
- **Exclusion path:** the resolve commit body includes a YAML block `resolve.exclusion_reason:` listing the v0.4 UID + a non-empty reason string (≥ 8 characters).

Resolve commits violating both paths are Tier 2 BLOCKER (audit walks the diff, identifies any v0.4-shaped unsigned member in the resolved group, verifies upgrade-or-exclude).

Bulk upgrade tool: `memforge upgrade-v04-memories` (operator-discretion; documented operator-experience break during transition). Audit emits a one-time MAJOR per unsigned v0.4 memory loaded under v0.5 until upgrade or explicit exclusion.

### Agent session attestation content scope (v0.5.1+)

(Closes the v0.5.0 MAJOR on agent session attestation content scope.)

When an operator launches an agent session (CC, Cursor, Aider, etc.), the operator's adapter MUST create a session-attestation record signed by the operator's current long-lived key. The record is persisted to `<memory-root>/.memforge/agent-sessions/<agent-session-id>.yaml` (file mode 0600, parent 0700; adapters MUST verify modes + ownership at startup; reject on mismatch / fail-closed).

**Record format:**

```yaml
agent_session_id: cc-2026-05-10-aaaa1234
operator_uuid: <UUIDv7>
agent_pubkey: <base64-of-agent-ephemeral-pubkey-material>
agent_pubkey_algo: gpg-rsa4096 | gpg-ed25519
nonce: <32-byte hex from CSPRNG>
issued_at: <ISO-8601 UTC>
expires_at: <ISO-8601 UTC>
capability_scope:
  memory_roots:
    - <absolute path>
  allowed_operations:
    - write
    - resolve
operator_signature:
  algo: gpg-rsa4096 | gpg-ed25519
  signing_time: <ISO-8601 UTC>
  value: <base64 over canonical {agent_session_id, operator_uuid, agent_pubkey, agent_pubkey_algo, nonce, issued_at, expires_at, capability_scope}>
```

**Required content (normative).**

- **`nonce`**: 32 bytes from a CSPRNG, hex-encoded. Binds the attestation to this specific issuance. Receivers maintain a seen-nonce set per `operator_uuid` and reject any attestation whose nonce is already present. Closes the replay attack where an attacker captures a stale attestation and binds it to a new agent ephemeral keypair.
- **`expires_at`**: `issued_at + identity.agent_session_max_lifetime_hours` (default **24 hours**; configurable in `.memforge/config.yaml`; floor 15 minutes; ceiling 7 days). Receivers MUST reject any agent-signed write whose `signature.signing_time > attestation.expires_at`. Audit MAJOR `agent_session_attestation_expired`.
- **`capability_scope.memory_roots`**: explicit absolute paths the agent is authorized to write under. Receivers MUST reject agent writes touching paths outside this scope. Audit BLOCKER `agent_session_out_of_scope_write`.
- **`capability_scope.allowed_operations`**: explicit list from `{write, resolve, revoke, registry-edit, key-rotation, fresh-start}`. Default if absent: `[write]`. Operations beyond `write` under an agent identity require explicit operator authorization at attestation issuance. Adapters SHOULD warn loudly when issuing an attestation that grants any operation beyond `write` and SHOULD require operator confirmation for `revoke` / `registry-edit` / `key-rotation` / `fresh-start`.

**Receiver-side enforcement (normative; MUST).**

On first observation of an agent-signed write, the receiver:

1. Reads the attestation file at `<memory-root>/.memforge/agent-sessions/<agent-session-id>.yaml`.
2. Verifies the file's filesystem mode + ownership.
3. Verifies `operator_signature` on the attestation record against the operator's current key in the operator-registry (subject to the cross-signed rotation chain + signing-time-aware verification + cool-down rules in §"Cryptographic attribution"). This binds the agent pubkey + capability_scope + nonce + expiry to a specific operator at a specific time.
4. Verifies `nonce` is not in the seen-nonce set for `operator_uuid`; adds to seen-nonce set on success.
5. Verifies the write's `signature.signing_time ∈ [issued_at, expires_at]`.
6. **Verifies the write's `signature.value` against the `agent_pubkey` field from the attestation, using `agent_pubkey_algo`, over the canonical envelope `{memory_body, identity, sender_uid, sequence_number, signing_time}` per §"Signed envelope scope (normative)".** This is the cryptographic anchor that binds the write to the attested agent key; without it, an attacker who forges or replays an `identity` frontmatter value could impersonate the agent. The verification key MUST be drawn from the attestation's `agent_pubkey` (NOT from the operator-registry), because agent keys are ephemeral and never appear in the registry.
7. Verifies the write's path is within `capability_scope.memory_roots`.
8. Verifies the operation (write / resolve / etc.) is in `capability_scope.allowed_operations`.

Any failure → reject write + emit fail-closed audit event (MAJOR for expired / out-of-scope; BLOCKER for nonce-replay / bad signature on attestation OR on write / wrong-scope-operation). On step 4 nonce check, the receiver MUST persist the seen-nonce set across restarts; loss of the set on disk corruption falls under the receiver-state HALT rule.

**Seen-nonce set bounding (SHOULD; operational risk).** To prevent resource exhaustion, receivers SHOULD garbage-collect the seen-nonce set: discard each nonce once its corresponding attestation's `expires_at + backdating_max_skew` (default 10 minutes) has passed. An implementation that does NOT bound the seen-nonce set is vulnerable to a disk + memory DoS where an attacker who can request many short-lived attestations causes unbounded growth of the set; such an implementation SHOULD warn the operator about this risk at startup. The receiver-state HALT rule applies if the persisted set becomes corrupted or grows past a configured operational limit.

**Capability-scope defaults via reference CLI (operator-facing).** When an operator launches an agent via `memforge attest-agent` (v0.5.1 reference CLI), the default attestation issues with `capability_scope.memory_roots: [<cwd-resolved memory-root>]` + `capability_scope.allowed_operations: [write, resolve]`. Operators who want broader scope pass `--capability revoke`, `--capability registry-edit`, etc. at attestation issuance. Adapters that bypass the CLI (programmatic embedding) MUST set capability_scope explicitly; absent fields are NOT defaulted by receivers (receivers reject attestations missing required scope fields).

**v0.5.0 → v0.5.1 transition posture.** v0.5.0 spec mandated that agent identities have an attestation record but did not specify its content scope. v0.5.0 attestation files that lack v0.5.1 required fields (`nonce`, `expires_at`, `capability_scope`) are accepted by v0.5.1 readers with a one-time MAJOR `v05_attestation_incomplete_content` per file until the attestation is re-issued via the v0.5.1 reference CLI. Subsequent attestations for the same agent-session-id under v0.5.1 MUST conform to the full content scope.

## Cryptographic attribution (v0.5.0+)

### GPG default backend

v0.5.0 ships with GPG (RSA-4096 or Ed25519). Storage: gpg-agent (default) or operator-specified backend via `gpg --homedir`. Spec **recommends** hardware-key backing for operator long-lived keys in production (YubiKey, Nitrokey, Apple Secure Enclave via gpg-agent shim, OS-specific TPM). v0.5.0 ships software-only reference; hardware-backed is v0.5.x scope. Future v0.5.x+ backend extension: pluggable signature backend for Ed25519-pure, age, Sequoia, post-quantum schemes (Dilithium3, Falcon).

### Signing-time-aware verification

A signature is **valid** if:

1. `signature.algo` is on the adapter's accepted-algo list.
2. The signing key's public material resolves from the operator-registry as of the commit that introduced the memory.
3. The signing key was **not revoked** at `signature.signing_time` per the revocation set (§"Key lifecycle + revocation").
4. The detached signature verifies against the canonical envelope using the signing key's public material.
5. `signature.signing_time` falls within the receiver's `first_seen_at` clock-skew window (next subsection).

### Clock-skew guard against signing-time backdating

Both `signature.signing_time` and revocation events' `revoked_at` are writer-controlled timestamps. An attacker in possession of a signing key can choose an arbitrarily old `signing_time` value to construct a signature that appears to predate honest revocation events. v0.5.0 bounds this coordinated-backdate attack with the following normative receiver-side rule.

**`first_seen_at` clock-skew guard (normative).** On first receipt of a memory under a given (`identity`, `uid`) pair, the receiver MUST record `first_seen_at` (ISO-8601 UTC wall-clock at receipt) into the per-receiver state. On subsequent verification of any signature claiming that same memory body, the receiver MUST require:

```
signing_time ∈ [first_seen_at − backdating_max_skew, first_seen_at + future_max_skew]
```

Defaults: `backdating_max_skew = 10 minutes`; `future_max_skew = 1 minute`. Configurable via `.memforge/config.yaml` keys `identity.backdating_max_skew_seconds` and `identity.future_max_skew_seconds`. A signature whose `signing_time` falls outside this window is rejected + audit MAJOR `signing_time_skew_out_of_window`.

**Honest-operator assumption.** Without trusted timestamping infrastructure (e.g., RFC 3161 Time-Stamping Authorities, server-signed receipt times), coordinated backdating cannot be fully prevented. v0.5.0 assumes operators publish revocation events promptly after compromise detection. The clock-skew window narrows the residual attack surface. Operators SHOULD run NTP-synced clocks; high-stakes deployments SHOULD additionally publish server-signed receipt times (v0.5.x extension).

### Cross-signed rotation chain

When an operator rotates their long-lived key:

1. New keypair generated.
2. New operator-identity record built, listing the new public key + rotation metadata (`rotated_at`, `rotated_from`, `chain_index: N+1`).
3. The new record is signed by **both** the old key (cross-signature) and the new key.
4. The operator-registry update commit (`memforge: operator-registry`) lands the new record. Adapter verifies the cross-signature before accepting the rotation.

**Mandatory cool-down period (MUST; closes the key-rotation privilege-escalation attack).** After a valid rotation commit lands, adapters MUST NOT honor signatures from the **new** key for `identity.rotation_cooldown_hours` after the rotation commit's git author-date (default **24 hours**; configurable in `.memforge/config.yaml`; minimum floor 1 hour). **Registry-layer enforcement (MUST; v0.5.3+).** Cool-down enforcement MUST occur at the registry layer (e.g., a reference-implementation helper such as `memforge.registry.verify_signing_key_acceptable`), NOT solely at the CLI layer. A signature-verification helper MUST refuse a signing key that is in cool-down regardless of which CLI or alternate consumer initiated the verification. Closes the v0.5.2 threat-model MAJOR where a buggy or hostile alternate CLI consumer of the registry could bypass the cool-down by signing with the new key directly. During the cool-down window, signatures continue to verify against the old key (which remains valid until cool-down expiry); the new key is recognized in the registry but its signatures are rejected with audit MAJOR `key_in_rotation_cooldown`. The cool-down gives the legitimate operator a window to detect a malicious rotation (attacker who compromised the old key cannot immediately use the new key) and initiate the `memforge: key-compromise` procedure (which bypasses the cool-down because it follows a different commit prefix and recovery-secret-derived countersignature path).

Verification cost bounded by chain length. To bound across long operator histories, operators MAY publish a fresh-start operator-registry every 10 rotations (or on discretion); fresh-start is signed by the current key only (breaks chain forward). Verification cost is `min(N, 10)` cross-signatures. Commit prefix for fresh-start: `memforge: fresh-start <operator-uuid>`. Fresh-start commits ARE subject to the cool-down period (same rationale: compromised-key-controlled fresh-start is a privilege-escalation vector).

### Per-memory vs per-commit signing

Per-memory signing is the v0.5.0 normative scope. Per-commit signatures remain operationally useful for git-layer integrity (recommended in §"Secure-mode adapter conformance"); v0.5 inherits the recommendation but does not require it. The format is substrate-independent: the same memory verifies whether it arrived via git pull or WebSocket.

## Operator identity + cross-store references (v0.5.0+)

### Operator-identity file (per-machine)

`~/.memforge/operator-identity.yaml`:

```yaml
operator_uuid: <UUIDv7 string>          # generated at first MemForge install
operator_name: <human-readable name>     # advisory; for adapter UX
created: <ISO-8601 UTC>
machine_origin: <hostname>               # advisory; for backup/recovery audit
```

**Filesystem restriction (normative; platform-agnostic).** The file MUST be restricted to the current-owner read/write only; its parent directory MUST be restricted to current-owner rwx-only. The normative requirement is platform-agnostic: the file and parent MUST not be accessible by other principals on the host (no Everyone / Other / Authenticated Users / Guest equivalents). Implementations satisfy this contract two ways:

- **POSIX (macOS, Linux, *BSD)**: file mode 0600, parent mode 0700, `stat()` uid MUST match the effective uid of the adapter process; mismatch → fail-closed.
- **Windows (NTFS)**: file ACL + parent ACL MUST grant access only to the current user (no inherited ACEs; no Everyone / Authenticated Users / BUILTIN\\Users / BUILTIN\\Guests / NT AUTHORITY\\INTERACTIVE / similar principals present). Implementations SHOULD use `icacls /inheritance:r` to remove inherited ACEs + `icacls /grant:r <current-user>:F` to grant Full Control to the current user only.

Adapters MUST verify the restriction at startup. v0.5.2 introduces this platform-agnostic phrasing; v0.5.0 and v0.5.1 mandated POSIX mode bits specifically, which was a no-op on native Windows and effectively excluded Windows from conformance. v0.5.2 Windows implementations satisfy the spec via the ACL path.

### Operator-registry file (per-memory-root)

`<memory-root>/.memforge/operator-registry.yaml`:

```yaml
spec_version: 0.5.0
operators:
  - operator_uuid: <UUIDv7>
    public_keys:
      - key_id: <gpg-fingerprint or hash-of-pubkey>
        algo: gpg-rsa4096 | gpg-ed25519
        public_material: <base64>
        chain_index: 0
        introduced_at: <ISO-8601 UTC>
        introduced_by_commit: <git-commit-hash>
      - key_id: <new-key-id>
        algo: gpg-ed25519
        public_material: <base64>
        chain_index: 1
        introduced_at: <ISO-8601 UTC>
        introduced_by_commit: <git-commit-hash>
        cross_signature_by_chain_index_0: <base64>
    status: active | superseded
    operator_name: <human-readable>
registry_signature:
  algo: gpg-ed25519
  signing_uuid: <operator-uuid of registry signer>
  signing_time: <ISO-8601 UTC>
  value: <base64>
```

**Registry signature requirement (normative).** The registry MUST be signed by at least one operator listed in the file. Adapters MUST verify the signature: (1) at adapter startup; (2) on every `references_store` resolution; (3) after every registry-modifying commit. Verification failure (signature invalid, file corrupted, file missing) → adapter HALTS with fail-closed message `"Operator registry unverifiable; refusing to load v0.5+ memories until resolved."`

**Verification cache (normative).** Hot-path verification on every cross-store reference is performance-critical. Adapters MUST implement a content-hash-anchored cache: key = `(registry-file-path, file-size, file-mtime, sha256-of-content)`. On read: re-compute `(size, mtime, sha256)`; if any differs, cache INVALID and signature re-verifies. On any commit observed in git log with prefix `memforge: operator-registry` or `memforge: fresh-start` since the cache anchor, cache INVALID regardless of file-stat match.

**Edit gate (Tier 2).** All edits to `.memforge/operator-registry.yaml` MUST occur in commits whose message starts with `memforge: operator-registry` AND that touch ONLY the registry file. Multi-file commits OR wrong-prefix commits modifying the registry are Tier 2 BLOCKER.

### Cross-store references

`MEMFORGE_STORE = <operator-uuid>:<store-name>` for memory references across operators / machines / forks. Example: `mem:store:<operator-b-uuid>:my-decisions`.

Resolution:
1. Look up `<operator-b-uuid>` in operator-registry. Absent → HALT.
2. Verify registry signature (cached per above).
3. Locate the named store via adapter-configured store-discovery (default: filesystem path `<memory-root>/<operator-uuid>/<store-name>/`). Future v0.5.x: WebSocket fetch from registered remote.
4. Load memory, verify per-memory signature per §"Cryptographic attribution".

### Trust-bootstrap procedure for multi-operator deployments (operator-facing)

The format is filesystem + git; trust establishment is operator-mediated out-of-band. Standard PGP-trust-bootstrap procedure:

1. Operator A initializes (`memforge init-operator` + `memforge init-store` for the first repo).
2. Operator B initializes on their own machine.
3. Operator A and Operator B exchange public-key fingerprints out-of-band (Signal / phone / in-person / verified email). This is standard PGP-trust-bootstrap; the spec does not invent a new primitive.
4. Operator A: `memforge operator-registry add <operator-b-uuid> --pubkey-fingerprint <fpr>`. CLI verifies the fingerprint matches the pubkey material before adding.
5. Operator A commits with prefix `memforge: operator-registry`; registry is signed by A; B's entry has `chain_index: 0`.
6. Operator B clones the repo; B's adapter loads operator-registry signed by A; B sees A as trusted (B already trusts A's key) and themselves listed; B may write under their identity.

Operators SHOULD verify fingerprints via at least two independent channels for adversarial-environment deployments. v0.5.0 spec does not mandate but recommends.

## Messaging adapter contract (WebSocket reference; v0.5.0+)

v0.5.0 specifies a WebSocket reference adapter contract. Substrate locked: WebSocket. Adapter contract: hybrid MUST behavior + MAY substrate-detail.

**When to use it.** WebSocket is opt-in. The default git-only substrate is correct for solo operators and for teams whose write cadence makes minute-scale pull-cadence latency acceptable. WebSocket is the right choice when minute-scale cross-claim-detection windows would let stale consensus build up between pulls (typically: two or more operators with high daily write cadence, or a central audit surface that needs every memory write in real time). The signed-envelope contract below applies identically across both substrates; teams can start on git-only and flip to WebSocket later without re-keying or losing audit continuity. Operator-decision framing with concrete trade-offs lives at `docs/team-bootstrap.md` §"Pick your transport: git-only or WebSocket?".

### Sender-uid format (MUST)

`sender_uid = "<operator-uuid>:<32-byte-hex>"`. Cryptographic binding to operator-uuid + 256-bit-entropy random suffix. Generated at sender start. Persisted alongside sender-sequence file. Adapters MUST reject sender-uids that do NOT match this format.

### Sender-sequence + signed checkpoints (MUST)

Sender-sequence file at `<memory-root>/.memforge/sender-sequence/<sender-uid>.yaml`. Globally monotonic per sender; persisted (FS mode 0600; parent 0700); survives restart.

```yaml
sender_uid: <operator-uuid>:<random-hex>
operator_uuid: <operator-uuid>
created: <ISO-8601 UTC>
current_sequence: <uint64>
checkpoints:
  - sequence: <int>
    timestamp: <ISO-8601 UTC>
    signature: <base64 over canonical {sender_uid, sequence, timestamp, operator_uuid}>
```

Every **100 sequences OR 24 hours** (whichever first), the sender publishes a signed checkpoint over the canonical envelope, computed using the sender's writing key with operator-uuid cryptographically bound. Receiver maintains highest-seen-checkpoint per sender. Receiver REJECTS any write with `sequence < highest-checkpoint-sequence`.

**Sender-sequence file deletion / corruption recovery.** If the file is missing or unreadable at sender startup, adapter MUST generate a NEW sender-uid (do NOT resume with sequence 1 against the old uid). Old sender-uid stays revoked-by-implication. This is an **intentional sender rotation**, not a security gap; receivers maintain `highest_seen_sequence` per old sender-uid and continue rejecting any message arriving under the old uid below its floor.

**Sequence number overflow.** `current_sequence` is uint64. Adapters MUST detect overflow + halt + surface to operator. Practical mitigation: FS mode 0600 closes the int.max DoS vector at the OS layer.

### Receiver state (MUST)

Receiver-side state tracks `highest_seen_sequence` AND `highest_seen_checkpoint` per sender-uid. Persisted at `<memory-root>/.memforge/receiver-state/<sender-uid>.yaml`:

```yaml
sender_uid: <operator-uuid>:<random-hex>
operator_uuid: <operator-uuid>
highest_seen_sequence: <uint64>
highest_seen_checkpoint:
  sequence: <uint64>
  timestamp: <ISO-8601 UTC>
  signature: <base64>
first_seen_at: <ISO-8601 UTC>          # for clock-skew guard per Element 2
updated: <ISO-8601 UTC>
```

Filesystem mode: file 0600, parent directory 0700. Adapters MUST verify modes + ownership (`stat()` uid == effective uid) at startup; reject on mismatch (fail-closed).

**Reject rule (normative; closes the silent-rollback-between-checkpoints window):** Receiver MUST reject ANY message with `sequence <= highest_seen_sequence` for the (sender-uid, operator-uuid) pair, regardless of where it falls relative to the latest checkpoint. The earlier-spec language "REJECTS any write with `sequence < highest-checkpoint-sequence`" is the WEAKER form; the receiver-state mandate is the stronger form that closes the gap between two checkpoints.

**Checkpoint signature verification (normative):** Receiver MUST verify the checkpoint signature on every checkpoint observation (not only at receipt). Bad signature → reject the checkpoint + audit MAJOR `bad_checkpoint_signature`. Receiver MUST NOT silently accept an unverifiable checkpoint as the new floor.

**HALT on corruption.** If the receiver-state file is corrupted, missing-with-prior-anchor, or shows impossible regression (e.g., `highest_seen_sequence` lower than the cached value), adapter HALTS with fail-closed message: `"Receiver state corrupted for <sender-uid>; refusing to process v0.5+ messages until resolved."` Manual recovery procedure: operator inspects state + either restores from backup OR generates a new clean state (which forces sender to issue a new sender-uid; old uid is revoked-by-implication).

### Substrate-independent envelope contract (normative)

The sender_uid + sequence_number + signed-checkpoint machinery applies to ALL v0.5+ writes regardless of substrate. Git-only writers (no WebSocket adapter active) MUST still mint a sender_uid + monotonically increment sequence + publish signed checkpoints per the contract. Receivers MUST enforce envelope fields (signature, sender_uid, sequence_number, signing_time, clock-skew window) irrespective of how the message arrived.

### Connection security (MUST)

**Transport: TLS-only (`wss://`).** Adapters MUST reject any `messaging.url` whose scheme is `ws://` (plain WebSocket) at startup with a fail-closed message: `"MemForge v0.5.0 requires TLS for the WebSocket connection. Configure messaging.url to use wss://; ws:// is rejected."` This closes the network-MITM information-disclosure threat surface. Test/development environments that need plain WebSocket MUST run on localhost-only and pass an explicit `MEMFORGE_ALLOW_WS_LOCALHOST_ONLY=1` env var; even then the WS endpoint MUST resolve to a loopback address and adapters MUST refuse non-loopback `ws://` URLs.

**Operator authentication: strong, per-operator (MUST).** Adapters MUST authenticate every incoming WebSocket connection using one of:

- **(a) Mutual TLS (mTLS)** with operator-bound client certificates. Server validates the client certificate's subject against the operator-registry's accepted operator-UUIDs; reject on mismatch.
- **(b) Per-operator bearer tokens** (e.g., JWTs signed by the operator's long-lived key, with a short expiry of ≤ 1 hour and binding to the connection's TLS session via channel binding tokens or equivalent). Server validates the token's signature against the operator-registry's accepted operator-UUIDs; reject on signature failure, expired, or wrong audience.

Static / shared / no-auth schemes are rejected. Choice between (a) and (b) is implementation MAY; the requirement for per-operator strong authentication is normative MUST. Adapters MUST emit fail-closed startup error if the configured authentication mechanism does not satisfy this contract.

**Circuit-breaker on local queue exhaustion (MUST).** When local-first messaging falls back to queueing (per §"Sender / receiver MUST behavior" inherited above), adapter MUST stop accepting new local writes (fail-closed) when the local queue exceeds `messaging.max_local_queue_disk_space_mb` (default 1024 MB; configurable). Closes the WebSocket-server-DoS-causes-client-disk-exhaustion threat. The audit MAJOR thresholds (queue length > 100; oldest-message-age > 1 hour) remain advisory; the disk-space threshold is hard.

### Multi-server hard-stop (v0.5.0)

v0.5.0 single-server only. Adapter MUST emit startup warning: `"MemForge v0.5.0 messaging adapter supports single-server deployments only. Multi-server deployments have known consistency gaps. Set MEMFORGE_ACKNOWLEDGE_SINGLE_SERVER=1 to suppress this warning."` Multi-server with WebSocket needs Redis Pub/Sub or equivalent fan-out layer; deferred to v0.5.x / v0.6+.

### WebSocket server-side onboarding (operator-facing)

v0.5.0 ships client-side adapter only. The WebSocket server is operator's responsibility. Options:
- **Single-operator-mode-no-server.** Most multi-developer teams don't need WebSocket sync if they coordinate through git. Skip messaging adapter; rely on git-pull-based propagation.
- **Self-hosted server.** A v0.5.x reference server is planned but not in v0.5.0 scope. Operators who need WebSocket sync today can write their own following the MUST contract (sender-auth, sender-uid + sequence routing, multi-client fan-out).
- **Hosted service** (future v0.5.x+). Not in v0.5.0.

### MAY (substrate / implementation choices; non-normative)

- WebSocket library, serialization, retry/backoff, keepalive, operator-auth mechanism, reconnection strategy.
- **Server-signed receipt times** (v0.5.x consideration): WebSocket server countersigns the message on receipt with its own key + UTC clock; binds `signing_time` to a third-party clock. Stronger anchor than receiver-side `first_seen_at`. v0.5.0 ships without; v0.5.x considers.

## Key lifecycle + revocation (v0.5.0+)

### Revocation events as git commits

Each revocation = git commit with message prefix `memforge: revoke <key_id>` and body:

```yaml
key_id: <gpg-fingerprint or hash-of-pubkey>
revoked_at: <ISO-8601 UTC>
reason: <free-form, >= 8 characters>
revoked_by: <operator-uuid>
revocation_uid: <UUIDv7>
```

**Revocation commit signature (MUST; v0.5.0 normative).** The commit body MUST be GPG-signed by the revoking operator's current long-lived key. The signature MUST appear in the commit message body as `revocation_signature: <base64>`. Adapters MUST extract + verify this signature before honoring the revocation, in BOTH full-history walk mode AND remote-fetch fallback mode. Unsigned revocation commits are rejected + audit BLOCKER `unsigned_revocation_commit`. This contract applies regardless of how the commit reaches the adapter (local clone, remote fetch, sparse-checkout fallback).

**Signing-key-matches-revoked_by check (MUST).** Adapters MUST verify that the GPG key used to sign the revocation body is the currently active key (i.e., chain-index marked `status: active`) for the operator-UUID specified in the `revoked_by` field per the operator-registry. Mismatch → reject revocation + audit BLOCKER `revocation_signer_mismatch`. Closes the spoofing attack where an attacker with one operator's compromised key signs a revocation event nominally attributed to a different operator.

**`revoked_at` clock-skew guard (MUST; closes the immortal-revocation attack).** On receipt of a `memforge: revoke` commit, the adapter MUST verify that `revoked_at` falls within an acceptable clock-skew window relative to the commit's git author-date: `revoked_at ∈ [author_date − backdating_max_skew, author_date + future_max_skew]` (using the same defaults as §"Clock-skew guard against signing-time backdating": ±10 min backdating, +1 min future skew). Revocation events with `revoked_at` outside the window are rejected + audit BLOCKER `revoked_at_skew_out_of_window`. Closes the future-revocation attack where a compromised-key-holding attacker sets `revoked_at: 2099-01-01` to make the compromised key effectively un-revocable by signing-time-aware verification.

Tier 2 BLOCKER for any commit modifying revocation state whose message does NOT start with `memforge: revoke`.

### Reader-side revocation walk

At adapter startup:
1. Walk git history from the latest revocation-snapshot commit forward (or repo root if none).
2. Collect every `memforge: revoke` commit; build revocation set keyed by `key_id` → `revoked_at`.
3. Cache at `<memory-root>/.memforge/revocation-cache.yaml` with anchor (HEAD commit hash for full-history; remote-head for remote-fetch fallback).
4. On every subsequent startup, re-walk new commits since anchor + re-verify cache. Cache valid only if operator-registry signature verifies AND anchor matches.

**Bounded walk (MUST; v0.5.3+).** The revocation walk MUST be bounded by BOTH a maximum-commits cap (default 100,000) AND a maximum-bytes cap (default 100 MB). When either cap is exceeded, the adapter MUST halt the walk, MUST terminate any child subprocess spawned for the walk (`kill` + `wait`; leaking subprocesses is unacceptable per §"Cross-cutting fail-closed posture"), and MUST emit a fail-closed message pointing the operator at `memforge revocation-snapshot`. The `revocation-snapshot` reference CLI command (shipped in v0.5.1) writes a signed `memforge: revocation-snapshot <hash>` commit whose body contains the canonical revocation set; on subsequent walks, adapters start from this snapshot commit forward (NOT from repo root), bounding cold-start cost. Operators with legitimately large histories MAY raise the caps via `.memforge/config.yaml` keys `revocation.walk_max_commits` and `revocation.walk_max_bytes`.

**Framing-injection defense (MUST; v0.5.3+).** Implementations MUST NOT mix attacker-controllable commit-body content with framing/record-separator bytes in a single subprocess output stream. The v0.5.2 reference implementation that used `git log --format=%H%x00%B%x00END%x00` was vulnerable: an attacker who could land a commit could embed `\x00END\x00` in the body to inject fake revocation records or hide legitimate ones. The v0.5.3 normative approach is two-pass: fetch commit hashes via `git log --format=%H` (40-hex-char strings; not attacker-controllable, validated with regex), then fetch each commit body via a separate `git log -1 --format=%B <hash>` invocation per hash. The per-body fetch is isolated; injected separator bytes in one commit's body cannot bleed into another commit's parsing.

Closes the v0.5.2 threat-model MAJOR where an unbounded walk on a malicious or pathological repo would OOM any adapter walking revocation state at startup, AND the v0.5.3 panel BLOCKER on framing-injection.

### Sparse-checkout / shallow-clone fallback verification mode

Detected at adapter startup via `git config --get core.sparseCheckout` and `git rev-parse --is-shallow-repository`. When detected:

1. **Pin remote URL + transport (MUST).** Operator configures the trusted remote URL via `revocation.fallback_remote_url` in `.memforge/config.yaml` AND a transport pin (`revocation.fallback_transport`: `ssh` OR `https-with-pin`). Adapter rejects fetches whose effective remote does not match the pinned URL + transport.
2. **TOFU on first fetch (MUST).** First fetch caches the remote's HEAD commit hash + SHA256 of the remote-fetch ref content as the trust anchor at `<memory-root>/.memforge/revocation-cache.yaml`.
3. **Fast-forward-only after first fetch (MUST).** Subsequent fetches MUST be fast-forward-only relative to the pinned anchor. Non-fast-forward fetches OR history divergence → adapter HALTS with fail-closed message: `"Revocation history divergence detected on remote fetch; refusing to load v0.5+ memories until resolved. Investigate the remote OR rotate to full-clone mode."`
4. **Signature verification on every fetched commit (MUST).** For each `memforge: revoke` commit on the fetched ref, the adapter MUST verify the `revocation_signature` per §"Revocation commit signature." Unsigned OR wrongly-signed commits are rejected from the revocation set + audit BLOCKER `unsigned_revocation_commit_remote_fetch`.
5. **Cache TTL (default 1 hour; configurable).** Re-fetches at TTL expiry OR on operator-explicit `memforge revoke-cache-refresh`.
6. **Fail-closed on fetch failure.** If remote fetch fails (network unavailable; remote unreachable; auth fails; pinned URL mismatch): adapter HALTS with fail-closed message.

Adapters claiming v0.5.0 conformance MUST support BOTH full-history walk (default) AND remote-fetch fallback (when sparse/shallow detected). Both paths apply the same signature-verification + commit-prefix discipline; the difference is only the location of the commit history (local clone vs remote fetch).

**Operator signaling for remote-fetch fallback mode (advisory).** When the adapter is in remote-fetch fallback mode, the adapter MUST emit a startup banner: `"MemForge v0.5.0: this deployment is using sparse-checkout / shallow-clone fallback verification with TOFU + fast-forward-only + signature verification on revocation commits. Switch to full-clone mode if you prefer signed-commit verification on the local clone."` This is informational; it does NOT indicate a security gap (v0.5.0 closes the prior unsigned-revocation gap).

### Recovery-secret filesystem mode (normative)

`~/.memforge/recovery-secret.bin` MUST be filesystem-mode **0600**. Parent directory **0700**. Adapters MUST verify modes at startup + verify ownership (`stat()` uid == effective uid). Mismatch → fail-closed.

Recovery-secret format: 32 random bytes (cryptographically secure RNG). Used as input to a KDF to derive the countersignature key for key-compromise events.

**Recovery-secret content integrity (MUST; closes the content-tampering attack).** At install time (`memforge recovery-init`), the adapter MUST compute SHA256 of the recovery-secret bytes and store the hash in the signed operator-registry as `operators[<operator-uuid>].recovery_secret_sha256`. At every adapter startup, the adapter MUST re-compute SHA256 of `~/.memforge/recovery-secret.bin` and compare against the registry value. Mismatch → fail-closed: `"Recovery-secret content has changed since install; refusing to load. The file may have been tampered with or replaced. Reinstall via memforge recovery-init OR investigate."` This binds the secret's content integrity to the signed registry; defeats the same-user-shell-malware attack where an attacker replaces the secret content while preserving the FS mode + ownership.

**Recovery-secret backup acknowledgment (MUST).** The `memforge recovery-init` CLI MUST require explicit operator acknowledgment that an offline backup of the recovery-secret has been created before completing setup. The acknowledgment is recorded as `recovery.acknowledged_backup_procedure: true` in `.memforge/config.yaml` (operator's per-machine config, NOT the per-memory-root config). Adapters MUST refuse to load v0.5+ memories until this flag is set, with fail-closed message: `"Recovery-secret backup procedure not acknowledged. Run memforge recovery-backup-confirm after backing up ~/.memforge/recovery-secret.bin to offline media."` Closes the recovery-secret-deletion DoS attack: even if the secret file is deleted, the operator has an offline backup.

**Spec recommends hardware-key backing** (YubiKey FIDO2 attestation, Apple Secure Enclave, OS-specific TPM, hardware HSM). v0.5.0 ships software-only reference; hardware-backed is v0.5.x scope. Software-only deployment MUST emit persistent startup WARN naming the threat boundary + the operator action (install hardware-backed via `memforge recovery-init --hardware <backend>`). WARN suppressible only via explicit `recovery.acknowledged_software_only: true` config flag.

### Operator key compromise recovery

When operator detects compromise:
1. Generate new long-lived keypair under the recovery-secret-derived fallback flow.
2. Compose a key-compromise event commit: prefix `memforge: key-compromise <old-key-id>`; body signed by the new key + countersigned via the recovery-secret-derived attestation.
3. Republish operator-registry with the new key. Cross-signature chain breaks at this point.
4. Bulk-revoke memories signed under the compromised key (operator-discretion).

### Revocation snapshot mechanism

To bound O(N) cold-start cost, operator MAY publish a snapshot of the current revocation set every 100 revocations (or on discretion):

```
git commit --message "memforge: revocation-snapshot <hash>" --allow-empty
```

Commit body contains the full current revocation set, signed by operator's long-lived key. Adapter walks git history from the latest snapshot forward (NOT from the beginning). *Snapshot canonical hash + ancestor-of-verified-head verification: v0.5.0.1 patch target.*

### Cross-instance revocation propagation (documented limitation)

When operator A revokes a key, operator B's instance does NOT immediately see the revocation; propagates via the messaging adapter as a `revocation_event` payload at the next write OR at next remote-fetch refresh (default TTL 1 hour). During the window, operator B's instance continues trusting the compromised key.

For multi-developer-teams (the target audience), this window is acceptable for v0.5.0. For high-stakes deployments (financial / regulatory / healthcare), v0.5.0 SHOULD NOT be deployed in multi-instance configurations until v0.6+ adds gossip / consensus protocol for sub-second propagation.

## Security considerations (v0.5.0+)

Operator-facing boundary statements collected for v0.5.0 deployment:

1. **Honest-operator assumption.** v0.5.0 trust model assumes operators publish revocation events promptly after compromise detection. Without trusted timestamping, coordinated backdating attacks are bounded but not fully prevented by the clock-skew guard. Recommended mitigation: NTP-synced clocks + server-signed receipt times (v0.5.x).
2. **Software-only recovery-secret boundary.** Default v0.5.0 install is software-only filesystem-mode-protected. Does NOT protect against full-system compromise. Recommended mitigation: hardware-backed recovery-secret + offline backup on separate physical media.
3. **Same-user shell malware.** FS-mode + uid-ownership checks bound this; an attacker who escalates to operator's uid has full-system compromise (out of v0.5.0 scope).
4. **Sparse-checkout / shallow-clone revocation verification.** v0.5.0 ships with unsigned-revocation-commit acceptance in remote-fetch fallback mode. Documented as Known Limitation 2. Mitigation: use full-history mode until v0.5.0.1.
5. **Cross-instance revocation propagation lag.** Up to 1 hour TTL between instances in default config. Mitigation: high-stakes deployments single-instance until v0.6+ gossip.
6. **Hardware-key recommendation for operator long-lived keys.** Spec recommends YubiKey / Secure Enclave / TPM-attested storage for production. v0.5.0 software-only reference is for low-stakes / dogfooding.
7. **Recovery-secret backup mechanism.** Options for offline storage: USB key in a safe; printed QR code in fireproof storage; encrypted text on dedicated removable media. The recovery-secret must be available + uncompromised + physically separated from the signing-key machine for the key-compromise recovery procedure to function.
8. **Multi-operator registry-edit policy (operator-facing).** Technical guards (registry signature, commit-prefix BLOCKER) protect against unauthorized registry edits but cannot prevent social engineering of a legitimate operator. For production-environment registry changes (adding a new operator, rotating a key, fresh-start), operators SHOULD adopt a 2-of-N sign-off policy: require two independent operators to verify + countersign the registry change before commit, and require multi-operator review of registry-modifying PRs at the git-provider level. v0.5.0 does NOT enforce 2-of-N at the format level (see v0.6+ scope: 2-of-N revocation signing for team-shared deployments); operators in adversarial-environment deployments implement the policy at the git-provider review layer. Spec recommends; does not mandate.
9. **Operator name homograph defense.** The advisory `operator_name` field is human-readable + susceptible to homograph attacks (visually similar Unicode characters substituted for ASCII). v0.5.0 makes the operator-UUID authoritative for trust decisions; the human-readable name is informational. UIs SHOULD always display the UUID alongside the name + flag visually-similar names against the existing operator set. `memory-audit` SHOULD warn on new operator additions whose `operator_name` has Levenshtein distance ≤ 2 from any existing operator's name.
10. **Receiver clock manipulation.** The clock-skew guard depends on the receiver's local wall-clock. Adapters SHOULD warn loudly on startup if they cannot verify a recent successful NTP sync (within the last 24 hours). High-security deployments SHOULD use signed NTP sources (NTS) or equivalent; this is operator-side hardening outside the format.
11. **Accepted-algo denylist.** The `identity.required_algos` config key is operator-set. Adapters MUST refuse to use any algorithm on a built-in known-bad denylist (currently includes plaintext, MD5, SHA-1, RSA-key-size < 3072) regardless of operator config. Spec maintains the denylist; v0.5.x can extend.

## Cross-cutting fail-closed posture (v0.5.1+)

(Closes the v0.5.0 MINOR on cross-cutting fail-closed posture documentation.)

The v0.5.0 / v0.5.1 normative text mandates fail-closed behavior on dozens of distinct failure modes (signature invalid, registry unverifiable, receiver state corrupted, FS mode relaxed, etc.). This section gathers the fail-closed posture as a single operator-facing reference.

<!-- markdownlint-disable MD029 -->

**Hard fail-closed (adapter HALTS; refuses to load v0.5+ memories).**

1. Operator-registry signature invalid or registry file corrupted.
2. Receiver-state file corrupted, missing-with-prior-anchor, or shows impossible regression.
3. `~/.memforge/operator-identity.yaml` FS mode relaxed (not 0600 / parent not 0700) OR ownership mismatch.
4. `~/.memforge/recovery-secret.bin` FS mode relaxed OR ownership mismatch OR SHA256 content drift from the registry-anchored hash.
5. Recovery-secret backup procedure not acknowledged (`recovery.acknowledged_backup_procedure` not set).
6. Sender-sequence file FS mode relaxed OR ownership mismatch.
7. Agent-session attestation file FS mode relaxed OR ownership mismatch.
8. WebSocket adapter configured with non-TLS `ws://` URL (except localhost-only with explicit env var).
9. WebSocket adapter configured without per-operator strong authentication (mTLS or per-operator bearer tokens).
10. Sparse-checkout / shallow-clone fallback fetch fails OR non-fast-forward divergence detected.
11. Local queue exceeds `messaging.max_local_queue_disk_space_mb`.
12. Sequence number overflow on sender side.
13. Unsigned revocation commit observed (either local clone OR remote fetch).
14. Wrong-prefix commit modifying revocation state.

**Per-write fail-closed (adapter rejects the specific write; other writes continue).**

15. Signature invalid OR algo not on accepted-algo list.
16. `signing_time` outside clock-skew window relative to `first_seen_at`.
17. Sequence ≤ highest-seen-sequence for the (sender-uid, operator-uuid) pair.
18. Sender-uid format invalid.
19. Agent-session attestation expired OR out-of-scope path OR out-of-scope operation.
20. Agent-session nonce already in seen-nonce set (replay).
21. Key in 24-hour rotation cool-down window.
22. Key revoked at `signing_time` per the revocation set.
23. v0.4 memory pulled into a v0.5 resolve commit without upgrade-or-exclude.

**Per-config fail-closed (adapter refuses to use the configuration; surfaces to operator).**

24. Algorithm on the built-in known-bad denylist (plaintext, MD5, SHA-1, RSA < 3072) regardless of operator config.
25. `identity.agent_session_max_lifetime_hours` configured below 15 minutes OR above 7 days.

**Soft fail-closed (adapter warns; operator MAY acknowledge to suppress).**

26. NTP sync not verifiable within last 24 hours (advisory; high-security deployments SHOULD reject).
27. Software-only recovery-secret (suppressible via `recovery.acknowledged_software_only: true`).
28. Multi-server WebSocket deployment (suppressible via `MEMFORGE_ACKNOWLEDGE_SINGLE_SERVER=1`).
29. Sparse-checkout / shallow-clone mode (advisory banner; not a security gap given the v0.5.0 TOFU + fast-forward + signature contract).

The discipline: every failure mode either lands an operator action (configure / rotate / investigate) or fails closed. There is no degraded-mode read of v0.5+ memories. The reference adapter SHOULD log every fail-closed event with the numbered category above plus the specific message text for operator triage.

<!-- markdownlint-enable MD029 -->

## Privacy considerations (v0.5.1+)

(Closes the v0.5.0 MINOR on privacy considerations subsection.)

The v0.5.0 / v0.5.1 cryptographic-attribution surface introduces operator-bound metadata into every memory file: `identity` (operator-UUID + optional agent-session-id), `signature.signing_time`, and via the messaging adapter, `sender_uid`. This section catalogs the privacy boundary statements operators should understand before deploying in adversarial or regulated environments.

**Operator-UUID linkability.** The operator-UUID is stable across all stores an operator participates in. Any third party with read access to multiple stores can link them by operator-UUID. v0.5.1 does NOT support privacy-preserving cross-store unlinkability (Schnorr / BBS-like signature schemes that produce different signatures per store). High-privacy deployments SHOULD use one operator-UUID per logical identity context (work-identity, side-project-identity, etc.) at install time; v0.5.x considers per-store operator-UUID derivation.

**Signing-time linkability.** Every v0.5+ memory carries an ISO-8601 UTC `signing_time` precise to the second. An attacker with read access can build a timeline of when each operator wrote each memory. v0.5.1 does NOT support time-bucketing or signing-time obfuscation. Operators handling memories that should not reveal write-time (e.g., legal strategy, transaction planning) SHOULD either (a) batch-write memories in a single signing session to obscure individual write times, or (b) store such memories outside MemForge entirely.

**Agent-session-id leakage.** The `agent-session-id` reveals which adapter (CC, Cursor, etc.) the operator was using when the memory was written and the session start date. This is intentional operator-facing observability. Privacy-sensitive operators SHOULD prefer operator-direct writes (no agent prefix in identity) when the adapter choice itself is sensitive.

**Sender-UID linkability.** The `sender_uid` (`<operator-uuid>:<32-byte-hex>`) links every WebSocket-routed write back to the operator-UUID. Same linkability as identity above. Receivers SHOULD treat sender-UIDs as PII-equivalent in any logging or metrics surface.

**Operator-name homograph defense interacts with privacy.** The advisory `operator_name` field is informational + susceptible to homograph attacks (§"Security considerations" item 9). The operator-UUID is authoritative. Privacy: the operator-name appears in operator-registry and is therefore visible to all readers of the registry. Operators who want pseudonymous participation SHOULD set `operator_name` to a pseudonym at `init-operator` time.

**Cross-store reference disclosure.** A memory in store A that uses `MEMFORGE_STORE = <operator-b-uuid>:<store-name>` reveals the existence of operator-B's named store to anyone reading store A. v0.5.1 does NOT support encrypted cross-store references. Operators SHOULD use cross-store references only when the existence of the cross-store relationship is itself non-sensitive.

**Receiver-side state files.** Receiver-state files (`<memory-root>/.memforge/receiver-state/<sender-uid>.yaml`) accumulate per-sender first-seen and last-seen timestamps. An attacker with read access to receiver-state learns when each sender first contacted this receiver and most-recent activity. Receiver-state files SHOULD inherit the same containment posture as the memory-root.

**Out-of-scope for v0.5.1.** Zero-knowledge proofs of authority, blind signature schemes, group signatures, anonymous credentials, mix-network message routing, time-bucketed timestamps. v0.5.x / v0.6+ may add some of these; v0.5.1 ships an honest-operator pseudonymity model where operator-UUIDs are pseudonyms that may be linked across stores by anyone with read access to multiple stores.

## Known limitations (v0.5.1)

**v0.5.1 ships with no BLOCKER-class known limitations.** v0.5.1 closes the following v0.5.0 residuals in normative text:

- **Agent session attestation content scope** (was v0.5.0 MAJOR) , closed at §"Agent session attestation content scope (v0.5.1+)" with mandatory nonce + expires_at + capability_scope content.
- **Agent-session-id format guidance** (was v0.5.0 MINOR) , closed at §"Frontmatter additions (v0.5.0+)" with normative regex.
- **Cross-cutting fail-closed posture documentation** (was v0.5.0 MINOR) , closed at §"Cross-cutting fail-closed posture (v0.5.1+)" with 29-item operator reference.
- **Privacy considerations subsection** (was v0.5.0 MINOR) , closed at §"Privacy considerations (v0.5.1+)" with 7 boundary statements + out-of-scope list.

**Reference CLI + operator-facing documentation gaps closed in v0.5.1:**

- `memforge init-operator`, `init-store`, `operator-registry add|verify|remove|fresh-start`, `rotate-key`, `revoke`, `revocation-snapshot`, `memories-by-key`, `revoke-memories`, `upgrade-v04-memories`, `revoke-cache-refresh`, `messaging-doctor`, `recovery-init`, `recovery-backup-confirm`, `attest-agent` CLI commands ship in v0.5.1.
- Hardware-key install paths (`recovery-init --hardware`) remain v0.5.x scope.

**Residual issues tracked for v0.5.x patches (non-BLOCKER class):**

- 4 MAJORs (checkpoint signer ambiguity for agent ephemeral keys, revocation snapshot ancestor requirement + canonical hash, sender/receiver posture nuances, cache TTL semantics for high-stakes deployments).
- 1 MINOR (TTL semantics for revocation cache).

Full list at `known-limitations.md` (sibling file to this spec; living document that tracks residuals across versions; each Zenodo deposit includes a versioned snapshot).

The v0.5.1 spec is shippable as production-ready with reference CLI; the residual MAJORs are refinements, not security gaps.

## Integrity invariants

A MemForge folder is well-formed if, and only if:

1. Every `.md` file other than `MEMORY.md` (and within `archive/`) has a valid frontmatter block.
2. Every such file has `name`, `description`, and `type` set, and `type` is one of `user | feedback | project | reference`.
3. If present, `sensitivity` is one of `public | internal | restricted | privileged`. Absence is allowed and equivalent to `internal`.
4. If present, `tier` is one of `index | detail`. Absence at top-level is treated as `index`. Absence inside a rollup subfolder is treated as `detail`.
5. If present, `status` is one of `active | proposed | gated | superseded | dropped | archived`. Absence is treated as `active`.
6. If present, `uid` is unique across the folder (and across the cross-folder reference graph if `references_global` is used).
7. Every `tier: index` file MUST appear exactly once in `MEMORY.md`.
8. Every pointer in `MEMORY.md` resolves to an existing `tier: index` file.
9. `MEMORY.md` has no frontmatter block.
10. Files inside `archive/` MAY have any status (including pre-archive). Archive content is excluded from the integrity check.
11. If a file has `status: superseded`, a `superseded_by` field MUST be set. In v0.4.0+, `superseded_by` MUST have length exactly 1 (a single winner UID), and the asymmetric-supersession invariant in §"Multi-agent concurrency" applies (the winner's `replaces:` MUST list this UID, both share `decision_topic`, and the winner is the sole `status: active` member of the group).
12. (v0.4.0+) For any `decision_topic` group whose anchor or any member carries `ever_multi_member: true`, exactly one member has `status: active`. Zero or two-plus active is BLOCKER.
13. (v0.4.0+) The `ever_multi_member` flag, once `true`, is monotonic: any commit that flips it to `false` is BLOCKER.
14. (v0.4.0+) The `status` field is one of the enumerated six values; any other value is BLOCKER.
15. (v0.4.0+) The full layered audit rule set in §"Multi-agent concurrency" applies (Tier 1 HEAD-pure + Tier 2 commit-log).
16. (v0.5.0+) Every v0.5+ write MUST carry valid `identity` + `signature` frontmatter; the signature MUST verify against the canonical envelope per §"Cryptographic attribution". Unsigned v0.5+ writes are BLOCKER. v0.4 memories loaded under v0.5 readers are tagged read-only-untrusted (one-time MAJOR per unsigned v0.4 memory until upgrade or explicit exclusion).
17. (v0.5.0+) `signature.signing_time` MUST fall within the receiver's `first_seen_at` clock-skew window (default ±10 min backdating + 1 min future skew). Out-of-window signatures are rejected + audit MAJOR.
18. (v0.5.0+) Resolve commits touching v0.4 (unsigned) memories MUST either upgrade them (in-commit signature) OR explicitly exclude via `resolve.exclusion_reason:`. Tier 2 BLOCKER otherwise.
19. (v0.5.0+) Operator-registry (`<memory-root>/.memforge/operator-registry.yaml`) MUST have a valid signature by at least one listed operator. Verification failure → adapter HALTS (fail-closed). Edits MUST occur in commits with prefix `memforge: operator-registry` AND single-file scope (Tier 2 BLOCKER otherwise).
20. (v0.5.0+) Sender-sequence files at `<memory-root>/.memforge/sender-sequence/<sender-uid>.yaml` MUST be filesystem-mode 0600 (parent 0700). `current_sequence` MUST be uint64 monotonic per sender. Signed checkpoints MUST publish every 100 sequences OR 24 hours. Receiver MUST reject `sequence < highest-checkpoint-sequence` per sender.
21. (v0.5.0+; restated platform-agnostically in v0.5.2; TOCTOU-tightened in v0.5.3) The following files MUST be filesystem-restricted to the current owner (read/write for the owner only, no access for other principals on the host); their parent directories MUST be restricted to current-owner rwx-only:
    - `~/.memforge/operator-identity.yaml`
    - `~/.memforge/recovery-secret.bin`
    - `~/.memforge/config.yaml`
    - `<memory-root>/.memforge/sender-sequence/<sender-uid>.yaml`
    - `<memory-root>/.memforge/agent-sessions/<agent-session-id>.yaml`
    - `<memory-root>/.memforge/seen-nonces/<operator-uuid>.yaml`
    - `<memory-root>/.memforge/receiver-state/<sender-uid>.yaml`

    **Enforcement (platform-agnostic).** POSIX implementations enforce via mode 0600 / 0700 + `stat()` uid match against effective uid. Windows implementations enforce via NTFS ACLs verified by **SID-based identifier check** (NOT localized principal names; v0.5.2-era implementations that string-matched icacls output failed open on non-English Windows locales). The Windows ACL MUST grant access only to the current user's SID; the ACL MUST NOT contain any well-known-forbidden SID (`S-1-1-0` Everyone, `S-1-5-7` ANONYMOUS LOGON, `S-1-5-11` Authenticated Users, `S-1-5-32-545` BUILTIN\Users, `S-1-5-32-546` BUILTIN\Guests, `S-1-5-4` INTERACTIVE, `S-1-5-2` NETWORK, `S-1-5-3` BATCH, `S-1-5-6` SERVICE, `S-1-5-1` DIALUP, `S-1-5-13` TERMINAL SERVER USER, `S-1-5-14` REMOTE INTERACTIVE LOGON). Adapters MUST verify at startup; fail-closed on any deviation.

    **TOCTOU-safe read (MUST; v0.5.3+).** For every file in the list above, adapters MUST minimize the window between mode/ownership verification and the actual read. POSIX implementations MUST open with `O_NOFOLLOW` (refuses symlinks at the OS level) AND verify mode + ownership on the file descriptor (`fstat` after open, NOT `stat` on the path before open). Windows implementations MUST perform path-level verification before open AND verify ownership of the opened handle where the platform supports it; native Windows lacks an `O_NOFOLLOW`-equivalent atomic primitive, so a residual same-uid TOCTOU window exists between the path-level verify and the open call. The Windows residual is bounded by the existing §"Security considerations" item 3 ("Same-user shell malware" = full-system compromise, out of v0.5.x scope) and is documented in `known-limitations.md` as a Windows-specific MINOR pending native API uptake (CreateFile + GetSecurityInfo on file handle is the v0.6 target). Closes the v0.5.2 threat-model MAJOR on POSIX; bounds it on Windows.
22. (v0.5.0+) Revocation events MUST land as commits with prefix `memforge: revoke <key_id>`. Wrong-prefix commits modifying revocation state are Tier 2 BLOCKER. Snapshot commits use prefix `memforge: revocation-snapshot <hash>`.
23. (v0.5.1+) Agent-signed writes MUST resolve to a valid session-attestation file at `<memory-root>/.memforge/agent-sessions/<agent-session-id>.yaml` whose `operator_signature` verifies against the operator-registry, whose `nonce` is unique in the per-receiver seen-nonce set, whose `signing_time` is within `[issued_at, expires_at]`, AND whose `signature.value` on the WRITE itself verifies against the attestation's `agent_pubkey` (using `agent_pubkey_algo`) over the canonical envelope `{memory_body, identity, sender_uid, sequence_number, signing_time}` per §"Signed envelope scope (normative)". The verification key for the write MUST come from the attestation, NOT the operator-registry (agent keys are ephemeral and never registry-listed). Failure: BLOCKER (nonce-replay / bad attestation signature / bad write signature) or MAJOR (expired / out-of-window).
24. (v0.5.1+) Agent-signed writes MUST land at paths within `attestation.capability_scope.memory_roots` and the operation MUST be in `attestation.capability_scope.allowed_operations`. Out-of-scope path → BLOCKER `agent_session_out_of_scope_write`; out-of-scope operation → BLOCKER `agent_session_out_of_scope_operation`.
25. (v0.5.1+) The `<agent-session-id>` portion of `identity` MUST match the regex `^[a-z0-9]+-\d{4}-\d{2}-\d{2}-[a-z0-9]{8,16}$`. Non-matching agent-session-ids are rejected on read with audit MAJOR `agent_session_id_format_invalid`.

The `tools/memory-audit` script verifies these invariants plus health heuristics. v0.4.0+ adds the Tier 1 / Tier 2 split documented in §"Multi-agent concurrency". v0.5.0+ adds the multi-identity + cryptographic-attribution + key-lifecycle invariants documented in §"Multi-identity primitives" + §"Cryptographic attribution" + §"Operator identity + cross-store references" + §"Messaging adapter contract" + §"Key lifecycle + revocation". v0.5.1+ adds agent-session attestation content scope + format guidance + cross-cutting fail-closed posture + privacy considerations.

## Cross-folder references

The `references_global` and `referenced_by_global` fields support cross-folder relationships (per-cwd memory ↔ global memory). When a topic spans both folders:

- The per-cwd memory file declares `references_global: [<uid>]` pointing to the global memory it depends on.
- The global memory MAY declare `referenced_by_global: [<uid>]` for back-references (audit can also derive these).
- Audit tools verify cross-folder UID resolution and flag broken references.

## Versioning

**Current spec version**: 0.5.3.

The spec version lives in `spec/VERSION`. Breaking changes bump per semantic versioning applied to spec semantics:

- **Major**: invariants change in a way that existing well-formed folders can become malformed.
- **Minor**: new optional fields, new types, new conventions that existing folders remain compatible with.
- **Patch**: documentation or wording changes with no behavioral effect.

v0.3.0 was a minor bump (new optional fields + rollup-subfolder formalization). v0.4.0 was a major bump: required-field set expanded, byte-match CI tightened, multi-agent concurrency surface added. v0.5.0 was a minor bump: v0.4 folders remain well-formed (the v0.5 frontmatter additions `identity` + `signature` are REQUIRED on v0.5+ writes but optional in v0.4 frontmatter). v0.4 memories load under v0.5 readers as `(v0.4: unsigned)` read-only-untrusted; mixed-deployment posture in §"Multi-identity primitives". v0.5.1 is a patch bump: closes the v0.5.0 agent-session-attestation content-scope MAJOR + 3 MINORs (agent-session-id format guidance, cross-cutting fail-closed posture, privacy considerations) and ships the reference CLI binaries; v0.5.0 folders remain well-formed under v0.5.1 readers.

Adapters and tools SHOULD declare which spec version they target.

## Expected content sensitivity

MemForge is **not** a secrets store. Never put credentials, tokens, or private keys into memory files regardless of their `sensitivity` or `access` label. Encryption at rest is outside the spec; rely on the host's filesystem encryption (FileVault, LUKS, BitLocker).

The `sensitivity` and `access` frontmatter fields exist to let adapters make containment decisions about memories whose content is legitimately in the folder but whose exposure scope is narrower than the folder itself. Privileged legal material, pre-launch commercial strategy, attorney correspondence: these belong in the folder, but a cloud-IDE adapter should never export them.

Adapters MAY add encryption layers if they target a multi-developer or shared-workspace scenario, but the core format assumes plaintext-at-rest is acceptable for the content the format is designed to hold.

## Not in scope for v0.5.1

- Specific RBAC enforcement (adapter responsibility, not spec).
- Encryption protocols (adapter responsibility).
- Specific embedding model selection for `dynamic_supplement` queries (Phase 1+ tool concern).
- Generator implementation (Phase 1+ tool, see `tools/memory-index-gen`).
- Centralized `decision_topic` taxonomy file. v0.5.0 uses inline `topic_aliases:` from v0.4.
- Per-decision ledger (one file per topic with monotonic `resolution_version` + winner UID + losers). Deferred; the v0.4 layered Tier 1 + Tier 2 model plus config protection plus secure-mode deployment guidance covers the realistic threat model. Promote if dogfooding surfaces history-rewrite attacks.
- DAG cycle rejection on `replaces:` chain (audit warning first; future scope).
- UUIDv7 normative UID format for memory UIDs. Current `mem-YYYY-MM-DD-slug` continues (UUIDv7 is used for operator-UUIDs and `revocation_uid` in v0.5.0).
- Vector-clock or sequence-number tie-breaker for memory updates (sender-sequence in v0.5.0 covers cross-instance writes; per-memory `updated` timestamp remains the local tie-breaker).
- Auto-derive of `decision_topic` for legacy v0.3.x memories.
- Cryptographic immutability for snooze `created_by` or memory `owner` (best-effort provenance only).
- Cross-machine merge resolution beyond exactly-one-active. Same as v0.3.x / v0.4: git merge with operator hand-resolution.
- CRDT semantics. Future ADR if surface-not-resolve fails under load.
- **Hardware-key reference implementation for operator long-lived keys + recovery-secret** (v0.5.x scope; v0.5.0 ships software-only with persistent startup WARN).
- **2-of-N multi-key signing for revocations** (v0.6+ scope; v0.5.0 single-operator signing only).
- **Post-quantum signature algorithms** (Dilithium3, Falcon). Frontmatter `signature.algo` reserves the slot.
- **Centralized identity authority** (CA / OIDC). Format is filesystem + git; trust anchor is operator-registry signed by operator's own key.
- **Sub-second cross-instance revocation propagation** (v0.6+ gossip / consensus protocol).
- **Bulk re-key tool for emergency mass-rotation** across an operator's full memory tree. v0.5.x scope.
- **Agent-key persistence across sessions.** Agent keys are ephemeral by design; new session = new key.
- **Auditable trust scoring** for cross-operator messages (reader contract is binary: verified or not).
- **Multi-server WebSocket deployment.** v0.5.0 single-server only with hard-stop. v0.5.x / v0.6+ adds Redis Pub/Sub or equivalent fan-out.
- **gRPC streaming / NATS JetStream / Kafka / Redis Streams / Cloudflare Realtime adapters.** v0.5.x scope; v0.5.0 stays on plain WebSocket.
- **WebSocket multiplexing.** Per OpenAI WebSocket Mode constraints, one connection per sender-uid in v0.5.0.
- **Cross-store WebSocket federation** (operator A's store visible to operator B's WebSocket subscribers). v0.5.x scope.
- **Server-signed receipt times.** v0.5.x consideration for the WebSocket path; v0.5.0 ships with receiver-side `first_seen_at` clock-skew guard.
- ~~**Reference CLI binaries.**~~ Shipped in v0.5.1.
- **Privacy-preserving cross-store unlinkability** (Schnorr / BBS-like signatures). v0.5.x / v0.6+ scope; v0.5.1 ships honest-operator pseudonymity (per §"Privacy considerations").
- **Per-store operator-UUID derivation** for privacy-sensitive multi-store deployments. v0.5.x scope; v0.5.1 ships one stable operator-UUID per operator.

## Versioning history

- v0.1.0 , initial format; flat folder, name+description+type required.
- v0.2.0 , sensitivity classification (4 levels) + consumer obligations.
- v0.3.0 , schema expansion (uid, tier, tags, owner, status, last_reviewed, etc.); rollup-subfolder formalization; access labels; cross-folder references; tag taxonomy.
- v0.4.0 , major bump: `uid`, `tier`, `tags`, `owner`, `status`, `created` required (was optional in v0.3.x). v0.3.x files load in degraded mode. Reader contract tightened for byte-match CI on generated `MEMORY.md`. New §"Multi-agent concurrency" section adds five frontmatter keys (`decision_topic`, `replaces`, `superseded_by`, `topic_aliases`, `ever_multi_member`), a snooze record (`.memforge/snoozes/<topic>.yaml`), a config file (`.memforge/config.yaml`), the resolve operation contract, the canonical reader-side competing-claim block, and a layered Tier 1 + Tier 2 audit rule set. Status enumeration is now strictly enforced (BLOCKER on any value outside the six listed). Sensitivity enforcement (`§"Sensitivity enforcement"`) adds three default-on, operator-disable-able checks (export-tier gate, DLP label/content cross-check, conformance fixtures) with a hard floor at `privileged`. Closes the v0.3.x §"Not in scope" deferral on multi-user concurrency.
- v0.5.0 , minor bump: extends single-operator multi-agent format to multi-identity team-scale memory with cryptographic attribution and a real-time messaging substrate. Eight new sections: §"v0.5.0 surface map", §"Multi-identity primitives", §"Cryptographic attribution", §"Operator identity + cross-store references", §"Messaging adapter contract" (with subsections including the normative §"Receiver state", §"Connection security" mandating wss:// + per-operator strong auth, and circuit-breaker), §"Key lifecycle + revocation" (with mandatory cool-down on key rotation closing the rotation-as-privilege-escalation attack), §"Security considerations" (eleven enumerated boundary statements), §"Known limitations". New REQUIRED v0.5+ frontmatter: `identity` + `signature` (v0.4 frontmatter remains valid; v0.5 readers tag unsigned v0.4 memories as read-only-untrusted). WebSocket locked as v0.5.0 messaging substrate (OpenAI Responses API Feb 23 2026 launch alignment); multi-server hard-stop for v0.5.0. Sender-uid format `<operator-uuid>:<32-byte-hex>` mandatory; sender-sequence + signed checkpoints every 100 sequences or 24 hours. Signing-time-aware revocation verification + `first_seen_at` clock-skew guard (default ±10 min) close the coordinated-backdate attack class. `revoked_at` clock-skew guard closes the immortal-revocation attack. Recovery-secret filesystem mode (0600/0700) + uid-ownership check + SHA256 content-integrity check anchored in the signed operator-registry + mandatory backup acknowledgment; persistent startup WARN until hardware-backed install. Sparse-checkout / shallow-clone fallback uses TOFU + fast-forward-only + signature verification on revocation commits. Reference CLI binaries (`memforge init-operator`, `operator-registry`, `revoke-memories`, etc.) ship in v0.5.1. v0.4 folders remain well-formed under v0.5 readers. v0.5.0 ships with no BLOCKER-class known limitations; residual MAJORs + MINORs documented at `v0.5.0-known-limitations.md`.
- v0.5.1 , patch bump: closes the v0.5.0 agent-session-attestation content-scope MAJOR with normative `nonce` + `expires_at` + `capability_scope` content (§"Agent session attestation content scope (v0.5.1+)") and closes 3 v0.5.0 MINORs: agent-session-id format regex (in §"Frontmatter additions (v0.5.0+)"); cross-cutting fail-closed posture documentation as a single 29-item operator reference (§"Cross-cutting fail-closed posture (v0.5.1+)"); privacy considerations subsection with 7 boundary statements + out-of-scope list (§"Privacy considerations (v0.5.1+)"). Reference CLI ships: `memforge init-operator`, `init-store`, `operator-registry add|verify|remove|fresh-start`, `rotate-key`, `revoke`, `revocation-snapshot`, `memories-by-key`, `revoke-memories`, `upgrade-v04-memories`, `revoke-cache-refresh`, `messaging-doctor`, `recovery-init`, `recovery-backup-confirm`, `attest-agent`. New integrity invariants 23-25 (agent-session attestation verification, capability-scope enforcement, agent-session-id format). v0.5.0 folders remain well-formed under v0.5.1 readers; v0.5.0 attestation files lacking v0.5.1 required content fields are accepted with one-time MAJOR `v05_attestation_incomplete_content` per file until re-issued via the v0.5.1 reference CLI.
- v0.5.3 , patch bump: closes the 3 remaining MAJORs from the v0.5.2 retrospective threat-modeler pass. Registry-layer cool-down enforcement: `memforge.registry.verify_signing_key_acceptable` now refuses a signing key in cool-down regardless of CLI consumer; CLI-only enforcement was a bypass surface. Bounded git-log walk: `walk_revocation_set` now streams output line-by-line with caps on commits-walked + bytes-read (default 100k commits / 100 MB; operator-configurable). TOCTOU-safe read on every secure-file path: `_security.secure_read_text` + `secure_read_bytes` use `O_NOFOLLOW` + post-open fd `fstat` verification on POSIX; path-level verify on Windows. v0.5.2 folders remain well-formed under v0.5.3 readers; no spec-frontmatter or normative-contract regressions.
- v0.5.2 , patch bump: closes 2 BLOCKERs + 1 MAJOR surfaced by the post-v0.5.1 retrospective code + spec panel (critic via gemini-pro + threat-modeler via gemini-pro) AND adds native Windows support via a platform-agnostic ACL-based restriction abstraction. Canonical-form Unicode NFC normalization MUST on signed envelopes (§"Signed envelope scope (normative)"); closes the repudiation vector where visually-identical inputs in different normalization forms produced different signatures. Seen-nonce set bounding promoted from MAY to SHOULD with explicit GC contract; closes the unbounded-set DoS. Reference implementation atomicity: `write_secure_yaml` + `write_secure_bytes` now use O_CREAT|O_EXCL + atomic rename to close the TOCTOU window between file create and chmod. **Cross-platform secure-file abstraction:** §"Operator-identity file (per-machine)" + integrity invariant 21 are restated platform-agnostically; POSIX mode 0600/0700 and Windows NTFS ACLs (via `icacls`) are both normative implementations of the "file restricted to current owner" contract. v0.5.1 folders remain well-formed under v0.5.2 readers; the Unicode normalization change is backward-compatible for any input that was already in NFC form (the common case for ASCII + standard composed Unicode).

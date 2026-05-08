# MemForge spec

Version 0.3.0 (draft).

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

## Access labels

The `access` field (v0.3.0+) is independent of `sensitivity`. Where sensitivity describes containment-by-content-type, access describes who can read the memory in a multi-tenant context. Adapters MAY enforce access labels via RBAC.

Initial vocabulary (extensible per deployment):

- `public` — anyone can read
- `internal` (default) — workspace members
- `counsel` — privileged legal-advisory role
- `team:<name>` — team-scoped (e.g., `team:security`, `team:platform`)

Access enforcement is an adapter responsibility. The spec defines only the field shape and the controlled-vocabulary expectation.

## Tag taxonomy

The `tags` field carries a list of taxonomy entries. Tags follow `<namespace>:<value>` format:

- `topic:<name>` — primary topic anchor (e.g., `topic:auth`, `topic:infra`). Drives rollup clustering.
- `area:<name>` — broader area for cross-cutting concerns (e.g., `area:ops`, `area:governance`).
- `priority:<value>` — operational priority (e.g., `priority:critical`).

The controlled vocabulary lives in `spec/taxonomy.yaml`. The vocabulary is versioned with the spec. Adapters MAY warn on tags outside the vocabulary; tools SHOULD provide a synonym map (e.g., `oauth` → `topic:auth`).

## Index format (`MEMORY.md`)

`MEMORY.md` is an index, not a memory. It MUST NOT carry frontmatter. It SHOULD be kept under 150 total lines.

In v0.3.0+, MEMORY.md is treated as a generated build artifact. Manual edits remain valid (current state) but the long-term direction is generator-driven via the `memory-index-gen` tool (Phase 1).

Each `tier: index` memory file MUST appear exactly once in the index (top-level files + rollup README.md files), as a pointer line in this shape:

```
- [<title>](<path>) — <one-line hook describing relevance>
```

For top-level files: `<path>` is the filename. For rollup README.md files: `<path>` is `<topic>/README.md`.

The hook line SHOULD stay under 150 characters (and 150 bytes — em-dashes are 3 UTF-8 bytes; prefer colons). Tools SHOULD flag overruns as warnings, not errors.

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
|---|---|
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

The `tools/memory-audit` script verifies these invariants plus health heuristics. v0.4.0+ adds the Tier 1 / Tier 2 split documented in §"Multi-agent concurrency".

## Cross-folder references

The `references_global` and `referenced_by_global` fields support cross-folder relationships (per-cwd memory ↔ global memory). When a topic spans both folders:

- The per-cwd memory file declares `references_global: [<uid>]` pointing to the global memory it depends on.
- The global memory MAY declare `referenced_by_global: [<uid>]` for back-references (audit can also derive these).
- Audit tools verify cross-folder UID resolution and flag broken references.

## Versioning

**Current spec version**: 0.4.0-draft.

The spec version lives in `spec/VERSION`. Breaking changes bump per semantic versioning applied to spec semantics:

- **Major**: invariants change in a way that existing well-formed folders can become malformed.
- **Minor**: new optional fields, new types, new conventions that existing folders remain compatible with.
- **Patch**: documentation or wording changes with no behavioral effect.

v0.3.0 was a minor bump (new optional fields + rollup-subfolder formalization). v0.4.0 is a major bump: it makes `uid`, `tier`, `tags`, `owner`, `status`, `created` required (formerly optional), and tightens the reader contract for byte-match CI on generated `MEMORY.md`. v0.3.x files load in degraded mode (see §"File format").

Adapters and tools SHOULD declare which spec version they target.

## Expected content sensitivity

MemForge is **not** a secrets store. Never put credentials, tokens, or private keys into memory files regardless of their `sensitivity` or `access` label. Encryption at rest is outside the spec; rely on the host's filesystem encryption (FileVault, LUKS, BitLocker).

The `sensitivity` and `access` frontmatter fields exist to let adapters make containment decisions about memories whose content is legitimately in the folder but whose exposure scope is narrower than the folder itself. Privileged legal material, pre-launch commercial strategy, attorney correspondence: these belong in the folder, but a cloud-IDE adapter should never export them.

Adapters MAY add encryption layers if they target a multi-developer or shared-workspace scenario, but the core format assumes plaintext-at-rest is acceptable for the content the format is designed to hold.

## Not in scope for v0.4.0

- Specific RBAC enforcement (adapter responsibility, not spec).
- Encryption protocols (adapter responsibility).
- Specific embedding model selection for `dynamic_supplement` queries (Phase 1+ tool concern).
- Generator implementation (Phase 1+ tool, see `tools/memory-index-gen`).
- Centralized `decision_topic` taxonomy file (v0.5.0; v0.4.0 uses inline `topic_aliases:`).
- Per-decision ledger (one file per topic with monotonic `resolution_version` + winner UID + losers). Deferred to v0.5.0; the layered Tier 1 + Tier 2 model plus config protection plus secure-mode deployment guidance covers the realistic threat model. Promote if dogfooding surfaces history-rewrite attacks.
- DAG cycle rejection on `replaces:` chain (v0.5.0; audit warning first).
- UUIDv7 normative UID format. Current `mem-YYYY-MM-DD-slug` continues.
- Vector-clock or sequence-number tie-breaker (v0.5.0; `updated` timestamp is the v0.4.0 tie-breaker).
- Auto-derive of `decision_topic` for legacy v0.3.x memories.
- Cryptographic immutability for snooze `created_by` or memory `owner` (best-effort provenance only).
- Cross-machine merge resolution beyond exactly-one-active. Same as v0.3.x: git merge with operator hand-resolution.
- CRDT semantics. Future ADR if surface-not-resolve fails under load.

## Versioning history

- v0.1.0 — initial format; flat folder, name+description+type required.
- v0.2.0 — sensitivity classification (4 levels) + consumer obligations.
- v0.3.0 — schema expansion (uid, tier, tags, owner, status, last_reviewed, etc.); rollup-subfolder formalization; access labels; cross-folder references; tag taxonomy.
- v0.4.0 (in draft) — major bump: `uid`, `tier`, `tags`, `owner`, `status`, `created` required (was optional in v0.3.x). v0.3.x files load in degraded mode. Reader contract tightened for byte-match CI on generated `MEMORY.md`. New §"Multi-agent concurrency" section adds five frontmatter keys (`decision_topic`, `replaces`, `superseded_by`, `topic_aliases`, `ever_multi_member`), a snooze record (`.memforge/snoozes/<topic>.yaml`), a config file (`.memforge/config.yaml`), the resolve operation contract, the canonical reader-side competing-claim block, and a layered Tier 1 + Tier 2 audit rule set. Status enumeration is now strictly enforced (BLOCKER on any value outside the six listed). Closes the v0.3.x §"Not in scope" deferral on multi-user concurrency.

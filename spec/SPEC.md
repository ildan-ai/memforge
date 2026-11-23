# MemForge spec

Version 1.0.0.

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
aliases: [<uid>, ...]
pinned: <true | false>
dynamic_supplement: [<query string>, ...]
triggers: [<keyword or phrase>, ...]            # v0.6.0+: explicit recall keywords (see §"Recall operation")
always: <true | false>                          # v0.6.0+: surface in recall regardless of query
do_not_inject: <true | false>                   # v0.6.0+: exclude from query-triggered recall injection
references_global: [<uid>, ...]
referenced_by_global: [<uid>, ...]
access: <label, e.g., public | internal | counsel | team:security>
decision_topic: <slug>                          # v0.4.0+: identifies a multi-claim decision (see §"Multi-agent concurrency")
replaces: [<uid>, ...]                          # v0.4.0+: advisory, proposes that listed UIDs be superseded
superseded_by: [<uid>]                          # v0.4.0+: set ONLY by the resolve operation; length exactly 1
topic_aliases: [<slug>, ...]                    # v0.4.0+: mutual aliases on the canonical anchor memory
ever_multi_member: <true | false>               # v0.4.0+: monotonic anchor flag, set by resolve, never cleared
agent_session: <free string>                    # v1.0.0+: advisory (see §"Attribution fields and legacy fields")
on_behalf_of: <person string>                   # v1.0.0+: advisory (see §"Attribution fields and legacy fields")
identity: <string>                              # LEGACY (v0.5.x): preserved, never verified or written
signature: <mapping>                            # LEGACY (v0.5.x): preserved, never verified or written
---

<body in markdown>
```

Required in v0.4.0: `name`, `description`, `type`, `uid`, `tier`, `tags`, `owner`, `status`, `created`. The `sensitivity` field remains independent (separate from `access`); when absent, consumers MUST treat the memory as `internal`.

**v0.3.x compatibility.** Files written under v0.3.x that lack the newly-required fields (`uid`, `tier`, `tags`, `owner`, `status`, `created`) load in **degraded mode**: adapters MUST accept them, MAY emit a warning, and SHOULD prompt the operator to backfill. A degraded-mode memory still appears in `MEMORY.md` but cannot participate in rollup contracts (no `owner` / `last_reviewed`), status-driven archival (no `status`), or any v0.4 reader-side contract that depends on a v0.4-required field. Backfill is one-shot per file: once the required fields are present, the file is fully v0.4-conformant.

`tier: index` is the default when absent (any top-level file is treated as index unless it explicitly declares `tier: detail`).

Filenames SHOULD be lowercase kebab-case and SHOULD start with the `type:` value (for example: `feedback_no_em_dashes.md`, `project_auth_migration.md`). Tools MAY warn on deviation; adapters MUST NOT reject non-conforming filenames.

### Attribution fields and legacy fields

**Advisory attribution (v1.0.0+).** Two optional fields record who or what was behind a write:

- `agent_session` (free string): an identifier for the agent session that wrote the memory.
- `on_behalf_of` (a person string): the person the agent acted for.

`owner` stays as it was. These fields carry no integrity weight beyond the signed commit that introduced them. No tool treats them as proof, and no tool refuses a memory for lacking them. The only cryptographic evidence is the signature on the commit (§"Commit trust (v1.0.0+)"). It shows which listed key signed the commit, not which person or agent wrote the memory. It does not survive a squash merge or a copy.

**Legacy fields.** Earlier revisions (v0.5.x) defined `identity` and `signature` frontmatter. Both are legacy. A file that carries them is valid: tools read them like any other field, MUST preserve them byte for byte when they rewrite the file (each field's lines in the frontmatter block, from its key line through the last line of its value, with line terminators excluded; a field that shares a line with another field, as in flow-style frontmatter, has no lines of its own and only its value is kept), MUST NOT verify them, MUST NOT write them, and MUST NOT raise an audit finding for them. The "unsigned v0.4 memory" tag from v0.5.x no longer exists.

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

- **`index`** (default): Surfaces in MEMORY.md. Eligible for session-start loading and/or query-triggered recall (see §"Recall operation"). Includes top-level memories AND rollup README.md files inside subfolders. Should be concise; the body of an index file should contain the synthesized rollup, not deep detail.
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

The conformance harness (`pytest tests/test_conformance_sensitivity.py`) is enabled by default. (The scenario fixtures live under `tests/conformance/sensitivity/`; the test module that drives them is `tests/test_conformance_sensitivity.py`. Pointing pytest at the fixtures directory collects zero tests and falsely passes.) Adapter authors who deliberately target a non-secure-mode profile MAY skip via `pytest.skip("non-secure-mode")` with the rationale documented in their adapter README; spec-conformance claims are forfeit for the skipped scenarios.

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

The controlled vocabulary lives in `spec/taxonomy.yaml`. The vocabulary carries its own independent `version` plus a `spec_compatible` range declaring which spec versions it is valid against; it is revised on its own cadence rather than tracking every spec release. Adapters MAY warn on tags outside the vocabulary; tools SHOULD provide a synonym map (e.g., `oauth` → `topic:auth`).

## Index format (`MEMORY.md`)

`MEMORY.md` is an index, not a memory. It MUST NOT carry frontmatter. It SHOULD be kept under 180 total lines.

In v0.3.0+, MEMORY.md is treated as a generated build artifact. Manual edits remain valid (current state) but the long-term direction is generator-driven via the `memory-index-gen` tool (Phase 1).

Each `tier: index` memory file MUST appear exactly once in the index (top-level files + rollup README.md files), as a pointer line in this shape:

```
- [<title>](<path>) , <one-line hook describing relevance>
```

For top-level files: `<path>` is the filename. For rollup README.md files: `<path>` is `<topic>/README.md`.

The hook line SHOULD stay under 180 characters (and 180 bytes; em-dashes are 3 UTF-8 bytes; prefer colons). Tools SHOULD flag overruns as warnings, not errors.

When the index is generated (v0.6.3+), the generator MUST keep each emitted pointer line within the 180-byte cap by truncating the hook portion to fit: split on a UTF-8 codepoint boundary and append the literal three-character string `...` when any truncation occurs. The authoritative full `description` is preserved in the file's frontmatter and in the recall index, so hook truncation is lossless for recall (recall surfaces the frontmatter `description`, never the MEMORY.md hook). If the `[<title>](<path>):` prefix leaves three or fewer bytes for the hook, the generator omits the hook entirely (emitting `- [<title>](<path>)`) rather than appending an over-cap hook. A `[<title>](<path>)` whose title or path alone exceeds the cap is emitted intact; that residual overrun is unavoidable and attributable to the title/path, not the hook.
<!-- TODO: project-specific pointer-line limits become configurable via `.memforge/config.yaml` in a future major version. -->

Section headers (`##`, `###`) MAY group pointers by theme. An introductory paragraph at the top is permitted.

**Operator text in a generated file.** A generator that copies text it does not control into a generated file (the preamble of a generated `MEMORY.md`, the body of a pinned memory placed in an `AGENTS.md`) SHOULD copy it as literal text: inside a backtick code fence one backtick longer than the longest run of backticks in the text, and at least three. No line of the text can close a fence that long, so nothing in it (a heading, a fence of its own, an HTML block, a comment) reaches the generated text around it, and no body heading is moved or rewritten. The reference generators (`memory-index-gen`, `agents-md-gen`) do this. A tool that reads such a preamble back (`memory-preamble-extract`) takes a block that is one such fence from its first line to its last as the preamble, without the fence lines, and does not end the preamble at a heading or bullet inside the fence. A generator still refuses text that carries a marker only the generator writes (the generated header, the competing-claims block), because line-based readers do not track fences.

## Recall operation (v0.6.0+)

Earlier spec versions assume an adapter loads the `tier: index` hotlist (`MEMORY.md`) into every session. As a memory set grows, bulk-loading the whole index becomes token-expensive and the one-line index pointers become too terse to reliably surface the right memory at the right moment. The **recall operation** is an alternative, query-driven loading strategy: given a query string, a recall consumer surfaces only the memories whose triggers match, injecting their `description` text (not their bodies) so the agent can read the full file on demand.

Consistent with the loading-semantics principle (see §"Goal": loading is the adapter's responsibility), this spec defines recall by its **frontmatter contract** and **operation post-conditions**, NOT by any index format, matching algorithm, ranking function, or injection mechanism. Those are reference-implementation and adapter concerns. Any implementation that honors the post-conditions below is a conforming recall consumer.

### Frontmatter fields (all optional; v0.6.0+)

- **`triggers`** (list of strings): explicit recall keywords or short phrases for this memory. Optional. A recall consumer MUST also derive base triggers from `name`, `tags`, and `description`, so `triggers` is a high-signal refinement, not a requirement. A memory with no `triggers` field is fully recall-eligible via its derived triggers. If `triggers` is present but is not a list of strings, a consumer MUST ignore it and fall back to derived triggers (audit WARN `triggers_malformed`).
- **`always`** (boolean, default `false`): when `true`, the memory is surfaced by recall regardless of whether the query matches. This is the always-on tier (rules an agent should hold every session). An unbounded always-set defeats the recall budget (it is the recurring cost paid on every query), so operators SHOULD keep the always-set small and bounded. If `always` is present but not a boolean, a consumer MUST treat it as the default `false` and SHOULD emit audit WARN `always_malformed`.
- **`do_not_inject`** (boolean, default `false`): when `true`, the memory is excluded from query-triggered recall injection. Use when the memory's content is already present in the agent's context by another path (for example, migrated into an always-loaded instruction file), so recall need not re-inject its description. `do_not_inject` does NOT remove the memory from the folder, the index, audits, or `MEMORY.md`; it only suppresses recall injection. If `do_not_inject` is present but not a boolean, a consumer MUST treat it as the default `false` and SHOULD emit audit WARN `do_not_inject_malformed`.

`always` and `do_not_inject` are independent. `always: true` with `do_not_inject: true` means "always relevant, but already in context by another path"; a conforming consumer surfaces neither (the do-not-inject suppression wins the injection decision).

### Relationship to `dynamic_supplement`

`triggers` (recall) and `dynamic_supplement` are opposite directions and MUST NOT be conflated:

- **Recall (inbound):** a query surfaces *this* memory. `triggers` help a memory *be found*.
- **`dynamic_supplement` (outbound):** *this* memory declares queries whose results it wants pulled in as supplement. It describes what a memory *wants to know*.

A recall consumer MUST ignore `dynamic_supplement` when computing recall matches.

### Post-conditions (normative)

Given a query string and a memory folder, a conforming recall consumer produces a result set such that the following hold. Post-conditions 1 and 2 define the candidate set (inclusion); post-conditions 3, 5, and 6 are exclusion filters applied to that candidate set; the final result set is the candidates minus the exclusions.

1. **Always-set inclusion.** Every live memory with `always: true` AND `do_not_inject: false` is in the result set, independent of the query.
2. **Match inclusion.** A live memory with `do_not_inject: false` is eligible for inclusion when the query matches its trigger set, where the trigger set is `explicit triggers UNION derived(name, tags, description)`. A memory with no `triggers` field is still match-eligible via its derived trigger set. Matching MAY apply case-folding, stemming, and synonym expansion; the specific scheme is implementation-defined.
3. **Injection suppression.** No memory with `do_not_inject: true` appears in the result set.
4. **Body exclusion.** The result set carries each memory's `description` plus a resolvable reference (path or uid), NOT its body. Because `description` is public-classification metadata that MUST be free of secrets, PII, and codenames (see §"Sensitivity and the description field"), recall injection is safe by construction. A consumer MUST NOT inject body content as part of recall.
5. **Sensitivity / access honored.** A recall consumer MUST apply the same `sensitivity` and `access` filtering it would apply to any other surfacing path (see §"Sensitivity classification", §"Access labels").
6. **Liveness.** Memories with `status ∈ {superseded, dropped, archived}` are excluded, consistent with the live-set partition in §"Status semantics".
7. **Fail-open-empty.** A recall consumer that cannot produce a result (missing or unreadable index, parse error, timeout) MUST yield an empty result set and MUST NOT block, delay, or fail the operation it serves. Recall is an enhancement, never a gate.

The result set MAY be ranked and truncated to a budget (top-K, character or token cap); ranking and budget are implementation-defined and non-normative.

### Index artifact (informative)

Recall is latency-sensitive when wired to a per-query hook, so reference implementations precompute an inverted index at memory-change time rather than walking the folder per query. The build step and the query step are separate operations; in the reference implementation `memory-index-gen` emits the recall index and a thin `memory-recall` reader consumes it. The index is a derived build artifact (like `MEMORY.md`); it is not part of the on-disk format contract and MAY be regenerated at any time. Implementations SHOULD version the artifact and reject an incompatible version by falling back to a rebuild or to empty recall (per post-condition 7).

The index is a local cache, not a shared file. It SHOULD NOT be committed, because a copy that arrives by `git pull` is input from another machine and its entries are injected into an agent's context. An implementation SHOULD store in the index a fingerprint of the memory files it was built from (each file's relative name, size and modification time in nanoseconds, in a fixed order), SHOULD compare it with the fingerprint of the folder as it is now before it serves a query, and SHOULD rebuild the index on any difference (a change to the synonym override file counts), so a stale, pulled or hand-edited index is never served. An implementation that compares file modification times SHOULD also store the time the build started (the reference index field `built_ns`, an integer count of nanoseconds since the epoch, read before the first memory file is read) and SHOULD treat the index as stale when any memory file's modification time is within 2 seconds of it, inclusive, before or after it, so an edit that keeps its size and falls in the same file-clock tick as the build is not served stale (a coarse file clock rounds times up to 2 seconds on some file systems). An index with no usable build time is rebuilt. Readers ignore index fields they do not know. The `folder` it reports for an entry is the folder it read, whatever the index file says. An index it cannot write is not an error: it answers from the build it made in memory. Per post-condition 7 an index it cannot build yields empty recall.

### Tier and recall (clarification, v0.6.1+)

Recall eligibility is governed by liveness and sensitivity, not by tier. A live `tier: detail` memory carries a `description` and is recall-eligible on the same terms as a `tier: index` memory; the `index`/`detail` split governs the session-start `MEMORY.md` hotlist (§"Tier semantics"), not query-driven recall. A consumer MAY rank `index`-tier memories above `detail`-tier on a tie, but MUST NOT exclude a live `detail` memory from recall solely for being detail-tier.

## Recall-readiness lint (v0.6.1+)

The **lint operation** is a quality analysis distinct from audit. Audit (§"Integrity invariants", §"Audit invariants for competing claims") answers a deterministic, binary question: is this memory *conformant*? Lint answers a graded question: is this memory *recall-effective and token-efficient*? Lint is advisory, it is READ-ONLY (it MUST NOT mutate memory files), and it MUST NOT be a hard gate on any other operation (a non-deterministic quality signal must never block a conformance-correct write or a resolve).

This spec defines lint by the dimensions it scores and the safety posture it MUST observe, NOT by a scoring algorithm, numeric threshold, or model prompt. Any implementation that scores these dimensions and honors the safety posture is a conforming lint.

### Dimensions

1. **Recall-context.** How findable is the memory through the recall trigger model (§"Recall operation")? A description is recall-strong when it contributes distinctive query-bearing terms beyond what `name` and `tags` already supply, and recall-weak when it is contentless filler, contributes no terms the name does not, or contributes only terms that are common across the corpus (high-collision, hard to disambiguate from neighbors). A conforming implementation SHOULD evaluate findability against the same trigger derivation the recall consumer uses, so the score reflects real recall behavior rather than a parallel heuristic.

2. **Token-cost.** What does the memory cost the recall budget? Covers: `description` length (the per-match injection cost); the always-set budget (see below); `do_not_inject` candidacy (a memory whose rule already lives in an always-loaded instruction file need not be re-injected); and oversized `index`-tier bodies (a read-cost signal; suggests rollup or `tier: detail`).

3. **Well-formedness handoff.** Deterministic conformance (required fields, enum validity, malformed recall fields, missing `**Why:**`/`**How to apply:**`) is audit's job, not lint's. Lint SHOULD defer to audit for these rather than re-implement them, keeping a clean seam between deterministic conformance and graded quality.

### Always-set budget (advisory)

The always-set (memories with `always: true` and `do_not_inject: false`) is injected on every recall query, so it is the recurring per-query cost. Tooling SHOULD surface a budget WARN when the always-set exceeds a configured count OR a configured combined-`description`-character budget. This is **advisory (WARN), never a BLOCKER** in v0.6.1: an existing memory set may already exceed the budget, and an upgrade MUST NOT fail a conformant store on a quality threshold (consistent with the v0.3.x degraded-mode principle). The budget is operator-configurable; reference defaults are a small count and a small character total. A future spec revision MAY tighten this to a hard cap after a deprecation window.

### Cloud-dispatch safety posture (normative)

A lint implementation that consults an external (network-bound) model to suggest improved descriptions or triggers MUST observe the following, because memory content is operator data and a description may carry sensitive material if the store is not yet compliant with §"Sensitivity and the description field":

1. **Local-only by default.** External dispatch MUST be off unless the operator explicitly enables it. A consumer MUST be runnable with no external dispatch at all.
2. **Metadata-only by default.** When external dispatch is enabled, the default payload MUST be limited to recall metadata (`name`, `tags`, `description`). Shipping the memory **body** to an external model MUST require a separate, stronger operator opt-in.
3. **Pre-dispatch screen.** A deterministic secret/credential screen MUST run on the payload before any external dispatch; a positive screen MUST suppress dispatch for that memory (fail-closed).
4. **Sensitivity and access honored.** External dispatch MUST honor the same `sensitivity`/`access` filtering as any other surfacing path.
5. **Suggestions are advisory.** Returned suggestions MUST NOT be written to memory files automatically; applying a suggestion is an operator action.

### Consolidation orchestration (informative)

An adapter MAY orchestrate lint around the resolve operation (§"The resolve operation"): lint the competing candidates before resolution (so the operator chooses among well-formed claims), then, after the scope-locked `memforge: resolve` commit, regenerate the recall index so the new active winner is immediately recallable. The index regeneration MUST be a separate step from the resolve commit, since the resolve commit may touch only the topic's group members (resolve post-condition 4); folding index regeneration or content repair into the resolve commit is a scope violation. Verifying the winner is recallable after regeneration (a smoke query) is a recommended post-condition.

## Write-validation operation (v0.7.0+)

The **validate operation** is the syntax-aware write-boundary gate: the agent-neutral primitive an adapter invokes BEFORE accepting or committing a memory write, so a malformed file is rejected at the boundary instead of surfacing at the next `memory-audit`. Like lint and recall, validate is defined by its operation contract + post-conditions; matching, wiring, and UX are reference-implementation + adapter concerns (per the loading-semantics principle). It is the portable replacement for editor-specific write hooks: a single adapter-neutral check any surface (git pre-commit, in-editor on-save, an agent's pre-write hook) wires once, rather than each IDE reimplementing YAML parsing.

Validate is READ-ONLY (it MUST NOT mutate a file) and is a deliberately FAST single-file subset of audit, not a replacement: `memory-audit` remains the full-corpus conformance pass (orphans, supersession graph, sensitivity gates, commit-log invariants); validate is the latency-bound pre-write check on one file (or a small batch).

**Contract.**

- **HARD** (a validate FAILURE, the gate): the file's frontmatter satisfies integrity invariant 27 (a present `---` fence is closed and parses as a YAML mapping). A failure here MUST cause the operation to report failure (nonzero exit for a CLI adapter) regardless of any soft-check configuration, because the gate's purpose is `validate || reject`.
- **SOFT** (reported, advisory): the `MEMORY.md` pointer/line caps (§"Index format"), presence of the v0.4 required fields on a memory file, and `tier` / `status` within their enums. These overlap audit; validate surfaces them as the early single-file signal but a SOFT finding MUST NOT block a write unless the operator opts into a strict mode (mirrors the lint "never a hard gate" rule for the advisory layer).

**Post-conditions (normative).**

1. Validate never mutates any file.
2. A file with no `---` fence is not a validate failure (presence is invariant 1's concern for memory files, not validate's).
3. A HARD failure is reported per-file with a machine-consumable code identifying the cause (malformed-fence / non-mapping / parse-error).
4. The SOFT check set is a strict subset of `memory-audit`'s checks and uses the same caps + enums (single-sourced), so validate and audit can never disagree on a shared check.
5. Validate is composable into any write path; per-surface wiring (git pre-commit over staged files, in-editor on-save, agent pre-write hook) lives in `docs/adapter-implementation-guide.md` §"Write-boundary gate".

**Reference implementation.** `memory-validate [--path DIR ...] [FILE ...] [--json] [--strict]`. Single-file and batch; read-only; exit 0 when no HARD failures (and, under `--strict`, no SOFT findings either).

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
| CLI reference (load-bearing) | `memforge-resolve <topic>` console script, ships in the canonical reference package. (Currently a standalone console script, not a `memforge` dispatcher subcommand; see `known-limitations.md` §"Reference CLI status".) |
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
- `.memforge` and `.memforge/snoozes` MUST be real folders and a snooze record a regular file. A committed link at `.memforge`, at `.memforge/snoozes`, on the way to the snooze folder or under it is a Tier 2 BLOCKER, and the resolve operation MUST refuse to delete a snooze through one, before it changes any member.
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

**Edit gate (Tier 2).** All edits to `.memforge/config.yaml` MUST occur in commits whose message starts with `memforge: config` AND that touch ONLY the config file. Multi-file or wrong-prefix commits modifying config are BLOCKER. The config file follows links, so "the config file" here is every committed path a loader can open as it: any case spelling of `.memforge/config.yaml` or of `.memforge` (macOS and Windows open either), a committed link at `.memforge`, at `.memforge/config.yaml` or anywhere on the way, and the file inside the repository that such a link chain ends at, as committed before or after the commit. A link chain is followed as the file system follows it: one name at a time, `..` applied after the link before it, and an absolute target under the repository top read as that repository path. A committed config link chain that cannot be followed to a file inside the repository (it leaves the repository, climbs above its top, or passes more than 40 links) is a BLOCKER in any commit that changes a link of that chain, whatever the prefix.

**Two-phase cutover for `decision_bearing_tags` shrinks.** A config change that shrinks `decision_bearing_tags` does NOT take effect immediately. For one full `audit.audit_window_days` after the shrink commit, the effective set is `union(old_tags, new_tags)` and audit emits MAJOR for the duration. At window end, the shrink takes effect. Operators may override via `memforge: config-major` prefix with a manifest of affected UIDs in the commit body; audit BLOCKER until each manifested UID is reconciled.

### Reader-side competing-claim contract

A consumer (CC adapter, memory-query, MEMORY.md generator) MUST:

1. Group memories by `decision_topic`, including topic-aliases-mapped groups (mutual aliases only). Memories with `status ∈ {superseded, dropped, archived}` are excluded; memories with `status ∈ {active, proposed, gated}` are included. Memories with any other `status:` value are excluded from grouping AND trigger the status-enumeration BLOCKER in audit.
2. For any group with two or more live members AND no active snooze, surface the set as a competing-claim block per the canonical serialization below.
3. **Snooze interaction**: when a snooze is in effect, the competing-claim block is SUPPRESSED but the canonical serialization includes a separate "snoozed" line listing the topic, snooze creator, `snoozed_until`, and `snooze_reason`. Operators are NOT blinded.
4. Refuse to silently pick a winner. Refuse to drop members based on any unratified `replaces:` claim.

A generator run with a viewer filter MUST still list every live member of a group, and MUST show only the `uid` and a restricted marker (`restricted: true`) for a member the viewer may not read.

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
- String values single-quoted only when they contain reserved YAML characters; otherwise plain. A string is also single-quoted when its plain form would not read back under a YAML 1.1 reader as the same string (for example `<<`, `=`, `.5e+3`). A string holding a control character, U+2028, U+2029, a surrogate, U+FFFE or U+FFFF is double-quoted, with those characters escaped (`\n`, `\r`, `\t`, `\0`, else `\xXX` for C0, DEL and C1 controls; `\uXXXX` for the rest).
- `first_line` truncation: NFKD normalize first, then take the first 117 UTF-8 bytes (split safely on UTF-8 boundary), then append the literal three-character string `...` only if any truncation occurred.
- Stable sort: groups by `decision_topic` ascending; within each group, members by `updated` descending then `uid` ascending.
- Empty groups (zero or one live member after exclusion AND no active snooze) are omitted.

**Migration story for the per-group field rename (v4 `status:` → v5+ `state:`).** For the entire v0.4.x train, parsers MUST accept BOTH `state:` (canonical) and `status:` (legacy) in the per-group block. Generators MUST emit `state:` only. CI byte-match tolerance MAY treat both forms as equivalent during v0.4.x; at v0.5.0, byte-match becomes strict and only `state:` is accepted. Adapters SHOULD provide a one-shot fixer (e.g., `memforge migrate-claim-block`) to rewrite legacy blocks.

### Audit invariants for competing claims

`memory-audit` v0.4.0+ enforces the layered rule set below. Tier 1 invariants are evaluated against HEAD alone (history-independent); Tier 2 invariants walk git log over the audit window (default 30 days, floored at 30 by config validation). Tier 2 reads the repository that holds the store: repository-local git variables in the caller's environment (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` and the like, which a git hook sets) are not passed to git.

**Tier 1 (HEAD-pure) BLOCKERs**: asymmetric supersession (every `superseded` has `superseded_by: [exactly 1 UID]` pointing to sole active winner of same group; symmetric `replaces:` on winner); exactly-one-active for any group whose anchor or any member carries `ever_multi_member: true`; status enumeration; cross-topic replaces; dangling replaces; alias cycle; resolve-commit producing zero or two-plus active in resolved group; `ever_multi_member: true` → `false` transition; sub-floor `audit.audit_window_days` in config.

**Tier 1 MAJORs**: replaces cardinality > 20; alias cap > 10.

**Tier 1 WARNs**: alias non-mutual; near-duplicate slugs (edit distance ≤ 2 lacking mutual alias); adapter horizon misconfiguration; snooze author-correlation heuristic.

**Tier 2 (commit-log) BLOCKERs**: status transition to `superseded` outside resolve commit; `superseded_by:` written outside resolve commit; `decision_topic` mutation on existing memory outside resolve commit; memory deletion of group member outside resolve commit (a live member moved into `archive/` counts as deleted; a `superseded`, `dropped` or `archived` one does not); resolve-commit scope violation; config edit outside `memforge: config` commit; a config link chain that cannot be followed to a file inside the repository (§"Config file"); a committed link at or under the snooze folder or on the way to it (§"Snooze record"); waiver-file edit outside a waiver-only `memforge: waiver` commit (§"Audit waivers").

**Merge commits (Tier 2).** A merge is judged by what it changes on its first parent, whichever parent the change came from. A change that a merged branch's own commits made since the merge base is checked on those commits, not again on the merge, when both hold: the merge result for that path is the branch's (the same file, or for a memory the same `status`, `superseded_by`, `decision_topic` and `topic_aliases`), and every branch commit that touched the path is inside the audit window. A merge that writes another value of the same kind, or that brings in a branch commit older than the window, is checked as the merge's own change. Anything else a merge brings onto its first parent (a side chosen against a change on the first parent, a branch with no merge base, a new result) is checked as the merge commit's own change under the rules above.

**Tier 2 MAJORs**: pending resolution (scoped to same-topic, at least one other live member); live collision past stale threshold; long-snoozed past horizon; orphaned snooze; snooze preemption (collision-recency primary); snooze cap (per-author and per-topic); snooze date-forgery; alias edit outside resolve or `memforge: alias`; snooze edit outside `memforge: snooze`.

**Tier 2 BLOCKER (escalated)**: long-snoozed past 2× horizon.

### Residual git-layer threat (informative)

Tier 2 invariants can be partially evaded by a writer with force-push privileges across a long enough rewrite that erases the entire commit-log audit window. Tier 1 invariants still catch any inconsistent residue, but a sophisticated writer who rewrites all history into a fabricated-clean state (including the `ever_multi_member` flag's introduction, which is its own BLOCKER on HEAD) can evade the Tier 2 layer entirely.

This is a **git-layer threat**, not a MemForge-layer threat. The mitigation is at the git provider:

- **Branch protection** on the canonical branch (no force-push; require pull-request review).
- **Required signed commits** so author identity is harder to forge.
- **Required-status-checks** that include `memory-audit` running on the resulting HEAD.

`memforge sync` (§"Commit trust (v1.0.0+)") adds a receiver-side check of the signed first-parent history against the committed signer list. It is detective for people who run it, and it does not replace branch protection.

**Secure-mode adapter conformance (informative).** Adapters MAY claim secure-mode. Secure-mode adapters MUST: (1) at startup, detect (via the git provider's API where available) whether the canonical branch has branch protection with no force-push, required pull-request review, and required signed commits; (2) emit a startup MAJOR if any is missing; (3) refuse to perform any resolve operation if branch protection is absent (operator may override with `--insecure`, recorded in commit metadata).

Adapters that do NOT claim secure-mode MUST emit an informative startup notice that the deployment is operating without git-layer protection and that Tier 2 audit guarantees are reduced. Solo-operator deployments running without branch protection are explicitly NOT in secure-mode; the operator accepts the residual force-push threat as part of running solo.

### Audit waivers (R2; v0.7.0+)

The Tier 2 (commit-log) layer flags authority-state transitions (e.g. a `status: superseded` transition) that land in a commit lacking a recognized `memforge:` prefix. Some such commits are immutable historical facts: a corpus migration or a rename predating the prefix convention produces a permanent floor of Tier 2 findings that cannot be fixed without rewriting history. The waiver mechanism lets an operator record an explicit, auditable allowlist so a genuinely-clean corpus audits to zero rather than carrying a permanent nonzero floor that desensitizes the operator to real findings.

**File.** `<memory-root>/.memforge/audit-waivers.yaml`, a frontmatter-free YAML mapping (so `memory-validate` and `memory-audit` can themselves check it). Recognized keys:

- `waived_commits` (list of commit SHAs, short ≥ 7 chars or full): a Tier 2 finding whose commit SHA is prefixed by a listed entry is waived.
- `superseded_transition_waived_before` (optional ISO date `YYYY-MM-DD`): a superseded-transition finding on a commit dated strictly before this cutoff is waived. Intended for a migration-era floor.
- `reason` (optional string): operator note recorded alongside the allowlist.

**Semantics (normative).**

1. **Reported, never silent.** When any waiver applies, `memory-audit` MUST emit a visible "N finding(s) waived" line listing the waived commit SHAs. A waiver suppresses a finding from the BLOCKER set; it MUST NOT hide that a waiver occurred.
2. **Fail-closed.** A missing, unreadable, malformed, or non-mapping waiver file yields an EMPTY waiver set, so genuine violations still fire. Only an explicit, valid waiver committed at HEAD suppresses a finding: `memory-audit` reads the file from the HEAD commit, never from the work tree, so an untracked, staged-only or locally edited waiver file waives nothing. A HEAD entry that is not a regular file (a committed link) yields an empty set. When the work-tree or index copy differs from HEAD, `memory-audit` MUST emit a WARN saying only the committed version is honored.
3. **Minimum SHA length.** A `waived_commits` entry shorter than 7 characters MUST be ignored, so a stray short token cannot waive broadly by prefix-match.
4. **Trust boundary.** The waiver file is git-tracked and shares the trust boundary of the memory files themselves: anyone who can write the repo can author a waiver, but only in a gated commit (rule 5), so every waiver change is a separate, reviewable commit in the log. Deployments with untrusted writers rely on the same git-provider branch-protection + signed-commit mitigations as the rest of the Tier 2 layer (§"Residual git-layer threat"); a waiver is not a security control, it is a recorded, reviewable suppression of a known-benign historical finding.
5. **Edit gate (Tier 2).** Every add, change or delete of `.memforge/audit-waivers.yaml` MUST land in a commit whose subject is `memforge: waiver` or starts with `memforge: waiver` followed by a space, AND that touches ONLY the waiver file. A waiver-file change in any other commit is a Tier 2 BLOCKER. A waiver never applies to a commit that changes the waiver file: every Tier 2 finding on such a commit (the waiver-edit BLOCKER and any other finding in the same commit) is reported whatever the waiver file lists, including a SHA entry for that commit and the `superseded_transition_waived_before` cutoff. This holds for the commit that introduced the file and for every later waiver-file commit. Waivers for all other commits work as rules 1 to 3 state.

## Commit trust (v1.0.0+)

MemForge does not sign memories and does not hold keys. A store that several people or agents share is protected by the signed commits of its git history, using the identity, signing and git host controls the organization already runs. The store commits a list of the signers it accepts. The `sync` operation fetches the shared branch and accepts new history only when every commit on it passes the check below. There is no key lifecycle inside MemForge: nothing to generate, rotate, revoke or recover.

### Trust statement

`sync` accepts a range of the shared branch only when every commit on its first-parent history passes the store's mode check: a valid signature by a signer that the mode's committed list named one commit earlier.

The git host is trusted to run the signing policy it advertises and to be run by people who are not the attacker. Host enforcement is weaker than the rule above. Hosts require signed commits and registered keys, not membership in the committed list. An unauthorized but registered signer can reach the branch, and `sync` then refuses it for everyone until the remote's owner removes it.

Only `sync` checks anything. No other operation (audit, recall, index generation, validation, resolve, or any adapter load) is gated on verified state, warns about it, or exits because of it. Those operations read what is checked out. A receiver who runs plain `git pull`, `git merge` or `git checkout`, who edits or adds files in the work tree, or who never runs `sync`, gets unverified content in front of an agent, and MemForge does not stop it. The controls for that are the host's branch protection and required signed commits. They narrow who can push, but they do not enforce the committed list. The local check is detective: it catches a misconfigured host for people who use `sync`.

### Signer lists and modes

Each mode has one committed list under `<memory-root>/.memforge/`:

| Mode | List file | Line format | What decides a commit |
| --- | --- | --- | --- |
| `ssh` | `allowed_signers` | git's `allowed_signers` format | the signature is valid for a listed key |
| `gpg` | `allowed_gpg_fingerprints` | one 40-hex primary key fingerprint per line; `#` starts a comment | the signature is valid, the key is in the receiver's keyring, not expired, not revoked, and listed |
| `keyless` | `allowed_identities` | `<certificate-identity> <oidc-issuer-url>`, matched exactly | a Fulcio certificate chain and a Rekor entry are valid, as decided by `gitsign` |

**Mode derivation.** The mode of a commit C is the mode of the single list file present in C's first parent. There is no mode setting to store or change.

- Two or more list files in the first parent: `ambiguous_list`. A tree that names the same list file more than once is `ambiguous_list` as well.
- No list file in the first parent: `list_missing`.
- A list that does not parse, or has no entries: every commit judged against it fails `list_invalid`.
- A commit whose own tree does not hold exactly one valid list with at least one entry is refused `list_removed`. This keeps a listed signer from deleting the list by accident. It does not stop a listed signer from blocking later syncs on purpose, for example with a valid list of keys nobody holds, or by switching to a mode a receiver refuses.
- A commit with no signature header is refused `unsigned` once its first parent's list is validated and before its own tree's list is read, so `unsigned` takes precedence over `list_removed`.

The checks run in this order: the list file count in the first parent (`list_missing`, `ambiguous_list`), the mode release gate (`mode_unreleased`), the first parent's list (`list_invalid`), the signature header (`unsigned`), the commit's own list (`list_removed`), then the signature. The list is validated before any signature is checked. A parse or content failure (including a list line with an unsupported option) is `list_invalid` and takes precedence over any verifier result.

Because the list is read from the first parent, a commit that edits the list cannot authorize itself. A signer is added by a commit signed by an already listed signer. A mode is changed by a listed signer in one commit that deletes the old list file and adds the new one. That commit is judged under the old mode, and its descendants under the new mode.

In `ssh` mode the principal on a list line is only a label. git finds the principal by the signing key, not by the commit's author identity, so a commit authored as one person and signed by a key listed under another principal passes. Use one principal per person.

### Isolated verification

Repo-local git configuration changes verdicts. For example, `gpg.ssh.revocationFile` can flip a listed signer's result. So a conforming `sync` MUST verify in isolation. This applies to the verification commands of step 5. Fetch, fast-forward and the ancestry checks of steps 2 to 4 use the store's own repository as git normally does, because no verdict depends on them.

1. Make a throwaway bare git directory with no configuration, whose `objects/info/alternates` points at the store's object directory. Remove it on every exit path.
2. Run every verification command as `git --git-dir=<throwaway> ...` with a cleared environment: `PATH`, a fixed locale, a private temporary directory, an empty `HOME` (unless the mode declares `HOME` as an input, as `gpg` and `keyless` do), `PROGRAMDATA` pointing at an empty private directory (OpenSSH for Windows does not run without it), `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` pointing at an empty file, system configuration disabled, and the variables the mode declares as inputs. Pin the mode's `gpg.*` keys with `-c`.
3. Before any program runs, refuse a memory root that has no `.git` entry of its own: `root_not_toplevel` when a folder above it has one, else `not_a_repository`. Then resolve `git` and the mode's verifier (`ssh-keygen`, `gpg`, `gitsign`) to absolute paths once. Those absolute paths, not the bare names, are what `gpg.ssh.program`, `gpg.program` and the `gitsign` call use. Each run that passes that check prints one line per executable (`git`, `ssh-keygen`, `gpg`, `gitsign`) in the form `verifier <name>: path=<absolute path> version=<version> sha256=<hex>`, with `not found` for an executable that is absent, so the executable that is printed is the one that produces the verdict. `ssh-keygen` has no version flag, so its version is that of the `ssh` binary beside it.

### Verifying one commit

**`ssh`.** Extract `allowed_signers` from the first parent to a temporary file inside the throwaway directory. Run `verify-commit` with `-c gpg.format=ssh`, `-c gpg.ssh.program=<resolved ssh-keygen>` and `-c gpg.ssh.allowedSignersFile=<temporary file>`. Exit 0 passes. No decision reads a clock or a commit date.

**List grammar.** The grammar is narrower than `ssh-keygen`'s, so that no line can mean one thing to the implementation and another to `ssh-keygen`. Any failure below makes the whole list `list_invalid`. For every mode:

- The tree entry that names a list file MUST name a blob. An entry that names a tag, a tree or any other object is `list_invalid`, decided from the object type before the object is read.
- A list is at most 1 MiB (1,048,576 bytes). A larger list is `list_invalid`, decided from the object size before the list is read.
- The list is UTF-8.
- Lines are split on newline (LF) only. One carriage return at the end of a line is removed.
- Any other control character in a line (U+0000 to U+0008, U+000A to U+001F, U+007F; tab is allowed) makes the list invalid, in a comment line too.
- Only space and tab are trimmed from each end of a line. A line that is then empty is blank, and a line whose first character is then `#` is a comment. Blank and comment lines are ignored.
- Every entry line (a line that is neither blank nor a comment) is tab and printable ASCII (U+0020 to U+007E) only, so the characters trimmed and the characters that split fields can only be space and tab.
- A list with no entries is `list_invalid`.

In `ssh` mode each entry line MUST also meet all of these:

- A double quote appears only in a `namespaces="..."` value (the option name in any case, at the start of the line or after a space, tab or comma). After every such value is removed, no `"` is left.
- No `cert-authority`, `valid-after=` or `valid-before=` (any case) starts the line or follows a space, tab, comma or double quote.
- At least one whitespace-separated token starts with `ssh-`, `ecdsa-sha2-` or `sk-`.

Those are the only `list_invalid` checks for `ssh` beyond the common rules. The rest of each line (principals, other options, the key record) is passed to `ssh-keygen` unchanged, so a malformed key record shows up as `ssh_not_verified` when a signature is checked. In `gpg` mode an entry is exactly one 40-hex primary key fingerprint (either case, compared case-insensitively), optionally followed by `#` and a comment. In `keyless` mode an entry is exactly two fields separated by spaces or tabs, the certificate identity and the issuer URL; a trailing comment is not allowed.

Reasons: `unsigned` (git shows no signature header; decided without `ssh-keygen`), `ssh_not_verified` (any non-zero exit on a signed commit: a bad signature, a key not on the list and a missing `ssh-keygen` all look the same; git's stderr is shown unchanged), `list_missing`, `list_invalid`.

The verdict has no receiver-local input except the installed `git` and `ssh-keygen` executables, so receivers whose executables behave the same agree.

**`gpg`.** Declared inputs: `GNUPGHOME` and `HOME`, passed through unchanged from the receiver's environment so `gpg` finds the receiver's keyring. Run `verify-commit --raw` with `-c gpg.format=openpgp` and `-c gpg.program=<resolved gpg>`, never with `-v`. Read only output lines that start with `[GNUPG:]` followed by a space, and ignore `TRUST_*` lines. Apply these rules in order. Failure lines always win over success lines.

1. `BADSIG`, or `ERRSIG` without `NO_PUBKEY`: `bad_signature`. `ERRSIG` with `NO_PUBKEY`: `no_key`.
2. `EXPSIG`, `EXPKEYSIG` or `KEYEXPIRED`: `expired`. `REVKEYSIG` or `KEYREVOKED`: `revoked`.
3. No signature header on the commit (decided by git, without gpg): `unsigned`. No status lines but a signature present and a non-zero git exit (gpg absent or crashed): `verifier_failed`. A non-zero git exit with only success lines: `bad_signature`.
4. Exactly one `GOODSIG`, exactly one `VALIDSIG` and a zero git exit; otherwise `bad_signature`.
5. Take the primary key fingerprint from `VALIDSIG`. Nine arguments (fingerprint, date, timestamp, expire, version, reserved, pubkey-algo, hash-algo, sig-class) mean the signer is the primary key and the first argument is its fingerprint. Ten arguments mean the tenth is the primary key's fingerprint. Any other count is `bad_signature`.
6. A primary fingerprint that is not on the list: `signer_not_listed`.

Receiver-dependent inputs in `gpg` mode: the keyring (including revocation certificates), key expiry against the receiver's clock, and the installed `gpg`.

**`keyless`.** For each commit, and for each list line in order, run `gitsign verify --certificate-identity=<identity> --certificate-oidc-issuer=<issuer> <commit>` with `GIT_DIR` set to the throwaway directory. The first exit 0 passes. Use the exact-match flags only, never a pattern flag. MemForge parses no `gitsign` output; the exit code is the only contract. The implementation shells out to the installed `gitsign` and adds no library dependency.

Reasons: `gitsign_missing` (the executable is not found, nothing else), `unsigned` (no signature header; decided by git), `keyless_not_verified` (any non-zero exit from an installed `gitsign` for every pair, including an unsupported subcommand; its stderr is shown unchanged), `list_missing`, `list_invalid`.

Declared inputs in `keyless` mode, passed through from the receiver's environment on top of the cleared environment: `HOME`, every variable that starts with `GITSIGN_` or `SIGSTORE_`, `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `SSL_CERT_FILE` and `SSL_CERT_DIR`. Receiver-dependent inputs in `keyless` mode: trust roots, configuration, network access and certificate validity against the clock. An outage of the trust roots or the log refuses every commit.

**Mode release gate.** An implementation MAY refuse a mode whose proofs have not passed on the tool versions it pins. If it does, a commit whose first parent carries the list of such a mode MUST be refused with `mode_unreleased`, naming the commit, before any verification of it runs. The reference implementation refuses `gpg` and `keyless` until their proofs pass, including a run against the real `gpg` and the real `gitsign`.

### The sync operation

Steps run in this order. Each step has one failure rule. A failure at a step stops the run, and nothing a later step does has happened. Let S be the start of the range: `verifiedHead` (V) when it is set, otherwise the pin.

| Step | Action | Failure |
| --- | --- | --- |
| 1. Start | The store MUST be the top level of its git repository; `--memory-root` names that top level. If V is unset, this is a first sync: a pin (`--trust-head <commit>` or `--adopt-local`) is required, and S is the pin. If V is set, a pin flag is refused, and S is V. | No pin on a first sync: exit 1 `no_pin`. A pin flag with V set: exit 1 `pin_refused`. Memory root not the repository top level: exit 1 `root_not_toplevel`, checked before any state is read or anything is fetched. Nothing changed. |
| 2. Fetch | Fetch only the branch named by the local upstream setting, never a value from a committed file. At most that one remote-tracking ref and its objects update. | Exit 1, nothing changed locally that `sync` uses: fetched objects and remote-tracking refs may have been updated, and are never used until verified. Fetch failure: `fetch_failed`. A detached `HEAD` is `no_branch`; a branch with no upstream setting is `no_upstream`. |
| 3. Target | H is the fetched head if the local branch is its ancestor, or the local head if the fetched head is its ancestor. `sync` never merges or rebases. | Neither: exit 1 `diverged`, nothing changed. |
| 4. Shape | S must be on H's first-parent chain. If the repository is shallow, `S..H` must be fully present. | S not on the chain: exit 2 `history_rewrite`, naming both commits. Missing shallow range: exit 2 `shallow_range`. Nothing changed. |
| 5. Verify | Verify every commit on the first-parent range `S..H`, in order, each by the rules above. | Exit 2 naming the first failing commit and its reason. The local branch, work tree and V are as they were (on a first sync V stays unset). The fetched objects remain and are never used. |
| 6. Fast-forward | Move the local branch to H if it is behind, with `git merge --ff-only`. | Exit 1 `ff_failed` (for example, local work-tree changes block it). The branch is unmoved and V is as it was. The verification result is discarded and redone on the next run. |
| 7. Record | Write V = H. On a first sync this sets V for the first time. | Exit 1 `state_write_failed`. The branch is already at H and V is as it was, so the next run re-verifies from the old V, or asks for the pin again on a first sync. |
| 8. Done | Exit 0. | |

V is only a lower bound on what was verified, so a lost write costs time and is never unsafe. V MUST NOT be written before the fast-forward has succeeded.

**Exit codes.** 0: verified and recorded (with `--check`: every commit in the range passed, and nothing was recorded). 2: refused by verification, history or shallow rules. 1: any other error, named in the step.

**Flags.** `--check` runs steps 1 to 5 and stops before step 6. It fetches like any run, so the one remote-tracking ref and its objects may update, but it changes no local branch, no work tree and no `verifiedHead`. It prints whether `HEAD` equals `verifiedHead`, or that `verifiedHead` is unset on a first sync. Its failures and exit codes are those of steps 1 to 5. `--trust-head <commit>` takes the full commit id and `--adopt-local` pins the local `HEAD`; they are the first-sync pins, and giving both is an error. `--memory-root <dir>` names the store (default: the current directory) and MUST be the repository top level; the signer list is read from `.memforge/` under it.

**No override.** A failing commit makes `sync` refuse, with no per-file marking and no override flag. The way out of a refused commit is the remote's owner removing it. In `gpg` and `keyless` a refusal can be local (`no_key`, `gitsign_missing`, `keyless_not_verified`): the receiver fixes its keyring or network and runs `sync` again.

**Output.** Each run prints the `verifier` lines above. After verification it prints one mode line per mode that judged a commit, in the form `mode <name>: <text>`. For `ssh`, the text is `verdict has no receiver-local input except the git and ssh-keygen executables`. For `gpg` and `keyless`, it is `verdict depends on this receiver's keyring, configuration and clock`. A run refused before any commit is judged prints no mode line.

**Other exit-1 errors.** Errors outside the steps above also exit 1 and are named: `root_not_toplevel` (the memory root is not the repository top level), `bad_arguments` (both pin flags given), `pin_invalid` (`--trust-head` is not a full commit id), `state_invalid` (the state key is not a commit id), `git_missing`, `not_a_repository`, `object_missing` (an object the range needs is not present and the clone is not shallow), `bad_commit` (a commit in the range whose parent header is not a lowercase hex object id, such as an uppercase id that git stores; it names a commit in the range being verified, usually a fetched one, so treat it like a refusal), `isolation_failed` (the throwaway directory could not be made), `git_failed` (a git command the run depends on failed) and `tool_in_store` (`git` or a verifier executable resolves to a file inside the store).

### Local state and the first sync

State is one local key, `memforge.verifiedHead`, in the repository's own `.git/config`. It is per repository, which is why the store must be the top level of its repository (step 1). It records where verification stops, so `sync` does not re-verify from the root. Nothing else is needed: the mode comes from the list in the first parent, and the first-sync pin is the starting point. A push cannot change it, because it is not in the work tree. Moving it back only makes `sync` re-verify more. Moving it forward needs write access to local configuration, which is local code execution. Only step 7 of a `sync`, and nothing else, writes it.

A first sync (V unset) refuses unless it is given a pin:

- `--trust-head <commit>` takes an id a teammate gives you, usually the head of their last verified sync.
- `--adopt-local` pins the local `HEAD`. You created the store or have inspected this clone. It is a statement, not a check, and the command says so.

The pin means "I vouch for everything up to and including this commit". Earlier commits are not verified, so an existing store with unsigned history keeps loading. The first commit after the pin is judged against the list in the pin. A first sync sets V only at step 7, so any failure before it leaves V unset and the next attempt needs the pin again. A receiver whose remote was replaced makes a fresh clone and pins again. MemForge has no reset command.

**History rewrite.** If H does not descend from V on the first-parent chain, `sync` refuses and names both commits. Local files are untouched.

### Merges and hosts

Only the first-parent chain is verified. A merge's signer vouches for its second parent: content on the second-parent side of a merge that a listed signer signs is covered by that signature.

A host that signs web or squash merges signs with its own key. A list that contains the host key trusts everyone the host lets merge. A host workflow is supported in a mode only when a real host-generated commit has been verified against it; until then the supported path is a locally signed merge. An `ssh` store cannot accept a GPG-signed host commit. The host SHOULD require review of changes to `.memforge/` (for example, with a code owners rule).

### Change notifications

A channel that tells receivers the shared branch moved MAY exist. It has no authority and carries no content. The receiver runs `sync`.

### What commit trust stops

For a store whose users run `sync`:

- A first-parent commit made by someone who has no listed signer.
- A rewrite of history already verified.
- A change to the list by a commit that a listed signer did not sign.
- In `ssh` mode, any attack that lies about a time.

### What commit trust does not stop

- A stolen or misused listed key. Removing it from the list stops later commits only. There is no reach-back and no revocation inside MemForge.
- Anyone who holds a listed key or can merge, since any listed signer can add another signer.
- A compromised host or host admin, and everyone the host lets merge.
- An agent that runs in a listed person's session. The commit that holds its memory carries that person's signature.
- Prompt injection written by a trusted signer.
- Proof of which person or agent wrote a memory after a squash merge or a copy.
- Rollback or deletion by a listed signer.
- A host with branch protection off, beyond detection by `sync`.
- Any content that reaches an agent by a path other than `sync`.

### Setup guidance (informative)

Three cases cover most stores. Full walkthroughs are in `docs/team-bootstrap.md`.

- **Two people on a private repository (`ssh`).** Each person sets `gpg.format ssh`, `user.signingkey` to their public key, and `commit.gpgsign true`. Both public keys go in `.memforge/allowed_signers`, one line each, in a signed commit. The creator runs `memforge sync --adopt-local`. The other person clones and runs `memforge sync --trust-head <commit>` with an id the creator gives. Host branch protection can need a paid plan on private repositories; without it, the only control is each person's `sync`. Merge locally and push.
- **A company with single sign-on and required signed commits.** The host requires signed commits, a code owners rule covers `.memforge/`, and the list is generated from the identity provider's key records. In `gpg` mode each receiver's keyring holds the signers' public keys. Receivers run `memforge sync --trust-head <commit>` once. The host enforces signing; `sync` enforces the committed list.
- **Keyless (`gitsign`).** `.memforge/allowed_identities` lists each certificate identity and issuer. Receivers install `gitsign` with their organization's trust configuration and run `memforge sync --trust-head <commit>`. Needs network access to the trust roots and the log.

## Security considerations

Commit trust (§"Commit trust (v1.0.0+)") is the one supported trust model. The boundary statements an operator needs:

1. **What is trusted.** The shared branch's signed history, the committed signer list, the git host's signing policy, and the people who can merge. `sync` checks the first-parent history against the list. It does not check what a host or a listed signer was allowed to do.
2. **What is checked, and where.** Only `sync` checks. Content that reaches an agent by any other path is not verified. The host's branch protection and required signed commits are the control for that path.
3. **Fail closed.** `sync` refuses on any commit that does not pass, on a list it cannot read, on a history rewrite, on a shallow range it cannot see, and on a mode it has not released. It has no override flag. Nothing is trusted by default.
4. **Same-user compromise.** The local state key and the throwaway verification directory live under the receiver's own account. An attacker with that account has local code execution and can pin past a refused commit; MemForge cannot prevent it.
5. **No secrets in memory.** MemForge is not a secrets store (see §"Expected content sensitivity").

## Privacy considerations

Commit signatures and signer lists reveal who signs the store: a list names keys, fingerprints or certificate identities, and every signed commit names its signer to anyone who can read the repository. Operators who want pseudonymous participation SHOULD use keys and identities that do not name the person.

The advisory fields `agent_session` and `on_behalf_of` are free text that every reader of the store sees. They MUST NOT carry secrets or personal data the store's readers should not see. Commit times and messages are visible to every reader of the repository.

## Known limitations

These are the residual risks of commit trust. They are stated here so a deployer decides with them in view. The living list, with the mechanisms not yet proven against the real tools, is `known-limitations.md` (a sibling file to this spec).

- **R1. Unverified content reaches agents by any path other than `sync`.** Plain `git pull`, `merge` or `checkout`, edits or new files in the work tree, and never running `sync` all put unverified content in front of an agent. MemForge does not stop it. The host's branch protection and required signed commits are the control.
- **R2. A listed key is trusted completely.** There is no revocation, no reach-back and no second approval inside MemForge. The trust base includes the host and the people who can merge. Agents commit under a person's signature.
- **R3. Verdicts can differ between receivers.** In `ssh` mode, verdicts agree only across receivers whose `git` and `ssh-keygen` executables behave the same; versions and SHA-256 values are printed, not pinned. In `gpg` and `keyless` modes two receivers can disagree on one commit, because of keyrings, revocation certificates, clocks, trust roots and network. `keyless` depends on `gitsign` and the network, and an outage refuses every commit.
- **R4. A pin vouches for earlier history without checking it.** A pin from a compromised teammate pins a compromised history. A local user can always clone again and pin past a refused commit, or edit local configuration. MemForge offers no command for it and cannot prevent it.
- **R5. A refused commit blocks every receiver** until the remote's owner removes it.
- **R6. Verification costs one `verify-commit` per commit in the range.**

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
<!-- markdownlint-disable MD029 -->

**Invariants 16 to 25 are retired.** These invariants belonged to the in-repo signing model removed in 1.0.0 (per-memory signatures, clock-skew windows, the operator registry, sender sequences, file-mode rules for key material, revocation commits, agent attestations). The numbers are not reused, so invariants 26 to 28 keep their numbers.
 A folder is never malformed for carrying `identity` or `signature` frontmatter (§"Attribution fields and legacy fields").

26. (v0.6.0+) The recall frontmatter fields are optional and, when present, well-typed: `triggers` MUST be a list of strings (malformed `triggers` is audit WARN `triggers_malformed`, and consumers fall back to derived triggers); `always` and `do_not_inject` MUST be boolean. A non-boolean `always` or `do_not_inject` is audit WARN (`always_malformed` / `do_not_inject_malformed`), NOT BLOCKER; consumers MUST treat the malformed value as the default `false` (no coercion of truthy strings). Absence is equivalent to `always: false`, `do_not_inject: false`, and derived-only triggers. These fields do not affect the well-formedness of any pre-v0.6.0 folder.
27. (v0.7.0+) **Frontmatter parses as a YAML mapping.** Wherever a memory file carries a *recognized* frontmatter block (an opening `---` fence with a closing `---` on its own line), the enclosed block MUST parse (via a YAML safe-loader) as a *mapping* of key/value pairs or be empty (an empty block is the empty mapping). A YAML parse error (most commonly an unquoted `:` followed by a space inside a value such as `description:` or `name:`) or a non-mapping scalar/list result is a HEAD-pure BLOCKER. This restates the parse requirement implicit in invariant 1 as a named, write-time-checkable property: the validate operation (§"Write-validation operation (v0.7.0+)") enforces exactly this subset eagerly at the write boundary, so the break is rejected before commit instead of surfacing at the next full audit. An opening `---` with no recognized closing fence, like the absence of any fence, is treated as no frontmatter block and is invariant 1's concern for memory files, not a violation of this invariant.
28. (v0.8.0+) **A rollup README reaches every sibling it hides.** For every rollup subfolder that contains a `README.md`, each sibling `.md` file in that subfolder (excluding `README.md` itself) MUST be referenced by at least one POINTER in that `README.md`, and every pointer in that `README.md` MUST resolve to a `.md` file that exists somewhere in the folder. A POINTER, for this invariant, is exactly one of two forms and nothing else: a wikilink `[[slug]]` (optionally `[[slug|label]]` or `[[slug#anchor]]`), resolving to `slug.md`; or a markdown inline link whose target ends in `.md`, `](path.md)` (optionally `](./path.md)` or with a trailing `#anchor`), resolving to that path's basename. The set is CLOSED so the check is deterministic and portable: any other textual mention of a filename, including a backticked bare filename in prose, is NOT a pointer and an implementation MUST NOT count it as one. Within those two forms an implementation MUST NOT require a particular list marker or line position, because a numbered-prose entry carrying an inline link is as valid as a bulleted one. An external URL that happens to end in `.md` is not a folder-relative pointer and is excluded. Pointers are read from PROSE ONLY: text inside a fenced code block, an inline code span, or an HTML comment MUST be excluded before extraction, in both directions. A link shown as a documentation example, or one left in a commented-out note, is not a membership claim, and leaving that unstated would let two conforming implementations disagree on the same folder. This closes the reachability hole left by invariants 7 and 8: those govern `MEMORY.md`, and by §"Rollup subfolders" item 4 a detail file is deliberately NOT pointed to from `MEMORY.md`, so the rollup `README.md` is a detail file's ONLY path to a reader. A README that omits a sibling makes that memory exactly as unreachable as a subfolder with no README at all, which the folder layout ("README.md, Required when subfolder exists") already treats as malformed. Conformance is decided by comparing the pointer SET against the sibling SET in both directions, never by comparing counts: a duplicate pointer alongside an omission yields equal counts while a memory is still lost. A pointer resolving to a file in a DIFFERENT subfolder, or at folder root, is a legitimate cross-reference and satisfies the resolvability direction; it does NOT satisfy the coverage direction for any sibling. Applies only where a rollup `README.md` is present.

<!-- markdownlint-enable MD029 -->

The `tools/memory-audit` script verifies these invariants plus health heuristics. v0.4.0+ adds the Tier 1 / Tier 2 split documented in §"Multi-agent concurrency". Commit trust (§"Commit trust (v1.0.0+)") is an operation on the shared branch, not a property of folder shape, so it adds no integrity invariant.

## Cross-folder references

The `references_global` and `referenced_by_global` fields support cross-folder relationships (per-cwd memory ↔ global memory). When a topic spans both folders:

- The per-cwd memory file declares `references_global: [<uid>]` pointing to the global memory it depends on.
- The global memory MAY declare `referenced_by_global: [<uid>]` for back-references (audit can also derive these).
- Audit tools verify cross-folder UID resolution and flag broken references.

## Versioning

**Current spec version**: 1.0.0.

The spec version lives in `spec/VERSION`. Breaking changes bump per semantic versioning applied to spec semantics:

- **Major**: invariants change in a way that existing well-formed folders can become malformed, or a normative section or operation is removed or replaced.
- **Minor**: new optional fields, new types, new conventions that existing folders remain compatible with.
- **Patch**: documentation or wording changes with no behavioral effect.

v0.3.0 was a minor bump (new optional fields + rollup-subfolder formalization). v0.4.0 was a major bump: required-field set expanded, byte-match CI tightened, multi-agent concurrency surface added. v0.5.0 was a minor bump that added the in-repo signing model (the `identity` and `signature` frontmatter fields, an operator registry, attestations, revocation and a messaging contract); v0.5.1 to v0.5.3 were patch bumps to it. All of that is removed in v1.0.0 (see below).

v0.6.0 is a minor bump: adds three OPTIONAL recall frontmatter fields (`triggers`, `always`, `do_not_inject`) and the §"Recall operation" contract. All three are optional, so every pre-v0.6.0 folder remains well-formed under v0.6.0 readers, and v0.6.0 folders remain readable by older tools (which ignore the unknown keys).

v0.7.0 is a minor bump: adds the §"Write-validation operation (v0.7.0+)" (the agent-neutral write-boundary gate) and the §"Audit waivers (R2; v0.7.0+)" allowlist, names integrity invariant 27 (frontmatter parses as a YAML mapping), and reconciles the `MEMORY.md` pointer/line caps to a single 180-byte/180-line source. No new REQUIRED frontmatter field and no existing-folder regression: invariant 27 only restates the parse requirement already implicit in invariant 1, so every well-formed pre-v0.7.0 folder remains well-formed under v0.7.0 readers, and the waiver file is optional (fail-closed when absent).

v0.8.0 is a MINOR bump, not a patch: it names integrity invariant 28 (a rollup README reaches every sibling it hides), and a folder that was well-formed under v0.7.x can be non-conformant under v0.8.0 if a rollup `README.md` omits a sibling. That is a new hard requirement, so SemVer requires a minor bump even though the change adds no surface and no REQUIRED frontmatter field. It closes a reachability hole: invariants 7 and 8 govern `MEMORY.md`, and by §"Rollup subfolders" item 4 a detail file is deliberately absent from `MEMORY.md`, so the rollup README is a detail file's only path to a reader, yet nothing previously required that README to actually reference its siblings. The pointer set is CLOSED to two forms (wikilink, markdown inline link), and pointers are read from prose only (fenced code blocks, inline code spans, and HTML comments are excluded), so the check is deterministic and portable across adapters. A pre-v0.8.0 folder whose rollup README omitted a sibling was already losing that memory silently; under v0.8.0 the loss is reported instead of hidden.

v0.6.1 is a patch bump: adds the §"Recall-readiness lint" section (lint dimensions, always-set budget guidance, a normative cloud-dispatch safety posture, informative consolidation orchestration) and the §"Tier and recall (clarification, v0.6.1+)" note (a live `tier: detail` memory is recall-eligible; the `index`/`detail` split governs the session-start `MEMORY.md` hotlist, not query-driven recall). Additive and backward-compatible: no frontmatter or normative-contract change, so every v0.6.0 folder remains conformant under v0.6.1 readers.

v1.0.0 is a major bump that deprecates the in-repo trust model. It removes the normative sections for per-memory signatures, the operator registry, agent attestations, sender sequences, key rotation and revocation, and the messaging adapter contract, and it replaces them with §"Commit trust (v1.0.0+)", where the signed commits of the shared branch carry trust and the organization owns every key. It is major because normative sections and operations are removed, not because a folder becomes malformed: every folder that was well-formed under v0.8.0 remains well-formed under v1.0.0. Files that carry `identity` and `signature` frontmatter keep loading, and the fields are preserved and ignored. It adds two advisory attribution fields (`agent_session`, `on_behalf_of`), retires integrity invariants 16 to 25, and updates the versioning, security, privacy and known-limitations sections. Implementations of the retired sections have nothing to migrate: see `docs/migrating-to-1.0.md`.

Adapters and tools SHOULD declare which spec version they target.

## Expected content sensitivity

MemForge is **not** a secrets store. Never put credentials, tokens, or private keys into memory files regardless of their `sensitivity` or `access` label. Encryption at rest is outside the spec; rely on the host's filesystem encryption (FileVault, LUKS, BitLocker).

The `sensitivity` and `access` frontmatter fields exist to let adapters make containment decisions about memories whose content is legitimately in the folder but whose exposure scope is narrower than the folder itself. Privileged legal material, pre-launch commercial strategy, attorney correspondence: these belong in the folder, but a cloud-IDE adapter should never export them.

Adapters MAY add encryption layers if they target a multi-developer or shared-workspace scenario, but the core format assumes plaintext-at-rest is acceptable for the content the format is designed to hold.

## Not in scope as of v1.0.0

- Specific RBAC enforcement (adapter responsibility, not spec).
- Encryption protocols (adapter responsibility).
- Specific embedding model selection for `dynamic_supplement` queries (Phase 1+ tool concern).
- Generator implementation (Phase 1+ tool, see `tools/memory-index-gen`).
- Centralized `decision_topic` taxonomy file. The format uses inline `topic_aliases:` from v0.4.
- Per-decision ledger (one file per topic with monotonic `resolution_version` + winner UID + losers). Deferred; the v0.4 layered Tier 1 + Tier 2 model plus config protection plus secure-mode deployment guidance covers the realistic threat model. Promote if dogfooding surfaces history-rewrite attacks.
- DAG cycle rejection on `replaces:` chain (audit warning first; future scope).
- UUIDv7 normative UID format for memory UIDs. Current `mem-YYYY-MM-DD-slug` continues.
- Vector-clock or sequence-number tie-breaker for memory updates (the per-memory `updated` timestamp remains the local tie-breaker).
- Auto-derive of `decision_topic` for legacy v0.3.x memories.
- Cryptographic immutability for snooze `created_by` or memory `owner` (best-effort provenance only).
- Cryptographic per-person or per-agent attribution inside MemForge, attribution that survives a squash merge, and any key lifecycle (generation, rotation, revocation, recovery, a registry). Keys belong to the organization's own identity, signing and git host controls.
- A real-time relay or messaging substrate. A notification that the shared branch moved MAY exist but has no authority (§"Commit trust (v1.0.0+)").
- Verification of content that reaches an agent by a path other than `sync`.
- Cross-machine merge resolution beyond exactly-one-active. Same as v0.3.x / v0.4: git merge with operator hand-resolution.
- CRDT semantics. Future ADR if surface-not-resolve fails under load.

## Versioning history

Entries for v0.5.0 through v0.5.3 describe the in-repo trust model that v1.0.0 removes.

- v0.1.0 , initial format; flat folder, name+description+type required.
- v0.2.0 , sensitivity classification (4 levels) + consumer obligations.
- v0.3.0 , schema expansion (uid, tier, tags, owner, status, last_reviewed, etc.); rollup-subfolder formalization; access labels; cross-folder references; tag taxonomy.
- v0.4.0 , major bump: `uid`, `tier`, `tags`, `owner`, `status`, `created` required (was optional in v0.3.x). v0.3.x files load in degraded mode. Reader contract tightened for byte-match CI on generated `MEMORY.md`. New §"Multi-agent concurrency" section adds five frontmatter keys (`decision_topic`, `replaces`, `superseded_by`, `topic_aliases`, `ever_multi_member`), a snooze record (`.memforge/snoozes/<topic>.yaml`), a config file (`.memforge/config.yaml`), the resolve operation contract, the canonical reader-side competing-claim block, and a layered Tier 1 + Tier 2 audit rule set. Status enumeration is now strictly enforced (BLOCKER on any value outside the six listed). Sensitivity enforcement (`§"Sensitivity enforcement"`) adds three default-on, operator-disable-able checks (export-tier gate, DLP label/content cross-check, conformance fixtures) with a hard floor at `privileged`. Closes the v0.3.x §"Not in scope" deferral on multi-user concurrency.
- v0.5.0 , minor bump: extends single-operator multi-agent format to multi-identity team-scale memory with cryptographic attribution and a real-time messaging substrate. Eight new sections: §"v0.5.0 surface map", §"Multi-identity primitives", §"Cryptographic attribution", §"Operator identity + cross-store references", §"Messaging adapter contract" (with subsections including the normative §"Receiver state", §"Connection security" mandating wss:// + per-operator strong auth, and circuit-breaker), §"Key lifecycle + revocation" (with mandatory cool-down on key rotation closing the rotation-as-privilege-escalation attack), §"Security considerations" (eleven enumerated boundary statements), §"Known limitations". New REQUIRED v0.5+ frontmatter: `identity` + `signature` (v0.4 frontmatter remains valid; v0.5 readers tag unsigned v0.4 memories as read-only-untrusted). WebSocket locked as v0.5.0 messaging substrate (OpenAI Responses API Feb 23 2026 launch alignment); multi-server hard-stop for v0.5.0. Sender-uid format `<operator-uuid>:<32-byte-hex>` mandatory; sender-sequence + signed checkpoints every 100 sequences or 24 hours. Signing-time-aware revocation verification + `first_seen_at` clock-skew guard (default ±10 min) close the coordinated-backdate attack class. `revoked_at` clock-skew guard closes the immortal-revocation attack. Recovery-secret filesystem mode (0600/0700) + uid-ownership check + SHA256 content-integrity check anchored in the signed operator-registry + mandatory backup acknowledgment; persistent startup WARN until hardware-backed install. Sparse-checkout / shallow-clone fallback uses TOFU + fast-forward-only + signature verification on revocation commits. Reference CLI binaries (`memforge init-operator`, `operator-registry`, `revoke-memories`, etc.) ship in v0.5.1. v0.4 folders remain well-formed under v0.5 readers. v0.5.0 ships with no BLOCKER-class known limitations; residual MAJORs + MINORs documented at `v0.5.0-known-limitations.md`.
- v0.5.1 , patch bump: closes the v0.5.0 agent-session-attestation content-scope MAJOR with normative `nonce` + `expires_at` + `capability_scope` content (§"Agent session attestation content scope (v0.5.1+)") and closes 3 v0.5.0 MINORs: agent-session-id format regex (in §"Frontmatter additions (v0.5.0+)"); cross-cutting fail-closed posture documentation as a single 29-item operator reference (§"Cross-cutting fail-closed posture (v0.5.1+)"); privacy considerations subsection with 7 boundary statements + out-of-scope list (§"Privacy considerations (v0.5.1+)"). Reference CLI ships: `memforge init-operator`, `init-store`, `operator-registry add|verify|remove|fresh-start`, `rotate-key`, `revoke`, `revocation-snapshot`, `memories-by-key`, `revoke-memories`, `upgrade-v04-memories`, `revoke-cache-refresh`, `messaging-doctor`, `recovery-init`, `recovery-backup-confirm`, `attest-agent`. New integrity invariants 23-25 (agent-session attestation verification, capability-scope enforcement, agent-session-id format). v0.5.0 folders remain well-formed under v0.5.1 readers; v0.5.0 attestation files lacking v0.5.1 required content fields are accepted with one-time MAJOR `v05_attestation_incomplete_content` per file until re-issued via the v0.5.1 reference CLI.
- v0.5.2 , patch bump: closes 2 BLOCKERs + 1 MAJOR surfaced by the post-v0.5.1 retrospective code + spec panel (critic via gemini-pro + threat-modeler via gemini-pro) AND adds native Windows support via a platform-agnostic ACL-based restriction abstraction. Canonical-form Unicode NFC normalization MUST on signed envelopes (§"Signed envelope scope (normative)"); closes the repudiation vector where visually-identical inputs in different normalization forms produced different signatures. Seen-nonce set bounding promoted from MAY to SHOULD with explicit GC contract; closes the unbounded-set DoS. Reference implementation atomicity: `write_secure_yaml` + `write_secure_bytes` now use O_CREAT|O_EXCL + atomic rename to close the TOCTOU window between file create and chmod. **Cross-platform secure-file abstraction:** §"Operator-identity file (per-machine)" + integrity invariant 21 are restated platform-agnostically; POSIX mode 0600/0700 and Windows NTFS ACLs (via `icacls`) are both normative implementations of the "file restricted to current owner" contract. v0.5.1 folders remain well-formed under v0.5.2 readers; the Unicode normalization change is backward-compatible for any input that was already in NFC form (the common case for ASCII + standard composed Unicode).
- v0.5.3 , patch bump: closes the 3 remaining MAJORs from the v0.5.2 retrospective threat-modeler pass. Registry-layer cool-down enforcement: `memforge.registry.verify_signing_key_acceptable` now refuses a signing key in cool-down regardless of CLI consumer; CLI-only enforcement was a bypass surface. Bounded git-log walk: `walk_revocation_set` now streams output line-by-line with caps on commits-walked + bytes-read (default 100k commits / 100 MB; operator-configurable). TOCTOU-safe read on every secure-file path: `_security.secure_read_text` + `secure_read_bytes` use `O_NOFOLLOW` + post-open fd `fstat` verification on POSIX; path-level verify on Windows. v0.5.2 folders remain well-formed under v0.5.3 readers; no spec-frontmatter or normative-contract regressions.
- v0.6.0 , minor bump: query-triggered recall. Three new OPTIONAL frontmatter fields (`triggers`, `always`, `do_not_inject`) plus the §"Recall operation" section defining recall by frontmatter contract + post-conditions (UI-neutral; matching, index, and injection are reference-implementation + adapter concerns per the loading-semantics principle). Recall surfaces memory `description` text (never bodies), honors `sensitivity` / `access` + the live-set partition, and is fail-open-empty (never blocks the operation it serves). Reconciles with the reserved outbound `dynamic_supplement` field. Reference implementation: `memory-index-gen` emits a derived recall inverted-index artifact and a thin `memory-recall` reader does query-time matching for latency-sensitive per-query hooks. All fields optional, so pre-v0.6.0 folders remain well-formed and older tools ignore the new keys. New integrity invariant 26.
- v0.8.0 , minor bump: rollup reachability. Adds integrity invariant 28 (a rollup `README.md` MUST reference every sibling `.md` in its subfolder, and every pointer in it MUST resolve somewhere in the folder). Minor rather than patch because a folder well-formed under v0.7.x can be non-conformant under v0.8.0. Invariants 7 and 8 only govern `MEMORY.md`, and a detail file is deliberately absent from `MEMORY.md`, so the rollup README is its only path to a reader. The pointer set is CLOSED to wikilink and markdown-inline-link forms so the check is deterministic; a backticked bare filename in prose is explicitly NOT a pointer. Conformance is a SET comparison in both directions, never a count comparison. Applies only where a rollup README is present.
- v0.7.0 , minor bump: write-boundary hardening. Adds the §"Write-validation operation (v0.7.0+)" (`memory-validate`: the agent-neutral pre-write gate that rejects a malformed frontmatter file -- the recurring unquoted-colon break -- at the write boundary instead of at the next audit), the §"Audit waivers (R2; v0.7.0+)" allowlist (`.memforge/audit-waivers.yaml`: an explicit, reported, fail-closed suppression of immutable migration-era Tier 2 findings so a clean corpus audits to zero), and integrity invariant 27 (a present `---` fence MUST parse as a YAML mapping). The `MEMORY.md` pointer-byte + line caps are single-sourced (both 180) so audit, validate, and index-gen cannot drift. No new REQUIRED field; invariant 27 restates invariant 1's implicit parse requirement, so every well-formed pre-v0.7.0 folder stays well-formed and the waiver file is optional (fail-closed absent).
- v0.6.3 , patch bump: deterministic pointer-hook truncation. The index generator MUST keep every emitted `MEMORY.md` pointer line within the 180-byte cap by truncating the hook on a UTF-8 boundary + appending `...`; the authoritative full `description` stays in frontmatter + the recall index, so truncation is lossless for recall (§"MEMORY.md format"). Closes the long-standing gap where a rich `description` produced an over-cap generated pointer line, reconciling the generator with the existing 180-byte audit check. No frontmatter or normative-contract change; every v0.6.2 folder remains conformant under v0.6.3 readers and regenerating an index only shortens over-cap hooks. (Spec VERSION jumps 0.6.2 -> 0.6.3; there is no 0.6.2-internal change.)
- v0.6.1 , patch bump: adds the §"Recall-readiness lint" section (defines the lint operation by the dimensions it scores and a normative cloud-dispatch safety posture, NOT by a scoring algorithm; lint is advisory, read-only, and never a hard gate) plus the §"Tier and recall (clarification, v0.6.1+)" note (a live `tier: detail` memory is recall-eligible on the same terms as an `index`-tier memory; the `index`/`detail` split governs the session-start `MEMORY.md` hotlist, not query-driven recall). Reference implementation: the `memory-lint` reader scores recall-readiness + token cost, and `memory-audit` adds spec-mandated recall-field WARNs plus an advisory always-set budget WARN. No frontmatter or normative-contract change, so every v0.6.0 folder remains conformant under v0.6.1 readers.
- v1.0.0 , major bump: commit trust replaces the in-repo trust model. Removes the sections "v0.5.0 surface map", "Multi-identity primitives" (except its frontmatter fields, replaced by the advisory attribution fields), "Signed envelope scope", "Mixed v0.4 / v0.5 deployment posture", "Agent session attestation content scope", "Cryptographic attribution", "Operator identity + cross-store references", "Messaging adapter contract", "Key lifecycle + revocation" and "Cross-cutting fail-closed posture". Replaces "Security considerations", "Privacy considerations" and "Known limitations". Adds §"Commit trust (v1.0.0+)": per-mode signer lists committed to the store (`ssh`, `gpg`, `keyless`), mode derived from the list in a commit's first parent, isolated verification, the `memforge sync` operation with ordered steps, failure reasons and exit codes, one local state key, first-sync pins, and an explicit list of what it does not protect. Adds the optional advisory fields `agent_session` and `on_behalf_of`. Marks `identity` and `signature` legacy (preserved, never verified or written). Retires integrity invariants 16 to 25. Every folder well-formed under v0.8.0 remains well-formed.

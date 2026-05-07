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
---

<body in markdown>
```

Required in v0.3.0: `name`, `description`, `type`. All other fields are optional in v0.3.0 to allow gradual migration. v0.4.0 will require `uid`, `tier`, `tags`, `owner`, `status`, `created`. The `sensitivity` field remains independent (separate from `access`); when absent, consumers MUST treat the memory as `internal`.

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
11. If a file has `status: superseded`, a `superseded_by` field MUST be set with at least one valid uid (after a transition window per the time-to-archive SLO).

The `tools/memory-audit` script verifies these invariants plus health heuristics.

## Cross-folder references

The `references_global` and `referenced_by_global` fields support cross-folder relationships (per-cwd memory ↔ global memory). When a topic spans both folders:

- The per-cwd memory file declares `references_global: [<uid>]` pointing to the global memory it depends on.
- The global memory MAY declare `referenced_by_global: [<uid>]` for back-references (audit can also derive these).
- Audit tools verify cross-folder UID resolution and flag broken references.

## Versioning

The spec version lives in `spec/VERSION`. Breaking changes bump per semantic versioning applied to spec semantics:

- **Major**: invariants change in a way that existing well-formed folders can become malformed.
- **Minor**: new optional fields, new types, new conventions that existing folders remain compatible with.
- **Patch**: documentation or wording changes with no behavioral effect.

v0.3.0 is a minor bump (new optional fields + rollup-subfolder formalization). v0.4.0 will be a major bump (will require `uid`, `tier`, `tags`, `owner`, `status`, `created`).

Adapters and tools SHOULD declare which spec version they target.

## Expected content sensitivity

MemForge is **not** a secrets store. Never put credentials, tokens, or private keys into memory files regardless of their `sensitivity` or `access` label. Encryption at rest is outside the spec; rely on the host's filesystem encryption (FileVault, LUKS, BitLocker).

The `sensitivity` and `access` frontmatter fields exist to let adapters make containment decisions about memories whose content is legitimately in the folder but whose exposure scope is narrower than the folder itself. Privileged legal material, pre-launch commercial strategy, attorney correspondence: these belong in the folder, but a cloud-IDE adapter should never export them.

Adapters MAY add encryption layers if they target a multi-developer or shared-workspace scenario, but the core format assumes plaintext-at-rest is acceptable for the content the format is designed to hold.

## Not in scope for v0.3.0

- Specific RBAC enforcement (adapter responsibility, not spec).
- Encryption protocols (adapter responsibility).
- Specific embedding model selection for `dynamic_supplement` queries (Phase 1+ tool concern).
- Generator implementation (Phase 1+ tool, see `tools/memory-index-gen`).
- Multi-user concurrency semantics (Phase 2+ concern).

## Versioning history

- v0.1.0 — initial format; flat folder, name+description+type required.
- v0.2.0 — sensitivity classification (4 levels) + consumer obligations.
- v0.3.0 — schema expansion (uid, tier, tags, owner, status, last_reviewed, etc.); rollup-subfolder formalization; access labels; cross-folder references; tag taxonomy.
- v0.4.0 (planned) — make uid + tier + tags + owner + status + created required.

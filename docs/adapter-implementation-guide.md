# MemForge adapter implementation guide

**Status:** informative (not normative).
**Spec target:** v0.4.0+.

This guide is for adapter authors integrating MemForge into a coding agent, IDE, CLI, or web UI. It covers UX patterns, deployment guidance, and a conformance test approach. The normative spec lives in [`spec/SPEC.md`](../spec/SPEC.md); this document explains how to implement it cleanly across user surfaces.

## Audience

You are writing or maintaining one of:

- A Claude Code skill, Cursor rule, Continue.dev slash command, Aider macro, vim/emacs plugin, web UI, or any other surface that wraps the resolve operation.
- A CI integration that runs `memory-audit` on a memory repo.
- A new adapter for a memory-storage backend (different filesystem layout, cloud sync, etc.) that still produces v0.4-conformant data.

If you're an end user just running `memforge-resolve` from the shell, you don't need this document.

## The two contracts an adapter touches

1. **The data contract.** What's on disk: frontmatter shape, `MEMORY.md` fenced blocks, `.memforge/snoozes/<topic>.yaml`, `.memforge/config.yaml`, and the integrity invariants `memory-audit` enforces.
2. **The operation contract.** What a resolve, snooze, alias-edit, or config-edit does on disk and in git history (commit message prefix, scope, atomic commit).

Adapters implement UX on top of these contracts. UX is non-normative; the contracts are normative. As long as your adapter produces conformant data and operations, your users get the spec's authority guarantees regardless of UX idiom.

## Implementation surfaces

### CLI reference (load-bearing)

The package ships these binaries (after `pip install ildan-memforge`):

- `memforge-resolve <topic>` — runs the resolve operation. Interactive by default; non-interactive via `--winner-uid <uid>`. Writes the atomic `memforge: resolve <topic>` commit.
- `memory-audit` — runs Tier 1 + Tier 2 audit. Use as a CI gate.
- `memforge-migrate-claim-block` — rewrites legacy per-group `status:` to canonical `state:` in `MEMORY.md`. One-shot fixer for v4-shape data.
- `memory-promote`, `memory-rollup`, `memory-cluster-suggest`, `memory-link-rewriter`, etc. — pre-existing v0.3.x tools that work unchanged.

Every other adapter is, ideally, a thin wrapper over these binaries. If your UX is shell-able, just shell out.

### Claude Code skill

A reference skill ships at `~/.claude/skills/consolidate-memory/SKILL.md` (in the operator's home, not in the public repo). The skill walks the memory folders to find competing-claim groups, asks the operator to pick a winner in chat, then invokes `memforge-resolve <topic> --winner-uid <picked-uid>`. The CLI handles all mutations + commit.

The trigger phrasing in the skill description tells Claude when to invoke (`/consolidate-memory`, `resolve competing claims on <topic>`, etc.). Adapt for your terminology.

### Cursor / Continue.dev / Aider

These tools support project-level slash commands or rules. Add a project rule that runs `memforge-resolve <topic>` (interactive) or implement a slash command that prompts for the topic + winner inside the tool's chat UI, then invokes `memforge-resolve <topic> --winner-uid <picked-uid>`.

### Plain shell, vim, emacs

Direct invocation is fine. Nothing to wrap.

### Web UI

POST-to-shell pattern. The button POSTs `{topic, winner_uid}` to a server-side handler that invokes `memforge-resolve --winner-uid <uid>`. The server-side handler runs in the same git working tree as the memory folder so the atomic commit lands correctly.

## UX patterns for the resolve operation

When you build a UI for `memforge-resolve`, follow these patterns:

### 1. Refuse to silently auto-resolve

**Never pick a winner without explicit operator confirmation.** This is the single most important UX rule. The whole point of the resolve operation is operator-mediated; an adapter that auto-resolves breaks the contract.

Acceptable: suggest a winner ("the most recently updated member, by the same owner, with the strongest body content"). Then ask. Don't just apply.

### 2. Surface every live member side-by-side

Show: `uid`, `owner`, `updated`, the first non-empty body line (truncated), and the file path. Operators decide based on body content; don't hide it.

Sort: most-recently-updated first. Tie-break by `uid` ascending.

### 3. Offer "write a new memory" as an option

Sometimes the right resolution isn't to pick an existing claim but to write a synthesis. Your UX should support this. The new memory must inherit the same `decision_topic` and list every prior member's UID in `replaces:`.

### 4. Confirm before committing

After mutations are applied but before the atomic commit fires, show the operator a one-line summary: `winner=<uid>, superseded=<uids>, snooze deleted: <yes/no>`. Last chance to abort.

### 5. After commit: run audit

Always run `memory-audit --path <root>` after a resolve. Expect zero new BLOCKERs. If any fire, surface them — the resolve ran but the result has a problem the spec catches.

### 6. Interaction with snooze

If the topic has an active snooze, the competing-claim block is suppressed (operators see only a "snoozed" line, not the full block). Two options for handling this in the UX:

- **Block resolve while snoozed**: refuse the resolve operation; surface the snooze to the operator and prompt for cancellation first. Strict.
- **Allow resolve to override snooze**: the resolve commit deletes the snooze file as part of its atomic transaction. Permissive.

The spec allows both. Pick the one that matches your operator's expectations. Default to strict.

## Deployment guidance

The spec acknowledges that Tier 2 (commit-log) audit invariants can be partially evaded by a writer with force-push privileges who rewrites the entire audit window. Tier 1 (HEAD-pure) invariants survive force-push, but a sophisticated rewrite can produce a HEAD that satisfies all Tier 1 rules while erasing the commit-log evidence of how it got there.

The mitigation is at the git provider, not in MemForge. If your deployment has any of: untrusted writers, multi-tenant memory repos, regulated content, or any setting where adversarial writers are a realistic threat, configure the canonical branch with:

1. **Branch protection** — no force-push on the canonical branch; require pull-request review.
2. **Required signed commits** — author identity is harder to forge.
3. **Required status checks** — `memory-audit` runs on every PR + on the canonical branch tip; PR cannot merge if audit fires BLOCKERs.

### Secure-mode adapter conformance (informative)

Adapters MAY claim secure-mode conformance. A secure-mode adapter MUST:

1. At startup, detect (via the git provider's API where available) whether the canonical branch has the three protections above.
2. Emit a startup MAJOR if any is missing.
3. Refuse to perform any resolve operation if branch protection is absent. Operator override via `--insecure` flag, recorded in commit metadata.

Implementation note: GitHub, GitLab, and Bitbucket each have different branch-protection APIs. A reference implementation for GitHub via the `gh` CLI:

```bash
gh api "repos/$REPO/branches/$BRANCH/protection" \
  --jq '.required_pull_request_reviews and .required_signatures.enabled and .allow_force_pushes.enabled == false'
```

Adapters that do NOT claim secure-mode MUST emit an informative startup notice: "Operating without git-layer protection; Tier 2 audit guarantees reduced. Solo-operator deployments are explicitly not in secure mode."

Solo-operator deployments running without branch protection are explicitly OK to run; the operator accepts the residual force-push threat as part of running solo. Don't refuse to start in solo mode just because branch protection is missing — that's a false-positive.

## Conformance test approach

Before declaring your adapter v0.4-conformant, run the following test scenarios. All produce known states the spec catches; passing tests verify that your UX surfaces the right data and that mutations land in the right commit shape.

### T1 — Two competing memories, pick existing winner

1. Create two memories with the same `decision_topic`, both `status: active`.
2. Invoke your adapter's resolve flow.
3. Operator picks member 1 as winner.
4. **Assert**: post-state has member 1 active, member 2 superseded with `superseded_by: [member-1-UID]`, member 1's `replaces:` lists member 2's UID, both have `ever_multi_member: true`, single commit with message `memforge: resolve <topic>`, `memory-audit` passes.

### T2 — Two competing memories, write new winner

1. Same setup as T1.
2. Operator picks "write new memory" with body content.
3. **Assert**: post-state has the new memory active with `decision_topic == <topic>` and `replaces: [m1-UID, m2-UID]`; m1 and m2 superseded with `superseded_by: [new-UID]`; commit message and audit as above.

### T3 — Single live member (no-op)

1. One memory with `decision_topic: foo`, `status: active`. No competing members.
2. Invoke resolve flow.
3. **Assert**: adapter exits cleanly with "nothing to resolve" message; no mutations applied; no commit.

### T4 — Snooze present (strict mode)

1. Two competing memories + a snooze file at `.memforge/snoozes/<topic>.yaml` with `snoozed_until` in the future.
2. Invoke resolve flow.
3. **Assert**: adapter refuses to proceed (in strict mode); surfaces the snooze; operator prompted to cancel snooze first.

### T5 — Cross-root members (must error)

1. Place one member in `~/.claude/global-memory/` and one member in `~/.claude/projects/<hash>/memory/`, same `decision_topic`.
2. Invoke resolve flow.
3. **Assert**: adapter errors with "members span multiple memory roots"; cross-root resolution out of scope for v0.4.

### T6 — Audit catches asymmetric supersession

1. Manually edit a memory: `status: superseded` with `superseded_by: [does-not-exist]`.
2. Run `memory-audit`.
3. **Assert**: BLOCKER fires citing the dangling reference.

### T7 — Audit catches alias non-mutuality

1. Anchor of topic A lists topic B in `topic_aliases:`. Anchor of topic B does NOT list A.
2. Run `memory-audit`.
3. **Assert**: WARN fires; alias is inactive; both groups continue to be treated as separate.

### T8 — Migrate-claim-block on legacy data

1. Hand-craft a `MEMORY.md` with `status: competing` (legacy v4 shape) at the per-group level inside the fenced block.
2. Run `memforge-migrate-claim-block`.
3. **Assert**: per-group `status:` rewritten to `state:`; per-member `status:` (under `members:`) untouched; running again is a no-op.

A reference test fixture set is at `tests/conformance/` (TODO; populated as adapters land).

## Authority threat model summary (for adapter authors)

If you're integrating MemForge as the substrate for a multi-agent system, you should understand the threat model. Short version:

- Tier 1 invariants are HEAD-pure. They survive any history rewrite. If your adapter trusts `memory-audit`'s pass on HEAD, you can trust the data shape.
- Tier 2 invariants depend on git history. Force-push erases history. Defending against force-push is the git provider's job (branch protection + signed commits).
- Solo-operator deployments accept the residual force-push threat. Multi-writer / regulated / adversarial deployments must enable secure-mode.
- The resolve operation is the only path that mutates resolution state. All other writes are advisory until the resolve operation ratifies them.
- `created_by` on snoozes is best-effort provenance. Pure git allows author amend; adapters seeking unforgeable provenance should require signed commits.

## Pointers

- Spec: [`spec/SPEC.md`](../spec/SPEC.md), in particular §"Multi-agent concurrency: competing claims" and §"Integrity invariants".
- Reference CLI: [`src/memforge/cli/resolve.py`](../src/memforge/cli/resolve.py).
- Audit: [`src/memforge/cli/audit.py`](../src/memforge/cli/audit.py) + [`src/memforge/cli/_concurrency_audit.py`](../src/memforge/cli/_concurrency_audit.py).
- Migration fixer: [`src/memforge/cli/migrate_claim_block.py`](../src/memforge/cli/migrate_claim_block.py).

---
name: consolidate-memory
description: Review and clean up MemForge folders. Runs memory-audit + memory-dedup on configured folders, presents findings, and applies fixes one at a time with the user's approval. Invoke on "/consolidate-memory", "clean up memory", "review memory", "consolidate memory", "memory health check", or equivalent.
---

# Consolidate Memory

Periodic housekeeping pass over the MemForge folders. Runs the audit + dedup tools, surfaces every actionable finding, and applies approved fixes one at a time. Never batch-edits; the user approves each change.

Distinct from session-handoff workflows, which operate on episodic resume folders, not on the semantic memory folders.

## Scope

Operates on these folders by default (the MemForge tooling defaults):

- `~/.claude/projects/${USER}-claude-projects/memory/` (per-cwd memory)
- `~/.claude/global-memory/` (global memory)

The user may scope to a single folder with "just global memory" or "just the per-cwd memory" at invocation time, or pass an explicit `--path` to either tool.

## Steps

1. **Run `memory-audit`.** For each target folder, run `memory-audit --path <dir> --strict` via Bash. Capture violations and health warnings.

2. **Run `memory-dedup`.** For each target folder, run `memory-dedup --path <dir>` via Bash. Capture any candidate duplicate groups. (`memory-dedup` defaults to local-only mode; the user must opt in to a cloud dispatcher.)

3. **Build a consolidated review list.** Group the findings by folder, then by category:
   - **Integrity** (must-fix): orphan files, orphan pointers, missing frontmatter, invalid types, empty bodies, `MEMORY.md` cap violations.
   - **Health** (should-fix): feedback / project memories missing `**Why:**` or `**How to apply:**`, stale files (over 90 days mtime).
   - **Dedup** (judgment call): candidate duplicate groups flagged by the LLM.

   Show the user the list grouped this way before proposing any action.

4. **Work through findings one at a time.** For each finding:
   - Propose a specific fix (for example, "delete the orphan pointer `foo.md` from global MEMORY.md line 42" or "merge `feedback_A.md` into `feedback_B.md` and archive `feedback_A.md`").
   - Ask the user: apply, modify, or skip.
   - If apply: edit the file(s) directly (the auto-commit hook captures each change).
   - If modify: incorporate the user's adjustment, then apply.
   - If skip: move on.

   **Never batch-apply fixes.** One at a time; wait for the user's call.

5. **Re-run `memory-audit --strict` at the end.** Confirm zero integrity violations. If any remain, flag them and ask how to proceed.

## Guardrails

- **Never delete a memory file.** Prefer archiving: move to `<folder>/archive/` so the entry is excluded from audit recursion but remains recoverable.
- **Never auto-merge duplicates.** Dedup verdicts are judgment calls; the user decides every merge.
- **Respect the auto-commit hook.** Every `Write` / `Edit` inside a memory folder produces a git commit via the PostToolUse hook. Don't bypass it with `git commit --amend` or `--no-verify`; the commit-per-change is the audit trail.
- **No new frontmatter without intent.** If you're backfilling `**Why:**` / `**How to apply:**` lines, ask the user what the actual reason was; don't invent one from context.

## When NOT to invoke

- Mid-task context reset: use a session-save / handoff workflow instead.
- Fresh audit result with no violations and no dedup candidates: nothing to consolidate; tell the user the folder is clean.

## Related

- `tools/memory-audit` — integrity + health checks driving step 1.
- `tools/memory-dedup` — LLM-backed dedup driving step 2.
- `tools/memory-promote` — use this if a finding is "this is cross-folder, should be global"; promotes per-cwd → global with pointer fixup.
- `spec/SPEC.md` — canonical format reference. Every fix should leave the folder conformant.

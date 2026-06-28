# adapters/claude-code

Claude Code adapter for MemForge. Loads a MemForge folder via the `autoMemoryDirectory` setting in `~/.claude/settings.json`, and versions it with an auto-commit hook so every change is captured as a git commit.

## Setup

1. Pick a location for your memory folder (typically `~/.claude/projects/<workspace>/memory/` for per-cwd memory, or `~/.claude/global-memory/` for cross-cwd memory).
2. Create the folder and seed a `MEMORY.md` index file.
3. Add to `~/.claude/settings.json`:
   ```json
   {
     "autoMemoryDirectory": "~/.claude/projects/<workspace>/memory/"
   }
   ```
4. Install the auto-commit hook: see `hooks/memory-auto-commit.sh`. Register it under `PostToolUse` in `~/.claude/settings.json` matching `Write|Edit`.
5. `git init` in the memory folder so the hook has somewhere to commit.

Restart Claude Code for the settings change to take effect.

## Hooks

### Auto-commit hook

`hooks/memory-auto-commit.sh` runs after every `Write` or `Edit` on a file under a memory folder. It stages everything under that folder and writes one git commit per event. Paths are derived from `$HOME` / `$USER` at runtime so the same hook works on any machine.

Matches two folders by default:
- `$HOME/.claude/projects/${USER}-claude-projects/memory/` (per-cwd memory)
- `$HOME/.claude/global-memory/` (global memory)

Both must be git-initialized for the hook to commit. Edit the hook to add other folders.

Register under `PostToolUse` with matcher `Write|Edit`.

### Read-tracker hook

`hooks/memory-read-tracker.sh` runs after every `Read` of a memory file. It updates a sidecar ledger at `<memory-folder>/.last_used.json` with the current UTC timestamp for that filename. The ledger is gitignored so it never enters version history.

Memory files that have never been read (missing from the ledger) and memory files last read long ago are surfaced by `memory-audit`'s staleness check, which prefers the ledger over filesystem mtime when the ledger is present.

CC's automatic session-start memory load does NOT fire a `Read` tool event. Only explicit agent reads update the ledger. That's intentional — rote session loading is not meaningful "use" for the staleness signal.

Register under `PostToolUse` with matcher `Read`.

### Recall hook (spec v0.6.0)

`hooks/memory_recall_hook.py` runs on every user prompt (`UserPromptSubmit`) and injects the descriptions of the memories whose triggers match the prompt, so the agent recalls the right memory at the right moment instead of relying on a bulk-loaded `MEMORY.md`. It invokes the installed `memory-recall` reader (spec v0.6.0 §"Recall operation") via `subprocess` (no shell), which reads a precompiled recall index. It is fail-open-empty: any failure prints nothing and never delays the prompt (a subprocess timeout guards latency). It is a Python hook (not bash) for Windows portability and to avoid shell-parsing fragility. Injected descriptions are wrapped in an untrusted-context preamble so the agent treats recalled text as reference, not instructions.

Keep the recall index fresh one of two ways:

- The auto-commit hook above already calls `memory-recall --rebuild` after each memory write (recall-index-only refresh; it does not regenerate `MEMORY.md`).
- Or rebuild explicitly alongside the human index: `memory-index-gen --with-recall-index`.

The recall index is a derived build artifact at `<memory-folder>/.memforge/recall-index.json`; it carries only memory descriptions (never bodies) and may be regenerated at any time.

Register under `UserPromptSubmit`.

### Write-boundary gate hook (spec v0.7.0)

Claude Code is the one adapter that can reject a malformed memory write *before the bytes hit disk* (Tier A in the adapter guide), via a `PreToolUse` hook matching `Write|Edit`. The shim reconstructs the post-write content (Write = the `content` field; Edit = the current file with `old_string` -> `new_string` applied) and pipes it to the `memory-validate` primitive `memforge.frontmatter.validate_frontmatter`; on a frontmatter-parse failure it returns `permissionDecision: deny` with the reason. It MUST fail open on any internal error so the gate never wedges the editor.

This replaces the interim hand-written CC frontmatter hook: the parsing now lives in the installed package (shared with the git pre-commit gate every other IDE uses), so the rule tracks spec updates. The reference shim + the universal git pre-commit fallback are in [`../../docs/adapter-implementation-guide.md`](../../docs/adapter-implementation-guide.md) §"Write-boundary gate".

Register under `PreToolUse` with matcher `Write|Edit`.

### Settings.json registration

In `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [{
          "type": "command",
          "command": "/absolute/path/to/hooks/memory-auto-commit.sh",
          "timeout": 10
        }]
      },
      {
        "matcher": "Read",
        "hooks": [{
          "type": "command",
          "command": "/absolute/path/to/hooks/memory-read-tracker.sh",
          "timeout": 5
        }]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [{
          "type": "command",
          "command": "python3 /absolute/path/to/hooks/memory_recall_hook.py",
          "timeout": 10
        }]
      }
    ]
  }
}
```

Restart Claude Code for the registration to take effect.

## Honoring sensitivity

Claude Code loads `MEMORY.md` verbatim at session start; it does not filter by `sensitivity` label on its own. To exclude `restricted` or `privileged` memories from sessions, pre-generate a filtered index and point `autoMemoryDirectory` at a folder containing the filtered version:

```bash
memory-index-gen --print --viewer-tier internal \
  --path ~/.claude/global-memory \
  > ~/.claude/global-memory/MEMORY.md
```

Re-run when memory changes (or wire it into the auto-commit hook).

### v0.4 enforcement

When the CC adapter exports memory to external surfaces (cloud-IDE bridges, shared workspaces, AGENTS.md generation), run the v0.4 enforcement gates:

```bash
memory-audit --export-tier=internal --strict
memory-dlp-scan --memory-folders --strict
```

The audit's export-tier gate fails BLOCKER on declared-tier > export-tier; the DLP scan's cross-check fails BLOCKER on body content whose implied tier exceeds the declared label. Both have config disable knobs in `.memforge/config.yaml`, except for `privileged` which is a hard floor.

Solo-operator deployments accept the residual git-layer threat and typically run with default config (everything default-on). Multi-writer or regulated deployments should also wire the conformance suite at `tests/conformance/sensitivity/` into CI.

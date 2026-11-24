# adapters/claude-code

Claude Code adapter for MemForge. Loads a MemForge folder via the `autoMemoryDirectory` setting in `~/.claude/settings.json`, and versions it with an auto-commit hook so every change is captured as a git commit.

## Setup

1. Pick a location for your memory folder (typically `~/.claude/projects/<project>/memory/` for per-cwd memory, or `~/.claude/global-memory/` for cross-cwd memory). Claude Code names `<project>` after the project's absolute path, with every character other than an ASCII letter or digit replaced by `-`: `/home/alice/notes` becomes `-home-alice-notes`. Names over 200 characters are cut and given a hash suffix. For auto-memory the project is the git root, so the memforge tools walk up from the current folder to the nearest `.git` entry (a directory, or the `.git` file of a linked worktree) and use the current folder only outside a repository. For a linked worktree that gives the worktree's own folder; whether Claude Code uses that or the main checkout is unconfirmed, so set `MEMFORGE_MEMORY_PATH` to the store if the tools do not find it.
2. Create the folder and seed a `MEMORY.md` index file.
3. Add to `~/.claude/settings.json`:
   ```json
   {
     "autoMemoryDirectory": "~/.claude/projects/<project>/memory/"
   }
   ```
4. Install the auto-commit hook: see `hooks/memory-auto-commit.sh`. Register it under `PostToolUse` in `~/.claude/settings.json` matching `Write|Edit`.
5. `git init` in the memory folder so the hook has somewhere to commit.

Restart Claude Code for the settings change to take effect.

## Hooks

### Auto-commit hook

`hooks/memory-auto-commit.sh` runs after every `Write` or `Edit` on a file under a memory folder. It commits only the written file and the `.md` files outside `.memforge/`, one git commit per event that changed any of them; it never commits the recall index (a local cache); it never commits a signer list, `config.yaml`, a snooze or a waiver, and a file staged by hand outside that scope stays staged. Paths are derived from `$HOME` at runtime so the same hook works on any machine.

Matches two kinds of folder by default:
- `$HOME/.claude/projects/<project>/memory/` for any `<project>` (per-cwd memory)
- `$HOME/.claude/global-memory/` (global memory)

Both must be git-initialized for the hook to commit. Edit the hook to add other folders.

Register under `PostToolUse` with matcher `Write|Edit`.

### Read-tracker hook

`hooks/memory-read-tracker.sh` runs after every `Read` of a memory file. It updates a sidecar ledger at `<memory-folder>/.last_used.json` with the current UTC timestamp for that filename. The ledger is not a `.md` file, so `memory-watch` and the auto-commit hook leave it out of their commits (unless a tool call writes the ledger itself). Nothing writes a `.gitignore` for it; add one if you commit by hand. When the ledger is a link, or anything but a regular file, the hook leaves it alone and records nothing.

Memory files that have never been read (missing from the ledger) and memory files last read long ago are surfaced by `memory-audit`'s staleness check, which prefers the ledger over filesystem mtime when the ledger is present.

CC's automatic session-start memory load does NOT fire a `Read` tool event. Only explicit agent reads update the ledger. That's intentional: rote session loading is not meaningful "use" for the staleness signal.

Register under `PostToolUse` with matcher `Read`.

### Recall hook (spec v0.6.0)

`hooks/memory_recall_hook.py` runs on every user prompt (`UserPromptSubmit`) and injects the descriptions of the memories whose triggers match the prompt, so the agent recalls the right memory at the right moment instead of relying on a bulk-loaded `MEMORY.md`. It invokes the installed `memory-recall` reader (spec v0.6.0 §"Recall operation") via `subprocess` (no shell), which reads the recall index. The index is a local cache: before each query `memory-recall` compares a fingerprint of the memory files (name, size, modification time) with the one stored in the index and rebuilds it when they differ or when `.memforge/recall-synonyms.yaml` changed, so an index that arrived by `git pull` or was edited by hand is never served. It is fail-open-empty: any failure injects nothing and never delays the prompt (a subprocess timeout guards latency). It is a Python hook (not bash) for Windows portability and to avoid shell-parsing fragility. Injected descriptions are wrapped in an untrusted-context preamble so the agent treats recalled text as reference, not instructions.

The hook starts `memory-recall` by absolute path, never by bare name, because it runs in the folder Claude Code was opened in and anyone who can commit there can place a program of that name. It uses the `memory-recall` in the scripts folder of the Python environment that runs the hook, else the first one on an absolute `PATH` entry. It runs neither when it lies inside a memory store (the `MEMFORGE_MEMORY_PATH` roots, else the Claude Code layout when present, else `~/.memforge/memory` and `~/.memforge/global-memory`). A program found on `PATH` is refused in more places: inside the folder Claude Code was opened in, inside `~/.claude/projects`, `~/.claude/global-memory` and `~/.memforge` (also while `MEMFORGE_MEMORY_PATH` is set, and for any project, not only this one), and inside the git repository that holds any of those folders or any store. The hook's own environment is exempt from the repository check and from the extra folders, so a virtual environment under `~/.memforge` works. So register the hook with the absolute path of the Python interpreter of the environment you installed `ildan-memforge` into, not a bare `python3`: that interpreter can import memforge, and the shell that runs the hook command does not look it up. For a virtual environment that is `<env>/bin/python` (`<env>\Scripts\python.exe` on Windows); for a pipx install it is `bin/python` under the `ildan-memforge` folder that `pipx list` names. Run by an interpreter that cannot import memforge, the hook searches `PATH` itself and refuses exactly the same folders, with the same messages: absolute `PATH` entries only, none of the folders above, and no check of its own environment (it has none). When no `memory-recall` may be started, it prints one line to stderr, `memory_recall_hook: recall is off: <reason>`, and the prompt goes through.

The recall index needs no upkeep: `memory-recall` rebuilds it when the memory files changed. You can still refresh it ahead of time one of two ways:

- The auto-commit hook above already calls `memory-recall --rebuild` after each memory write (recall-index-only refresh; it does not regenerate `MEMORY.md`).
- Or rebuild explicitly alongside the human index: `memory-index-gen --with-recall-index`.

The recall index is a derived local cache at `<memory-folder>/.memforge/recall-index.json`; it carries only memory descriptions (never bodies) and may be regenerated at any time. Neither the hook nor `memory-watch` commits it. If your repository already tracks it from an earlier version, run `git rm --cached .memforge/recall-index.json`, commit that, and add `.memforge/recall-index.json` to the folder's `.gitignore`.

Register under `UserPromptSubmit`.

### Write-boundary gate hook (spec v0.7.0)

Claude Code is the one adapter that can reject a malformed memory write *before the bytes hit disk* (Tier A in the adapter guide), via a `PreToolUse` hook matching `Write|Edit`. The shim reconstructs the post-write content (Write = the `content` field; Edit = the current file with `old_string` -> `new_string` applied) and pipes it to the `memory-validate` primitives; on failure it returns `permissionDecision: deny` with the reason. It MUST fail open on any internal error so the gate never wedges the editor; it then prints one line to stderr naming the error class, so a gate that did not check is not mistaken for one that passed.

**Shipped as [`hooks/gate-memory-frontmatter.py`](hooks/gate-memory-frontmatter.py).** It runs two checks in order: `validate_frontmatter` (does the block parse) and then `validate_required_fields` (are the non-derivable fields present). Both are needed, and the second is the one that matters in practice: a harness that writes `type` nested under a `metadata:` block instead of the spec's top-level `type:` produces *valid YAML*, so a parse-only gate accepts it, and no backfill can invent a semantic type to repair it. Such files accumulate silently until an audit finds them.

Scope is `*.md` under a memory root only. `MEMORY.md` is skipped: it is the folder index, not a memory, and carries no frontmatter by spec. Memory roots come from `memforge.paths.default_memory_paths()`, overridable with `MEMFORGE_MEMORY_ROOTS` (a `PATH`-style list); the override takes precedence rather than acting as a fallback, so a non-standard layout is expressible and the gate is testable.

For an `Edit`, the gate validates the *reconstructed* file rather than `new_string` alone. An edit that removes a required field is only visible once the substitution is applied to what is already on disk.

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
          "command": "/absolute/path/to/memforge-env/bin/python /absolute/path/to/hooks/memory_recall_hook.py",
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

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
    ]
  }
}
```

Restart Claude Code for the registration to take effect.

# adapters

Reference adapters for wiring MemForge into specific coding agents. The spec, taxonomy, and `tools/` are agent-neutral; an adapter does two things:

1. **Loads `MEMORY.md` into the agent's session-start context.** How depends on the agent's surface: a hook, a config rule, an `AGENTS.md` reference, a `--read` flag.
2. **Versions the memory folder.** When the agent (or any tool) writes to a memory file, the change lands in git. Either via an event hook, the agent's native commit cycle, or the portable `memory-watch` filesystem watcher.

Optional extras (some agents support them, others don't):

- **Read tracking** — update `<folder>/.last_used.json` when a memory file is read so `memory-audit` can flag staleness based on real use, not filesystem mtime.
- **Slash commands / skills** that wrap memory-management workflows (`consolidate-memory`, etc.).

## Adapter status

| Adapter | Auto-load | Auto-commit | Read tracking | Skills |
| --- | --- | --- | --- | --- |
| `claude-code/` | `autoMemoryDirectory` | `PostToolUse` Write/Edit hook | `PostToolUse` Read hook | `consolidate-memory` skill |
| `cursor/` | `.cursor/rules/` rule with `@MEMORY.md` | `memory-watch` (or daemon) | none | none |
| `aider/` | `--read MEMORY.md` (or `.aider.conf.yml`) | aider native + `memory-watch` for external writes | none | none |
| `codex/` | `AGENTS.md` reference | `memory-watch` (or daemon) | none | none |
| `vscode-copilot/` | `.github/copilot-instructions.md` + `github.copilot.chat.codeGeneration.instructions` setting | `memory-watch` (or daemon) | none | none |

The `codex/` adapter covers any agent that follows the AGENTS.md convention (Cline, Continue, Windsurf, future ones). The wiring is identical because AGENTS.md is the contract.

GitHub Copilot Chat does NOT honor AGENTS.md; it has its own `.github/copilot-instructions.md` surface plus VS Code settings. Hence the dedicated `vscode-copilot/` adapter. If you run Copilot alongside other agents in the same repo, both `AGENTS.md` and `.github/copilot-instructions.md` can point at the same `MEMORY.md` — they coexist cleanly.

## Cross-platform support

All adapters work on macOS, Linux, and Windows. The auto-commit watcher is the only piece with platform-specific surface:

- **`tools/memory-watch`** — Python + `watchdog`, cross-platform. One install (`python3 -m pip install watchdog`) covers all three OSes.
- **Daemon templates** in `cursor/`:
  - macOS: `daemon-launchd.plist`
  - Linux: `daemon-systemd.service`
  - Windows: `daemon-windows-task.ps1` (PowerShell-driven Scheduled Task)

Other adapters reference the cursor/ daemon files (they're agent-agnostic).

## Adapter responsibilities (per spec/SPEC.md)

The MemForge spec is explicit on what an adapter MAY and MUST do:

- **MUST NOT** reject memory files with non-conforming filenames (tools warn; adapters accept).
- **MAY** honor `sensitivity` and `access` frontmatter labels for filtering exports.
- **MAY** add encryption layers for shared / multi-developer scenarios.
- **MUST** treat absent `sensitivity` as `internal` (not `public`) for safe-default filtering.

Loading semantics (how the agent sees the folder) are entirely the adapter's responsibility. The spec defines only the file format.

## Writing a new adapter

If you're adding support for an agent not yet covered:

1. Find the agent's session-start surface (CLI flag, config file, rules folder, repo-root convention). Wire `MEMORY.md` into it.
2. Find the agent's write-event surface (commit hook, file-change event, autosave hook). Wire `git add -A && git commit` for the memory folder. If the agent has none, document that the operator should run `memory-watch` instead.
3. Document what works and what does NOT work vs the Claude Code adapter (typically: read tracking and skills are CC-specific).
4. PR the new adapter folder under `adapters/<agent-name>/` (post-CLA-template clearance).

Existing adapters are intentionally short; aim for the same. Less than 200 lines of README + config templates is plenty.

## Why these adapters and not others

The five adapters here are the ones with both the largest user base and the cleanest mapping to MemForge's contract. Other agents are reachable via:

- **GitHub Copilot CLI** (terminal, not VS Code Chat) — supports AGENTS.md; use `codex/` adapter.
- **GitHub Copilot Chat in VS Code** — uses `.github/copilot-instructions.md`, not AGENTS.md; use `vscode-copilot/` adapter.
- **Cline (VS Code)** — supports AGENTS.md; use `codex/` adapter.
- **Continue (VS Code / JetBrains)** — supports AGENTS.md; use `codex/` adapter.
- **Windsurf (Codeium Cascade)** — symlink AGENTS.md to `.windsurfrules`; use `codex/` adapter.
- **Zed** — assistant uses `.rules` files; copy the cursor/ rule body content verbatim.

If you want a dedicated adapter folder for any of those, the contract is identical to `codex/` (or `vscode-copilot/` for Copilot-style instruction files) — only the README needs to be agent-specific.

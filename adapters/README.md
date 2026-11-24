# adapters

Reference adapters for wiring MemForge into specific coding agents. The spec, taxonomy, and `tools/` are agent-neutral; an adapter does two things:

1. **Loads `MEMORY.md` into the agent's session-start context.** How depends on the agent's surface: a hook, a config rule, an `AGENTS.md` reference, a `--read` flag.
2. **Versions the memory folder.** When the agent (or any tool) writes to a memory file, the change lands in git. Either via an event hook, the agent's native commit cycle, or the portable `memory-watch` filesystem watcher.

Optional extras (some agents support them, others don't):

- **Read tracking**: update `<folder>/.last_used.json` when a memory file is read so `memory-audit` can flag staleness based on real use, not filesystem mtime.
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

GitHub Copilot Chat does NOT honor AGENTS.md; it has its own `.github/copilot-instructions.md` surface plus VS Code settings. Hence the dedicated `vscode-copilot/` adapter. If you run Copilot alongside other agents in the same repo, both `AGENTS.md` and `.github/copilot-instructions.md` can point at the same `MEMORY.md`; they coexist cleanly.

## Paths in the templates

Where a template or command shows `<path to your memforge clone>` (or `MEMFORGE_CLONE` in the launchd plist), put the folder you cloned this repository into. If you installed from PyPI instead, the tools are on your `PATH` (for example `memory-watch`) and need no clone path.

## Finding the memory folders

An adapter that runs code should not work out the store folder itself. Run `memforge paths`: it prints the default memory folders, one per line, in the order every memforge tool searches them (`MEMFORGE_MEMORY_PATH` if set, else the Claude Code layout if it exists, else `~/.memforge`), and nothing else on stdout. It prints each folder whether or not it exists, so test each one before you use it. It exits 0, or 1 if a folder name holds a newline. The aider wrappers work this way. A static template cannot run it and uses a `PROJECT_FOLDER` placeholder; run `memforge paths` from the project to see the folder to put there.

## Cross-platform support

All adapters work on macOS, Linux, and Windows. The auto-commit watcher is the only piece with platform-specific surface:

- **`memory-watch`**: Python + `watchdog`, cross-platform. It is installed with `ildan-memforge`, `watchdog` included, so one install covers all three OSes.
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
- **Secure-mode adapters (v0.4.0+) MUST** run `memory-audit --export-tier=<level> --strict` and `memory-dlp-scan --strict` before exporting memory to any external surface, and MUST pass the conformance suite at `tests/conformance/sensitivity/`. The privileged tier is a hard floor that no config knob disables. See `docs/adapter-implementation-guide.md` §"Secure-mode sensitivity enforcement (v0.4.0+)".

Loading semantics (how the agent sees the folder) are entirely the adapter's responsibility. The spec defines only the file format.

## Writing a new adapter

If you're adding support for an agent not yet covered:

1. Find the agent's session-start surface (CLI flag, config file, rules folder, repo-root convention). Wire `MEMORY.md` into it.
2. Find the agent's write-event surface (commit hook, file-change event, autosave hook). Wire a commit of the memory files only: stage the memory `.md` files outside `.memforge/`, by name, and commit just those names. Never commit `.memforge/recall-index.json`: it is a local cache that `memory-recall` rebuilds from the memory files. Never stage `.memforge/` itself: the signer list, config, snoozes and waivers change only in deliberate commits with their own `memforge:` prefix. Never use `git add -A` over the folder. `memory-watch` and the Claude Code `memory-auto-commit.sh` hook are the reference for this scope. If the agent has no write-event surface, document that the operator should run `memory-watch` instead.
3. Document what works and what does NOT work vs the Claude Code adapter (typically: read tracking and skills are CC-specific).
4. PR the new adapter folder under `adapters/<agent-name>/`, with a DCO sign-off on every commit (see `CONTRIBUTING.md`).

Existing adapters are intentionally short; aim for the same. Less than 200 lines of README + config templates is plenty.

## Why these adapters and not others

The five adapters here are the ones with both the largest user base and the cleanest mapping to MemForge's contract. Other agents are reachable via:

- **GitHub Copilot CLI** (terminal, not VS Code Chat): supports AGENTS.md; use `codex/` adapter.
- **GitHub Copilot Chat in VS Code**: uses `.github/copilot-instructions.md`, not AGENTS.md; use `vscode-copilot/` adapter.
- **Cline (VS Code)**: supports AGENTS.md; use `codex/` adapter.
- **Continue (VS Code / JetBrains)**: supports AGENTS.md; use `codex/` adapter.
- **Windsurf (Codeium Cascade)**: symlink AGENTS.md to `.windsurfrules`; use `codex/` adapter.
- **Zed**: assistant uses `.rules` files; copy the cursor/ rule body content verbatim.

If you want a dedicated adapter folder for any of those, the contract is identical to `codex/` (or `vscode-copilot/` for Copilot-style instruction files); only the README needs to be agent-specific.

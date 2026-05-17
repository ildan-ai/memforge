# adapters/routemeld

[routemeld](https://routemeld.ai) IDE adapter for MemForge. Surfaces a memory folder via the bundled `routemeld-trust` extension's native chat-participant context, and versions it via the cross-platform `memory-watch` filesystem watcher.

routemeld is a VS Code (Code-OSS) fork with a built-in chat participant that calls models through a cert-authed multi-provider substrate. The adapter wires MemForge into that chat participant so every turn includes the relevant memory in the system prompt.

This adapter is for the **routemeld IDE** specifically. If you run GitHub Copilot Chat inside the same IDE, also wire the `vscode-copilot/` adapter; both can point at the same memory folder without conflict.

## Setup

1. Pick a memory folder. `git init` if not already.
2. Seed `MEMORY.md` (or copy from an existing memory folder).
3. **Load on session start.** Choose one of:

   **Option A: per-repo `.routemeld/memory.json` (recommended).** Copy `memory-config.json.template` to `<repo>/.routemeld/memory.json`, edit the memory-folder paths. The `routemeld-trust` extension reads this file on workspace open and includes the referenced memory in every chat turn.

   **Option B: user settings.** Add to `.vscode/settings.json` (works because routemeld is a VS Code fork and shares the settings schema) or your user settings:
   ```json
   "routemeld.trust.memoryFiles": [
     "${workspaceFolder}/.claude/memory/MEMORY.md",
     "${userHome}/.claude/global-memory/MEMORY.md"
   ]
   ```
   Each entry is read on chat-turn build and inlined into the system prompt.

4. **Auto-commit on writes.** Install + run the cross-platform watcher:
   ```bash
   python3 -m pip install watchdog        # one-time, all platforms
   memory-watch
   ```
   For daemon-style operation pick the template from `../cursor/`:
   - **macOS** : `daemon-launchd.plist`
   - **Linux** : `daemon-systemd.service`
   - **Windows** : `daemon-windows-task.ps1`
5. Restart routemeld (or reload the routemeld-trust extension).

## What works

- **Auto-load via chat participant.** routemeld-trust reads `MEMORY.md` (project + global) and includes it in the system prompt for every `@routemeld` turn. Same pattern as Claude Code's auto-memory.
- **Multi-folder.** The `routemeld.trust.memoryFiles` setting accepts multiple absolute or workspace-relative paths.
- **Model picker integration.** Memory context is injected regardless of which model is selected in the chat picker (Haiku, Sonnet, Opus, Grok, Gemini, local Qwen, or auto-routing).
- **Skill-surfaced workflows.** Command Palette entries shell to MemForge tools: `memory-audit`, `memory-dedup`, `memforge-resolve`, `memory-promote`, `init`, plus session save/recall flows. Each command runs the tool against the workspace and displays results.
- **Sensitivity awareness.** Memory entries tagged `restricted` or `privileged` are filtered before the chat substrate ships them out of the host, per the spec's sensitivity classification.
- **Auto-commit.** `memory-watch` versions every settled write under the memory folder, independent of which agent did the write.
- **All MemForge CLI tools work unchanged.** They operate on filesystem markdown.

## What does NOT work (vs Claude Code adapter)

- **No automatic read tracking.** routemeld does not yet emit per-file Read events for `.last_used.json` ledger updates. Planned for a later release.
- **CC-session-dependent skills are path-openers.** `session-save` and `session-recall` skills depend on a Claude Code session for context capture. In routemeld these are Command Palette entries that open the resume folder for browsing; full skill execution requires Claude Code alongside.

## Files in this adapter

- `memory-config.json.template` : drop-in template for `.routemeld/memory.json`. Edit the memory-folder paths.
- `settings.snippet.json` : copy/paste-ready snippet for routemeld user or workspace `settings.json` to wire the user-level memory load.

## Operator-side prompts variants

routemeld supports VS Code's `.github/prompts/*.prompt.md` convention (where enabled) for reusable prompt templates. A memory-aware template can use the `routemeld.trust.memoryFiles` setting plus a `#file:` reference to MEMORY.md to render the prompt with current memory context.

## Honoring sensitivity

routemeld's substrate is cert-authed and respects the `sensitivity` frontmatter field per the MemForge spec §"Sensitivity classification" + §"Sensitivity enforcement". Concretely:

- Memory entries tagged `public` or `internal` flow through the chat participant to whichever model is selected in the picker (cloud or local).
- Memory entries tagged `restricted` route only to providers the operator has explicitly marked as restricted-eligible (typically the operator's own cloud account, never third-party shared tenants).
- Memory entries tagged `privileged` route only to local models (Ollama via the routemeld picker's local entries). The substrate refuses to ship privileged content to any network-bound destination, matching the spec's `privileged` semantics.

The classification happens at chat-context build time in routemeld-trust, before the prompt leaves the IDE. Cross-check enforcement happens substrate-side at egress.

## Status

routemeld is in early development. The chat-participant + model-picker surface is shipping; the Memory Inspector sidebar view and per-call audit-row badging are planned for a later release.

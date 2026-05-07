# tools

Shared tooling that works across adapters. Nothing here should depend on a specific agent.

- `memory-audit` — integrity + health checks for a MemForge folder. See the script's `--help`.
- `memory-promote` — move a memory entry from one folder to another (defaulting per-cwd → global). Removes the pointer from the source `MEMORY.md`, appends it to the target, and commits both folders' git repos. See the script's `--help`.
- `memory-dedup` — ask a local LLM to flag near-duplicate entries in a folder. Reports candidates; never acts. Defaults to local-only mode (probes `$PATH` for `ollama` / `llama.cpp` / `lms`); override with `$MEMORY_DEDUP_DISPATCHER` or `--dispatcher`. Cloud dispatchers require explicit `--allow-cloud-dispatcher` opt-in.

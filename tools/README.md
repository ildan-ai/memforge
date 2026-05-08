# tools

Shared tooling that works across adapters. Nothing here should depend on a specific agent.

> **Note (v0.4.1+):** The `.py` shim scripts here are reference implementations,
> not the canonical install path. They invoke `from memforge.cli.<module> import main`,
> which requires the `memforge` package on the Python interpreter's import path.
> `pip install ildan-memforge` (or the editable / venv variants in the project
> README) installs all CLI commands as proper `console_scripts` entry points
> on `$PATH` — that is the supported install path. The shims here remain useful
> if you want the source-of-truth implementation visible at one path per tool.

- `memory-audit` — integrity + health checks for a MemForge folder. See the script's `--help`. v0.4 adds `--export-tier=<level>` (sensitivity export gate; fails BLOCKER on declared-tier > export-tier; privileged hard floor regardless of config).
- `memory-promote` — move a memory entry from one folder to another (defaulting per-cwd → global). Removes the pointer from the source `MEMORY.md`, appends it to the target, and commits both folders' git repos. See the script's `--help`.
- `memory-dedup` — ask a local LLM to flag near-duplicate entries in a folder. Reports candidates; never acts. Defaults to local-only mode (probes `$PATH` for `ollama` / `llama.cpp` / `lms`); override with `$MEMORY_DEDUP_DISPATCHER` or `--dispatcher`. Cloud dispatchers require explicit `--allow-cloud-dispatcher` opt-in.
- `memory-dlp-scan` — pre-commit DLP scanner for credentials, tokens, PII, and high-entropy strings. v0.4 adds the sensitivity label / content cross-check (default-on; emits BLOCKER `sensitivity_label_mismatch` when body content's implied tier exceeds the declared label). Disable for non-privileged tiers with `--no-sensitivity-cross-check` or `dlp.enforce_sensitivity_cross_check: false` in `.memforge/config.yaml`; privileged-implied findings always fire.

A full reference for every CLI tool (17 commands as of v0.4.1) lives in the project root `README.md` under "Key tools". Spec-level v0.4 enforcement guidance is in `spec/SPEC.md` §"Sensitivity enforcement (v0.4.0+)" and `docs/adapter-implementation-guide.md` §"Secure-mode sensitivity enforcement (v0.4.0+)".

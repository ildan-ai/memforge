# Deployment and installation

What runs where, in what order to set it up, and which document carries each step. MemForge has no server: a user installs the package, makes a memory store, optionally adds commit trust, and wires an adapter. Architecture and threats are in [architecture/overview.md](architecture/overview.md) and [architecture/threat-model.md](architecture/threat-model.md).

## What runs where

| Piece | Runs on | Operated by | Runbook or guide |
|---|---|---|---|
| `ildan-memforge` package (the `memforge` dispatcher and the per-tool console scripts) | The user's machine (macOS, Linux, native Windows), Python 3.10 or later | The user | [quickstart.md](quickstart.md) "Step 1: install" |
| Memory store (a memory folder, optionally a git repository) | The user's machine; a git remote for a shared store | The user; the store's signers for shared history | [quickstart.md](quickstart.md) "Step 2: create a store" |
| Commit trust (`.memforge/allowed_signers`, `memforge sync`) | The store repository and each receiver's clone | The store's signers and receivers | [team-bootstrap.md](team-bootstrap.md) |
| Claude Code hooks (`memory-auto-commit.sh`, `memory-read-tracker.sh`, `memory_recall_hook.py`, `gate-memory-frontmatter.py`) | The user's machine, registered in `~/.claude/settings.json` | The user | [../adapters/claude-code/README.md](../adapters/claude-code/README.md) |
| Other adapters (Cursor, Aider, Codex, Copilot Chat) and `memory-watch` | The user's machine | The user | [../adapters/README.md](../adapters/README.md) |
| Write-boundary and commit-message hooks | The store's git hooks | The user | [quickstart.md](quickstart.md) "Commit hygiene and the `memforge:` prefixes"; [adapter-implementation-guide.md](adapter-implementation-guide.md) "Write-boundary gate (v0.7.0)" |
| Release of the package to PyPI | GitHub Actions (`release.yml`) and PyPI | The project maintainer | [runbooks/release.md](runbooks/release.md) |

## Order of a first deployment

For one person:

1. Install the package and check `memforge --version` ([quickstart.md](quickstart.md), Step 1). Keep `git`, `jq`, `detect-secrets` and the package's environment outside every memory store, in folders named on `PATH` by absolute path: the tools and the Claude Code shell hooks ignore relative `PATH` entries and refuse a program inside the store they work on. The tools also refuse one inside the git repository that holds the store, when the store is a folder below that repository's top, unless it is in the scripts folder of the Python environment memforge runs from. `memory-audit` skips Tier 2 with a WARN when its `git` is refused.
2. Run `memforge paths` to see which folders the tools will use. Set `MEMFORGE_MEMORY_PATH` if you want others.
3. Create the store with `memforge init-store`, add memory files, run `memory-index-gen --path .` and `memory-audit --strict --path .` ([quickstart.md](quickstart.md), Step 2).
4. Wire an adapter so the agent loads `MEMORY.md` and changes are committed ([../adapters/README.md](../adapters/README.md)).
5. Add the git hooks you want: the `memory-validate` pre-commit hook and the example commit-msg hook ([quickstart.md](quickstart.md), "Commit hygiene"), and a `memory-dlp-scan --staged --strict` line in `.git/hooks/pre-commit`, which stops a commit whose staged content holds a BLOCKER finding such as an AWS key (exit 1).

For a shared store, after step 3:

1. Make the store the top level of its own git repository. `memforge sync` refuses any other layout (`root_not_toplevel`).
2. Each signer configures git signing inside the store's repository, and the creator runs `memforge init-store --mode ssh` ([team-bootstrap.md](team-bootstrap.md), Case 1).
3. Commit the signer list signed and push with an upstream.
4. The creator runs `memforge sync --adopt-local`; every other receiver clones and runs `memforge sync --trust-head <commit>` with an id the creator gives.
5. Turn on the host's branch protection and required signed commits, and a code owners rule on `.memforge/`, where the host offers them. `memforge sync` checks only for people who run it.
6. Everyone runs `memforge sync` instead of `git pull` from then on ([team-bootstrap.md](team-bootstrap.md), "Daily use").

Coming from 0.x: [migrating-to-1.0.md](migrating-to-1.0.md).

## What cannot be deployed yet

- Shared stores in `gpg` or `keyless` mode. `memforge sync` refuses a commit whose parent holds either list with `mode_unreleased`.
- A store in a subfolder of a larger repository with `memforge sync`. It must move to the top level of its own repository.
- Merges made on the git host's web interface as verified history. Host-generated commits have not been verified against any mode; merge locally and sign the merge.

## Run from source

No credentials are needed. From a clone of the repository:

```
python -m venv .venv
.venv/bin/pip install -e '.[dev,dlp]'
.venv/bin/pytest -q
```

This proves the tools and their tests on your platform. It does not prove a real `gpg` or `gitsign` run, and it does not exercise your git host.

## Build

From a clean checkout:

```
python -m pip install 'build==1.2.2'
python -m build
```

This writes the sdist and wheel to `dist/`. Published builds come only from `release.yml`; see [runbooks/release.md](runbooks/release.md).

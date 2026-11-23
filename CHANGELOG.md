# Changelog

All notable changes to MemForge are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The version number tracked here is the **package / tooling** version. The on-disk **spec version** (used in memory frontmatter and folder-index files) is tracked separately at `spec/VERSION` and follows its own SemVer track. A package release MAY include a spec bump; a spec bump MAY ship in a patch release.

## [Unreleased]

## [1.0.0] - 2026-11-20

**Major: commit trust replaces the in-repo trust model. Package 1.0.0 / spec 1.0.0 (was 0.8.0). Nothing to migrate: existing stores keep loading. See `docs/migrating-to-1.0.md`.**

MemForge no longer carries its own key system. Trust in a shared store comes from signed git commits and a signer list the store commits, using the identity, signing and git host controls an organization already runs. There is nothing in MemForge to generate, rotate, revoke or recover.

### Added

- `memforge sync`: fetches the shared branch and accepts new history only when every first-parent commit passes the store's mode check (a valid signature by a signer the committed list named one commit earlier). Ordered steps, named failure reasons, exit codes 0, 1 and 2, `--check`, `--trust-head <commit>` and `--adopt-local`. It refuses a history rewrite, a shallow range it cannot see, and a divergent branch, and it has no override flag. One local state key, `memforge.verifiedHead`, records where verification stopped. It fetches only the branch named by the upstream setting, and it requires the store to be the top level of its git repository (`root_not_toplevel` otherwise). Only `memforge sync` checks anything; no other command is gated.
- Three signer-list modes: `ssh` (`.memforge/allowed_signers`), `gpg` (`.memforge/allowed_gpg_fingerprints`) and `keyless` (`.memforge/allowed_identities`). The mode comes from the one list file in a commit's first parent. `ssh` is available. `gpg` and `keyless` are refused (`mode_unreleased`) until their proofs pass on the pinned tool versions.
- Isolated verification: a throwaway config-free git directory and a cleared environment, with the verifier executables resolved to absolute paths and printed with their versions and SHA-256.
- Spec section "Commit trust (v1.0.0+)", with the trust statement, setup guidance, what is and is not protected, and the residual risks.
- Optional advisory frontmatter fields `agent_session` and `on_behalf_of`. They carry no integrity weight beyond the signed commit that introduced them.
- `memforge paths` prints the default memory folders, one per line, in the order every memforge tool searches them (`MEMFORGE_MEMORY_PATH`, else the Claude Code layout if present, else `~/.memforge`), and nothing else on stdout. It prints each folder whether or not it exists. It writes names as file-system bytes, so the output does not depend on the locale. Exit 0, or 1 if a folder name holds a newline. It is meant for scripts and adapters that cannot import the package.
- `docs/architecture/`: an architecture overview with a control table, a threat model, and eight PlantUML diagrams (topology, the sync sequence, data flow, trust boundaries, keys and signing, automatic commits, index and audit, sensitivity tiers) with SVG and PNG renders. Also `docs/deployment.md` and the maintainer's release runbook, `docs/runbooks/release.md`.
- `docs/architecture/diagrams.md`: the eight architecture diagrams with a description of each.
- Each GitHub Release attaches the architecture PDF (diagrams, overview and threat model), built by `release.yml` from the tagged sources with the pandoc and Chrome versions pinned in `docs/pdf/toolchain.conf`. `docs/pdf/build-pdf.sh` builds it locally.
- One build provenance attestation covers the sdist, wheel, SBOM and PDF on the GitHub Release, and the release notes list the sha256 of each attached file. Verify a download with `gh attestation verify <file> --repo ildan-ai/memforge` (see `docs/runbooks/release.md`).
- `docs/migrating-to-1.0.md`, and a rewritten `docs/team-bootstrap.md` for three setups: two people on a private repository (`ssh`), a company with single sign-on and required signed commits, and keyless signing.

### Changed

- `memforge init-store` is repurposed. It creates the `.memforge/` layout and, with `--mode ssh|gpg|keyless`, a signer list for that mode: your own configured signing key for `ssh` and `gpg` when it resolves, otherwise a comment-only file you fill in (always for `keyless`). It never overwrites. It no longer creates a registry, and `--force` is gone.
- `identity` and `signature` frontmatter are legacy: read like any field, preserved on rewrite, never verified, never written, and no audit finding. `memforge.frontmatter.render` takes the file's current text as `original` and keeps those fields' lines as written (line terminators aside); `memforge-resolve` and `memory-frontmatter-backfill` pass it. Before this, a rewrite re-serialized them (a signature timestamp came back requoted). The "(v0.4: unsigned)" tag no longer exists.
- Spec: versioning and versioning history updated; the in-repo trust model is deprecated by this major revision. Integrity invariants 16 to 25 are retired (numbers not reused). No code ever enforced them in `memory-audit`, so audit behavior does not change.
- `spec/known-limitations.md` rewritten around the residual risks of commit trust and the mechanisms not yet proven against the real tools. `docs/quickstart.md`, `docs/adapter-implementation-guide.md`, the README and the `examples/git-hooks/commit-msg*` hooks and auto-commit watchers no longer mention the registry, revocation or attestation paths.
- The README no longer says external adoption waits on a CLA flow. Pull requests are open under Apache-2.0 with a DCO sign-off.
- The default Claude Code store is now `~/.claude/projects/<project>/memory/`, where `<project>` is named the way Claude Code names it: every character other than an ASCII letter or digit becomes `-`, and a name over 200 characters is cut and given a hash suffix. The folder named is the git root of the current folder (the nearest folder up the tree that holds a `.git` entry, found without running git), or the current folder outside a repository, so a tool run from a subfolder finds the same store as one run from the root. The old `<USER>-claude-projects` folder matched no real Claude Code project and is no longer read. `MEMFORGE_MEMORY_PATH` still takes precedence. In a linked worktree memforge uses the worktree's own folder; whether Claude Code does the same is not confirmed, so set `MEMFORGE_MEMORY_PATH` if the store is not found there.
- Adapters: the aider wrappers (`aider-memory-cmd.sh`, `.ps1`) call `memforge paths` and no longer carry their own copy of the store-naming rule, so they find the same store as the package (non-ASCII folders and folder names over 200 characters included) and honor `MEMFORGE_MEMORY_PATH`. They need `memforge` on `PATH`; without it they say so and do not start aider. The per-project `--read` now comes before the global one. The Claude Code auto-commit and read-tracker hooks act on the memory folder of any Claude Code project, not one folder named after the user. Templates take `PROJECT_FOLDER` and `<path to your memforge clone>` placeholders, explained in `adapters/README.md`, instead of a fixed clone location.
- `memory-link-rewriter` (`check`, `rename`, `rename-batch`, `upgrade`) reads every file in the folder before it changes anything. A file it cannot read (permissions, invalid UTF-8, a named pipe or other file that is not a regular file) stops the command with exit 2, naming each such file, and nothing is moved or written, so a refusal cannot leave a rename half applied. `check` reports each such file and exits 2.
- `memory-promote` reads both `MEMORY.md` files before it changes anything, and stops with exit 2, keeping the source file, if a read or write is refused. The copy keeps the bytes, permission bits and times of the file, but no longer copies extended attributes or file flags.
- `agents-md-gen` no longer inlines memories by a built-in list of file names. It inlines the body of a memory whose frontmatter has `pinned: true` (a YAML boolean; a quoted `"true"` or `1` does not count), under the same sensitivity, `--inline-above-public` and DLP rules as before. Every other memory is a description-only entry.
- Generated AGENTS.md, the rollup README, `memory-query` markdown output and the `memory-dedup` prompt use colons and parentheses where they used dashes. The `memory-index-gen` header is unchanged, so a checked-in `MEMORY.md` still passes `--check` after the upgrade.

- External pull requests are open. Contributions come in under Apache-2.0 (inbound equals outbound) with a Developer Certificate of Origin sign-off on every commit; the planned Contributor License Agreement is dropped. A new `DCO sign-off` check fails a PR when any commit, merge commits included, lacks a `Signed-off-by:` line matching its author. It runs on `pull_request_target` from `main`'s copy of the workflow and checker, so the PR under check cannot edit them, and it reads the PR's commits as data without checking out or running PR code. Dependabot PRs are exempt. `.github/CODEOWNERS` assigns CI, release config, packaging, the spec, governance records and the policy files (LICENSE, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT) to the maintainer.
- CI builds the architecture PDF on pull requests that change `docs/`.
- GitHub Issues are back on for bug reports and design proposals; Discussions stay off. Contributor docs and templates no longer point at Discussions, which had been disabled while still being advertised.

### Removed

- The 13 key-system commands: `init-operator`, `operator-registry` (`add`, `verify`, `remove`, `fresh-start`), `rotate-key`, `revoke`, `revocation-snapshot`, `revoke-cache-refresh`, `memories-by-key`, `revoke-memories`, `upgrade-v04-memories`, `recovery-init`, `recovery-backup-confirm`, `attest-agent` and `messaging-doctor`. Each name stays as a stub that prints a removal notice pointing to `docs/migrating-to-1.0.md` and exits non-zero, and does nothing else.
- The modules behind those commands (`registry`, `revocation`, `crypto`, `identity`, `agent_session`, `sender_sequence`, `recovery`, `_security`) and the tests that exercised them. The kept code imports none of them.
- Spec sections: "v0.5.0 surface map", "Multi-identity primitives" (its frontmatter rows are replaced by the advisory fields), "Signed envelope scope", "Mixed v0.4 / v0.5 deployment posture", "Agent session attestation content scope", "Cryptographic attribution", "Operator identity + cross-store references", "Messaging adapter contract", "Key lifecycle + revocation", and "Cross-cutting fail-closed posture". "Security considerations", "Privacy considerations" and "Known limitations" are replaced.
- The `examples/websocket/` scaffold and the spec's messaging adapter contract. The package never shipped a WebSocket client or relay.
- Registry, revocation and attestation commit-prefix checks from the example commit-msg hooks and watchers.

### Security

Found by a red team of this release before it was tagged, and fixed in it. Most of these are in tools older than 1.0. None is known to have been used against anyone. Upgrade from any earlier version.

- **Code execution in `memforge-detect`.** It started its helper commands in a way that let a Python file placed in a memory folder (for example `yaml.py`) run as code when someone ran `memforge-detect` from inside that folder. The helpers now run in the interpreter's isolated mode with this process's own import path.
- **Automatic commits could change who may sign.** `memory-watch`, the Claude Code `memory-auto-commit.sh` hook and the example watchers committed `.memforge/`, signer list included, so any process that could write one file in a listed signer's store could get a new key committed under that signer's signature. They never commit a signer list file now. `memory-watch` commits memory files and `MEMORY.md` only, and the example watchers skip everything under `.memforge/`, so config, snoozes and waivers no longer ride along in an automatic commit. The Claude Code hook used to commit every change in the repository with each write; it now commits only the written file and the memory `.md` files outside `.memforge/`, and nothing under `.memforge/`, even when that is the file the tool call wrote. A file outside that scope that you staged by hand is left staged. The hook also put the tool call's file name in the commit message as given, so a name with a newline could add lines, trailers such as `Signed-off-by:` included, to a commit made under your signature. Control characters in the tool and file names are now replaced with `?`.
- **Writes and reads through links and special files.** A symlink placed in a memory folder could make `memory-frontmatter-backfill`, `memory-index-gen`, `memory-preamble-extract`, `memory-recall --rebuild`, `memory-audit --add-defaults` and `--fix`, `memforge-resolve`, `memforge-migrate-claim-block` and `memory-audit-log append` write outside the folder, and could make the tools that walk a folder read a file outside it. Writers refuse a symlinked target with a message for that file, except the recall index: `memory-recall --rebuild` and `memory-index-gen --with-recall-index` write it to a new file that replaces a link at its name, so the file the link names is left as it was. The check is made on the opened file, so a link swapped in after an earlier check is refused too. Writers also refuse a file that has a second hard link (the write would change the same file under another name, possibly outside the folder) and a named pipe or other file that is not a regular file (a named pipe used to make the tool hang). Folder walks skip symlinked files and do not enter symlinked folders, and their reads refuse a link swapped in after the walk. On Linux and macOS a tool opens a store file by walking folder handles down from the memory folder, so a folder below it that is or becomes a link is refused too, for reads and writes. Every tool that reads or writes a store file now works this way, including `memory-audit` (its `MEMORY.md` read and the `--add-defaults` and `--fix` re-reads), `memory-promote`, `memory-rollup`, `memory-link-rewriter`, `memory-validate`, `memory-dedup`, `memory-cluster-suggest`, `memforge-detect`, `memory-audit-deep`, `agents-md-gen`, `memory-dlp-scan --memory-folders`, the recall synonyms and index, and the `memory-index-gen` snoozes and `--check`. A file you name on the command line is read as named. `memory-dlp-scan` reports each symlink it did not read.
  - Known limits: on Windows, where a file cannot be opened relative to a folder handle, a folder swapped for a link or junction between the check and the open is still followed (a link already there is refused). On Windows, a link swapped in for a file that does not exist yet, between the check and the open, has its target created empty before the write is refused; nothing is written through it. On every platform the memory folder itself is opened by path, so replacing the whole memory folder with a link is followed. Moving, renaming and deleting a file go by path and are not covered; none of them reads or writes file content.
- **`memory-dlp-scan` could be made to skip a file.**
  - One byte that is not valid UTF-8 made the scanner drop the whole file. The file is now scanned with the bad bytes replaced and reported as `invalid_utf8` (MAJOR). A named file that cannot be read is `unreadable_file` (BLOCKER).
  - `--staged` scanned the working-tree copy, not the staged content, and misread file names with a newline, a quote or a non-ASCII character. It now scans the staged content and lists names safely. It also skipped a file that had been a symlink and was replaced by a regular file (git reports that as a type change); it now scans it.
  - A file name starting with a dash reached `detect-secrets` as an option.
  - `detect-secrets` was run in a way that covered only files under the current folder, so the deep layer silently skipped everything else. It now covers every scanned file.
  - A deep layer that failed read as clean. When `detect-secrets` is installed and its run fails (it cannot start, times out, exits with no usable output, prints something that is not a valid report, or the staged content cannot be copied for it), the scan now adds a `deep_scan_failed` finding (BLOCKER), keeps every other finding and prints a warning, so `--strict` fails. A `detect-secrets` that is not installed is still only a warning.
  - A secret in a UTF-16 file without a byte-order mark matched no pattern. A file that holds NUL bytes is now also scanned with them removed and is reported as `nul_bytes` (MAJOR).
  - A folder the scan could not list was skipped without a word. It is now `unreadable_file` (BLOCKER).
  - A long hex string was not reported on any line that also held a word such as `commit`, `hash`, `digest` or `tree`. That exemption now applies only to strings of exactly 40 or 64 hex characters, and never on a line that also holds a secret word such as `password`, `secret` or `token`. A real git hash on such a line is now reported.
- **One hostile file could stop every tool.** Frontmatter built from nested YAML aliases made every tool hang. A list or mapping where a text value belongs (`tier: [index]`), an invalid date (`created: 2024-13-45`) or a non-text `uid` made audit, validate, lint and the recall build stop with an exception, and in one case let `memory-audit --strict` pass with its competing-claim checks not run. Frontmatter is now limited to 65536 bytes and 10000 nodes. None of these stops a tool now; `memory-audit` reports each as a finding for that one file (`memory-validate` does not check `uid`). A file that is not valid UTF-8 stopped `memory-validate` with an exception and was left out of `memory-audit-deep` without a word, so it could report clean; both now report it as a finding for that file.
- **Index content from the store's config.** `memory-index-gen` copied the `preamble` and `title` from `_memforge.yaml` into `MEMORY.md` as written. They must now be text, within 4096 and 200 bytes, with control characters removed, and may not imitate the generated header or a competing-claims block. A preamble is copied as literal text inside a code fence, so it cannot leave a fence or comment open over the generated text (see **A pinned body or preamble could hide the generated sections**).
- **Filtered index showed restricted claims.** With a viewer filter, the competing-claims block printed the first line, path and owner of members the viewer may not read. It now lists such a member by `uid` with a restricted marker only.
- **`memory-audit-log`.** `verify` now fails on a line it cannot read, or a blank line, instead of skipping it. The CEF export escapes field values. `tail` no longer stops on a record with a missing field. `verify`, `tail` and `export` refuse a log that is a symlink. `append` refuses a log or lock that is a symlink, has a second hard link, or is not a regular file, creates a new log or lock only where no name exists (so it creates nothing through a dangling link), and its `--file` hashing no longer hangs on a named pipe. The log is no longer described as tamper-evident: `verify` detects accidental corruption and an edit that does not recompute the chain, not a rewrite or truncation by someone who can write the file.
- **Claude Code hooks and paths with `.` or `..`.** The auto-commit and read-tracker hooks take the written file's path from the tool call. A path such as `~/.claude/projects/../memory/note.md` made them act on a folder outside the project memory folders. They now ignore any path with a `.` or `..` segment. They also ignore a path with an empty project folder name, such as `~/.claude/projects//memory/note.md`, which made them act on `~/.claude/projects/memory`.
- **`agents-md-gen` wrote outside the repo and across projects.** It followed a symlink in the repo it runs in: a cloned repo could commit `AGENTS.md` as a link to a file outside it and have that file overwritten, or commit `CLAUDE.md` as a link and have an outside file's text copied into `AGENTS.md`. `CLAUDE.md` and an existing `AGENTS.md` are now read, and `AGENTS.md` and its `AGENTS.bak-<stamp>.md` backup written, without following a link and only inside `--cwd`; a link, a hard-linked `AGENTS.md` or a file that is not a regular file stops the run with exit 2 and nothing written. Run from one project with `--cwd` naming another, it built the second repo's `AGENTS.md`, a file meant to be committed, from the first project's memories, `internal` ones included. It now reads the store of the repo `--cwd` names; `memforge.paths.default_memory_paths` takes an optional `start` folder for this.
- **One memory could forge index content.** `memory-index-gen` now treats each memory's name, description, topic tag and file name as untrusted, as it already did the `_memforge.yaml` preamble. Line breaks and other control characters in them become spaces, so one memory cannot start a line of its own in `MEMORY.md`. A value carrying a marker only the generator writes (the generated header, the competing-claims block), an unclosed HTML comment or a code fence is refused: that memory is left out of `MEMORY.md`, named on stderr, and `--write`, `--check` and `--print` exit 1; `memory-audit` reports it as an integrity violation. One memory could otherwise forge a resolved competing-claims block and hide the real one inside an open comment.
- **`memforge sync` signer list and tool lookup.** The signer list is now read with a grammar narrower than ssh-keygen's, so no line can mean one thing to memforge and another to ssh-keygen. Lines split on newline only; a control character other than tab makes the list `list_invalid`; only a line whose first character other than space or tab is `#` is a comment; an `ssh` entry must be printable ASCII and may use a double quote only around a `namespaces="..."` value. Before this, a line starting with a vertical tab and `#` was a comment to memforge and a trusted signer to ssh-keygen, and `*""cert-authority` passed the forbidden-option check. `memforge sync` and `memforge init-store` look up `git`, `ssh-keygen`, `gpg` and `gitsign` only in absolute `PATH` entries, never in the current folder (on Windows, tool lookup searches the current folder first, so a `git.bat` or `gpg.cmd` committed to the store's top level would have run), and `memforge sync` refuses with `tool_in_store` (exit 1) when one of them resolves to a file inside the store; `memforge init-store` does not run such a tool. `memforge init-store` refuses a `.memforge` that is a symlink or not a folder, and writes the signer list through the same no-follow writer as the other tools.
- **The concurrency audit skipped prefixed commits.** `memory-audit` Tier 2 now checks commits that carry a `memforge:` prefix instead of skipping them. A `memforge: resolve <topic>` commit may touch only members of `<topic>` and that topic's snooze file; anything else in it is a resolve-commit scope violation (BLOCKER), and other prefixes no longer excuse a `superseded` transition, a `superseded_by` write or a `decision_topic` change. Tier 2 compares the parsed frontmatter of each changed memory before and after the commit, so a quoted `status: 'superseded'`, a block-list `superseded_by` or a moved `decision_topic` line is caught, and body text that looks like frontmatter no longer is. It also enforces the spec's config, snooze and deletion rules: a `.memforge/config.yaml` change outside a config-only `memforge: config` commit is a BLOCKER, deleting a `decision_topic` member outside its resolve commit is a BLOCKER, and a snooze file change outside a single-file `memforge: snooze <topic>` commit (or the topic's resolve) is a MAJOR. A merge is judged by what it changes on its first parent, so a deletion, topic change or config revert brought in by taking the other side is reported on the merge. Every committed path the config loader can open as `.memforge/config.yaml` is gated (case variants, a committed link at `.memforge` or the config, and the file a link names inside the repository), and the snooze folder the same way. A live group member moved into `archive/` outside its topic's resolve commit is a group member deletion.
- **Audit waivers could be added without a trace.** `memory-audit` read `.memforge/audit-waivers.yaml` from the work tree, so an untracked or locally edited waiver file turned Tier 2 BLOCKERs into a WARN. It now reads the waiver file only as committed at HEAD, and warns when the work-tree copy differs. Every add, change or delete of the waiver file must land in a commit whose subject starts `memforge: waiver` and that touches only that file; anything else is a `waiver edit` BLOCKER, and a waiver never applies to a commit that changes the waiver file. A `topic_aliases` change on a current anchor outside its `memforge: resolve <topic>` commit or a single-file `memforge: alias <topic>` commit is now an `alias edit` MAJOR, as the spec already stated. The example commit-msg hooks reject a waiver commit without the prefix or with other files in it.
- **Helper programs found in the store.** Every memforge tool now finds the programs it starts (`git`, `detect-secrets`, `memory-link-rewriter`, and a probed local-model CLI) the way `memforge sync` does: only through absolute `PATH` entries, never in the current folder on Windows, started by that absolute path, and refused when the real path is inside the store or memory folder the tool works on. Before, `memory-audit` Tier 2, `memory-dlp-scan --staged` and its deep layer, `memory-watch`, `memforge-resolve`, `memory-promote`, `memory-rollup`, `memory-dedup` and `memforge-detect` ran `git` and `detect-secrets` by bare name, so a `git.exe` committed to the store ran on Windows when the tool was started from the store, and a relative `PATH` entry did the same anywhere. `memforge sync` now refuses a memory root that is not the top of its own repository before it runs any program. The Claude Code hooks `memory-auto-commit.sh` and `memory-read-tracker.sh` take every program they run from absolute `PATH` entries only and skip one whose real path is under `~/.claude/projects` or `~/.claude/global-memory`.
- **`memory-dlp-scan` printed part of a secret.** Excerpts were cut with offsets from the line as matched but applied to the stripped line, so an indented `key AKIA...` printed the key in clear. Every match on a line is now redacted in every excerpt for that line.
- **`agents-md-gen` could write forged sections and secrets.** A memory's name or description with a line break wrote a forged `# Repo-specific` or `## Critical rules` section into the committed AGENTS.md. Names and descriptions are now cleaned as `memory-index-gen` cleans them, a pinned body is copied as literal text inside a code fence, so it cannot add a heading to the file (see **A pinned body or preamble could hide the generated sections**), and a name, description or topic that leaves a comment or code fence open is left out and named on stderr. The write is also refused (exit 2, nothing written) when the rendered file holds a MAJOR secret finding such as a JWT or a password assignment, not only a BLOCKER.
- **Release workflow.** A release tag must be annotated, carry a signature GitHub verifies, and point at a commit that is on `main`, or nothing is built or published. The GitHub Release step reads the tag again just before it creates the release and stops unless it is still the same signed tag object the first check verified, pointing at the same commit, so a tag moved or replaced while the build and publish ran does not get its name on the release page. The build and the PyPI upload always use the checked commit, whatever the tag does; this last check runs after the upload and guards only the release page. The release page names the tag, so a tag moved after that last check is not caught by the workflow; keeping release tags from moving is a repository setting.
- **Helper programs found elsewhere in the store's repository.** The tools that refuse a program inside the store or memory folder they work on (`git`, `detect-secrets`, `memory-link-rewriter`, a probed local-model CLI) now also refuse one inside the git repository that holds that folder, found by walking up to the nearest `.git` entry without running git. A program in the scripts folder of the Python environment memforge itself runs from is exempt from this repository check (anyone who can write there can already change memforge), so a project's own virtual environment inside the project repository keeps working. Before, a memory folder below its repository's top (or `memory-dlp-scan --staged` run from a subfolder) would run a `git` committed elsewhere in that repository when an absolute `PATH` entry pointed there. `memforge init-store` and `memory-audit` Tier 2 now use the same check; Tier 2 skips with a WARN that names the refused `git`. Tier 2 also skips with a WARN, instead of passing with no findings, when git reports an empty or relative top level, one that does not hold the audited folder, or a `git log` line that is not a commit record; before, an empty answer was read as the current folder. `memforge-resolve` refuses the same top-level answers with exit 2 before it changes any member, where before it committed in the current folder's repository, and `memforge sync` refuses an empty or relative one as `root_not_toplevel`.
- **Git commands could act on another repository.** Every git command memforge runs against a named repository (`memory-watch`, `memforge-resolve`, `memory-promote`, `memforge init-store`, `memory-audit` Tier 2, and `memforge sync`'s own store commands) ignores `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` and the other variables `git rev-parse --local-env-vars` lists. The Claude Code auto-commit hook and the example watchers unset them. Before, a tool started from a git hook or a shell with `GIT_DIR` exported read or committed to that other repository; `memory-audit` Tier 2 read another repository's history and waiver file while reporting on this store, so a violating history could pass or a clean one fail. `memory-dlp-scan --staged` keeps the inherited variables, since it runs inside git's own hook.
- **Merges in the concurrency audit.** `memory-audit` Tier 2 leaves a merge's change to the merged branch only when the merge keeps the branch's `status`, `superseded_by`, `decision_topic` and `topic_aliases` for that memory. A merge that writes another value of the same kind (for example a different `superseded_by` than the branch's resolve commit wrote) is reported on the merge. A change whose branch commits are older than the audit window, or that git cannot list, is also reported on the merge that brought it in; before, those commits were not checked and the merge finding was dropped.
- **Claude Code hooks and `readlink`.** The hooks never run a `readlink` that is itself a link; they use the first `readlink` on an absolute `PATH` entry that is a regular file outside the stores, and do nothing when there is none.
- **Read tracker and a linked ledger.** The Claude Code read tracker never reads or writes `.last_used.json` through a link, and leaves it alone when it is not a regular file. Before, a committed link made the hook copy the JSON file it named (for example a registry credentials file) into the store as a tracked change, and a dangling link made it create a file outside the store.
- **Frontmatter gate failed open in silence.** `gate-memory-frontmatter.py` still allows the write on an internal error, and now prints `gate-memory-frontmatter: internal error (<error class>); write allowed unchecked` to stderr.
- **`memory-promote` could leave a stale link.** It skipped a file it could not read, or a folder it could not list, when it looked for links to the memory it moves, then moved the memory with a clean exit. It now refuses before anything moves, naming each such path (exit 2).
- **`agents-md-gen` copied the home `CLAUDE.md` into AGENTS.md.** It read `~/.claude/CLAUDE.md`, following a link, and copied its `## Writing voice`, `## No fabrication`, `## One question at a time` and `## Shell` sections (matched by prefix, with their subsections) into the AGENTS.md it writes for the repository to commit. It no longer opens that file. AGENTS.md is built from the memory folders and `<cwd>/CLAUDE.md` only, and the `# Voice and rules (from ~/.claude/CLAUDE.md)` section and its source line are gone. Python callers of `render_agents_md` drop its first parameter, `claude_md_content`.
- **A pinned body or preamble could hide the generated sections.** The body of a pinned memory in the AGENTS.md that `agents-md-gen` writes, and the `_memforge.yaml` preamble in the `MEMORY.md` that `memory-index-gen` writes, could open a code fence, an HTML comment or an HTML block that swallowed the generated sections after it, or add a heading of its own. Both tools now copy each as literal text inside a backtick fence one longer than the longest run of backticks in it (at least three), so no line of it can close the fence and nothing in it (a heading, a fence, a comment, an HTML block) reaches the generated text. A body or preamble is no longer left out for an open fence, comment or HTML block, and no body heading is moved. A preamble that carries `generated by memory-index-gen` or `memforge:competing-claims` is still refused, since `memory-preamble-extract` and `memforge-migrate-claim-block` read `MEMORY.md` line by line and do not track fences. A name, description, topic or file name that opens a fence or comment is refused as before, because it lands in the middle of a line. `memory-preamble-extract` reads such a fenced preamble back: lines inside a fence never end the preamble, and a preamble that is one fence from its first line to its last is unwrapped, so extract then generate does not fence it twice. A hand-written `MEMORY.md` whose whole preamble is one code block is unwrapped on extract and shown as literal text again after generate. The cost: a pinned body's headings and lists now show as source text in a code block, not rendered (see Upgrade notes).
- **The competing-claims block could fail to parse.** It now quotes every string a YAML reader would not read back as the same text (`<<`, `=`, `.5e+3` and others) and escapes U+2028, U+2029, surrogates, U+FFFE and U+FFFF in double quotes. Before, `yaml.safe_load` refused the block, so a reader saw no competing claim. A value the old rules wrote plain and that reads back is still written plain.
- **Unicode line breaks in generated files.** U+2028 and U+2029 now count as line breaks in every generator cleaner: a space in names, descriptions, topics and file names, and a line break in pinned bodies and the preamble. VT, FF, U+001C to U+001E and U+0085 in a pinned body are line breaks too. A `_memforge.yaml` title holding U+2028 or U+2029 is refused and the default title used. Before, a reader that splits lines the way Python's `str.splitlines` does saw a forged `## Competing claims` or `# Repo-specific` line.
- **`memforge-resolve` deleted a file through a linked snooze folder.** A committed `.memforge/snoozes` (or `.memforge`) link made `memforge-resolve` delete `<topic>.yaml` wherever the link pointed: outside the store or, with `snoozes -> .` and topic `config`, the store's own `.memforge/config.yaml`, which it then committed. It now opens the snooze folder one name at a time without following a link and refuses, with exit 2 before any member changes, a `.memforge` or `.memforge/snoozes` that is a link or not a folder and a snooze that is a link or not a regular file. `memory-audit` Tier 2 reports a committed link at `.memforge`, at `.memforge/snoozes`, on the way to it or under it as a `snooze folder link` BLOCKER.
- **`memory-rollup` moved a linked file into the store, and undo trusted its history record.** With no `memory-link-rewriter` on `PATH`, `memory-rollup create` resolved a source that was a link and moved the file it named (from anywhere the user can write) into the rollup folder; with the rewriter found, a link to another store file moved that file. `create` now refuses a source that is a link or not a regular file (exit 2), and its fallback move copies without following a link and never replaces a file. `memory-rollup undo` deleted whatever store file a committed history record named as its README and, with no rewriter, overwrote any store file named as a move's source. It now takes each path only where `create` writes it (`<slug>/README.md`, `<slug>/<name>`, `<name>`), refuses an existing source, a linked moved file, a linked rollup folder and a malformed record, and changes nothing when it refuses (exit 2).
- **`memforge-resolve` and `memory-promote` committed files a name matched as a pattern.** File names were passed to `git add` and `git commit` as pathspecs, so a file named `*.md` swept staged and untracked files into the scope-locked resolve or promote commit. Names are now passed literally. `memory-audit` Tier 2 reads file names literally too, so a file named `*.md` no longer pulls older commits into a merge's judgment and a store folder named with `*` no longer reads another folder's waiver state.
- **One character in a commit subject turned Tier 2 off.** A line-break character other than `\n` (U+2028, `\x0b`, `\x1c`, `\r` and the others Python splits lines on) in any commit subject made `memory-audit` skip Tier 2 for the whole window with a WARN. The commit log is now read NUL-separated, so a subject is one commit's subject.
- **Config links were followed on their text, not as the file system follows them.** Tier 2 applied `..` in a config link's text before following an earlier link in it, and treated an absolute link into the repository as leaving it, so a later ordinary commit could change the config with no finding. Link chains are now followed one name at a time, an absolute target under the repository top is that repository path, and a config link chain that cannot be followed to a file inside the repository is a `config link` BLOCKER.
- **Recall hook found `memory-recall` by name.** The Claude Code `memory_recall_hook.py` started `memory-recall` by bare name, so on Windows a `memory-recall.exe` in the folder Claude Code was opened in ran on every prompt with no `PATH` mistake, and a `.` or relative `PATH` entry did the same elsewhere. It now starts the `memory-recall` in the scripts folder of the Python environment that runs it, else the first one on an absolute `PATH` entry, by absolute path, and runs neither when it lies inside a memory store (one found on `PATH` also when it lies inside the working folder, `~/.claude/projects`, `~/.claude/global-memory` or `~/.memforge`, also while `MEMFORGE_MEMORY_PATH` is set, or inside the repository that holds the working folder, one of those folders or a store). Run by an interpreter that cannot import memforge, it searches `PATH` itself and refuses exactly the same folders (absolute `PATH` entries only), with the same messages. When no `memory-recall` may be started, it writes one stderr line, `memory_recall_hook: recall is off: <reason>`, and the prompt goes through.
- **The architecture PDF build could print local files and fetch from the network.** pandoc passed raw HTML in the architecture docs through, and Chrome printed it with scripts, frames and the network on, so a docs change could make the attested PDF carry a file from the build machine or bytes fetched at build time. `docs/pdf/build-pdf.sh` now stops (no PDF written) on raw content in any format (pandoc's HTML writer also copies `revealjs`, `s5`, `slidy`, `slideous` and `dzslides` into the page; a TeX command such as `\newpage` counts), an image other than a diagram in `docs/architecture/diagrams/`, a key=value attribute or document metadata in the Markdown. Every file and folder the build reads must be a regular file or folder, not a link. Every entry of `docs/architecture/diagrams/` is checked, whatever its name or case, and a diagram is opened only under the exact name the check listed, so a link named `x.SVG` cannot stand in for `x.svg` on a case-insensitive file system. The stylesheet may not use `url(`, `image-set(`, `@import`, a backslash or `<`. The diagrams and stylesheet are written into the page, so its content security policy allows only inline `data:` images and inline styles and the page loads no file: no script, frame, object or connection. Chrome sends every request to a closed local proxy and resolves no host name, and the PDF jobs in `ci.yml` and `release.yml` keep no token in their checkout.
- **`memforge sync` read a signer list of any size.** A listed signer could commit a valid list padded with comments to any size, and every receiver read it whole for each commit judged against it. A list over 1 MiB (1,048,576 bytes) is now `list_invalid` (`list_removed` with detail `list_invalid` for the commit that writes it), decided from the object size before the list is read. An unsigned commit is now refused `unsigned` before its own list is read; before, an unsigned commit whose own list was invalid was refused `list_removed`, after the list was read in full.
- **`memforge sync` read more of a hostile tree than it needed.** A list file entry that names a tag was followed to its target and read at any size, so the 1 MiB limit did not hold for a tag-wrapped list, and the whole `.memforge/` folder of a commit was listed into memory, so a commit with millions of entries there made every receiver use memory in proportion. The entry must now name a blob: a tag or any other object is `list_invalid` (`list entry names a <type>, not a blob`), decided before the object is read, and a tag whose target was never sent is that refusal (exit 2) and no longer `object_missing` (exit 1). `memforge sync` asks git only for the three list file names and reads at most 4 KiB of the answer, so the memory it uses no longer grows with the entries (about 28 MiB flat at 3,000,000 entries on Python 3.12, against 869 MiB before); git still holds the tree it reads in its own process. On Windows, where the `git` found on `PATH` can be a launcher that starts the real program, an oversized listing is refused the same way: the two git pipes are closed and the process waited for, not read to the end, so the bound holds there too. A tree that names a list file more than once is `ambiguous_list` (before, the last copy was used). In `gpg` and `keyless` lists, as in `ssh` lists, every entry line is tab and printable ASCII only and fields split on space and tab only; both modes are still refused (`mode_unreleased`), so no store that syncs today changes. A well-formed store sees no change; the cost is one more `git cat-file -t` per list read.
- **A recall index that arrived by `git pull` was served as it came.** `memory-recall` loaded `.memforge/recall-index.json` without checking it against the memory files, and `memory-watch` and `memory-auto-commit.sh` committed it, so a repository could carry an index whose entries, labels (`do_not_inject`, `access`) and `folder` differed from the memory files and have it reach an agent's prompt. The index is now a local cache. It stores a fingerprint of the memory files (each file's relative name, size and modification time); `memory-recall` computes the same fingerprint from the folder before every query and rebuilds the index when the two differ, or when the synonym override `.memforge/recall-synonyms.yaml` changed, so no `--rebuild` is needed to keep it fresh (`--rebuild --force-rebuild` still rebuilds unconditionally, and answers from the fresh build in memory when the store cannot be written). A store it cannot write is answered from the build in memory, a linked index file is replaced by a regular file as before, and each entry's `folder` is the folder read, whatever the file says. The index also records the time it was built (`built_ns`, read before the first memory file is read), and a memory file whose modification time lies within 2 seconds of that time, before or after it, counts as changed and forces a rebuild, so an edit that keeps its size and lands in the same file-clock tick as the build (about 15 ms on Windows, 2 s on FAT) is not missed; a file older than that never forces one, and a file dated far in the future does not make every query rebuild. An index written before this field existed has no build time and is rebuilt once. Only names, sizes and times are compared; no file content is hashed. A pulled copy cannot match, since a fresh checkout has its own modification times. `memory-watch` and `memory-auto-commit.sh` no longer stage or commit the index. Every query now stats each memory file. If your repository already tracks the index, see Upgrade notes.
- **Automatic commits made a pre-commit hook skip the files.** `memory-watch`, `memory-auto-commit.sh` and the example watchers committed with `GIT_LITERAL_PATHSPECS=1` in the environment, which git also hands to the repository's hooks. A `memory-validate` pre-commit hook that selects `'*.md'` then matched no file and let a memory with broken frontmatter in, and so did the hook the quickstart printed. Each tool now names every file to git with the `:(literal)` pathspec magic and removes the variable from the environment of the commit (the PowerShell watcher removes it with the other git variables; that change was checked by reading, since no PowerShell was available to run it). A file named `*.md` or `[a].md` is still committed as itself only.
- **Control characters in store text reached the terminal.** A file name, a frontmatter value or a quoted line holding an escape sequence (a clipboard write, a window title, a screen erase) was printed as it was, and a line break in a name could forge an output line. Each of these tools now shows a store-derived field with every control character, a line break and tab included, as a visible escape such as `\x1b` or `\x0a`, and escapes every other control character, DEL, C1 character, U+2028 and U+2029 that it writes to standard output or standard error, whether or not a call site cleaned it: `memory-query`, `memory-audit`, `memory-audit-deep`, `memory-audit-log`, `memory-validate`, `memory-lint`, `memory-dedup`, `memory-cluster-suggest`, `memory-frontmatter-backfill`, `memory-link-rewriter`, `memory-dlp-scan`, `memory-rollup`, `memory-promote`, `memory-index-gen`, `agents-md-gen`, `memory-preamble-extract`, `memory-recall`, `memory-watch`, `memforge-resolve`, `memforge-detect`, `memforge-migrate-claim-block`, and `memforge sync` and `memforge init-store`. `--json` output is unchanged and a store with no control characters prints the same bytes as before. Not covered: `memforge paths`, which writes folder names as file-system bytes by design. `memory-recall` keeps `--format json` free of escapes except that DEL, C1 characters, U+2028 and U+2029 are written as `\uXXXX`. A commit message a tool writes still carries a file name as given, except the Claude Code hook, which replaces control characters.
- **A folder a tool could not look at was taken for absent.** The tools tested a folder with `os.path.exists`, `isdir` and `isfile`, which answer false for any error, so a memory folder, store, config or subfolder that was there but could not be searched (permissions on a folder on the way, a link loop) was skipped as if missing. `memory-audit` said it does not exist, `memory-validate` said not validated, `memory-index-gen` said `folder does not exist`, `memory-audit-log verify` said `skipping nonexistent` and exited 0, and `memforge-resolve` decided on the other folders only. A path that is there but cannot be looked at is now reported the way each tool reports a folder it cannot list: `memory-audit` and `memory-audit-deep` as a violation (exit 1 with `--strict`), `memory-validate` as a file it cannot read (exit 1), `memory-index-gen` with `cannot list the memory folder` (`MEMORY.md not built`, exit 1), `memory-audit-log verify` as `TAMPER ... cannot check <log>` (exit 1), `memory-audit-log append` with `refusing to append: cannot check <log>` (exit 1; it was `folder not found`, exit 2), `memory-link-rewriter` with exit 2, `memory-dlp-scan --memory-folders` as `unreadable_file` (BLOCKER) and `memforge-detect` as an integrity finding. `memory-query`, `memory-cluster-suggest`, `memory-frontmatter-backfill`, `memory-preamble-extract` and `memory-lint` print `error: cannot read <folder>: <reason>` and exit 1 (they said `skipping nonexistent` or reported a clean, empty folder with exit 0), and the other folders are still processed; `memory-dedup` exits 2 with the same message; `memory-watch` skips the folder with `cannot read it`; `memforge-migrate-claim-block` warns `cannot list`. `agents-md-gen` stops with exit 2 and writes nothing when a memory folder cannot be looked at (it wrote an AGENTS.md without that folder's memories). `memforge-resolve` stops with exit 2 before changing anything when a file or folder in the store cannot be read, since it may hold a live member of the topic. `memory-preamble-extract` reports an unreadable `MEMORY.md` (exit 1) and `memory-audit` reports `MEMORY.md: cannot read file`, where both said `MEMORY.md missing`. `memory-index-gen` reports an entry of a folder that can be listed but not searched as `cannot tell whether it is a folder` (exit 1; the rollup in it had been left out of `MEMORY.md` without a word), and warns on a `_memforge.yaml` it cannot look at and uses the defaults, as for a malformed one. The `.memforge/config.yaml` lookup stops at a config it cannot look at and warns `cannot read <path>; using the default config`; it walked past it, so a config further up the tree applied. The default memory folders keep the Claude Code layout when `~/.claude/global-memory` or the per-project folder is there but cannot be looked at, and every default folder that cannot be looked at keeps its place in the search; before, the tools switched to the `~/.memforge` store without a word or dropped the folder. A folder that does not exist keeps its message. `memory-recall` is the exception: recall is fail-open-empty by spec, so an unreadable folder yields no hits. `memforge.walk_memory_files` (public API) now yields a root it cannot look at, as it already did for a root it cannot list; it yielded nothing.
- **`memory-rollup` followed a linked or unreadable history folder.** `list` followed a `.memforge-rollup-history` that was a link and listed the records of a folder outside the memory folder; `undo` failed at the first record instead of refusing the folder; `create` crashed after it had moved the files when the history entry was a regular file, leaving the rollup with no record; and a history folder that could not be listed read as empty, so `list` exited 0 and `undo` said there was no history. `create`, `undo` and `list` now refuse a history folder that is a link or not a folder, before they read or move anything: `error: refusing to use a rollup history folder that is a symlink or not a folder: <dir>` (exit 2). A history folder they cannot list, and a memory or rollup folder they cannot check, are `error: cannot read ...` (exit 2); `list` no longer exits 0 for them, and `create` no longer says `folder not found` for a folder it could not check. A dangling link at the rollup name is `rollup folder already exists` (exit 2). `create` also checks that an existing history folder can take the record before it moves any file, and refuses with nothing moved (`cannot write to the rollup history folder`, exit 2); before, the files moved and no undo record was written. The check creates a probe file with a random name in one exclusive step, so a file already at that name is never opened, emptied or removed; it removes only the file it created (it compares the entry with the one it made, and a replaced entry is left and reported), tries the removal again when the first fails, and prints `error: left behind <path>; remove it by hand` when the probe stays. On Windows the probe is closed before it is removed, since an open file cannot be deleted there, so `memory-rollup create` works on a store that already has a history folder. The folder path and the error in its message are shown as visible text.

### Fixed

- The wheel now ships the tag taxonomy, so an installed `memory-audit-deep` can check tags. Against the shipped taxonomy an unknown tag is a warning and never changes the exit code. Against your own taxonomy (`--memforge-root`) it is a violation, as before. Until now the installed command skipped the check and `--strict` failed on every install.
- `memory-audit` and `memory-validate` no longer report a clean result when they checked nothing. With no folder to check, a named `--path` that does not exist, or a default folder path that is a file, they say so, and `--strict` exits non-zero.
- `memory-watch` no longer stops on a file name that is not valid UTF-8, and reads file names literally.
- The default store path no longer fails with an exception in a working folder whose name is not valid UTF-8.
- The quickstart's `memory-validate` pre-commit hook split a staged file name with a space into two arguments, so it checked the wrong files. It now passes names NUL-separated, and also checks a file that replaced a symlink.
- A memory whose `name` or `description` is not text (`name: 2024`, `description: [a]`) no longer stops `memory-index-gen` and `memory-query` for the whole folder; the value is shown as text. `memory-audit` reports it (`field 'name' must be text`) and `memory-validate` warns, as they already did for `type`, `sensitivity`, `tier` and `status`.
- `memory-index-gen` no longer leaves `superseded`, `dropped` or `archived` memories out of `MEMORY.md` while they sit outside `archive/`. Integrity invariant 7 requires a pointer for every `tier: index` file outside `archive/`, so the documented flow (`memforge-resolve`, `memory-index-gen`, `memory-audit --strict`) failed with `Orphan file (no pointer)` for the superseded member. It now passes. The resolve commit still leaves `MEMORY.md` unchanged, as resolve post-condition 4 requires; run `memory-index-gen` afterwards to clear the competing-claims block.
- A folder walk no longer skips a subfolder it cannot list without a word. `memory-link-rewriter` stops with exit 2 and names it, `memory-audit`, `memory-audit-deep` and `memory-validate` report it, and `memory-index-gen` reports it (and an unreadable top-level file) as an error, exit 1.
- A memory folder that cannot itself be listed (for example `chmod 000` on the folder) is reported. Tools saw an empty store: `memory-link-rewriter check` reported 0 files with exit 0 and `rename` moved the file without rewriting the links to it, and `memory-audit-deep` and `memory-validate --path` reported clean. Each now reports the folder as a file it cannot read: `memory-link-rewriter` stops with exit 2 and nothing is moved, `memory-validate` reports it and exits 1, and `memory-audit-deep` reports it as a violation (exit 1 with `--strict`, as for any violation). `memory-audit` and `memory-index-gen`, which stopped with a `PermissionError` traceback, now report `cannot list the memory folder` (an integrity violation in `memory-audit`, which goes on to the next folder; `MEMORY.md not built`, exit 1, in `memory-index-gen`). `memory-dlp-scan --memory-folders` already reported it as `unreadable_file`; the reason now says `Permission denied`. A folder that does not exist keeps its message.
- `agents-md-gen` reads every folder the memforge path rules resolve, in order. It kept only folders named `memory` or `global-memory`, so with `MEMFORGE_MEMORY_PATH` set to any other folder it wrote an AGENTS.md with no memories and exit 0. A `MEMFORGE_MEMORY_PATH` folder with no memories is named on stderr.
- A memory whose `sensitivity` is a list, a mapping or a number no longer stops `agents-md-gen` with an exception; the memory is left out and named on stderr.
- A `.memforge` that is a symlink no longer stops `memory-watch` from committing the memory change beside it.
- `memory-audit --help` names its real default folders (those `memforge paths` prints) instead of only `~/.claude/global-memory/`.
- `memory-index-gen` no longer stops with a `TypeError` on a competing-claims member whose `uid` or `updated` is a list, mapping, number or boolean. The value is shown as text, the member stays in its group, and stderr names the file (`field 'updated' must be text; it is shown as text in the competing-claims block`).
- On Python 3.10 to 3.13 the tools no longer stop with a `PermissionError` on a path inside a folder they cannot search: `memory-audit`, `memory-audit-deep`, `memory-index-gen`, `memory-lint`, `memory-validate`, `memory-link-rewriter`, `memory-query`, `memforge-resolve`, `memforge-migrate-claim-block`, `memory-preamble-extract`, `memory-frontmatter-backfill`, `memory-rollup`, and default-folder discovery under a home folder that cannot be searched. Each reports the file or folder as it does on Python 3.14.
- `memory-audit-log verify`, `tail` and `append` no longer stop with a traceback when the memory folder cannot be searched, on any Python. `verify` lists `cannot check <log>: ...` as a failure, and `tail` and `append` print `error: cannot check <log>: ...`; all three exit 1.
- `memory-rollup list` and `undo` no longer stop with a traceback when a record in the rollup history folder cannot be checked, on any Python. They print `error: cannot read rollup history at <dir>: ...` and exit 2.
- `memory-rollup undo` again reverses history written by the earlier `create` when the memory folder was given as a relative path or through a symlink (for example a relative `MEMFORGE_MEMORY_PATH`); the refusal for a record that named a path outside the memory folder had also refused these. It still takes each path only where `create` writes it.
- `memory-rollup undo` refuses a history record that is not valid JSON or not a JSON object with an error naming the record (exit 2, nothing changed) instead of a traceback, and its tampered-record refusal now names the record file. `memory-rollup list` shows such a record as `[unreadable]` or `[malformed]` (also when operation, timestamp or slug is not text, or moved is not a list) and lists the rest.
- `memory-index-gen --write` reports `error: <folder>: cannot write MEMORY.md: <reason>` (exit 1) for a store folder it cannot write, instead of a `PermissionError` traceback.
- `memory-watch` without `watchdog` tells the user to reinstall `ildan-memforge` (it comes with the package) and no longer suggests installing `watchdog` alone.
- On Windows, `memory-audit-log` appends no longer give up waiting for the log lock after ten seconds and write without it; under many concurrent writers that could fork the hash chain. An append now waits until the lock is free, as it does on macOS and Linux.
- A source tree that is not installed reports version 1.0.0 (`memforge.__version__` and the default spec version fell back to 0.9.1 and 0.9.0), and a test keeps both equal to `pyproject.toml`. Installed wheels were not affected.
- The documented `memory-validate` pre-commit hook starts with `unset GIT_LITERAL_PATHSPECS`, so a commit made from a shell or by a tool that sets the variable no longer skips the check.
- Windows: files are written with the platform's newline (CRLF), and every reader accepts LF and CRLF.
- Docs: the adapter guide's Tier B pre-commit hook now matches the quickstart hook, and its Tier A shim gates the folders `memforge paths` prints. The aider sensitivity recipe runs the export-tier audit and DLP scan before it builds the filtered index, and says `--viewer-tier` filters by `access`. Recall docs say there is no sensitivity ceiling unless `--sensitivity-max` is passed. The commit trust docs run the `git config` lines inside the store's repository, push with `-u`, write signer lines as `<name> namespaces="git" <key>`, and cover `no_upstream` and `list_removed`. The example commit-msg hook matches snooze files with non-ASCII, quote or backslash characters in the name, and its docs say what it does not check (a config commit with other files, a snooze name with a newline). `adapters/README.md` tells a new adapter to commit only memory files and never `.memforge/`, instead of `git add -A`. The adapter guide describes the shipped `consolidate-memory` skill as it is. `tool_in_store` is in the exit 1 lists of `docs/team-bootstrap.md` and the spec. The example watcher's usage line names a folder `memforge paths` prints. The quickstart points at the shipped commit-msg hook and watcher instead of printing copies of them; its watcher copy lacked the shipped file's `unset GIT_DIR ...` line. The adapter guide and README give `memforge-resolve` its `--memory-root <store>` flag, which a handler needs for a store that is not a default folder (without it the command searches the folders `memforge paths` prints and exits 0 with `no live members of decision_topic ... found.`). `docs/deployment.md` gives the `memory-dlp-scan --staged --strict` pre-commit line it names. The overview's hook check expects the memory file and not the signer list or the recall index in the commit. The spec states the signer-list grammar the code enforces and its 1 MiB cap. `docs/team-bootstrap.md` says `bad_commit` exits 1 while naming a fetched commit. `docs/architecture/diagrams.md` no longer says `memory-audit` Tier 2 runs a `git` found elsewhere in the store's repository. `memforge-migrate-claim-block` examples pass `--memory-root <store>`. The Aider and adapter watcher steps, and the Cursor systemd, launchd and Windows task templates, start the installed `memory-watch --path <memory folder>` command (the templates named `tools/memory-watch` and the Windows one asked for a separate `watchdog` install, which comes with the package). The Aider sensitivity recipe writes to a `mktemp` file. The conformance scenarios T6 and T7 pass `--path <store>`, and T6 names a `decision_topic` group. The `list_missing` detail text is explained. The recall docs describe the index as a local cache (`memory-recall` checks it before each query). The Windows known limits and the platform newline are in `spec/known-limitations.md`.
- The sdist carries the files its test suite needs.
- `SECURITY.md` names 1.0.x as the supported line and puts `memforge sync` in scope.
- `docs/team-bootstrap.md` says which sync failure reasons exit 1 and which exit 2. The README named an environment variable that does not exist (`MEMFORGE_PATHS`; the variable is `MEMFORGE_MEMORY_PATH`). The quickstart's audit step checked the default folders, not the store being set up.

### Upgrade notes

- `sync` ignores the old registry, revocation commits and per-memory signatures. Leave those files or delete them in a normal commit.
- To adopt commit trust, add a signer list for your signing method and run the first sync with a pin. See `docs/migrating-to-1.0.md`.
- Only `memforge sync` checks anything. Plain `git pull` and edits in your work tree are not checked.
- The writing tools and `memory-audit-log append` now refuse a file with a second hard link. If a backup or deduplication tool hard-links files in your store, those writes stop with a message naming the file; give the file its own copy (for example `cp note.md note.tmp && mv note.tmp note.md`) to write it again.
- If you relied on the old default store folder `~/.claude/projects/<USER>-claude-projects/memory/`, set `MEMFORGE_MEMORY_PATH` to it.
- `memory-dlp-scan --strict` now fails when an installed `detect-secrets` fails during the scan. Pre-commit hooks that ran clean only because the deep layer was failing will start to fail.
- A memory with a multi-line YAML description, or a tab or line break in its name, description or topic, now renders on one line in `MEMORY.md`. `memory-index-gen --check` reports drift on such a store until you regenerate it once.
- A memory whose name, description, topic or file name contains `generated by memory-index-gen` or `memforge:competing-claims`, an unclosed `<!--`, or a value that starts with a code fence, is left out of `MEMORY.md` and makes `memory-index-gen` exit 1. This includes a memory that only mentions the marker text. Reword it.
- `memory-index-gen` now writes a pointer for every top-level `tier: index` memory outside `archive/`, including `superseded`, `dropped` and `archived` ones, so `MEMORY.md` grows by one line per such file. A `MEMORY.md` that already passed `memory-audit` does not change. No tool moves a superseded file into `archive/` yet; do not move one by hand, since `memory-audit` then reports the winner's `replaces:` as dangling.
- `agents-md-gen` inlines only memories marked `pinned: true` (unquoted). If you relied on the old built-in file names, add `pinned: true` to those memories.
- The Claude Code hook and `memory-watch` no longer commit `.memforge/config.yaml`, snoozes or waivers. Commit those yourself, with their `memforge:` prefix.
- `memforge sync` refuses a signer list with a quoted principal or a control character: as `list_removed` (detail `list_invalid`) for the commit that writes it, and as `list_invalid` for commits after it. It no longer finds `git`, `ssh-keygen`, `gpg` or `gitsign` through a relative `PATH` entry (`.`, empty, `bin`) or, on Windows, in the current folder, and refuses with `tool_in_store` when one of them resolves to a file inside the store.
- `memory-audit` Tier 2 has new findings for commits in the last 30 days: a resolve commit that touches files outside its topic, an unprefixed config, snooze or member deletion, and a `superseded` status in any YAML spelling. List known benign commits in `.memforge/audit-waivers.yaml`.
- A setup that reached `git`, `detect-secrets` or a local-model CLI only through a relative `PATH` entry (`.`, an empty entry, `bin`) now gets "not found" from the tools, and `memory-audit` skips Tier 2 with a WARN that says so. Put the program's folder on `PATH` as an absolute path. A tool installed inside the memory store (for example a virtual environment created in the store folder) is now refused; move it out of the store. The Claude Code hooks skip helpers installed under `~/.claude/projects` or `~/.claude/global-memory`.
- `memory-audit` Tier 2 has new BLOCKERs for commits in the last 30 days: a merge that took the other side of a member, topic or config change, an ordinary commit to a case-variant or linked config path, and a live member moved into `archive/` by hand. Review each; list a benign one in `.memforge/audit-waivers.yaml` in a `memforge: waiver` commit, or let it age out.
- Every store gets a changed AGENTS.md on the next `agents-md-gen` run: the `# Voice and rules (from ~/.claude/CLAUDE.md)` section and its source line are gone, multi-line names or descriptions render on one line, and each pinned body now appears inside a code block as literal text (its headings and lists show as source text, not rendered, and it is no longer left out for an open fence or HTML block). Regenerate and commit it once.
- A store whose `_memforge.yaml` has a preamble gets one `memory-index-gen --check` drift report: the preamble is now copied as literal text inside a code fence, where it was bare text above the generated header. Regenerate `MEMORY.md` once. `memory-preamble-extract` unwraps such a fenced preamble, so extracting from the regenerated file gives the original text back.
- A store whose competing-claims block holds a value the block now quotes (`<<`, `=`, `.5e+3` and the like, or U+2028, U+2029, U+FFFE, U+FFFF) gets one `memory-index-gen --check` drift report. Regenerate `MEMORY.md` once.
- `memory-audit` Tier 2 has new BLOCKERs for commits in the last 30 days: a committed link at `.memforge`, at `.memforge/snoozes`, on the way to it or under it (`snooze folder link`), and a config link chain that cannot be followed to a file inside the repository (`config link`). Replace the link with the folder or file it names. An ordinary commit to the file a `..`-after-link or absolute in-repository config link names is now a `config edit` BLOCKER.
- Register `memory_recall_hook.py` with the absolute path of the Python interpreter of the environment `ildan-memforge` is installed in, not a bare `python3`. A bare `python3` still works when `memory-recall` is on an absolute `PATH` entry outside the folder Claude Code is opened in and its repository (a home folder kept as a git repository with `~/.local/bin` inside it does not qualify); otherwise the hook prints `memory_recall_hook: recall is off: <reason>` on stderr and recalls nothing.
- `memforge sync` refuses a signer list over 1 MiB, and refuses an unsigned commit as `unsigned` even when its own list is also bad (before: `list_removed`). Exit codes are unchanged (2).
- Waivers count only as committed at HEAD. If you kept the waiver file uncommitted, commit it alone as `memforge: waiver <reason>`. A waiver change made in an ordinary commit in the last 30 days is a `waiver edit` BLOCKER that no waiver can clear; review it and let it age out, or, in history you have not shared, split it into its own `memforge: waiver` commit. `topic_aliases` edits in ordinary commits are `alias edit` MAJORs and do not fail `--strict`.
- Tools kept inside the git repository that holds a memory folder are now refused, unless they are in the scripts folder of the Python environment memforge runs from. A project `.venv` inside the project repository, used to run memforge, is not refused. Tools in `~/.local/bin` when the home folder is a git repository holding the store are refused: move them outside that repository. A store that is its own repository is unaffected.
- On a system where every `readlink` on `PATH` is a link (busybox), the Claude Code hooks do nothing.
- A store whose last 30 days hold a merge that rewrote a branch's supersession or topic, or merged a branch commit older than 30 days that changed a member, a config, snooze, waiver or alias file, gets a new finding on that merge. Review it; if it is benign, waive the merge commit in a `memforge: waiver` commit or let it age out.
- `memory-promote` stops (exit 2) when the source folder holds a file it cannot read or a folder it cannot list. Fix the permissions, then promote again.
- If your repository already tracks `.memforge/recall-index.json` (an earlier version of `memory-watch` or the Claude Code hook committed it), nothing is deleted. The tracked file is rewritten locally as a cache and shows as modified, and no tool stages it. Run `git rm --cached .memforge/recall-index.json`, commit that, and add `.memforge/recall-index.json` to the memory folder's `.gitignore`. A copy that arrives by `git pull` is replaced on the next `memory-recall` query, because its fingerprint cannot match. `memory-index-gen --with-recall-index` writes the fingerprint too.
- A folder, store file or config a tool could not look at used to be skipped as absent, often with exit 0. These now exit 1 or 2 (see the Security entry), so a CI job or hook that ran clean only because of the skip will start to fail. Fix the permissions or the link loop.
- Command-line output prints a control character from a file name or frontmatter as an escape such as `\x1b`. A script that read the raw character out of that output gets the escape; `--json` is unchanged.
- On a Windows repository, files are written with CRLF line ends and read as LF or CRLF. A `memory-index-gen --check` run reads either.
- The Cursor daemon templates (`daemon-systemd.service`, `daemon-launchd.plist`, `daemon-windows-task.ps1`) changed: they now start the installed `memory-watch --path <memory folder>` command. Copy them again if you installed an earlier copy.

### Spec compatibility

Spec 0.8.0 to 1.0.0, a major revision. Integrity invariants 16 to 25 are retired and none is added, so a folder that was well-formed under 0.8.0 stays well-formed. The commit-history (Tier 2) rules are now enforced as the spec states them, and the spec adds the waiver-commit rule, so a store's recent history can show new findings; see Upgrade notes. Files that carry `identity` and `signature` frontmatter stay valid. What changes is the trust model: the normative text for per-memory signatures, the registry, revocation and attestations is gone, and commit trust is the one supported model.

### Pre-ship review

- **Tests.** `2202 passed, 12 skipped` locally on Python 3.12 and 3.14, and `135 passed` on the sync tests against a real GnuPG keyring. Each change merged only on green CI across three operating systems and three Python versions. New tests for a defect were run against the unfixed code first and failed there.
- **Panel.** Every voice came from a model family other than the one that wrote the change, and each was bound to the exact diff it reviewed. Architect (grok-reasoning), code review and adversarial critic (a second family), threat-modeler (a third family).
  - `memforge sync`: three rounds; all three voices approve the final round. A finding that the single-branch fetch could leave the tracking ref stale was rebutted with a test that asserts the ref moved.
  - Key-system removal: two rounds; architect approves; findings closed.
  - Legacy-field preservation on rewrite: found during this review (the reference tools reformatted an old `signature` block), fixed, three rounds. Architect and code review approve. The critic's last finding (quoted keys) is refuted by a passing test that failed on the first version of the fix.
  - Spec and docs text: the critic did not approve the draft in four rounds. On the final text the architect approved, the critic's two wording findings were fixed (the team guide overstated which commits are checked; the spec called a commit signature proof of authorship), and the next round passed.
  - Windows: the first CI run failed the sync tests on Windows only. Two causes, both fixed before merge: a file sync writes for git was written in text mode, and OpenSSH for Windows exits silently when `PROGRAMDATA` is missing from the environment, which the cleared verifier environment did not carry. The verifier now gets an empty folder of its own as `PROGRAMDATA`. The second cause was found and the fix proven on a Windows machine, not by CI rounds. Three rounds; architect and code review approve; the critic's two MINORs are that `PROGRAMDATA` also reaches the `gpg` and `keyless` tools, which were not run on Windows, and that a caller of the internal helper could override the folder (no call site does).
- **Threat-modeler.** Subprocess argument handling, temp-directory and signer-list file handling, fail-open paths in verification, the state write order and the legacy-field splice. No findings.
- **Carried, MINOR.** The shallow-range refusal depends on a parent object being absent rather than on the shallow file.
- **Not proven.** `keyless` has run only against a stub `gitsign`, and `gpg` verdicts depend on the receiver's keyring. Both modes stay refused (`mode_unreleased`) until their real-tool proofs pass.

## [0.12.1] - 2026-11-13

**Patch: `memforge-detect` resolves findings whose condition is gone. Package 0.12.1 / spec 0.8.0 unchanged. No format change; no folder migration.**

### Fixed

- `memforge-detect` now resolves stale findings. Before, a re-run only added items, so an open finding stayed open after its condition was fixed and the open count only grew. An open item in a category the run checked, and did not find again, is now marked `resolved` with a `resolved_by` note. Categories the run skipped (duplicates with no local dispatcher, lessons under `--no-lessons` or with no lessons file) are never auto-resolved. Done items stay done, and a resolved item that is found again is re-opened.

## [0.12.0] - 2026-08-05

**Minor: two new audit checks for defect classes that accumulated silently. Package 0.12.0 / spec 0.8.0 unchanged. No format change; no folder migration.**

Both come from auditing an 842-memory two-root corpus, and both share a shape: a real defect that every existing check passed over, so it grew unopposed until something looked for it directly.

### Added

- `memory-audit` reports a memory carrying no `topic:` tag. `tags` is load-bearing twice: recall folds it into the trigger set alongside `name` and `description`, and `memory-index-gen` derives the `MEMORY.md` topic section from it. So an untagged memory is both harder for recall to surface and lands in a flat `(no topic)` dump. Nothing detected it, because `memory-frontmatter-backfill` infers a topic only from a named subfolder or a filename matching its inline `KNOWN_TOPICS` set: a top-level file with an unrecognized name gets nothing, silently. 59 files had accumulated that way, putting 46 of 87 per-cwd pointers into `(no topic)`.
- `memory-audit` reports a memory whose top-level frontmatter disagrees with a nested `metadata:` block, naming each diverging field and both values. Some agent harnesses write their own canonical shape, nesting the prior frontmatter under `metadata:` and synthesizing fresh top-level fields. Nothing is lost, but every memforge tool reads only the top level, so the nested copy becomes invisible truth and the two drift apart. Measured: 460 of 842 files carried the nested block and 74 of those had at least one identity or lifecycle field disagreeing, while `--strict` exited 0 on all of it.

### Why these are HEALTH and not violations

Both files are well-formed and parse. An untagged memory is still reachable, just harder to find; a divergent one still loads. More to the point, a corpus that adopted a harness shape can carry many divergences through no fault of its own, and a check that fails `--strict` on that becomes a flag operators switch off. A disabled check protects nothing.

### Calibration

Fire rates were measured against the real corpus before shipping, not assumed. Missing-topic fires on 15 of 842 files (1.8%); divergence on 74 of 842 (8.8%). This project disabled a warning in 0.11.0 precisely because it fired on 101 of 101 files, and the same standard was applied here.

### Neither check repairs

Choosing a topic is a semantic call, and a wrong topic is worse than none because it files the memory where the operator will not look. Reconciling a divergence means deciding which level is authoritative, which is the operator's call, not a default. Both report and stop.

### Spec compatibility

No spec change. `spec/VERSION` stays 0.8.0. Both checks describe existing behavior rather than adding requirements, so every conformant folder stays conformant.

### Pre-ship review

**This release shipped before its review ran.** The package was tagged and published to PyPI without the pre-ship gate, and the review below was run afterward against the shipped diff. Recording that plainly, because a review section that does not say when it ran implies a gate that held.

- **What ran before the tag:** the full test suite (627 passing) and CI across three operating systems and three Python versions. Every new test was verified to fail on the unfixed code.
- **What did not run before the tag:** the cross-family voice panel and the judge pass.
- **Retroactive panel.** Architect (grok-reasoning) and adversarial critic (different family), plus an independent judge pass. **No BLOCKER, no MAJOR, and no 0.12.1 warranted.** Three MINOR findings, all noise-quality issues in a report-never-repair HEALTH feature that cannot corrupt data, change exit semantics, or fail a build. They ride along in the next scheduled release.
  - MINOR: `_metadata_divergence` compares with `!=`, so a value equal in meaning but not in type (an unquoted `created: 2026-07-17` parsed to a date against a quoted string) would be reported as diverging. Measured against the real corpus: 0 of 881 files hit this, and all 74 flagged divergences are genuine, so the class is real but currently unrealized. The published fire rate is unaffected.
  - MINOR: `_has_topic_tag` accepts a bare `topic:` with an empty suffix, which satisfies the check while still producing a degenerate topic section.
  - MINOR: a no-op f-string in one health message.
- **Refuted, with evidence.** The critic raised the case-sensitivity of `topic:` as a robustness gap. It is not: `recall`, `index_gen`, `cluster_suggest`, `query`, `rollup`, and `frontmatter_backfill` all match the exact lowercase literal, and the taxonomy states one canonical lowercase name per value. Matching case-insensitively would report `Topic:forge` as tagged while recall fails to strip the prefix and the index files it under `(no topic)`, hiding the very defect the check exists to surface. The check is correct as written.
- **Threat-modeler gate: does not apply.** Recorded as a deliberate determination rather than an omission, which is what was actually missing the first time. The gate triggers on new public modules, subprocess calls, file I/O, or cryptographic primitives. The release adds two private pure helpers to an existing module that read an already-parsed dict; zero of the four triggers fire, and all three reviewers reached that answer independently.

## [0.11.0] - 2026-08-05

**Minor: two signal-quality fixes to the diagnostic surface. Package 0.11.0 / spec 0.8.0 unchanged. No format change; no folder migration.**

Both changes come out of a full audit of an 834-memory two-root corpus. Neither is a correctness fix. Both target the same failure mode: a diagnostic that fires so often, or so silently, that the operator stops reading it.

### Changed

- `memory-dedup --description-warn-threshold` now defaults to `0`, which disables the length warning. The previous default of `50` flagged 101 of 101 top-level files in a real corpus. `description` is the authoritative recall text and is supposed to be descriptive: recall matches against it, and the `MEMORY.md` pointer hook is derived from it by deterministic truncation, so a long `description` costs nothing at the index. A check that fires on essentially every file is noise, and noise trains operators to skip the output that also carries the cloud-egress and sensitivity warnings. Pass a positive value to re-enable it.

### Added

- `memory-audit` now emits a HEALTH warning for a `MEMORY.md` pointer that carries no hook text because the `- [title](path):` prefix exhausted `POINTER_LINE_BYTE_CAP`. `memory-index-gen` has always degraded gracefully here (omit the hook rather than emit an over-cap line), but it did so silently, so the one line in the index with no relevance cue was also the one line nothing reported. The warning names the prefix byte count and the remediations: shorten the title, shorten the path, or move the memory behind a rollup README.

### Why not a hard failure

Failing `memory-index-gen` on this condition was considered and rejected. It has zero occurrences in the corpus that motivated the work, and hard-failing would trade a cosmetic loss for an availability one: a single over-long title would stop the index regenerating entirely. A generated artifact should degrade with observability rather than block. The emitted line remains a correct link, and truncation is lossless for recall because recall reads the frontmatter `description` and never the `MEMORY.md` hook.

### Spec compatibility

No spec change. Both the `180`-byte pointer cap and the hook-omission rule are unchanged behavior from spec v0.6.3 and v0.7.0; this release reports what the generator was already doing.

## [0.10.0] - 2026-08-02

**Minor: the write-boundary gate 0.9.0 described, actually shipped. Package 0.10.0 / spec 0.8.0 unchanged. Additive and backward-compatible; no existing well-formed folder breaks.**

0.9.0 added `memory-validate` and named a "Claude Code `PreToolUse` shim = pre-write" as the pre-write half of that gate. The shim was never written, and the check it would have called was parse-only, so the pre-write half did not exist in either sense. This release supplies both.

### Added

- `memforge.frontmatter.validate_required_fields(text, required=...)`: required-field conformance checking, the complement of `validate_frontmatter`. Two field sets ship: `NON_DERIVABLE_FIELDS` (`name`, `description`, `type`) as the default, and `V04_REQUIRED_FIELDS` for full spec conformance. The default is the narrow set deliberately, because `uid`, `tier`, `tags`, `owner`, `status` and `created` are all derivable later by `memory-frontmatter-backfill`; a write gate that denies on those fails noisily for no benefit, and a gate people switch off protects nothing.
- `adapters/claude-code/hooks/gate-memory-frontmatter.py`: the PreToolUse write-boundary gate the Claude Code adapter README has specified but which had never been written. Rejects a malformed memory write before the bytes reach disk, naming the offending field. For an `Edit` it validates the reconstructed post-write file rather than `new_string` alone, so an edit that strips a required field is caught. Fail-open on every error path, unconditionally.

### Changed

- `memory-frontmatter-backfill` now hoists an existing nested `metadata.type` to the required top-level `type`. Lossless and additive: the value is already in the file so nothing is guessed, and the `metadata` block is left intact. It still refuses to invent a `type` where none exists, since that is a semantic classification.

### Why

An agent harness may write `type` nested under a `metadata:` block, which is not the spec's top-level `type:`. That block is valid YAML, so a parse-only gate accepts it; no backfill could repair it, because inventing a semantic type is not safe; and the only component that detected it was `memory-audit`, whose findings land in a log. Files in that shape accumulated unnoticed. These three changes close the loop at each layer: detect at the write, repair what is repairable, and validate conformance separately from parseability.

### Spec compatibility

No spec change. `type` has been a required top-level field since v0.4.0; this release makes the tooling enforce and repair what the spec already stated.

## [0.9.2] - 2026-07-30

**Minor SPEC bump (0.7.0 -> 0.8.0) shipped in a package patch. Closes a second class of SILENT memory loss.**

This is BREAKING for `memory-audit --strict` on a store whose rollup `README.md` omits a sibling, in the same deliberate way 0.9.1's parentless-rollup check was: the condition it now fails on was previously passing while memories were unreachable.

### Spec compatibility

Spec 0.7.0 -> **0.8.0**, a minor bump. Names integrity invariant 28: **a rollup README reaches every sibling it hides.** Minor rather than patch because a folder well-formed under 0.7.x can be non-conformant under 0.8.0.

Invariants 7 and 8 govern `MEMORY.md`, and by the Rollup-subfolders section a detail file is deliberately NOT pointed to from `MEMORY.md`, so the rollup `README.md` is a detail file's ONLY path to a reader. Nothing previously required that README to actually reference its siblings, so a README could omit a memory and leave it exactly as unreachable as a subfolder with no README at all, which the folder layout already treats as malformed.

The pointer set is **CLOSED** to two forms so the check is deterministic and portable: a wikilink `[[slug]]` (optionally `[[slug|label]]` / `[[slug#anchor]]`), and a markdown inline link whose target ends in `.md`. A backticked bare filename is explicitly NOT a pointer. Pointers are read from **prose only**: fenced code blocks, inline code spans, and HTML comments are excluded, so a documentation example or a commented-out note cannot be mistaken for a membership claim. An open pointer set would let two conforming implementations disagree on the same folder, which an audit (binary) must never do.

### Added

- **`memory-audit` detects an INCOMPLETE rollup README, not just a missing one.** 0.9.1 added a check for a rollup subfolder with NO `README.md`; that check cannot see a subfolder whose README EXISTS but omits siblings. Three new INTEGRITY VIOLATIONS:
  - `Rollup README incomplete: <folder>/README.md has no pointer to N file(s): ...`
  - `Rollup README dangling pointer: <folder>/README.md points at N file(s) that exist nowhere in the store: ...`
  - `Rollup README unreadable: <folder>/README.md could not be read, so coverage cannot be verified (fail-closed); N sibling file(s) treated as unreachable`

  Both gap directions are reported and the specific slugs are always named in full rather than summarized as a count. The comparison is **set-based, never count-based**: a duplicate pointer alongside an omission yields equal counts while a memory is still lost.

  A pointer at a file in a different subfolder, or at store root, resolves against the whole store and is NOT dangling; only a target matching nothing anywhere is. `archive/`, dot-directories, and symlinked directories are excluded, matching the existing parentless-rollup check.

### Pre-ship review

Both spec-delta voices plus a threat-modeler pass. Five findings closed in-commit, two refuted with evidence:

- **Closed (BLOCKER, threat-modeler): fail-open on an unreadable README.** An earlier draft caught `OSError` and silently skipped the subfolder, so making a README unreadable suppressed every finding for it. A check whose purpose is finding completeness gaps must never report clean because it could not look. Now fails closed as its own violation.
- **Closed (MAJOR, architect): version bump misclassified** as a spec patch. A new hard conformance requirement that can invalidate previously-well-formed folders is a minor bump.
- **Closed (MAJOR, architect): non-deterministic audit surface.** "Pointer syntax unconstrained beyond resolvability" contradicted audit being binary. The set is now closed to two forms. Verified empirically first that no real store depends on the backtick form, so closing it breaks nothing live.
- **Closed (MAJOR, critic): links inside fenced blocks, code spans, and HTML comments** were unspecified, so two conforming implementations could disagree. Extraction is now prose-only. Latent rather than live when found.
- **Closed (self-found by dogfooding): a backtick citation of an external repo path** was read as a store pointer, producing a phantom dangling finding. Moot under the closed set.
- **Refuted (critic): "bump should be 0.9.0 because 0.7.x accepted backtick pointers."** 0.7.x had no rollup-coverage invariant at all, so no 0.7.x-conformant folder could have relied on them; the inverted test expectation was against an unreleased draft. The v0.4.0 precedent bumps the middle digit for exactly this class of change.
- **Refuted (threat-modeler): "an embedded NUL in a pointer crashes `Path(x).name`."** Verified it does not raise, and this code never opens a pointer target, it only compares basenames. No guard added rather than carrying dead defensive code; a regression test asserts the real property.

Deferred with a note, both pre-existing patterns shared with `_parentless_rollup_subfolders` and bounded by the audit running as the operator on the operator's own store: TOCTOU between the symlink check and the read, and an unbounded read on a very large README. The threat model itself recommended accept-and-document for the first.

### Tests

12 new cases: complete/markdown-style, wikilink-unreachable, numbered-prose (not bullet-anchored), dangling pre-rename, out-of-folder cross-reference, duplicate-pointer-masks-omission, backtick-is-never-a-pointer, backtick-external-repo-path, prose-only (fence + code span + HTML comment), unreadable-README-fails-closed, NUL-byte-does-not-crash, and end-to-end through `audit_target`. Audit suite 31 passed.

## [0.9.1] - 2026-07-29

**Patch: closes a class of SILENT memory loss. Package 0.9.1 / spec 0.7.0 (unchanged).**

One of these changes is BREAKING for `memory-audit --strict` on a store that
already has parentless rollup subfolders. See Breaking, below. The break is
deliberate: the condition it now fails on was previously passing while memories
were unreachable.

### Breaking

- **`memory-audit` now raises an integrity violation for a subfolder that holds `.md` files but has no `README.md` rollup parent.** Stores with such a subfolder will FAIL `--strict` until a rollup README is authored. This condition previously passed **silently** while every file in that subfolder was unreachable from `MEMORY.md`.

  Why it was invisible: `_disk_md_files()` adds `<topic>/README.md` to the pointer-comparable set only when that README exists. With the parent absent, the subfolder contributed nothing to the pointer/disk comparison, so there was nothing to compare and nothing to report. Spec §"Folder layout" already marks `README.md` "Required when subfolder exists"; the audit simply could not see the violation.

  Observed in the field before this release: a store with 17 subfolders holding 266 files had exactly ONE rollup parent. 266 memories were unreachable from the index and `memory-audit` reported clean.

  **Remediation:** author the missing `README.md` rollup parent(s), then regenerate with `memory-index-gen --write`. Do NOT add the detail files to `MEMORY.md` directly - that violates the rollup compression contract the spec defines (and the audit downgrades a direct pointer to a health advisory rather than accepting it as canonical).

  A file pointed at DIRECTLY from `MEMORY.md` is reachable and is NOT counted toward the violation; it continues to raise only the existing health advisory recommending the rollup pattern.

### Added

- **`memory-index-gen` now WARNS on stderr when it is about to ship a lossy index**, naming each parentless subfolder and its file count, on the `--write` path and on BOTH `--check` paths including `OK`. `OK` means "the generated file is byte-identical to the existing one", never "every memory is reachable" - staying silent there was the false-assurance case. Exit codes are unchanged; `memory-audit` remains the enforcement gate.

### Fixed

- **`memory-frontmatter-backfill` no longer fabricates an `access` label that contradicts an operator-set `sensitivity`.** It previously wrote `access: internal` unconditionally, so a file declaring `sensitivity: restricted` emerged asserting both - two classification fields disagreeing, one of them invented by tooling rather than chosen by the operator. Backfill now leaves `access` UNSET when `sensitivity` is `restricted` or `privileged`.

  Scope, stated precisely: this was **not** a live exposure. `recall._access_ok()` treats an absent access list and an `internal` label identically (both visible), and `sensitivity` is enforced by a separate ANDed gate, so nothing was bypassed. It would have BECOME an exposure the moment the no-label default changed to fail-closed - at which point every restricted file carrying a fabricated open label would have stayed visible while appearing gated. The non-restrictive default (`access: internal`) is unchanged.

- **`memory-audit`'s new subfolder walk skips symlinked directories.** `index_gen` guards symlinks in five places; `audit.py` had none. A symlink pointing outside the store (worst case `-> /`) would make the walk enumerate an arbitrary tree - a cheap denial-of-service against the audit - and could report a "subfolder" not part of the store.

- **`memory-audit`'s new subfolder walk excludes dot-directories** (`.git`, `.memforge`, and similar tooling state), matching `index_gen`. Previously only `archive/` was excluded, which was harmless before this release but would have raised an integrity violation for `.git/` as soon as any markdown appeared inside it.

## [0.9.0] - 2026-06-28

**Minor: write-boundary hardening. Package 0.9.0 / spec 0.7.0. Additive and backward-compatible; no existing well-formed folder breaks.**

### Added

- **`memory-validate` - the syntax-aware write-boundary gate.** The agent-neutral primitive an adapter invokes BEFORE accepting or committing a memory write, so a malformed file is rejected at the boundary instead of surfacing later as a `memory-audit` failure. HARD check (always fails): the frontmatter parses as a YAML mapping (the recurring unquoted-colon break, new integrity invariant 27). SOFT checks (fail only under `--strict`): pointer/line caps, required v0.4 fields, `tier`/`status` enums. Read-only; single-file and batch; `--json`. The parser (`memforge.frontmatter.validate_frontmatter`) and the caps/enums (`memforge.constants`) are shared with `memory-audit`, so the two cannot disagree. Replaces the previously Claude-Code-only frontmatter hook with one check every IDE wires (git pre-commit = universal; Claude Code `PreToolUse` shim = pre-write).
- **`memory-audit` waiver mechanism (`.memforge/audit-waivers.yaml`).** An explicit, recorded, fail-closed allowlist that suppresses immutable migration-era Tier 2 commit-log findings so a genuinely-clean corpus audits to zero. Waivers are REPORTED (audit prints "N waived" with the commit SHAs), never silent. Fail-closed: a missing, unreadable, malformed, non-mapping, or non-list-shaped waiver file yields an empty waiver set. `waived_commits` (>=7-char SHAs, short-token guarded) and/or a `superseded_transition_waived_before` date cutoff.
- **`memory-detect` hygiene orchestrator + `memory-audit` convention-drift health check** (folded from the curate work): runs the read-only detection primitives on a cadence and writes a prioritized findings queue; the MEMORY.md line-count + pointer-byte checks move from integrity violations to `[convention-drift]` health warnings (the spec uses SHOULD on those caps).

### Changed

- **Single-source `POINTER_LINE_BYTE_CAP` / `MEMORY_MD_LINE_CAP` (both 180)** in `memforge.constants`; `memory-audit`, `memory-validate`, and `memory-index-gen` all import them, closing the prior drift where the cap was defined independently in two modules. Fixed stale "under 150" warning text in `memory-audit`.

### Spec

- `spec/VERSION` 0.6.3 -> 0.7.0. New integrity invariant 27 (a recognized `---` fence MUST parse as a YAML mapping; an empty block is the empty mapping; an unclosed/absent fence is invariant 1's concern). New sections: "Write-validation operation (v0.7.0+)" (operation contract + post-conditions) and "Audit waivers (R2; v0.7.0+)" (file format + normative reported/fail-closed/min-SHA/trust-boundary semantics). No new REQUIRED frontmatter field; invariant 27 restates invariant 1's implicit parse requirement, so every well-formed pre-v0.7.0 folder stays well-formed.

### Tests

- New `tests/test_validate.py` (validate operation + the shared primitive: colon-break / non-mapping / empty + degenerate fence leniency / caps / required-field + enum warns / `--strict` escalation / `--json` / archive exclusion) and `tests/test_audit_waiver.py` (loader fail-closed shapes, short-token guard, date-cutoff coercion, git-integration suppress-and-report). Plus the folded `tests/test_detect.py` + `tests/test_audit_convention_drift.py`. Full suite 555 passed / 37 skipped.

### Pre-ship review

- Cross-family release-rigor panel. **Architect**: ship, no findings. **Threat-modeler (gemini-pro, gate 2)**: 2 MAJOR + 1 MINOR. MAJOR-1 fail-closed violation (a scalar `waived_commits` raised `TypeError` instead of yielding an empty set) and MAJOR-2 spoofing via type-confusion (`superseded_transition_waived_before: true` stringified to `"True"`, over-waiving all superseded transitions via string comparison) were both fixed; writing the regression tests surfaced a 3rd crash vector (an unquoted invalid date raises `ValueError` from the YAML timestamp constructor), also fixed by broadening the loader's fail-closed guard. The MINOR (unbounded file read on a local CLI) is carried per the voice's recommendation. **Adversarial critic (grok-reasoning, gate 1)**: 1 BLOCKER - `memory-validate` HARD-failed an empty/degenerate frontmatter fence (over-strict vs the lenient parser); fixed (validate now mirrors `has_frontmatter` leniency; empty block = empty mapping) and invariant 27 spec text reconciled.

## [0.8.1] - 2026-06-28

**Patch: deterministic pointer-hook truncation in `memory-index-gen`. Package 0.8.1 / spec 0.6.3. Backward-compatible; regenerating an index only shortens over-cap hooks, no folder breaks.**

### Fixed

- **`memory-index-gen` pointer lines now respect the 180-byte cap.** The generator embedded the full `description` in each `MEMORY.md` pointer line, so a rich description produced a pointer line over the spec's 180-byte cap. `memory-audit` flagged these as integrity violations, yet the generator itself produced them. The generator now truncates the hook to fit (split on a UTF-8 codepoint boundary, append the literal `...`); the authoritative full `description` is preserved in the file frontmatter and the recall index, so truncation is lossless for recall (recall reads the frontmatter `description`, not the MEMORY.md hook). The one un-truncatable case is a `[title](path)` prefix that alone exceeds the cap, which is emitted intact and attributable to the title/path.

### Spec

- `spec/VERSION` 0.6.2 -> 0.6.3. The "MEMORY.md format" section gains the generator pointer-truncation rule (generator MUST keep emitted pointer lines within the byte cap). Reconciles the generator with the existing 180-byte audit check. No frontmatter or normative-contract change; every v0.6.2 folder remains conformant under v0.6.3 readers.

### Tests

- New `tests/test_index_gen_pointer_truncation.py` (8 cases): `_truncate_hook` under-budget / over-budget-ellipsis / UTF-8-boundary split; `_bullet` no-description / short-untruncated / long-truncated-to-cap / frontmatter-description-preserved / prefix-over-cap-emitted-intact.

### Pre-ship review

- Cross-family panel (release-rigor gate 1): architect on grok-reasoning + adversarial critic on gemini-pro. The critic caught a BLOCKER (the prefix-over-cap edge case appended an over-cap hook instead of omitting it); fixed in-commit (hook now omitted when the title/path prefix leaves <=3 bytes). Resolution on the spec-invariant question: generator guarantee, no new folder integrity invariant (truncation is a generation/presentation rule, not a folder-conformance property). Gate 2 threat-modeler: WAIVED - the change is pure UTF-8 string formatting in index_gen with no new I/O, subprocess, filesystem, or cryptographic surface.

## [0.8.0] - 2026-06-27

**Minor: three additive improvements. Package 0.8.0 / spec 0.6.2. All changes are backward-compatible; no existing folder breaks.**

### Added

- **Wikilink rewriting in `memory-link-rewriter rename` and `rename-batch`** (`[[token]]` and `[[token|display]]`). Previously, renaming a file silently orphaned every inbound wikilink. The rewriter now rewrites `[[old-stem]]`, `[[old-name]]`, and `[[old-uid]]` occurrences when the token resolves unambiguously to the renamed file, preserving any `|display` text. A false-rewrite guard ensures only tokens in the rename alias set are touched; prose, ADR refs, and code fragments that happen to match `[[ ]]` are left untouched. Cross-root ambiguity resolves within the linking file's own root; tokens that remain ambiguous are skipped and listed in a printed "ambiguous/skipped" summary. Every rewrite is logged (`old -> new`, file, line) to stdout. Idempotent: a second identical rename-batch run produces zero changes.
- **Non-spec-tier warning in `memory-audit`**. Spec tiers are `index` and `detail` only. A file carrying any other `tier` value (e.g., `anchor`, `tactical`) was previously silently excluded from `MEMORY.md` and rollup generation with no diagnostic. `memory-audit` now emits a HEALTH warning `tier '<x>' is not a spec tier (index|detail)` for each such file. No auto-correction; this is advisory. Non-blocking (never an integrity violation).

### Changed

- **Pointer-line byte cap and MEMORY.md line cap raised from 150 to 180**. Descriptive filename slugs are longer than short identifiers; at the 150-byte ceiling, the hook portion of a pointer line had little room. In practice, the worst-case pointer line is approximately 159 bytes with a 55-character slug. 180 restores adequate hook room while remaining terminal-readable. This loosens an existing `SHOULD` warning threshold; no previously-conformant folder becomes non-conformant.

### Spec

- `spec/VERSION` 0.6.1 -> 0.6.2. `spec/SPEC.md` cap thresholds updated to 180. A `TODO` comment notes that project-specific pointer-line limits will become configurable via `.memforge/config.yaml` in a future major version.

### Tests

- New `tests/test_spec_delta_r1.py`: cap threshold flips at 180 (179 ok, 181 flags); wikilink rewrite covers stem form, name-with-underscores form, and `|display` passthrough; false-rewrite guard (unrelated prose token untouched); cross-root disambiguation (ambiguous token skipped); idempotency (second rename-batch pass = 0 changes); non-spec-tier warning fires for `tier: anchor` and not for `tier: detail`.

### Spec compatibility

- Fully backward-compatible. Existing folders remain conformant. Cap increase is a loosening. Wikilink rewriting is additive (previously ignored; now correctly maintained). Tier warning is advisory-only.

### Pre-ship review

- Multi-voice panel (architect + adversarial critic): wikilink alias-set approach confirmed correct; stem kept in alias set as required for the primary use case; idempotency and full rewrite logging adopted as mandatory mitigations; cross-root disambiguation specified. No blockers.

## [0.7.0] - 2026-06-14

**Minor: new `memory-lint` quality CLI + a broad security/correctness hardening pass. Package 0.7.0 ships the spec-0.6.1 surface; the spec track is on its own SemVer line.** All new frontmatter and config keys are optional, so existing folders remain conformant.

### Added

- **`memory-lint`** (20th console script): a recall-readiness + token-cost quality analyzer, sibling to `memory-audit`. Audit is deterministic conformance (binary pass/fail); lint is graded quality: it scores how findable each memory is through the recall trigger model (collision-based, reusing the recall index's own derivation), and flags token-cost issues (oversized descriptions, an over-budget always-set, weak-recall always-set members, oversized index-tier bodies, `do_not_inject` candidates). Lint is READ-ONLY and never gates another operation. Cloud safety: local-only by default, metadata-only payload, body behind a stronger opt-in, a fail-closed secret pre-scan, AND sensitivity/access filtering (restricted/privileged memories are never dispatched to a cloud model). Suggestions are advisory only.
- **`memory-audit` recall-field + budget checks**: spec-mandated WARNs for malformed `triggers`/`always`/`do_not_inject`, an advisory always-set budget WARN (configurable count + combined-description-char budget under `recall.*`), and a high-precision relative-date WARN scoped to `type: project`. All WARN-only (health), never integrity violations.

### Security

- **Path-traversal containment** across `resolve`, `rollup`, `index-gen` (slug validation + resolved-path containment; no arbitrary file delete/move/read outside the memory folder from hostile memory content).
- **Recall + lint + dedup RBAC**: recall `_access_ok` is fail-closed for non-`team:` access labels (counsel/role); lint and dedup cloud dispatch honor sensitivity/access; `memory-audit` and DLP read sensitivity through the one canonical parser.
- **Cryptographic attribution**: registry signatures verify against the registered key material in an ephemeral keyring (not the ambient GPG keyring), pin the exact fingerprint (RSA-4096 signing-subkey supported), bind the signing key id, and refuse to sign or load with a superseded key. The revocation-snapshot walk floor is disabled (full verified walk; pre-snapshot revocations stay enforced).
- **DLP coverage** broadened (bare AWS secret with padding, 64-char hex, unquoted credentials, spaced SSN); the hardened `is_local_dispatcher` classifies by executable allowlist (extensible via `MEMFORGE_LOCAL_DISPATCHERS`) so a cloud command cannot pose as local; `agents-md-gen` DLP pre-scan fails closed; YAML emission escapes control characters.

### Changed

- **Portability**: `default_memory_paths()` is now the single source for all CLIs: `MEMFORGE_MEMORY_PATH` env override, then a grandfathered Claude Code layout if present, then the IDE-neutral `~/.memforge/{memory,global-memory}` default. OS-neutral user detection (`USER` or `USERNAME`). The package is IDE- and OS-agnostic out of the box.

### Spec

- spec/VERSION 0.6.0 -> 0.6.1. Added "Recall-readiness lint" (dimensions, always-set budget guidance, normative cloud-dispatch safety posture, informative consolidation orchestration) and a tier-vs-recall clarification (live `tier: detail` memories are recall-eligible; the index/detail split governs the `MEMORY.md` hotlist, not query-driven recall). Additive and backward-compatible.

### CI

- New `package-version-check` (asserts `__version__` == installed metadata == a matching CHANGELOG heading), a release-time `test` gate (pytest + version consistency on the exact tag commit), and conservative runtime-dependency upper bounds.
- **Signed releases + SBOM.** The release tag is signed (SSH, GitHub-verified), and `release.yml` generates a CycloneDX SBOM from the built wheel and attaches it to the GitHub Release alongside the sdist and wheel.
- **Supply-chain audit gate.** A `supply-chain-audit` CI job resolves the shipped runtime dependency tree and runs `pip-audit --strict` against it (OSV/PyPI advisories) on every push and PR.

### Spec compatibility

- Minor + backward-compatible. Every pre-0.7.0 memory folder remains well-formed: all new frontmatter (`triggers`/`always`/`do_not_inject` from 0.6.0) and config keys (`recall.*`) are optional, and the new audit checks are WARN-only (never integrity violations). The package version (0.7.0) and the spec version (0.6.1, an additive lint section) move on independent SemVer tracks.

### Pre-ship review

- §1 spec-delta + §2 code threat-model: a multi-pass adversarial review (15 grounded reviewers across craftsmanship / extensibility / documentation / security, with an adversarial-verify pass on every high-severity finding, run to convergence over three iterations plus a targeted security re-verify). All BLOCKERs + confirmed MAJORs closed in-commit with regression tests; the deploy-critical security class (path traversal, recall RBAC, cryptographic-attribution trust root, revocation correctness) is fixed and re-verified.
- §6 test gate: full suite 495 passed / 1 skipped (gpg-gated crypto exercised); all CLI `--help` smokes OK; clean wheel builds at 0.7.0 with the `memory-lint` console script.
- Sanitization clean (operator paths, lockdown terms, dashes, hashtags) on the staged diff, the commit message, and new files.

## [0.6.1] - 2026-06-07

**Docs + packaging patch (as tagged). At the v0.6.1 tag this was a package-only release: no spec change, no code change, spec/VERSION 0.6.0.** Corrects publicly-rendered metadata on the v0.6.0 release: the PyPI package page and README badge row carried a broken Python-versions badge, a stale Status table, and a version-pinned DOI. PyPI artifacts are immutable per version, so the fixes ship as 0.6.1.

> Note for readers of the current tree: the **spec** version (an independent SemVer track per the header above) is bumped to 0.6.1 by the `## [Unreleased]` recall-readiness work, which also adds the `memory-lint` code surface. That spec-and-code work is NOT part of the tagged v0.6.1 package release described in this section; see `## [Unreleased]` for the in-flight changes the working tree carries.

### Packaging

- **PyPI trove classifiers added** (`Programming Language :: Python :: 3.10 / 3.11 / 3.12`, plus license / audience / topic). The shields.io `pypi/pyversions` badge reads classifiers, not `requires-python`, so without them the badge rendered "python | missing". Classifiers match the CI test matrix (3.10 to 3.12).

### Documentation

- README Status table: v0.6.0 row dated 2026-06-07 (was "(unreleased)").
- README DOI badge + "Cite this work" switched from the v0.5.6 version-specific DOI to the version-agnostic concept DOI (`10.5281/zenodo.20113963`), which always resolves to the latest archived release.
- README CLI-count corrected (17 -> 19 console scripts); `memory-frontmatter-backfill`, `memory-preamble-extract`, and `agents-md-gen` added to the tools table; quickstart `--version` comment bumped to 0.6.1.

### Spec compatibility

- None. Package-only patch; every folder valid under 0.6.0 stays valid.

### Pre-ship review

- WAIVER (§1 spec-delta panel): no `spec/SPEC.md` or `spec/VERSION` change.
- WAIVER (§2 code threat-model panel): no `src/memforge/**` change (packaging metadata + markdown only).
- §3 docs parity: README / CHANGELOG / Status table verified against the shipping version.
- §6 test gate: full suite green; CLI `--help` smoke OK.
- Sanitization clean (operator paths, lockdown terms, dashes, hashtags) on diff, commit message, and tag annotation.

## [0.6.0] - 2026-06-07

**Minor spec bump: query-triggered recall. spec/VERSION 0.5.3 -> 0.6.0; package and spec versions re-converge at 0.6.0.** Adds an alternative, query-driven loading strategy so adapters can surface only the memories whose triggers match a query instead of bulk-loading the whole `MEMORY.md` index every session. All new frontmatter is OPTIONAL, so existing folders remain well-formed and older tools ignore the new keys.

### Added

- **Spec: three optional recall frontmatter fields** (`triggers`, `always`, `do_not_inject`) and a new normative §"Recall operation (v0.6.0+)" defined by frontmatter contract + post-conditions. The matching algorithm, index format, ranking, and injection mechanism are explicitly reference-implementation + adapter concerns (per the loading-semantics principle, SPEC §"Goal"). Recall surfaces memory `description` text, never bodies; honors `sensitivity` / `access` and the live-set partition; and is fail-open-empty (never blocks the operation it serves). Reconciles with the reserved outbound `dynamic_supplement` field. New integrity invariant 26.
- **Tooling: `memory-index-gen --with-recall-index`** emits a derived recall inverted-index artifact alongside `MEMORY.md` (incremental + atomic). **New `memory-recall` reader** does query-time matching for latency-sensitive per-query hooks. The build step and query step are separate operations.
- **Adapter: Claude Code recall hook** in `adapters/claude-code/` that shells the installed `memory-recall` on each prompt (with a hard shell timeout), plus recompile-on-change wiring.

### Changed

- Tier semantics: `tier: index` is now "eligible for session-start loading and/or query-triggered recall" (was "loaded at session start").

### Spec compatibility

- Minor bump. Every pre-v0.6.0 folder remains well-formed under v0.6.0 readers; v0.6.0 folders remain readable by v0.5.x tools, which ignore the unknown `triggers` / `always` / `do_not_inject` keys. No required-field changes.

### Pre-ship review

- §1 spec-delta panel (architect + adversarial critic, different model families): ship-with-fixes, 0 BLOCKERs; fixes applied in-commit.
- §2 code threat-model panel (STRIDE, two families): 0 residual BLOCKERs; symlink-safe walk, load-time index hardening + injection preamble, fail-closed sensitivity, bounded reads. 5 security regression tests.
- §6 test gate: full suite 264 passed / 2 skipped; CLI --help smoke OK; CI green across Ubuntu / macOS / Windows on Python 3.10 to 3.12.
- Sanitization clean (operator paths, lockdown terms, dashes, hashtags) on diff, commit messages, and new files.

## [0.5.6] - 2026-05-11

**Docs + examples patch. No spec change. No code change. spec/VERSION stays at 0.5.3.** Closes "we described patterns but didn't ship code" gaps surfaced after v0.5.5: ship copy-paste-ready example scripts where the docs describe operator-side glue.

### New: `examples/` directory

Drop-in scripts operators can fork. Each example ships in two flavors: bash (macOS / Linux / Git Bash on Windows / WSL) and PowerShell (native Windows + cross-platform pwsh 7+).

- `examples/git-hooks/commit-msg` - bash `commit-msg` hook that enforces the `memforge:` prefix grammar for Tier 2 scoped paths (operator-registry, config, snoozes, revocations). Rejects bad commits with diagnostic before they land.
- `examples/git-hooks/commit-msg.ps1` - PowerShell port of the above. Windows install instructions in `examples/README.md` (thin `commit-msg` wrapper invoking pwsh).
- `examples/scripts/memforge-auto-commit-watcher.sh` - bash file-system watcher that auto-commits memory `.md` writes. macOS (fswatch) and Linux (inotifywait) supported. Explicitly skips Tier 2 scoped paths so the `memforge` CLI's own commits are not raced.
- `examples/scripts/memforge-auto-commit-watcher.ps1` - PowerShell port using `System.IO.FileSystemWatcher`; no external dependencies on Windows.
- `examples/websocket/README.md` - scaffold for operators standing up the v0.5 WebSocket messaging substrate. Names this folder as starter material, NOT a deployable relay. Documents the gap: the reference CLI does not yet ship a WebSocket adapter; teams implement / contract their own relay against the spec contract.
- `examples/websocket/config.example.yaml` - reference shape for the `.memforge/config.yaml` `messaging:` block when adapter is `websocket`. Forward-looking: schema a future MemForge client adapter will read.
- `examples/websocket/probe.py` - minimal Python diagnostic that opens a `wss://` connection, authenticates with a per-operator bearer token, round-trips a placeholder envelope, and prints the echo. Verifies TLS handshake, auth, and relay reachability. Not a complete MemForge client; placeholder signatures noted in-source.
- `examples/README.md` - index + Windows install instructions for each script.

### Documentation

- `docs/quickstart.md`: §"Commit hygiene + signed `memforge:` prefixes" now cross-links the bash + PowerShell example scripts. The two-pattern walkthrough (commit-msg hook + auto-commit watcher) shows the bash version inline and points readers at the PowerShell drop-ins.
- `docs/team-bootstrap.md`: §"Pick your transport: git-only or WebSocket?" gains a new sub-section "Starter material for standing up a relay" pointing at `examples/websocket/`.

### Pre-ship review

Docs + examples patch; below the panel-gate bar in the release-rigor protocol. Sanitization grep on the staged diff is clean. Example scripts pass syntax-level validation where the validator is available locally: bash via `bash -n` (OK); Python (probe.py) via `ast.parse` (OK); YAML (config.example.yaml) via `yaml.safe_load` (OK). The PowerShell scripts (`commit-msg.ps1` and `memforge-auto-commit-watcher.ps1`) were authored to PowerShell 5.1 + pwsh 7+ idioms but ship UNTESTED on Windows: the operator-side dev box did not have pwsh installed. Windows users hitting a syntax error should file an Issue; expect a fast turnaround patch.

### Spec compatibility

No spec or code change. Adapters built against v0.5.3 / v0.5.4 / v0.5.5 work unchanged on v0.5.6.

## [0.5.5] - 2026-05-11

**Docs-only patch. No spec change. No code change. No new tests.** Closes two documentation gaps surfaced by reader-question on the v0.5.4 blog post.

### Documentation

- `docs/team-bootstrap.md`: new section "Pick your transport: git-only or WebSocket?" between Prerequisites and Step 1. Operator-decision framing for the two v0.5 messaging substrates: when git-only is correct (solo / low-cadence teams), when WebSocket is the right choice (high cadence, central audit, regulated environments), what WebSocket costs (relay infrastructure + per-operator auth + TLS termination), and how teams can flip later without re-keying.
- `docs/quickstart.md`: new section "Commit hygiene + signed `memforge:` prefixes" right after Step 4 (init-store). Explains that MemForge does not install git hooks (operator responsibility); documents the `memforge:` commit-prefix grammar that audit / resolve / revoke parse; covers the Claude Code PostToolUse auto-commit-hook bonus path; sketches two reasonable wire-your-own patterns for non-CC users.
- `README.md`: pointer lines updated to surface the two new sections from the quickstart + team-bootstrap.
- `spec/SPEC.md` §"Messaging adapter contract (WebSocket reference; v0.5.0+)": new opening paragraph "When to use it" with operator-decision framing + link to the team-bootstrap section.

### Pre-ship review

Docs-only patch; below the panel-gate bar in the release-rigor protocol. Sanitization grep on the staged diff is clean.

### Spec compatibility

No spec or code change. spec/VERSION stays at 0.5.3. Adapters built against v0.5.3 / v0.5.4 work unchanged on v0.5.5.

## [0.5.4] - 2026-05-11

**Patch release. Reference-CLI bug fix only; no spec change (spec stays at 0.5.3).** This release ran the release-rigor critic + threat-modeler voices on the patch surface pre-tag; the critic surfaced one MAJOR (over-broad archive-subfolder exclusion) which was closed in-commit before tag.

### Reference implementation changes

- `memforge.cli.audit`: fix false-positive `Orphan pointer (no file)` integrity violations for MEMORY.md pointers targeting subfolder detail files that exist on disk. Background: the audit's canonical `_disk_md_files()` returns top-level `.md` + subfolder `README.md` rollups only, per spec §"Rollup subfolders"; pointers at `<topic>/<detail>.md` failed the disk-set check and were reported as missing even when the file was present in the tree. New `_all_md_files_recursive(folder)` helper distinguishes two cases at the pointer-check site: (a) pointer file truly missing → keep as INTEGRITY violation; (b) pointer file exists but at a non-canonical path → downgrade to HEALTH advisory `"Pointer at subfolder detail file (consider rollup README)"`. `--fix` continues to operate only on truly-orphan pointers; existing valid subfolder-detail pointers are never removed. The top-level `archive/` subfolder is excluded from the recursive walk (consistent with `_files_to_audit`).

### Tests

- 3 new audit tests: no integrity violation for an existing subfolder detail pointer; health advisory emitted for the same; truly-missing subfolder pointer still raises integrity violation. Full suite: 233 pass, 2 GPG-gated skipped.

### Spec compatibility

- No spec change. spec/VERSION stays at 0.5.3. Adapters built against v0.5.3 read paths work unchanged on v0.5.4.

## [0.5.3] - 2026-05-10

**Patch release.** Spec bump 0.5.2 -> 0.5.3. Closes the 3 remaining v0.5.x MAJORs surfaced by the v0.5.2 retrospective threat-modeler pass. This release ran the full multi-voice review panel (architect + critic + threat-modeler) on both the spec delta and the new code surface pre-tag; findings identified by the panel were addressed before ship. Note: multi-voice panel review is an internal review pass, not a substitute for independent red-teaming or third-party security audit. See `spec/known-limitations.md` for the residuals that remain.

### Spec changes

- §"Mandatory cool-down period" extended with a normative **registry-layer enforcement** mandate. Cool-down checks MUST occur at the registry layer (e.g., `memforge.registry.verify_signing_key_acceptable`), not solely at the CLI layer. Closes the v0.5.2 threat-model MAJOR where a buggy or hostile alternate consumer of the registry could bypass the cool-down by signing with the new key directly.
- §"Reader-side revocation walk" extended with a normative **bounded-walk** mandate. The revocation walk MUST be bounded by both a maximum-commits cap (default 100,000) AND a maximum-bytes cap (default 100 MB). When either cap is exceeded, adapters MUST halt the walk and emit a fail-closed message pointing the operator at `memforge revocation-snapshot`. Operator-configurable via `.memforge/config.yaml` keys `revocation.walk_max_commits` and `revocation.walk_max_bytes`. Closes the v0.5.2 threat-model MAJOR where an unbounded walk on a malicious or pathological repo would OOM any adapter walking revocation state at startup.
- Integrity invariant 21 extended with a normative **TOCTOU-safe-read** addendum. Adapters MUST verify mode + ownership on the file descriptor (`fstat` after open) rather than on the path (`stat` before open). POSIX implementations MUST open with `O_NOFOLLOW`; Windows implementations MUST perform path-level verification before open (since `O_NOFOLLOW` is not a Windows concept). Closes the v0.5.2 threat-model MAJOR where a same-uid attacker could swap the file between path-level mode-check and open.

### Reference implementation changes

- `memforge.registry`: new `key_is_in_cooldown(registry, key_id, at_time=None)` + `verify_signing_key_acceptable(registry, key_id, signing_time=None)`. The first reports cool-down status; the second combines registry-membership + cool-down into a single fail-closed verify gate. Cool-down hours floor (1h) now enforced inside `_compute_cooldown_expiry`.
- `memforge.revocation.walk_revocation_set`: rewritten to stream git-log output via `subprocess.Popen` + line-by-line parse, with `max_commits` + `max_bytes` kwargs (defaults match the new spec mandate). Aborts with explicit error pointing the operator at `memforge revocation-snapshot` on cap-exceeded.
- `memforge._security`: new `secure_read_text(path)` + `secure_read_bytes(path)` primitives. POSIX backend uses `O_NOFOLLOW` + post-open fd `fstat` for mode + owner verification; Windows backend uses path-level `verify_owner_restricted`. Refactored consumers: `identity.load_operator_identity`, `agent_session.load_attestation` + `is_nonce_seen` + `record_seen_nonce`, `sender_sequence.load_sender_sequence` now all read through the TOCTOU-safe path.

### Documentation

- `spec/known-limitations.md` (renamed from `spec/v0.5.0-known-limitations.md`; living document): updated to reflect v0.5.3 closures of the BLOCKERs and security-relevant MAJORs surfaced by the internal multi-voice review panel. 4 MINOR refinements remain on the living doc. SPEC.md cross-reference updated to point at the living-doc filename. The "panel-identified" qualifier matters: independent red-teaming or third-party pentest may surface findings the internal panel did not.

### Tests

- 10 new v0.5.3 tests covering registry cool-down (in-window / after-expiry / unlisted-key rejection / cool-down rejection / floor enforcement), bounded revocation walk (commit cap / byte cap / empty-history happy path), and TOCTOU-safe read (symlink refusal / happy path / relaxed-mode rejection). Full suite: 226 pass, 2 GPG-gated skipped.

### Pre-ship review

Ran the full release-rigor playbook (architect + critic + threat-modeler on both spec delta + new code surface). Findings + resolution are operator-side.

### Spec compatibility

- v0.5.2 folders remain well-formed under v0.5.3 readers. No frontmatter or normative-contract regressions.
- Adapters built against v0.5.2 read paths will work unchanged on v0.5.3; the TOCTOU-safe-read addendum is implementation-level + transparent to callers using the higher-level load functions.

## [0.5.2] - 2026-05-10

**Patch release.** Spec bump 0.5.1 -> 0.5.2. Closes 2 BLOCKERs + 1 MAJOR surfaced by the post-v0.5.1 retrospective code + spec panel pass (critic voice + threat-modeler voice on the new code surface; the panel scope skipped during the v0.5.1 first ship and run separately afterward).

### Spec changes

- New normative subsection at §"Signed envelope scope (normative)": **Canonical-form Unicode NFC normalization MUST on signed envelopes.** Closes the repudiation vector where two visually-identical inputs in different normalization forms (NFC vs NFD) would produce different signed envelopes and therefore different signatures, letting a sender repudiate a signature by claiming the verifier received the "other" form.
- §"Seen-nonce set bounding" promoted from MAY to SHOULD with explicit garbage-collection contract: discard each nonce once its corresponding attestation's `expires_at + backdating_max_skew` (default 10 minutes) has passed. Implementations that do NOT bound the set are vulnerable to a disk + memory DoS and SHOULD warn the operator at startup.

### Reference implementation changes

- `memforge.crypto.canonical_envelope` now NFC-normalizes every string in the envelope (keys + values, recursively) before JSON serialization. Backward-compatible for inputs that were already in NFC form (the common case for ASCII + standard composed Unicode). Inputs in NFD form will produce different envelope bytes (and therefore different signatures) under v0.5.2 vs v0.5.1; this is intentional and closes the repudiation vector.
- `memforge.identity.write_secure_yaml` + `write_secure_bytes` now use `O_CREAT|O_EXCL` on a sibling tmp path + `fsync` + atomic `os.replace` to close the TOCTOU window where the file would exist at default umask between create and chmod. Previously, a same-user concurrent reader could observe a file at the default umask permission for a brief window between `open(path, "w")` and `os.chmod(path, 0o600)`.
- `memforge.agent_session.record_seen_nonce` now garbage-collects expired entries on every call, per the new SHOULD-bounding contract.

### Pre-ship review

This release ran the full release-rigor playbook the v0.5.1 retrospective surfaced as missing: architect-voice + critic-voice + threat-modeler-on-new-code-surface. Findings:

- Critic (gemini-pro on spec delta): 1 MAJOR (seen-nonce set unbounded-DoS). Closed by promoting MAY to SHOULD with explicit GC contract in §"Seen-nonce set bounding".
- Threat-modeler (gemini-pro on src/memforge/ code): 2 BLOCKERs (TOCTOU on file create in identity.py write_secure_yaml; Unicode normalization missing in crypto.py canonical_envelope) + 4 MAJORs. Both BLOCKERs closed in this release. 3 MAJORs deferred to v0.5.3 backlog (registry-layer cool-down enforcement, bounded git-log walk in revocation set, TOCTOU between mode-check and read). 1 MAJOR (unbounded seen-nonce set) was the consensus finding from critic + threat-modeler and is closed here.

### Cross-platform support (NEW: native Windows in v0.5.2)

v0.5.0 / v0.5.1 enforced the "file restricted to current owner" spec contract via POSIX mode bits (0600 / 0700) + `stat().st_uid` match. This is correct on macOS / Linux but a no-op on native Windows. v0.5.2 introduces a platform-agnostic abstraction at `src/memforge/_security.py` and restates the spec normative requirement platform-agnostically.

- POSIX (macOS, Linux, *BSD): unchanged. Mode 0600 / 0700 + `stat()` uid match against effective uid.
- Windows (NTFS): ACL-based restriction via the built-in `icacls` binary. `restrict_file_to_owner` / `restrict_dir_to_owner` call `icacls /inheritance:r` + `icacls /grant:r <current-user>:F` to grant Full Control to the current user only with no inherited ACEs. `verify_owner_restricted` parses `icacls` output and rejects on the presence of any forbidden principal (Everyone, Authenticated Users, BUILTIN\\Users, BUILTIN\\Guests, NT AUTHORITY\\INTERACTIVE / NETWORK / BATCH / SERVICE / ANONYMOUS LOGON, etc.).
- Both paths satisfy the spec "file restricted to current owner" contract identically.
- Spec §"Operator-identity file (per-machine)" and integrity invariant 21 restated platform-agnostically; v0.5.0 / v0.5.1 POSIX-mode-specific language preserved as a normative POSIX implementation.

- CI matrix extends from `ubuntu-latest` to `[ubuntu-latest, macos-latest, windows-latest]` x Python 3.10 / 3.11 / 3.12.

- `os.uname()` (POSIX-only) replaced with `platform.node()` for hostname capture in operator-identity files.

### Spec compatibility

- v0.5.1 folders remain well-formed under v0.5.2 readers IF the envelope inputs were already in NFC form (ASCII + standard composed Unicode covers the common case). Inputs in NFD form produce different envelope bytes under v0.5.2 vs v0.5.1; a v0.5.1 signature over NFD content will NOT verify under v0.5.2. Operators with mixed-normalization-form historic content should re-sign affected memories via `memforge upgrade-v04-memories --apply` (the upgrade signs over the v0.5.2-canonical NFC form).
- Filesystem atomicity changes are transparent: existing files at the target paths are replaced atomically; no operator action required.
- Native Windows installs upgrading from v0.5.0 / v0.5.1 will likely have failed the FS-mode check at startup under those versions (POSIX mode bits map to no-op on NTFS); v0.5.2 is the first version where native Windows can satisfy the secure-file contract. WSL installs of v0.5.0 / v0.5.1 already worked (WSL presents a POSIX filesystem) and continue to work in v0.5.2.

## [0.5.1] - 2026-05-10

**Patch release.** Spec bump 0.5.0 -> 0.5.1. Closes the v0.5.0 agent-session-attestation content-scope MAJOR + 3 MINORs in normative text and ships the reference CLI binaries (14 commands).

### Added

- New §"Agent session attestation content scope (v0.5.1+)" subsection. Closes the v0.5.0 MAJOR with normative `nonce` (replay defense), `expires_at` (default 24h; floor 15 min; ceiling 7 days), and `capability_scope` (explicit memory_roots + allowed_operations from {write, resolve, revoke, registry-edit, key-rotation, fresh-start}). Receiver-side enforcement is an 8-step normative checklist including write-signature verification against the attested `agent_pubkey` (closes the trust-boundary gap surfaced by the v0.5.1 light-mode architect pass).
- Agent-session-id normative regex `^[a-z0-9]+-\d{4}-\d{2}-\d{2}-[a-z0-9]{8,16}$` in §"Frontmatter additions (v0.5.0+)". Closes the v0.5.0 MINOR on format guidance. Adapters MUST reject non-matching values on read (audit MAJOR) + refuse to mint non-matching on write.
- New §"Cross-cutting fail-closed posture (v0.5.1+)" section. Closes the v0.5.0 MINOR on documentation. 29-item operator reference organized as hard fail-closed (HALT; 14 items), per-write fail-closed (reject specific write; 9 items), per-config fail-closed (refuse config; 2 items), soft fail-closed (warn + acknowledge; 4 items).
- New §"Privacy considerations (v0.5.1+)" section. Closes the v0.5.0 MINOR on documentation. 7 boundary statements (operator-UUID linkability, signing-time linkability, agent-session-id leakage, sender-UID linkability, operator-name homograph + privacy, cross-store reference disclosure, receiver-side state-file leakage) + out-of-scope list.
- New integrity invariants 23-25 covering agent-session attestation verification (including write-signature verification against attested agent_pubkey), capability-scope enforcement, and agent-session-id format.
- Reference CLI: `memforge` top-level dispatcher with 14 subcommands.
  - `memforge init-operator` , generate operator-UUID + register a GPG key.
  - `memforge init-store` , bootstrap `.memforge/` in a memory-root + signed operator-registry.
  - `memforge operator-registry {add|verify|remove|fresh-start}` , manage operator-registry.
  - `memforge rotate-key` , cross-signed key rotation with 24h cool-down.
  - `memforge revoke <key_id> --reason ...` , build a signed revoke commit body.
  - `memforge revocation-snapshot` , emit a signed snapshot commit body.
  - `memforge memories-by-key <key_id>` , list memories signed by a key.
  - `memforge revoke-memories <key_id> --bulk` , mark memories under a revoked key as superseded.
  - `memforge upgrade-v04-memories --apply` , add v0.5 identity+signature to v0.4 memories in-place.
  - `memforge revoke-cache-refresh` , refresh the remote-fetch revocation cache (sparse/shallow mode).
  - `memforge messaging-doctor` , run the v0.5.1 fail-closed checklist + report posture.
  - `memforge recovery-init` , install ~/.memforge/recovery-secret.bin + anchor SHA256 in registry.
  - `memforge recovery-backup-confirm` , acknowledge offline backup; unlocks v0.5+ writes.
  - `memforge attest-agent` , issue a signed agent-session attestation.
- New Python modules: `memforge.identity` (UUIDv7 + operator-identity file + agent-session-id format), `memforge.crypto` (GPG subprocess wrappers + canonical envelope), `memforge.registry` (operator-registry read/write/sign), `memforge.revocation` (revoke commit builder + revocation-set walker), `memforge.sender_sequence` (sender-uid + sender-sequence + signed checkpoints), `memforge.agent_session` (attestation build/save/load/verify + scope checks + seen-nonce set), `memforge.recovery` (recovery-secret install + SHA256 anchoring + backup acknowledgment).
- 24 new tests in `tests/test_v05_cli.py` covering: dispatcher --help smoke + subcommand registration, UUIDv7 format, now_iso, agent-session-id format + minting + validation, identity parse, canonical envelope determinism, GPG algo denylist, sender-uid format, revoke body reason-length, and (gated on `MEMFORGE_TEST_GPG=1`) end-to-end happy-path round-trips for init-operator + init-store + revoke and attest-agent + scope checks against a sandboxed GPG keyring.

### Changed

- §"Known limitations" header bumped to v0.5.1; documents the 4 v0.5.0 residuals now closed in normative text. Residual MAJORs reduced from 5 to 4; MINORs reduced from 2 to 1.
- §"Not in scope" header bumped to v0.5.1. Reference CLI moved from "deferred to v0.5.1" to "shipped in v0.5.1". Added two new deferrals: privacy-preserving cross-store unlinkability (v0.5.x / v0.6+); per-store operator-UUID derivation (v0.5.x).
- §"Versioning" current spec version updated to 0.5.1. v0.5.1 entry added to §"Versioning history".

### Spec compatibility

- v0.5.0 folders remain well-formed under v0.5.1 readers.
- v0.5.0 attestation files lacking v0.5.1 required content fields (`nonce`, `expires_at`, `capability_scope`) are accepted with a one-time MAJOR `v05_attestation_incomplete_content` per file until re-issued via the v0.5.1 reference CLI.
- v0.4 folders continue to load as `(v0.4: unsigned)` read-only-untrusted; upgrade path via `memforge upgrade-v04-memories --apply`.

### Pre-ship review

A pre-ship architect pass caught one BLOCKER: receiver enforcement omitted normative verification that the write's `signature.value` validates against the attestation's `agent_pubkey`. Closed in the same session before tag.

## [0.5.0] - 2026-05-10

**Minor release.** Spec bump 0.4.0 -> 0.5.0. Extends single-operator multi-agent format to multi-identity team-scale memory with cryptographic attribution and a real-time messaging substrate (WebSocket). Reference CLI binaries ship in v0.5.1.

### Added

- New §"Multi-identity primitives": two identity classes (operator long-lived key; agent ephemeral per-session key). New REQUIRED v0.5+ frontmatter `identity` + `signature`. v0.4 frontmatter remains valid; v0.4 memories load read-only-untrusted under v0.5 readers.
- New §"Cryptographic attribution": GPG (RSA-4096 or Ed25519) default. Signing-time-aware verification + `first_seen_at` clock-skew guard (default +/- 10 min) close the coordinated-backdate attack class. Cross-signed rotation chain bounded by min(N, 10) via fresh-start operator-registry.
- New §"Operator identity + cross-store references": UUIDv7 at `~/.memforge/operator-identity.yaml` (per-machine, 0600/0700). Operator-registry at `<memory-root>/.memforge/operator-registry.yaml` (fail-closed signature verification + content-hash-anchored cache). `MEMFORGE_STORE = <operator-uuid>:<store-name>` for cross-store refs. Multi-operator trust-bootstrap procedure documented.
- New §"Messaging adapter contract (WebSocket reference)": substrate locked WebSocket (40% latency benchmarks; OpenAI Responses API Feb 23 2026 launch alignment; Cursor/Cline/Vercel adoption). Sender-uid format `<operator-uuid>:<32-byte-hex>` mandatory. Sender-sequence + signed checkpoints every 100 sequences or 24 hours. Multi-server hard-stop for v0.5.0. Substrate-independent envelope contract: git-only writers must use same sender_uid + sequence + checkpoint machinery.
- New §"Key lifecycle + revocation": revocation events as git commits with `memforge: revoke <key_id>` prefix. Reader walks git history. Sparse-checkout / shallow-clone fallback via remote-fetch with loud startup banner + audit MAJOR (revocation events NOT signature-verified in fallback mode; v0.5.0.1 patch target). Recovery-secret filesystem mode 0600/0700 + uid-ownership check; persistent startup WARN until hardware-backed install. Revocation snapshot mechanism bounds O(N) cold-start cost.
- New §"Security considerations": operator-facing boundary statements (honest-operator assumption, software-only recovery-secret boundary, same-user shell malware, sparse-checkout caveats, cross-instance propagation lag, hardware-key recommendation, recovery-secret backup mechanism).
- New §"Known limitations": 2 documented BLOCKERs (receiver-state silent-rollback window; remote-fetch unsigned revocation events) as v0.5.0.1 patch targets. Full list at `known-limitations.md` (renamed from per-version `v0.5.0-known-limitations.md` to a living document in v0.5.3; each Zenodo deposit ships a versioned snapshot).
- New §"v0.5.0 surface map": ASCII diagram showing the Element 1 -> 2 -> 4 -> 5 dependency flow.
- New invariants 16-22 in §"Integrity invariants" covering v0.5.0 frontmatter shape, clock-skew, mixed-deployment resolve, operator-registry, sender-sequence, identity-file FS modes, revocation commit prefix discipline.

### Changed

- Spec version 0.4.0 -> 0.5.0.
- `spec/VERSION` updated to `0.5.0`.
- Versioning history extended with v0.5.0 entry.
- §"Not in scope" relabeled "Not in scope for v0.5.0" with expanded list covering hardware-key reference impl, 2-of-N multi-key signing, post-quantum, centralized identity, sub-second propagation, etc.

### Closed in v0.5.0 (originally tagged as v0.5.0.1 patch targets)

Two BLOCKER-class issues identified during v0.5 development have been closed in the v0.5.0 normative spec rather than deferred:

- **Receiver-state silent-rollback window** -> closed via §"Receiver state (MUST)": mandates `<memory-root>/.memforge/receiver-state/<sender-uid>.yaml` (FS mode 0600/0700; ownership check); receiver MUST reject `seq <= highest_seen_sequence`; HALT on corruption.
- **Remote-fetch unsigned revocation events** -> closed via §"Revocation events as git commits" (every revocation commit MUST be GPG-signed; signing-key-matches-revoked_by check) + §"Sparse-checkout / shallow-clone fallback verification mode" (pin remote URL + transport; TOFU on first fetch; fast-forward-only after; signature verification on every fetched revocation commit).

### Known limitations

v0.5.0 ships with **no BLOCKER-class known limitations**. Residual MAJORs + MINORs (refinements; not security gaps) tracked at `known-limitations.md` (living document; renamed from per-version filename in v0.5.3) for v0.5.1 / v0.5.x patches:

- 6 MAJORs (checkpoint signer ambiguity, revocation snapshot ancestor + canonical hash, sender/receiver posture nuance, cache TTL high-stakes, cross-cutting fail-closed documentation, agent session attestation content scope).
- 7 MINORs (cross-cutting fail-closed posture, TTL semantics, trust graph disclosure, agent session ID format, key rotation chain DoS, operator name homograph audit, v0.4 memory flooding rate-limit).
- 4 reference-CLI MAJORs (`memforge init-operator`, `init-store`, `operator-registry`, `revoke-memories`) ship in v0.5.1 binary release.

## [0.4.3] - 2026-05-08

Patch release: closes the duplicate-keys / growing-frontmatter regression in `memory-frontmatter-backfill` that surfaced when a memory file's `description:` value contained an unquoted colon-space. No spec changes (spec stays at 0.4.0).

### Fixed

- `memory-frontmatter-backfill` no longer corrupts files whose YAML frontmatter fails to parse. Previously, when `yaml.safe_load` raised on a value like `description: A: B description with embedded colon-space`, the parser collapsed the failure to an empty dict, every required field appeared "missing", and `apply_change` line-appended a fresh set of fields. The `memory-auto-commit.sh` PostToolUse hook then re-ran backfill on every Write/Edit, producing growing blocks of duplicate keys. Two-layer fix: (1) `plan_change` detects "frontmatter present but YAML parse failed" via the new `_frontmatter_present_but_unparseable` helper and skips with a stderr warning; (2) `apply_change` now does a dict-merge round trip via `memforge.frontmatter.render` instead of line-level appending, so duplicate keys are structurally impossible in the output even when called repeatedly.

### Tests

- New `tests/test_frontmatter_backfill.py` covers the colon-space detection path, the duplicate-key edge case (PyYAML silently last-wins, helper correctly returns False), the round-trip render of valid YAML, idempotency under repeated `apply_change` calls, preservation of existing operator-set fields, and the defense-in-depth skip in `apply_change` when fed broken YAML directly.

## [0.4.2] - 2026-05-08

Patch release: completes the `memory-audit` recursive-validation gap surfaced after v0.4.1, and bumps GitHub Actions to Node.js 24-compatible versions. No spec changes (spec stays at 0.4.0).

### Fixed

- `memory-audit` per-file frontmatter validation now recurses into rollup subfolders (excluding `archive/`), as required by spec §"Rollup subfolders" ("Audit tools MUST recurse into rollup subfolders to validate frontmatter, but MUST NOT generate parent-MEMORY.md pointers for detail files"). Previously the per-file audit reused the pointer-comparable file set, so detail-tier files inside rollups were silently skipped: YAML parse failures, missing frontmatter, invalid types, sensitivity issues, and staleness in those files all went unreported. v0.4.1 closed the orphan-pointer half of this gap; v0.4.2 closes the per-file half.

### Changed

- New helper `_files_to_audit()` in `memforge.cli.audit` returns the recursive audit set (top-level `.md` plus every `.md` inside any first-level subfolder, excluding `archive/`). The orphan-pointer comparison still runs against `_disk_md_files()` (top-level + rollup READMEs only), so detail-tier files do NOT generate spurious `Orphan file (no pointer)` violations. The per-file audit at `audit_target` line ~193 now iterates `_files_to_audit()`.
- `.github/workflows/release.yml`: bumped pinned action SHAs to Node.js 24-compatible major versions:
  - `actions/checkout` v4 -> v6.0.2
  - `actions/setup-python` v5 -> v6.2.0
  - `actions/upload-artifact` v4 -> v7.0.1
  - `actions/download-artifact` v4 -> v8.0.1
  - `pypa/gh-action-pypi-publish` `release/v1` -> v1.14.0

  Closes the v0.4.1 publish-workflow annotation about Node.js 20 deprecation (forced default June 2, 2026; runner removal September 16, 2026).

### Added

- `tests/test_audit.py` grows from 5 to 9 cases. New: `_files_to_audit` semantics (top-level only / recurses into rollups / excludes archive recursively) plus an end-to-end test asserting that a YAML parse failure in a rollup detail file is correctly reported (regression coverage for the silent-skip behavior pre-v0.4.2).

## [0.4.1] - 2026-05-08

Patch release: audit fix, adapter improvement, install docs. No spec changes (spec stays at 0.4.0).

### Fixed

- `memory-audit` no longer fires false-positive `Orphan pointer (no file)` for rollup-subfolder README files. `_disk_md_files` now returns top-level `.md` files plus `<topic>/README.md` for each first-level subfolder (excluding `archive/`), matching the spec's §"Rollup subfolders" rule that rollup READMEs are tier:index files surfacing in the parent `MEMORY.md`. Per-file frontmatter validation also extends to those READMEs as a side effect, narrowing the audit-vs-spec gap on subfolder coverage. Closes #8.

### Changed

- Adapter `adapters/claude-code/hooks/memory-auto-commit.sh` now runs `memory-frontmatter-backfill --apply` before committing on every Write/Edit inside a memory folder. Auto-normalizes v0.3-shaped frontmatter (the Claude Code harness auto-memory instruction emits 3 fields) to v0.4 shape on write. Portable: uses `command -v` to find the backfill CLI; silently skips if absent (works on machines without the package installed).

### Documentation

- README adds a "macOS / Homebrew Python (PEP 668)" subsection covering pipx, dedicated venv, and `--user --break-system-packages` install variants. Same guidance applies to most current Linux distributions that mark their system Python `EXTERNALLY-MANAGED`.
- `tools/README.md` clarifies that the `.py` shim scripts are reference implementations, not the canonical install path. `pip install ildan-memforge` installs all CLI commands as `console_scripts` entry points on `$PATH`, which is the supported install path.
- Tool-count references updated from "15 CLI commands" / "15+ commands" to "17 commands" (v0.4.0 added `memforge-resolve` and `memforge-migrate-claim-block`).

## [0.4.0] - 2026-05-08

Package release carrying spec v0.4.0.

### Added

- `memforge-resolve` CLI: walk the operator through reconciling competing claims for a `decision_topic`. Mutates winner / loser frontmatter, deletes any active snooze, and commits with `memforge: resolve <topic>` prefix.
- `memforge-migrate-claim-block` CLI: idempotent depth-aware fixer for the canonical reader-side competing-claim block in `MEMORY.md`.
- `memory-audit --export-tier=<level>` flag: v0.4 sensitivity export-tier gate. Reads `audit.default_export_tier` from `.memforge/config.yaml` when the flag is absent. Privileged-tier files always block when the gate runs, regardless of `audit.enforce_sensitivity_export_gate` config.
- `memory-dlp-scan --no-sensitivity-cross-check` flag: per-invocation override for the v0.4 `sensitivity_label_mismatch` BLOCKER. Cannot disable when implied tier is `privileged` (hard floor).
- `memforge.cli._config` module: shared loader for `.memforge/config.yaml` with auto-discovery, defaults merge, frontmatter sensitivity parser, and canonical `TIER_ORDER` constant.
- `memforge.cli._concurrency_audit` module: layered Tier 1 (HEAD-pure) and Tier 2 (commit-log) audit invariants for the v0.4 multi-agent concurrency surface.
- `tests/conformance/sensitivity/`: five conformance scenarios covering export-tier-{public, internal, restricted, privileged} and label-mismatch-blocked.
- `.gitleaks.toml` allowlist for the DLP scanner files (their own regex patterns are detected by gitleaks otherwise).

### Changed

- `memory-dlp-scan`: `Pattern` dataclass gains `implied_tier` field; PATTERNS table maps every detector to a tier (secret-class -> restricted, PII -> restricted, high-entropy heuristic -> internal). Cross-check fires by default; emits BLOCKER `sensitivity_label_mismatch` when declared sensitivity is below the highest implied tier across findings.
- `memory-audit`: integrates the v0.4 concurrency audit (`run_concurrency_audit`) and the export-tier gate. Surfaces `[v0.4]` and `[v0.4 MAJOR]` / `[v0.4 WARN]` prefixes in the violation list.
- `memory-index-gen`: `render_competing_claims_block(folder_root)` emits the canonical fenced YAML block per the v0.4 reader-side contract; byte-match CI on the rendered block.

### Test coverage

- 163 tests pass on Python 3.10 / 3.11 / 3.12 (39 new in this release).

## [spec 0.4.0] - 2026-05-08

v0.4 is a major bump per SemVer applied to spec semantics. The package release carrying this spec is `[0.4.0]` (above).

### Added (spec)

- **§"Multi-agent concurrency: competing claims" section** in SPEC.md. Five new frontmatter keys (`decision_topic`, `replaces`, `superseded_by`, `topic_aliases`, `ever_multi_member`), a snooze record at `.memforge/snoozes/<topic>.yaml`, a config file at `.memforge/config.yaml` with hard-floor + edit-gate protection, the resolve operation contract (tool-neutral; CLI reference + Claude Code skill + Cursor / Continue / Aider / shell wrappers), the canonical reader-side competing-claim YAML block (byte-match CI), and a layered Tier 1 (HEAD-pure) + Tier 2 (commit-log) audit rule set. Closes the v0.3.x "Multi-user concurrency semantics (Phase 2+ concern)" deferral.
- **Status enumeration BLOCKER**: any value outside `{active, proposed, gated, superseded, dropped, archived}` is a HEAD-pure audit BLOCKER.
- **Status transition gating**: transitions to `superseded` MUST occur in a `memforge: resolve <decision_topic>` commit (Tier 2 BLOCKER if violated). Transitions to `archived` go through the generator (existing Phase-1 contract).
- **Secure-mode adapter conformance** (informative): adapters MAY claim secure-mode by detecting branch protection + required signed commits at startup; informative startup notice required when not in secure-mode.
- **Reserved-name slug denylist** for `decision_topic` (Windows device names: `con`, `aux`, `nul`, `prn`, `com[0-9]`, `lpt[0-9]`).
- **§"Sensitivity enforcement (v0.4.0+)"** in SPEC.md. Three default-on, operator-disable-able checks tied to a hard floor at the `privileged` tier: an audit-side **export-tier gate** (`memory-audit --export-tier=<level>` or `audit.default_export_tier` config), a DLP-side **label/content cross-check** that emits BLOCKER `sensitivity_label_mismatch` when declared sensitivity is below the implied tier of body content, and a **conformance fixture set** (`tests/conformance/sensitivity/`) covering five scenarios that secure-mode adapters MUST pass. Adds three config keys (`audit.default_export_tier`, `audit.enforce_sensitivity_export_gate`, `dlp.enforce_sensitivity_cross_check`); a `--no-sensitivity-cross-check` CLI flag on `memory-dlp-scan`; and a `--export-tier` flag on `memory-audit`. Privileged-tier enforcement cannot be disabled by config.

### Changed (spec)

- **Required frontmatter fields expanded.** `uid`, `tier`, `tags`, `owner`, `status`, `created` are now required (formerly optional in v0.3.x). A v0.4-conformant memory must carry: `name`, `description`, `type`, `uid`, `tier`, `tags`, `owner`, `status`, `created`. The `sensitivity` field remains independent.
- **v0.3.x backward compatibility: degraded mode.** Files written under v0.3.x that lack the newly-required fields load in degraded mode. Adapters MUST accept them, MAY warn, and SHOULD prompt the operator to backfill. Degraded-mode memories appear in `MEMORY.md` but cannot participate in rollup contracts, status-driven archival, or any v0.4 reader-side contract that depends on a v0.4-required field. Backfill is one-shot per file.
- **Integrity invariants** extended (rules 11-15) to cover the asymmetric-supersession contract, exactly-one-active per `ever_multi_member: true` group, `ever_multi_member` monotonicity, status enumeration, and the layered audit rule set.
- **§"Not in scope"** retitled to v0.4.0; multi-user concurrency removed (now in scope), v0.5.0 deferrals enumerated (centralized taxonomy, per-decision ledger, DAG cycle rejection, UUIDv7, vector-clock tie-breaker, CRDT, cryptographic provenance).

## [0.3.1] - 2026-05-07

Patch release wiring proper console scripts so `pip install ildan-memforge`
actually ships the CLI. Same on-disk format as 0.3.0; no schema or behavior
changes.

### Distribution name

The PyPI distribution is published under **`ildan-memforge`** because the
shorter `memforge` name on PyPI is already held by an unrelated project.
The Python import path is still `memforge`, and the CLI command names are
still `memory-audit`, `memory-watch`, etc. Only the install command differs.

### Added

- **`[project.scripts]` entry points for all 15 CLI tools.** After
  `pip install ildan-memforge`, the following commands land on `$PATH`:
  `memory-audit`, `memory-audit-deep`, `memory-audit-log`,
  `memory-cluster-suggest`, `memory-dedup`, `memory-dlp-scan`,
  `memory-frontmatter-backfill`, `memory-index-gen`, `memory-link-rewriter`,
  `memory-preamble-extract`, `memory-promote`, `memory-query`,
  `memory-rollup`, `memory-watch`, `agents-md-gen`. Previously the package
  was importable but none of the tools were on `$PATH`.

### Changed

- **Tool source moved into `src/memforge/cli/<name>.py` modules.** Each tool
  is now an importable module with a `main()` function; the corresponding
  `tools/<name>` script is now a thin shim that re-enters through the
  module's `main()`. Both invocation paths share the same code.
- **`memory-audit` rewritten in Python** (was bash). Cross-platform, uses
  `memforge.frontmatter.parse` for YAML parsing instead of awk, and gives a
  clearer error when frontmatter YAML fails to parse (single
  `"frontmatter YAML failed to parse"` violation instead of a misleading
  trio of `missing name/description/type`).
- **`memory-promote` rewritten in Python** (was bash). Cross-platform git
  operations via `subprocess`; same flag surface (`--source`, `--target`,
  `--dry-run`, `--no-commit`, `--yes`).

### Removed

- Bash sys.path / `_HERE` shims at the top of each tool (no longer needed
  now that tools live inside the installed package).

[Unreleased]: https://github.com/ildan-ai/memforge/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/ildan-ai/memforge/releases/tag/v0.3.1

## [0.3.0] - 2026-05-07

First public release.

### Added

- Project published under the `memforge` name. Tools, package imports (`memforge.frontmatter`, `memforge.discovery`, etc.), and CLI binaries all use the `memforge` namespace.
- **Spec 0.3.0** at `spec/SPEC.md`. Codifies:
  - The four memory types: `user`, `feedback`, `project`, `reference`.
  - Required `**Why:**` and `**How to apply:**` lines for `feedback` and `project` bodies (so future readers can judge edge cases instead of pattern-matching).
  - Rollup-with-subfolder pattern for topic clusters of five or more memories.
  - Optional UID frontmatter field (stable identifier for cross-folder linking via `mem:uid`).
  - Optional `access:` label for sensitivity-and-redaction scoping.
- **Reference adapter for Claude Code** at `adapters/claude-code/`. Includes auto-commit PostToolUse hook and the index-loading instruction that ships in `~/.claude/CLAUDE.md`.
- **Production adapters for Aider, Codex (OpenAI), Cursor, and GitHub Copilot Chat (VS Code)** under `adapters/`. Each adapter documents the agent-specific surface (rules file, instructions file, etc.) it bridges to the shared MemForge folder.
- **Cross-platform `memory-watch`** (Linux/macOS via watchdog).
- **Shared tooling at `tools/`:**
  - `memory-index-gen` (RBAC-aware folder index generator with `access:` filtering).
  - `memory-audit` and `memory-audit-deep` (integrity + health checks; `--strict` exits nonzero on violations).
  - `memory-cluster-suggest` (rollup candidate detection; O(n²) avoidance via inverted topic + token indexes).
  - `memory-query` (read-only structured query over a folder).
  - `memory-rollup` (rollup primitive with batch link rewriting).
  - `memory-link-rewriter` (UID-link integrity; `check`, `rename`, `rename-batch`, `upgrade` subcommands).
  - `memory-frontmatter-backfill` (Phase 1 migration helper).
  - `memory-preamble-extract` (Phase 1 migration helper).
  - `memory-dlp-scan` (detect-secrets + Shannon-entropy heuristic for memory bodies).
  - `memory-audit-log` (append-only JSONL with chained hash; `tail_record` for append without full reread).
  - `memory-dedup` (LLM-assisted near-duplicate detection; `--local-only` and `--redact-descriptions` are defaults).
- **Controlled topic taxonomy** at `spec/taxonomy.yaml`. Pinned vocabulary for the `topic:` axis to prevent label sprawl. Version 0.3.1 of the taxonomy ships alongside spec 0.3.0.
- **Repository-level `SECURITY.md`** with the `security@ildan.ai` reporting channel and the disclosure SLA.

### Changed

- **All shared tooling now imports `memforge.frontmatter.parse`** for YAML-frontmatter parsing. Eliminates seven prior copies of the same parser across the tooling tree.
- **`memory-dedup` rewritten in Python.** Defaults to local-only mode (no remote LLM call) and to redacted descriptions when remote dispatch is opted in.
- **`memory-cluster-suggest` clustering pre-filter** uses inverted topic and token indexes; eliminates the O(n²) pairwise pass on large folders.
- **Bash portability fixes** in `memory-audit`. Replaced macOS-specific `stat -f` and `date -r` with python3 inline calls so the tool runs the same on Linux + macOS.

### Fixed

- `memory-audit-log` no longer reads the full JSONL on append; uses `tail_record()` (seek-from-EOF) to read the prior chain hash in O(1).
- `memory-index-gen` preserves `access_labels` as a list (was being scalar-coerced); tier-and-team composition now applied correctly.

### Security

- DLP pass is now part of `memory-audit` health checks rather than a separate manual step.
- `memory-dedup` redacts memory descriptions before any optional remote LLM dispatch.

[0.3.0]: https://github.com/ildan-ai/memforge/releases/tag/v0.3.0

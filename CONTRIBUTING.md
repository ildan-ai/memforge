# Contributing to MemForge

Thanks for considering a contribution. This project is early; the scope is narrow on purpose.

## How to contribute

Pull requests are open. Contributions come in under the Apache License 2.0, the same license the project ships under, with a Developer Certificate of Origin (DCO) sign-off on every commit.

1. Fork the repo and branch from `main`.
2. Sign off every commit: `git commit -s`. This adds a `Signed-off-by: Your Name <you@example.org>` line, which certifies the [Developer Certificate of Origin 1.1](https://developercertificate.org/): you wrote the change, or have the right to submit it under the project's license. The email must match the commit's author email. Merge commits count too: merge with `git merge --signoff`. CI fails a PR with any commit that is not signed off. To fix one, amend with `git commit --amend -s` or rebase with `git rebase --signoff main`, then push again.
3. Open the PR against `main` and fill in the template.

A maintainer approves CI runs on PRs from outside contributors before they execute, then reviews and merges. Changes to CI, release config, packaging, the spec, or governance records are owned by the maintainer (see `.github/CODEOWNERS`) and get a closer look.

Security reports go through the private channel in `SECURITY.md`, never a public issue or PR.

## Why DCO

MemForge is a spec-first standards project. Inbound equals outbound: every contribution is licensed under Apache-2.0, including its patent terms, and nobody assigns copyright. A DCO sign-off records that you have the right to make that contribution. There is no separate agreement to sign.

## Before opening an Issue

- Search [open issues](https://github.com/ildan-ai/memforge/issues) and [closed issues](https://github.com/ildan-ai/memforge/issues?q=is%3Aissue+is%3Aclosed) first.
- Read the relevant section of `spec/SPEC.md` if your topic touches on-disk format behavior.
- For security-sensitive concerns, follow `SECURITY.md` and do **not** open a public issue.

## Checklist for every change

Every change, internal or external, must:

- Run `memory-audit --strict` (the installed console script; after `pip install -e .`) against the test fixtures and pass.
- Sign off every commit (`git commit -s`).
- Run `pytest` and pass. The full test suite ships with the package (unit + conformance fixtures under `tests/`); keep it green and add coverage for any new behavior.
- Add or update a CHANGELOG entry under `## [Unreleased]`.
- If `spec/SPEC.md` or `spec/taxonomy.yaml` changes, bump `spec/VERSION` accordingly (SemVer track).
- Keep the format-vs-loading distinction clean: anything that couples to a single agent (Claude Code, Cursor, Aider, Codex, GitHub Copilot Chat, etc.) belongs in `adapters/<agent>/`, not in `spec/` or `tools/`.

The PR template at `.github/PULL_REQUEST_TEMPLATE.md` formalizes this checklist.

## Scope

**In-scope:**
- Spec additions that stay agent-neutral.
- New adapters that conform to the spec without mutating shared tools.
- Tooling improvements to `memory-audit` and other cross-adapter utilities.
- Test coverage and CI hardening.

**Out-of-scope (for now):**
- Single-agent features leaking into the core spec or shared tools.
- Runtime integrations with agents that do not already have a rules / memory surface.
- Web UI, hosted services, or multi-user features.

## Code of Conduct

Participation in this project is governed by `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1). Reports go to `conduct@ildan.ai`.

## License

MemForge is released under the Apache License 2.0 (see `LICENSE`). Contributions are accepted under the same license, with a DCO sign-off.

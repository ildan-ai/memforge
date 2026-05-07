# Contributing to MemForge

Thanks for considering a contribution. This project is early; the scope is narrow on purpose.

## External PRs are paused

> **External pull requests are currently paused** pending the Contributor License Agreement (CLA) infrastructure. The CLA template is counsel-blocked. The CLA flow will land in a v0.3.x patch release once counsel clears the template.
>
> **In the meantime:**
> - **Issues are open.** File bug reports and feature requests via the templates in `.github/ISSUE_TEMPLATE/`.
> - **Discussions are open.** Use the [Discussions](https://github.com/ildan-ai/memforge/discussions) tab for design conversations, spec interpretation, and "is this a bug" triage.
> - **Security reports** go through the private channel in `SECURITY.md`, not via public issues or PRs.
>
> If you want to be notified when the CLA flow opens, watch the repo or subscribe to the GitHub release feed. The pinned issue + release notes will both call it out.

## Why a CLA and not DCO

A Developer Certificate of Origin alone does not grant the relicense rights or the patent grant that an open-core project with a single corporate steward needs. This is not a values judgment about DCO-only projects; it is a structural fit decision for MemForge specifically.

## Before opening an Issue or Discussion

- Search [open issues](https://github.com/ildan-ai/memforge/issues), [closed issues](https://github.com/ildan-ai/memforge/issues?q=is%3Aissue+is%3Aclosed), and [Discussions](https://github.com/ildan-ai/memforge/discussions) first.
- Read the relevant section of `spec/SPEC.md` if your topic touches on-disk format behavior.
- For security-sensitive concerns, follow `SECURITY.md` and do **not** open a public issue.

## Internal contributor / future external-PR checklist

For internal-team commits today, and for external PRs once the CLA flow is live, every change must:

- Run `tools/memory-audit --strict` against the test fixtures and pass.
- Run `pytest` and pass (the test suite is being added in v0.3.x; see CHANGELOG `## [Unreleased]`).
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

MemForge is released under the Apache License 2.0 (see `LICENSE`). Once the CLA flow is live, accepted contributions will be licensed under the same terms via the CLA grant.

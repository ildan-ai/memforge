# MemForge known limitations

**Status:** Living document. It covers spec v1.0.0 and reference package 1.0.0. Each release updates it.

## Why this document exists

It lists what the release does not do and what it has not yet proven, so a deployer decides with the facts in view. Where a mechanism has not been run against the real tool, this file says so.

This is not an external audit. The project's own review does not replace independent red-teaming or a third-party penetration test, and neither has been performed.

## Commit trust: residual risks

These are the residuals of the trust model in `SPEC.md` §"Commit trust (v1.0.0+)". They are not defects. They are the edges of the design.

- **Unverified content reaches agents by any path other than `memforge sync`.** Plain `git pull`, `git merge` or `git checkout`, edits or new files in the work tree, and never running `sync` all put unverified content in front of an agent. No other MemForge command checks verified state, warns or refuses. The control for that path is the git host's branch protection and required signed commits. `sync` is a detective check for people who run it.
- **A listed key is trusted completely.** There is no revocation, no reach-back and no second approval inside MemForge. Removing a key from the list stops later commits only. Any listed signer can add another signer.
- **The host and the people who can merge are in the trust base.** A compromised host, a host admin, or anyone the host lets merge can put content on the branch. Hosts enforce signed commits and registered keys, not membership in the committed list, so an unlisted but registered signer can reach the branch. `sync` then refuses it for everyone.
- **Agents commit under a person's signature.** An agent that runs in a listed person's session produces commits that carry that person's signature. Per-agent attribution is advisory only (`agent_session`, `on_behalf_of`). Attribution does not survive a squash merge or a copy.
- **A trusted signer can write a prompt injection.** Signing proves who committed, not that the content is safe.
- **`gpg` and `keyless` verdicts can differ between receivers.** They depend on each receiver's keyring, revocation certificates, clock, trust roots and network. Two receivers can disagree on one commit. In `ssh` mode verdicts agree only across receivers whose `git` and `ssh-keygen` executables behave the same; versions and SHA-256 values are printed, not pinned.
- **A refused commit blocks everyone.** Until the remote's owner removes it, every receiver who runs `sync` is refused. There is no override flag. `keyless` also refuses every commit during an outage of its trust roots or log.
- **A pin vouches for history it does not check.** A pin from a compromised teammate pins a compromised history. A local user can clone again and pin past a refused commit, or edit local configuration. MemForge offers no command for it and cannot prevent it.
- **Verification costs one `verify-commit` per commit** in the range being checked.
- **The store must be the top level of its git repository.** `memforge.verifiedHead` is one key per repository, so `sync` refuses a memory root that is not the repository top level (`root_not_toplevel`). A store that lives in a subfolder of a larger repository cannot use `sync` until it moves.
- **A listed signer can block later syncs on purpose,** with a valid list of keys nobody holds or a switch to a mode receivers refuse.

## Commit trust: mechanisms not yet proven with the real tools

- **`keyless` has not been run.** Every statement about the `gitsign` interface is unrun: the exact `verify` arguments, exact-match behavior for identity and issuer, and what `gitsign` returns for each failure. It is exit-code only by design, so the contract is small, but it is unproven. The reference implementation refuses the mode until a run against the real `gitsign` and a test certificate and log instance passes.
- **`gpg` was observed in a scratch setup, not proven in CI.** The status-line rules were observed on one GnuPG and git version with a scratch keyring. The nine-argument `VALIDSIG` form comes from the GnuPG documentation and is tested from a fixture only. The reference implementation refuses the mode until its proofs pass, including a run against the real `gpg`.
- **Host-generated commits are unverified.** What a host signs on a web merge or squash merge has not been checked against any mode. No web-merge workflow is promised. Until a real host commit is verified against a mode, the supported path is a locally signed merge. An `ssh` store cannot accept a GPG-signed host commit.
- **The environment passed to `gitsign` has not been run.** The declared pass-through list (`HOME`, `GITSIGN_*`, `SIGSTORE_*`, the proxy variables and `SSL_CERT_FILE` and `SSL_CERT_DIR`) is a design choice that has not been tried against a real `gitsign` with a real organizational trust configuration. It must be confirmed before `keyless` is released.
- **Environment injection through `GIT_CONFIG_COUNT` was not tested.** The cleared environment used for verification is the mechanism that is relied on.
- **The first-sync pin is a statement, not a check.** `--adopt-local` and `--trust-head` record trust a human established.

A mode that is refused because its proofs have not passed is the release gate working. It is not a defect.

## Removed in 1.0.0

Per-memory signatures, the operator registry, agent attestations, sender sequences, key rotation, revocation and recovery, the messaging adapter contract, and the 13 commands that implemented them are removed. See `docs/migrating-to-1.0.md`. Legacy `identity` and `signature` frontmatter is preserved and ignored.

## Other deferred refinements

- **Embedding or semantic ranking in recall.** Matching is deterministic keyword, light-stem and synonym expansion. Embedding ranking is out of scope; promote it if recall miss-rate data justifies it.
- **Per-file incremental recall index build.** The reference build recompiles the whole index and skips a rebuild when nothing changed. Adequate for typical folder sizes.
- **Lint scoring is non-normative.** The spec defines lint by the dimensions it scores and the safety posture it observes, not by a scoring algorithm. Do not treat a numeric score as a conformance signal.
- **The always-set budget is advisory.** It is a warning, never an integrity violation, so `--strict` is unaffected.
- **Wikilink rewriting is limited to the rename alias set** (old stem, name, uid). A uid-only strict mode for short-stem collision safety is deferred to a future major version.

## Reference CLI status

- `memforge resolve <topic>` is shipped as the standalone `memforge-resolve` console script. The spec defines the resolve operation by post-conditions, so the standalone script is conformant. Integration into the `memforge` dispatcher is the intended form and is not done.
- `memforge migrate-claim-block` is shipped as the standalone `memforge-migrate-claim-block` console script, with the same status.

## How this document is published

- `SPEC.md` cross-references this file as a sibling.
- The living version in this repository reflects the latest spec state. Git history keeps per-release snapshots.

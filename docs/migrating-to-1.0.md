# Migrating to MemForge 1.0

MemForge 1.0 drops its own key system. Trust in a shared store now comes from signed git commits and a signer list the store commits. Your organization owns the keys. MemForge holds none.

**There is nothing to migrate.** Your memory files, `MEMORY.md`, rollups and audit setup keep working with no change.

## What 1.0 ignores

- The operator registry (`.memforge/operator-registry.yaml`).
- Revocation commits and snapshots.
- Per-memory `signature` blocks and `identity` values.
- Agent-session attestations, sender sequences and recovery secrets.

`memforge sync` reads none of them. You can leave the files in place or delete them in a normal commit. Nothing breaks either way.

## What stays in your files

`identity` and `signature` in a memory's frontmatter are legacy fields. They are valid frontmatter. Tools read them like any other field, keep them when a file is rewritten, never verify them, never write them, and raise no audit finding for them.

## Removed commands

These 13 commands no longer do anything. Each prints a removal notice that points here (its first line is `memforge <name>: removed in 1.0.`) and exits 2. They touch no key, registry, keyring or file.

| Command | What to use instead |
| --- | --- |
| `memforge init-operator` | Nothing. Use your own git signing key. |
| `memforge operator-registry` (`add`, `verify`, `remove`, `fresh-start`) | The signer list file for your mode (see below). |
| `memforge rotate-key` | Rotate the key in your own identity or signing system, then update the signer list. |
| `memforge revoke` | Remove the key from the signer list. This stops later commits only. |
| `memforge revocation-snapshot` | Nothing. |
| `memforge revoke-cache-refresh` | Nothing. |
| `memforge memories-by-key` | Use `git log` and your signer list. |
| `memforge revoke-memories` | Edit or supersede memories in a normal commit. |
| `memforge upgrade-v04-memories` | Nothing. v0.4 memories need no upgrade. |
| `memforge recovery-init` | Nothing. |
| `memforge recovery-backup-confirm` | Nothing. |
| `memforge attest-agent` | Nothing. |
| `memforge messaging-doctor` | Nothing. |

`memforge init-store` stays, with a new job. It creates the `.memforge/` layout. With `--mode ssh`, `--mode gpg` or `--mode keyless` it also writes the signer list for that mode. For `ssh` and `gpg` it puts your own configured signing key in the list when it can find one. Otherwise, and always for `keyless`, it writes a comment-only file that you must fill in before the first sync. It never overwrites: if any of the three signer list files already exists, it changes nothing and exits 2, even without `--mode`. Without `--mode` it only creates the layout. The old `--force` flag is gone, and it no longer creates a registry.

The WebSocket messaging adapter contract and the `examples/websocket/` scaffold are removed.

## How to adopt commit trust

1. The store must be the top level of its git repository. `memforge sync` refuses anything else (`root_not_toplevel`).
2. Every person who writes to the store configures signed commits in git. For SSH signing: `git config gpg.format ssh`, `git config user.signingkey ~/.ssh/id_ed25519.pub`, `git config commit.gpgsign true`.
3. Create the signer list for your mode under `.memforge/`: `allowed_signers` (`ssh`), `allowed_gpg_fingerprints` (`gpg`) or `allowed_identities` (`keyless`). `memforge init-store --mode ssh` can write the first one for you.
4. Commit the list in a signed commit and push it.
5. The person who created the store runs `memforge sync --adopt-local`. Everyone else clones and runs `memforge sync --trust-head <commit>`, using a commit id the creator gives them.
6. From then on, everyone uses `memforge sync` to take in new history.

`gpg` and `keyless` modes are refused until their proofs pass. Use `ssh` today unless the release notes say otherwise.

Read [Team setup](./team-bootstrap.md) for the three common cases, and the "Commit trust" section of [the spec](../spec/SPEC.md) for the exact rules.

## What changed in trust, in plain words

- Only `memforge sync` checks anything. Plain `git pull` and edits in your work tree are not checked.
- A key on the list is trusted completely. Removing it stops later commits only.
- The git host and the people who can merge are part of the trust base.
- A refused commit blocks everyone until the remote's owner removes it.

The full list is in [known limitations](../spec/known-limitations.md).

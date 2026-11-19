# Team setup: shared memory with commit trust

How to share one MemForge store between people and agents. Trust comes from signed git commits and a signer list the store commits. MemForge holds no keys. Your organization's identity, signing and git host controls decide who can sign.

Read the [Implementation status](#implementation-status) and the [limits](#what-commit-trust-does-not-protect) before you rely on it.

## Implementation status

This guide describes what the 1.0 package does.

**It implements:**

- `memforge sync`: fetch the shared branch, check every first-parent commit against the committed signer list, then fast-forward and record where verification stopped. It has `--check`, `--trust-head <commit>` and `--adopt-local`.
- `memforge init-store`: create the `.memforge/` layout and, with `--mode`, a signer list for that mode.
- The `ssh` mode, which has no receiver-local input except the installed `git` and `ssh-keygen`.

**It refuses until their proofs pass:** the `gpg` and `keyless` modes. A commit whose parent holds a `gpg` or `keyless` list is refused with `mode_unreleased` until the mode is released. Check the release notes before you plan around either.

**It does not do:**

- Check anything outside `memforge sync`. Plain `git pull`, `git merge`, `git checkout`, and edits in your work tree are not checked. No other MemForge command is gated on verified state.
- Sign or verify a memory file. Trust is on the commit, not the file.
- Manage keys, rotation, revocation, recovery or a registry. Those belong to your organization.
- Prove which person or agent wrote a memory after a squash merge or a copy. `agent_session` and `on_behalf_of` are advisory text.

Upgrading from an older version? See [Migrating to 1.0](./migrating-to-1.0.md).

## How it works

The store must be the top level of its git repository. Run `memforge sync` from there, or pass `--memory-root` with that top level. Anywhere else it exits 1 with `root_not_toplevel`.

1. The store holds one signer list under `.memforge/`: `allowed_signers` (`ssh`), `allowed_gpg_fingerprints` (`gpg`) or `allowed_identities` (`keyless`).
2. Every commit on the shared branch's first-parent chain must be signed by someone the list named in the commit before it. A commit cannot add its own signer. A merge is checked as one commit: its signer vouches for what it merged in, and the commits on the merged side are not checked one by one.
3. `memforge sync` checks that for every new commit. If one fails, sync refuses. There is no override flag.
4. The first time, you tell sync where to start with a pin. The pin is a commit you trust. Older commits are not checked.
5. Sync records where it stopped in your local `.git/config` (`memforge.verifiedHead`), so it does not start over.

## Case 1: two people, a private GitHub repository (`ssh`)

Each person:

```bash
git config gpg.format ssh
git config user.signingkey ~/.ssh/id_ed25519.pub
git config commit.gpgsign true
```

Put both public keys in `.memforge/allowed_signers`, one line each. The name is only a label. Use one name per person.

```text
alice namespaces="git" ssh-ed25519 AAAA...
bob namespaces="git" ssh-ed25519 AAAA...
```

Do not use `valid-after`, `valid-before` or `cert-authority` on a line. They make the whole list invalid.

The creator commits the list in a signed commit and pushes. Then the creator runs:

```bash
memforge sync --adopt-local
```

This pins your local `HEAD`. It is a statement that you vouch for this clone, not a check, and the command says so.

The other person clones and runs the sync with the full commit id the creator gives them (usually the head of the creator's last sync):

```bash
git clone <repo-url> memory && cd memory
memforge sync --trust-head <commit>
```

From then on, both people use `memforge sync` to take in new history. Merge locally and push.

Host enforcement depends on your plan. Branch protection on private repositories can need a paid plan. Without it, the only control is each person's `sync`.

## Case 2: a company with single sign-on and required signed commits

- The host requires signed commits on the shared branch.
- A code owners rule covers `.memforge/`, so changes to the signer list need review.
- The list (`allowed_signers` or `allowed_gpg_fingerprints`) is generated from your identity provider's key records.
- In `gpg` mode each receiver's keyring must hold the signers' public keys.
- Each receiver runs `memforge sync --trust-head <commit>` once.

The host enforces signing. `sync` enforces the committed list. Hosts check that a commit is signed with a registered key. They do not check that the key is on the list. An unlisted but registered signer can reach the branch, and `sync` then refuses it for everyone until the remote's owner removes the commit.

If you merge through the host's web page, the host signs the merge with its own key. A list that holds the host key trusts everyone the host lets merge. Which host merge styles work with which mode has not been verified. Until it is, merge locally with a signed merge commit.

## Case 3: keyless signing (`gitsign`)

`.memforge/allowed_identities` lists each certificate identity and issuer, one pair per line:

```text
<certificate-identity> <oidc-issuer-url>
```

Matching is exact. Receivers install `gitsign` with their organization's trust configuration and run `memforge sync --trust-head <commit>`. It needs network access to the trust roots and the log, and an outage refuses every commit.

This mode has not been run against the real tool, and it is refused until its proofs pass.

## Daily use

```bash
memforge sync --check   # fetch and verify, change nothing local
memforge sync           # verify, fast-forward, record
```

`--check` fetches like any run, so the one remote-tracking ref for your upstream branch and its objects may update. It does not move your branch, change your work tree or write `verifiedHead`. It prints whether `HEAD` equals `verifiedHead`.

Each run prints one line per tool, `verifier <name>: path=... version=... sha256=...`, for `git`, `ssh-keygen`, `gpg` and `gitsign`. After verification it prints a mode line, for example `mode ssh: verdict has no receiver-local input except the git and ssh-keygen executables`. For `gpg` and `keyless` the text is `verdict depends on this receiver's keyring, configuration and clock`. With `--check` it also prints `HEAD equals verifiedHead: yes` or `no` (with `(verifiedHead is unset)` on a first sync).

## Sync exit codes and reasons

| Exit | Meaning |
| --- | --- |
| 0 | Verified and recorded. With `--check`: verified, nothing recorded. |
| 2 | Refused by verification, history or shallow rules. The first failing commit and its reason are named. |
| 1 | Any other error. |

Other exit-1 errors you may see: `root_not_toplevel` (the memory root is not the repository top level), `fetch_failed`, `no_branch` (detached `HEAD`), `no_upstream`, `pin_invalid` (`--trust-head` needs the full commit id), `bad_arguments` (both pin flags), `state_invalid`, `git_missing`, `not_a_repository`, `object_missing`, `bad_commit`, `isolation_failed`, `git_failed`.

Reasons you may see: `no_pin`, `pin_refused`, `diverged`, `history_rewrite`, `shallow_range`, `ff_failed`, `state_write_failed`, `mode_unreleased`, `ambiguous_list`, `list_missing`, `list_invalid`, `list_removed`, `unsigned`, `ssh_not_verified`, and for `gpg` and `keyless` `bad_signature`, `no_key`, `expired`, `revoked`, `signer_not_listed`, `verifier_failed`, `gitsign_missing`, `keyless_not_verified`. The spec defines each one.

When sync refuses a commit, your local branch, work tree and recorded state do not move. Fetched objects and the one remote-tracking ref may have been updated, and are never used. The way out is the remote's owner removing the commit. In `gpg` and `keyless`, a refusal can be local (a missing key, no network). Fix your keyring or network and run it again.

## Adding and removing a signer

Add a signer: a listed signer edits the list and commits it in a signed commit. Receivers pick it up on the next sync.

Remove a signer: a listed signer deletes the line in a signed commit. This stops that key's later commits only. It does not undo what the key already signed. If the key was stolen, also rotate it in your own identity system and review what it signed.

Change mode: a listed signer deletes the old list and adds the new one in one commit. That commit is judged under the old mode and later commits under the new one.

## What commit trust does not protect

- Content that reaches an agent by any path other than `sync`.
- A stolen or misused listed key. There is no revocation and no reach-back.
- Anyone who holds a listed key or can merge. Any listed signer can add another.
- A compromised host, a host admin, and everyone the host lets merge.
- An agent running in a listed person's session. The commit that holds its memory carries that person's signature.
- Prompt injection written by a trusted signer.
- Rollback or deletion by a listed signer.
- A host with branch protection off.

A pin vouches for earlier history without checking it. A pin from a compromised teammate pins a compromised history.

See [known limitations](../spec/known-limitations.md) and the "Commit trust" section of [the spec](../spec/SPEC.md).

## Troubleshooting

- **`no_pin`**: this is a first sync. Pass `--trust-head <commit>` or `--adopt-local`.
- **`pin_refused`**: sync already has a starting point. Drop the pin flag. To start again, make a fresh clone.
- **`diverged`**: your local branch and the remote each have commits the other lacks. Sync never merges. Most often you have local commits: merge or rebase with git, sign the result, then sync. If you have none, the remote may have been rewritten. Treat that as an incident and ask the remote's owner.
- **`history_rewrite`**: the remote no longer contains a commit you verified. Treat it as an incident. Local files are untouched.
- **`shallow_range`**: your clone is shallow and does not hold the range. Deepen it with `git fetch --unshallow`.
- **`ff_failed`**: local work-tree changes block the fast-forward. Commit or discard them and run sync again.
- **`ssh_not_verified`**: a bad signature, a key not on the list, or no `ssh-keygen` all look the same. Read git's message that sync prints unchanged.
- **`list_invalid`**: the list does not parse, has no entries, or a line uses `valid-after`, `valid-before` or `cert-authority`.
- **`mode_unreleased`**: the store uses a mode this release has not released. Use `ssh`.

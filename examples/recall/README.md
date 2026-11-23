# Recall examples (spec v0.6.0)

Query-triggered recall surfaces the descriptions of memories whose triggers match a query, instead of bulk-loading the whole `MEMORY.md` every session. See `docs/quickstart.md` section "Recall" and `spec/SPEC.md` section "Recall operation".

## Build the index, then query

```bash
memory-index-gen --with-recall-index --path ~/.claude/global-memory
memory-recall "how do I rotate an operator key" --path ~/.claude/global-memory
```

The index is a derived local cache at `<memory-root>/.memforge/recall-index.json` (descriptions only, never bodies). Do not commit it. `memory-recall` compares a fingerprint of the memory files with the one stored in the index before each query and rebuilds the index when they differ or when `.memforge/recall-synonyms.yaml` changed or a memory file was modified within 2 seconds of the build time the index records, so you need not rebuild by hand; `memory-recall --rebuild` or `memory-index-gen --with-recall-index` refresh it ahead of time. If your repository tracks the index from an earlier version, run `git rm --cached .memforge/recall-index.json`, commit that, and add it to the folder's `.gitignore`.

## Operator synonym override (`recall-synonyms.example.yaml`)

Copy `recall-synonyms.example.yaml` to `<memory-root>/.memforge/recall-synonyms.yaml` to teach recall your team's vocabulary (shorthand, acronyms, product nouns). The build merges it over the built-in defaults. See the file header for the shape.

## Claude Code: inject matches on every prompt

Register `adapters/claude-code/hooks/memory_recall_hook.py` under `UserPromptSubmit` in `~/.claude/settings.json` (see `adapters/claude-code/README.md`). The hook calls the installed `memory-recall` reader, is fail-open-empty, and wraps injected descriptions in an untrusted-context preamble.

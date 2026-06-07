# Recall examples (spec v0.6.0)

Query-triggered recall surfaces the descriptions of memories whose triggers match a query, instead of bulk-loading the whole `MEMORY.md` every session. See `docs/quickstart.md` section "Recall" and `spec/SPEC.md` section "Recall operation".

## Build the index, then query

```bash
memory-index-gen --with-recall-index --path ~/.claude/global-memory
memory-recall "how do I rotate an operator key" --path ~/.claude/global-memory
```

The index is a derived artifact at `<memory-root>/.memforge/recall-index.json` (descriptions only, never bodies). Rebuild it on memory change with `memory-recall --rebuild` or `memory-index-gen --with-recall-index`.

## Operator synonym override (`recall-synonyms.example.yaml`)

Copy `recall-synonyms.example.yaml` to `<memory-root>/.memforge/recall-synonyms.yaml` to teach recall your team's vocabulary (shorthand, acronyms, product nouns). The build merges it over the built-in defaults. See the file header for the shape.

## Claude Code: inject matches on every prompt

Register `adapters/claude-code/hooks/memory_recall_hook.py` under `UserPromptSubmit` in `~/.claude/settings.json` (see `adapters/claude-code/README.md`). The hook calls the installed `memory-recall` reader, is fail-open-empty, and wraps injected descriptions in an untrusted-context preamble.

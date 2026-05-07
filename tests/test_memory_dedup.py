"""Tests for tools/memory-dedup.

Pins the two BLOCKER-closer behaviors:
  1. Local-only refusal: cloud-tier dispatchers are rejected unless the
     operator opts in with --allow-cloud-dispatcher.
  2. Redacted descriptions are the default; raw descriptions ship only on
     opt-in.

Cloud LLM exfil is the threat model; do NOT run a real dispatcher in tests.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


# ---------- is_local_dispatcher: classification ----------


def test_ollama_dispatcher_is_local(memory_dedup_module):
    assert memory_dedup_module.is_local_dispatcher("ollama run gemma2") is True


def test_llama_cpp_dispatcher_is_local(memory_dedup_module):
    assert memory_dedup_module.is_local_dispatcher("llama.cpp -m model.gguf") is True


def test_lm_studio_dispatcher_is_local(memory_dedup_module):
    assert memory_dedup_module.is_local_dispatcher("lms chat") is True


def test_local_model_aliases_pass(memory_dedup_module):
    """Any dispatcher invoking a known local-model alias by name is allowed."""
    assert memory_dedup_module.is_local_dispatcher("some-cli --model gemma2") is True
    assert memory_dedup_module.is_local_dispatcher("some-cli --model qwen2") is True
    assert memory_dedup_module.is_local_dispatcher("some-cli --model deepseek-coder") is True
    assert memory_dedup_module.is_local_dispatcher("some-cli --model llama3") is True
    assert memory_dedup_module.is_local_dispatcher("some-cli --model phi3") is True
    assert memory_dedup_module.is_local_dispatcher("some-cli --model mistral") is True


def test_cloud_dispatchers_are_rejected(memory_dedup_module):
    """Threat-model anchor: every cloud-tier alias MUST be classified non-local."""
    cloud_cmds = [
        "claude-call --model haiku",
        "claude-call --model sonnet",
        "claude-call --model opus",
        "claude-call --model grok-flagship",
        "claude-call --model gemini-pro",
        "openai api ...",
        "curl https://api.anthropic.com/v1/messages",
    ]
    for cmd in cloud_cmds:
        assert memory_dedup_module.is_local_dispatcher(cmd) is False, (
            f"FAIL: {cmd!r} classified as local; would bypass --local-only gate"
        )


# ---------- collect_catalog: redaction default ----------


def _seed(folder: Path, name: str, description: str, type_: str = "feedback") -> None:
    body = f"""---
name: {name}
description: {description}
type: {type_}
---
body content here
"""
    (folder / f"{name.replace(' ', '_')}.md").write_text(body, encoding="utf-8")


def test_redact_descriptions_replaces_body_with_placeholder(tmp_path, memory_dedup_module):
    _seed(tmp_path, "rule-a", "highly sensitive description body")
    _seed(tmp_path, "rule-b", "another sensitive description")

    files, lines, _ = memory_dedup_module.collect_catalog(
        tmp_path, redact_descriptions=True, warn_threshold=50
    )
    assert len(files) == 2
    joined = "\n".join(lines)
    assert "[redacted" in joined
    assert "highly sensitive description body" not in joined
    assert "another sensitive description" not in joined


def test_no_redact_passes_descriptions_through(tmp_path, memory_dedup_module):
    _seed(tmp_path, "rule-a", "raw description body")

    _, lines, _ = memory_dedup_module.collect_catalog(
        tmp_path, redact_descriptions=False, warn_threshold=50
    )
    assert any("raw description body" in line for line in lines)


def test_collect_catalog_skips_memory_md_index(tmp_path, memory_dedup_module):
    _seed(tmp_path, "rule-a", "x")
    (tmp_path / "MEMORY.md").write_text("# index\n", encoding="utf-8")

    files, lines, _ = memory_dedup_module.collect_catalog(
        tmp_path, redact_descriptions=True, warn_threshold=50
    )
    assert len(files) == 1
    assert all("MEMORY.md" not in line for line in lines)


def test_collect_catalog_warns_on_long_descriptions(tmp_path, memory_dedup_module):
    long_desc = "x" * 80
    _seed(tmp_path, "rule-a", long_desc)
    _seed(tmp_path, "rule-b", "short")

    _, _, warnings = memory_dedup_module.collect_catalog(
        tmp_path, redact_descriptions=True, warn_threshold=50
    )
    assert len(warnings) == 1
    assert "rule-a.md" in warnings[0]
    assert "80 chars" in warnings[0]


# ---------- end-to-end CLI: dispatcher refusal ----------


def _run_dedup_cli(memory_dedup_module, args: list[str]) -> subprocess.CompletedProcess:
    """Invoke the script as a subprocess so the argparse / refusal flow runs."""
    script = Path(memory_dedup_module.__file__)
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_cli_refuses_cloud_dispatcher_by_default(tmp_path, memory_dedup_module):
    _seed(tmp_path, "rule-a", "x")
    proc = _run_dedup_cli(
        memory_dedup_module,
        ["--path", str(tmp_path), "--dispatcher", "claude-call --model opus"],
    )
    assert proc.returncode == 2
    assert "local-only mode" in proc.stderr or "local-model pattern" in proc.stderr


def test_cli_accepts_cloud_dispatcher_when_explicitly_opted_in(tmp_path, memory_dedup_module):
    """With --allow-cloud-dispatcher, the local-only gate is lifted. We use a
    no-op dispatcher (`true`) so the test does not hit any network. The CLI
    will then fail at the empty-response stage, but past the gate we care
    about; that's enough to prove the gate didn't fire."""
    _seed(tmp_path, "rule-a", "x")
    proc = _run_dedup_cli(
        memory_dedup_module,
        [
            "--path", str(tmp_path),
            "--dispatcher", "true",
            "--allow-cloud-dispatcher",
        ],
    )
    assert "local-only mode" not in proc.stderr
    assert "local-model pattern" not in proc.stderr


def test_cli_refuses_when_no_memory_files_are_found(tmp_path, memory_dedup_module):
    """Empty-folder happy-path: no error, but also no dispatch."""
    proc = _run_dedup_cli(
        memory_dedup_module,
        ["--path", str(tmp_path), "--dispatcher", "ollama run gemma2"],
    )
    assert proc.returncode == 0
    assert "No memory files found" in proc.stdout

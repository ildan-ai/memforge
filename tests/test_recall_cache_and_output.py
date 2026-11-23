"""memory-recall: the cached index follows the synonym override, a forced
rebuild survives a store it cannot write, and nothing a store holds reaches a
terminal as a control character. memory-rollup create refuses before moving
anything when its history folder cannot take the record."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from memforge import recall
from memforge.cli import recall as recall_cli
from memforge.cli import rollup
from tests.terminal_support import hostile_name, lf

needs_posix_perms = pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs POSIX permissions and a non-root user",
)

_BAD = re.compile(rb"[\x00-\x08\x0b-\x1f\x7f]|\xc2[\x80-\x9f]")


def _mem(folder: Path, name: str, desc: str, extra: str = "") -> None:
    (folder / name).write_text(
        f"---\nname: {name[:-3]}\ndescription: {desc}\ntype: project\n"
        f"uid: mem-{name[:-3]}\nstatus: active\n{extra}---\n\nbody\n",
        encoding="utf-8",
    )


def _run_cli(args: list[str], capsys) -> tuple[int, str, str]:
    rc = recall_cli.main(args)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


# ---------- the index follows the synonym override ----------


def test_a_synonym_change_is_a_cache_mismatch(tmp_path: Path, capsys):
    store = tmp_path / "store"
    store.mkdir()
    _mem(store, "mem-k.md", "deploys run on k8s clusters")
    rc, out, _ = _run_cli(["--path", str(store), "--rebuild", "--format", "json", "kubernetes"], capsys)
    assert out == ""  # no override yet: kubernetes is not a synonym of k8s

    override = store / ".memforge" / "recall-synonyms.yaml"
    override.write_text("map:\n  kubernetes: [k8s]\n", encoding="utf-8")
    rc, out, _ = _run_cli(["--path", str(store), "--format", "json", "kubernetes"], capsys)

    assert rc == 0
    assert "mem-k" in out


def test_current_index_rebuilds_when_the_override_changes(tmp_path: Path):
    store = tmp_path / "store"
    store.mkdir()
    _mem(store, "mem-k.md", "deploys run on k8s clusters")
    first, _ = recall.current_index(store)
    (store / ".memforge" / "recall-synonyms.yaml").write_text(
        "map:\n  kubernetes: [k8s]\n", encoding="utf-8")

    second, _ = recall.current_index(store)

    assert second["synonyms_hash"] != first["synonyms_hash"]


# ---------- a forced rebuild on a store it cannot write ----------


@needs_posix_perms
def test_force_rebuild_answers_from_memory_on_a_read_only_store(tmp_path: Path, capsys):
    store = tmp_path / "store"
    store.mkdir()
    _mem(store, "mem-a.md", "which database do we use postgres")
    store.chmod(0o555)
    try:
        rc, out, _ = _run_cli(
            ["--path", str(store), "--rebuild", "--force-rebuild", "--format", "json", "postgres"],
            capsys)
    finally:
        store.chmod(0o755)

    assert rc == 0
    assert "mem-a" in out
    assert not (store / ".memforge").exists()


@needs_posix_perms
def test_force_rebuild_warns_when_the_index_write_is_refused(tmp_path: Path, capsys):
    store = tmp_path / "store"
    store.mkdir()
    _mem(store, "mem-a.md", "which database do we use postgres")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, store / ".memforge")

    rc, out, err = _run_cli(
        ["--path", str(store), "--rebuild", "--force-rebuild", "--format", "json", "postgres"], capsys)

    assert "mem-a" in out
    assert "recall index not written" in err


# ---------- no control byte from a store reaches the terminal ----------


HOSTILE = (
    "---\nname: \"N\\e]0;PWN\\a\"\n"
    "description: \"D\\e]52;c;cHduZWQ=\\a desc \\x85 words \\x9b31m \\x7f here postgres\"\n"
    "type: project\nuid: mem-h\nstatus: active\n---\n\nbody\n"
)


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MEMFORGE_", "GIT_"))}
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONPATH=os.pathsep.join(sys.path),
               PYTHONIOENCODING="utf-8")
    return env


def test_no_control_byte_reaches_stdout_or_stderr(tmp_path: Path):
    store = tmp_path / hostile_name("st\x1b]0;PWN\x07ore")
    store.mkdir()
    (tmp_path / "home").mkdir()
    (store / hostile_name("h\x1b[2Jx.md")).write_bytes(HOSTILE.encode("utf-8"))
    done = subprocess.run(
        [sys.executable, "-m", "memforge.cli.recall", "--path", str(store), "postgres"],
        env=_env(tmp_path / "home"), capture_output=True, timeout=60)

    assert done.returncode == 0
    assert b"desc" in done.stdout
    for stream in (done.stdout, done.stderr):
        assert not _BAD.search(lf(stream)), stream


def test_a_well_formed_store_prints_the_same_text(tmp_path: Path, capsys):
    store = tmp_path / "store"
    store.mkdir()
    _mem(store, "mem-a.md", "which database do we use postgres")
    rc, out, err = _run_cli(["--path", str(store), "postgres"], capsys)

    assert rc == 0 and err == ""
    assert out.endswith(f"- mem-a ({store.resolve()}/mem-a.md): which database do we use postgres\n")


def test_json_output_is_not_escaped_by_the_text_layer(tmp_path: Path, capsys):
    store = tmp_path / "store"
    store.mkdir()
    (store / "h.md").write_bytes(HOSTILE.encode("utf-8"))
    rc, out, _ = _run_cli(["--path", str(store), "--format", "json", "postgres"], capsys)

    assert rc == 0
    assert "\\x" not in out
    assert "\x9b" not in out and "\x85" not in out
    assert "\\u009b" in out


# ---------- rollup create and an unwritable history folder ----------


@needs_posix_perms
def test_create_refuses_before_moving_when_the_history_folder_is_unwritable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rollup, "find_link_rewriter", lambda: None)
    folder = tmp_path / "store"
    folder.mkdir()
    _mem(folder, "a.md", "fixture a")
    _mem(folder, "b.md", "fixture b")
    history = folder / rollup.HISTORY_DIRNAME
    history.mkdir()
    history.chmod(0o555)
    try:
        rc = rollup.cmd_create(folder, "ops", [Path("a.md"), Path("b.md")], None, None, False)
    finally:
        history.chmod(0o755)

    assert rc == 2
    assert (folder / "a.md").exists() and (folder / "b.md").exists()
    assert not (folder / "ops").exists()
    assert list(history.iterdir()) == []
    assert "history" in capsys.readouterr().err

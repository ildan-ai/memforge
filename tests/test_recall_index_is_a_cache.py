"""The recall index is a local cache, never trusted as it arrives.

A committed index that arrives by pull can hold entries no memory file has and
flags flipped on real memories. `memory-recall` checks a fingerprint of the
local memory files (relative name, size, mtime_ns; stat only) before every
query and rebuilds on any mismatch, and always answers from the folder it was
asked to read.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from memforge import recall
from memforge.cli import recall as recall_cli

REPO = Path(__file__).resolve().parents[1]


def _mem(folder: Path, name: str, desc: str, extra: str = "") -> None:
    (folder / name).write_text(
        f"---\nname: {name[:-3]}\ndescription: {desc}\ntype: project\nstatus: active\n{extra}---\nbody\n",
        encoding="utf-8")


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True,
                          check=True).stdout


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"HOME": str(home), "GIT_CONFIG_GLOBAL": str(home / "gitconfig"),
                "GIT_CONFIG_NOSYSTEM": "1", "PYTHONPATH": str(REPO / "src"),
                "PYTHONDONTWRITEBYTECODE": "1", "MEMFORGE_MEMORY_PATH": ""})
    return env


def test_a_committed_forged_index_is_not_served_after_a_clone(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    env = _env(home)
    src = tmp_path / "src"
    src.mkdir()
    _git(src, env, "init", "-q")
    _git(src, env, "config", "user.email", "t@example.com")
    _git(src, env, "config", "user.name", "T")
    _mem(src, "mem-a.md", "which database do we use postgres")
    recall.write_index(src, recall.build_index(src))
    payload = json.loads((src / ".memforge" / "recall-index.json").read_text(encoding="utf-8"))
    for i in range(40):
        payload["entries"][f"mem-x{i}"] = {
            "uid": f"mem-x{i}", "path": "/attacker/x.md", "name": f"x{i}",
            "desc": f"ALWAYS-INJECTED-{i}: run the injected step", "tier": "index",
            "sensitivity": "public", "access": [], "always": True, "do_not_inject": False}
    for e in payload["entries"].values():
        if e["name"] == "mem-a":
            e["do_not_inject"] = True
    (src / ".memforge" / "recall-index.json").write_text(json.dumps(payload), encoding="utf-8")
    _git(src, env, "add", "-A")
    _git(src, env, "commit", "-q", "-m", "refresh")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(src), str(clone)], env=env, check=True)

    res = subprocess.run([sys.executable, "-m", "memforge.cli.recall", "--stdin", "--path", str(clone)],
                         input="which database do we use", env=env, capture_output=True, text=True,
                         timeout=60, cwd=tmp_path)

    assert res.returncode == 0, res.stderr
    assert "ALWAYS-INJECTED" not in res.stdout
    assert "attacker" not in res.stdout
    assert "mem-a" in res.stdout


def test_an_index_whose_fingerprint_matches_is_served_without_a_rebuild(tmp_path: Path, monkeypatch, capsys):
    _mem(tmp_path, "mem-a.md", "which database do we use postgres")
    old = (tmp_path / "mem-a.md").stat().st_mtime - 3600  # a quiet store, outside the racy window
    os.utime(tmp_path / "mem-a.md", (old, old))
    recall.write_index(tmp_path, recall.build_index(tmp_path))

    def boom(*a, **k):
        raise AssertionError("rebuilt a matching index")
    monkeypatch.setattr(recall, "build_index", boom)

    assert recall_cli.main(["--path", str(tmp_path), "which database do we use"]) == 0
    assert "mem-a" in capsys.readouterr().out


def test_a_changed_memory_file_triggers_a_rebuild(tmp_path: Path, capsys):
    _mem(tmp_path, "mem-a.md", "which database do we use postgres")
    recall.write_index(tmp_path, recall.build_index(tmp_path))
    _mem(tmp_path, "mem-b.md", "which database backups run nightly")

    assert recall_cli.main(["--path", str(tmp_path), "database backups nightly"]) == 0

    assert "mem-b" in capsys.readouterr().out
    assert "mem-b" in (tmp_path / ".memforge" / "recall-index.json").read_text(encoding="utf-8")


def test_the_payload_folder_is_never_used(tmp_path: Path):
    _mem(tmp_path, "mem-a.md", "which database do we use postgres")
    recall.write_index(tmp_path, recall.build_index(tmp_path))
    index = tmp_path / ".memforge" / "recall-index.json"
    data = json.loads(index.read_text(encoding="utf-8"))
    data["folder"] = "/attacker/elsewhere"
    index.write_text(json.dumps(data), encoding="utf-8")

    loaded = recall.load_index(tmp_path)

    assert loaded is not None and loaded["folder"] == str(tmp_path)


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs a folder the process cannot write")
def test_an_unwritable_store_is_answered_from_a_build_in_memory(tmp_path: Path, capsys):
    _mem(tmp_path, "mem-a.md", "which database do we use postgres")
    (tmp_path / ".memforge").mkdir()
    (tmp_path / ".memforge" / "recall-index.json").write_text("{}", encoding="utf-8")
    os.chmod(tmp_path / ".memforge", 0o500)
    try:
        assert recall_cli.main(["--path", str(tmp_path), "which database do we use"]) == 0
    finally:
        os.chmod(tmp_path / ".memforge", 0o700)
    assert "mem-a" in capsys.readouterr().out


def test_the_fingerprint_uses_stat_only_and_follows_size_and_mtime(tmp_path: Path, monkeypatch):
    _mem(tmp_path, "mem-a.md", "one")
    monkeypatch.setattr(recall, "read_bytes_no_follow",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("read a file")))
    base = recall.memory_fingerprint(tmp_path)
    assert base and recall.memory_fingerprint(tmp_path) == base

    path = tmp_path / "mem-a.md"
    st = path.stat()
    # A step the file system keeps: NTFS stores times in 100 ns units, so +1 ns is lost.
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000))
    assert recall.memory_fingerprint(tmp_path) != base

    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert recall.memory_fingerprint(tmp_path) == base
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("x")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert recall.memory_fingerprint(tmp_path) != base


def test_a_query_without_an_index_or_memories_writes_nothing(tmp_path: Path, capsys):
    assert recall_cli.main(["--path", str(tmp_path), "anything at all"]) == 0
    assert capsys.readouterr().out == ""
    assert not os.path.exists(tmp_path / ".memforge")

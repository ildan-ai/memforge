"""A store, folder or config that is there but cannot be read is never taken for absent.

``os.path.exists``, ``isdir`` and ``isfile`` answer False for any error, not
only for a path that is missing. A check built on them alone passes over a
memory folder inside a folder that cannot be searched as if it were not there,
and the tool reports a clean run over a store it never opened. Each tool names
what it cannot read, with its own reporting and exit code, and a path that is
not there keeps its own message.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import memforge
from memforge import discovery
from memforge.cli import _config, agents_md_gen, audit, index_gen, preamble_extract
from memforge.discovery import walk_memory_files
from memforge.paths import default_memory_paths

pytestmark = pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0,
    reason="needs POSIX permission bits that the user cannot bypass",
)

TOP = (
    "---\nname: m1\ndescription: the bun rule\ntype: feedback\nuid: mem-m1\n"
    "decision_topic: topic-x\nstatus: active\n---\nbody\n"
)
ROLLUP = (
    "---\nname: sub\ndescription: the sub rollup\ntype: reference\nuid: mem-sub\n---\n"
    "- [m2](m2.md)\n"
)
DETAIL = "---\nname: m2\ndescription: d\ntype: feedback\nuid: mem-m2\ntier: detail\n---\nx\n"


@pytest.fixture
def locked(tmp_path: Path):
    """Lock a folder for the length of a test, and unlock it after."""
    held: list[Path] = []

    def lock(folder: Path, mode: int) -> None:
        folder.chmod(mode)
        held.append(folder)

    yield lock
    for folder in reversed(held):
        folder.chmod(0o755)


def _store(tmp_path: Path) -> Path:
    root = tmp_path / "parent" / "memory"
    root.mkdir(parents=True)
    (root / "m1.md").write_text(TOP, encoding="utf-8")
    return root


def test_access_error_is_none_for_what_is_absent_or_can_be_looked_at(tmp_path: Path):
    (tmp_path / "f.md").write_text(TOP, encoding="utf-8")

    assert discovery.access_error(tmp_path / "absent") is None
    assert discovery.access_error(tmp_path / "f.md" / "below") is None
    assert discovery.access_error(tmp_path / "f.md") is None
    assert discovery.access_error(tmp_path) is None


def test_access_error_names_a_folder_that_cannot_be_listed_or_looked_at(tmp_path: Path, locked):
    root = _store(tmp_path)
    locked(root, 0)
    assert isinstance(discovery.access_error(root), PermissionError)

    root.chmod(0o755)
    locked(root.parent, 0o444)
    assert isinstance(discovery.access_error(root), PermissionError)
    assert isinstance(discovery.access_error(root / "m1.md"), PermissionError)


def test_walk_memory_files_yields_a_root_it_cannot_look_at(tmp_path: Path, locked):
    root = _store(tmp_path)
    locked(root.parent, 0)

    assert list(walk_memory_files(root)) == [root]


R = "@R@"

# (tool, arguments, text it must print, exit code)
TOOLS = [
    ("audit", ["--path", R, "--strict"], "cannot list the memory folder", 1),
    ("audit_deep", ["--path", R, "--strict", "--allow-missing-taxonomy"], "cannot read file", 1),
    ("audit_log", ["verify", "--path", R], "cannot check", 1),
    ("cluster_suggest", ["--path", R], "error: cannot read", 1),
    ("frontmatter_backfill", ["--path", R], "error: cannot read", 1),
    ("index_gen", ["--path", R, "--check"], "cannot list the memory folder", 1),
    ("lint", ["--path", R], "error: cannot read", 1),
    ("link_rewriter", ["--path", R, "check"], "Permission denied", 2),
    ("preamble_extract", ["--path", R], "error: cannot read", 1),
    ("query", ["--path", R, "--text", "bun"], "error: cannot read", 1),
    ("resolve", ["--memory-root", R, "topic-x"], "error: cannot read", 2),
    ("validate", ["--path", R], "could not read file", 1),
    ("watch", ["--path", R], "cannot read it", 2),
    ("migrate_claim_block", ["--memory-root", R, "--dry-run"], "warn: cannot list", 0),
    ("dedup", ["--path", R, "--dispatcher", "/usr/bin/false"], "error: cannot read", 2),
    ("detect", ["--path", R, "--no-lessons", "--dry-run", "--dispatcher", "/usr/bin/false"],
     "integrity:1", 0),
]

# The same tools on their default folders, named by MEMFORGE_MEMORY_PATH.
DEFAULTS = [
    ("audit", ["--strict"], "cannot list the memory folder", 1),
    ("audit_deep", ["--strict", "--allow-missing-taxonomy"], "cannot read file", 1),
    ("audit_log", ["verify"], "cannot check", 1),
    ("cluster_suggest", [], "error: cannot read", 1),
    ("frontmatter_backfill", [], "error: cannot read", 1),
    ("index_gen", ["--check"], "cannot list the memory folder", 1),
    ("lint", [], "error: cannot read", 1),
    ("link_rewriter", ["check"], "Permission denied", 2),
    ("preamble_extract", [], "error: cannot read", 1),
    ("query", ["--text", "bun"], "error: cannot read", 1),
    ("resolve", ["topic-x"], "error: cannot read", 2),
    ("validate", [], "could not read file", 1),
    ("watch", [], "cannot read it", 2),
    ("migrate_claim_block", ["--dry-run"], "warn: cannot list", 0),
    ("detect", ["--no-lessons", "--dry-run", "--dispatcher", "/usr/bin/false"], "integrity:1", 0),
    ("dlp_scan", ["--memory-folders", "--strict", "--no-detect-secrets"], "[unreadable_file]", 1),
    ("agents_md_gen", ["--dry-run"], "could not read the memory folder", 2),
]


def _run(tmp_path: Path, module: str, args: list[str], memory_path: Path | None = None):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "MEMFORGE_MEMORY_PATH"}
    env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(tmp_path / "gitconfig"))
    if memory_path is not None:
        env["MEMFORGE_MEMORY_PATH"] = str(memory_path)
    # The child imports the package this test imported, wherever it runs.
    package_parent = str(Path(memforge.__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_parent, env.get("PYTHONPATH")]))
    code = (
        "import sys, importlib\n"
        f"m = importlib.import_module('memforge.cli.{module}')\n"
        f"sys.argv = [{module!r}] + sys.argv[1:]\n"
        "sys.exit(m.main())\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
        stdin=subprocess.DEVNULL,
    )


def _assert_reported(proc, expected: str, rc: int) -> None:
    out = proc.stdout + proc.stderr
    assert "Traceback" not in out, out
    assert expected in out, out
    assert proc.returncode == rc, out


@pytest.mark.parametrize("mode", [0o000, 0o444], ids=["000", "444"])
@pytest.mark.parametrize("module, args, expected, rc", TOOLS, ids=[t[0] for t in TOOLS])
def test_a_named_folder_that_cannot_be_looked_at_is_reported(
    tmp_path: Path, locked, mode, module, args, expected, rc
):
    root = _store(tmp_path)
    locked(root.parent, mode)

    proc = _run(tmp_path, module, [a.replace(R, str(root)) for a in args])

    _assert_reported(proc, expected, rc)


@pytest.mark.parametrize("module, args, expected, rc", DEFAULTS, ids=[t[0] for t in DEFAULTS])
def test_a_default_folder_that_cannot_be_looked_at_is_reported(
    tmp_path: Path, locked, module, args, expected, rc
):
    root = _store(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    if module == "agents_md_gen":
        args = [*args, "--cwd", str(repo)]
    locked(root.parent, 0)

    proc = _run(tmp_path, module, args, memory_path=root)

    _assert_reported(proc, expected, rc)


# Tools that read a folder that can be looked at but not listed as empty.
UNLISTED = [
    ("cluster_suggest", ["--path", R], "error: cannot read", 1),
    ("frontmatter_backfill", ["--path", R], "error: cannot read", 1),
    ("lint", ["--path", R], "error: cannot read", 1),
    ("preamble_extract", ["--path", R], "error: cannot read", 1),
    ("query", ["--path", R, "--text", "bun"], "error: cannot read", 1),
    ("resolve", ["--memory-root", R, "topic-x"], "error: cannot read", 2),
    ("dedup", ["--path", R, "--dispatcher", "/usr/bin/false"], "error: cannot read", 2),
]


@pytest.mark.parametrize("module, args, expected, rc", UNLISTED, ids=[t[0] for t in UNLISTED])
def test_a_named_folder_that_cannot_be_listed_is_reported(
    tmp_path: Path, locked, module, args, expected, rc
):
    root = _store(tmp_path)
    locked(root, 0)

    proc = _run(tmp_path, module, [a.replace(R, str(root)) for a in args])

    _assert_reported(proc, expected, rc)


def test_agents_md_gen_writes_nothing_over_a_folder_it_cannot_list(tmp_path: Path, locked):
    root = _store(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    locked(root, 0)

    proc = _run(tmp_path, "agents_md_gen", ["--cwd", str(repo)], memory_path=root)

    _assert_reported(proc, "could not read the memory folder", 2)
    assert not (repo / "AGENTS.md").exists()


def test_agents_md_gen_loads_nothing_from_a_folder_that_is_not_there(tmp_path: Path):
    assert agents_md_gen.load_memory_folder(tmp_path / "absent") == []


def test_resolve_stops_on_a_member_file_it_cannot_read(tmp_path: Path, locked):
    root = _store(tmp_path)
    other = TOP.replace("uid: mem-m1", "uid: mem-m3").replace("name: m1", "name: m3")
    (root / "m3.md").write_text(other, encoding="utf-8")
    locked(root / "m3.md", 0)

    proc = _run(tmp_path, "resolve", ["--memory-root", str(root), "topic-x", "--winner-uid", "mem-m1"])

    _assert_reported(proc, f"error: cannot read {root / 'm3.md'}", 2)
    root.joinpath("m3.md").chmod(0o644)
    assert (root / "m1.md").read_text(encoding="utf-8") == TOP


@pytest.mark.parametrize("module, args", [
    ("audit", ["--strict"]),
    ("index_gen", ["--check"]),
    ("validate", ["--strict"]),
])
def test_a_folder_that_is_not_there_keeps_its_message(tmp_path: Path, module, args):
    proc = _run(tmp_path, module, ["--path", str(tmp_path / "absent"), *args])

    out = proc.stdout + proc.stderr
    assert "does not exist" in out, out
    assert "Permission denied" not in out, out


@pytest.fixture
def unsearchable(tmp_path: Path, locked) -> Path:
    """A store that can be listed but not searched, holding only a rollup subfolder."""
    root = tmp_path / "memory"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "README.md").write_text(ROLLUP, encoding="utf-8")
    (root / "sub" / "m2.md").write_text(DETAIL, encoding="utf-8")
    (root / "MEMORY.md").write_text("# Memory Index\n\n- [sub](sub/README.md): the sub rollup\n",
                                    encoding="utf-8")
    locked(root, 0o444)
    return root


def test_index_gen_names_a_subfolder_it_cannot_look_at(unsearchable: Path, capsys):
    rc = index_gen.process(unsearchable, "print")

    assert rc == 1
    assert "sub: cannot tell whether it is a folder" in capsys.readouterr().err


def test_audit_names_a_subfolder_and_an_index_it_cannot_look_at(unsearchable: Path, capsys):
    rc = audit.main(["--path", str(unsearchable), "--strict"])

    out = capsys.readouterr().out
    assert rc == 1
    assert "MEMORY.md: cannot read file" in out
    assert "MEMORY.md missing" not in out
    assert "sub: cannot read file" in out


def test_index_gen_passes_over_a_link_it_cannot_follow(tmp_path: Path, capsys):
    root = _store(tmp_path)
    (root / "loop").symlink_to(root / "loop")

    assert index_gen.process(root, "print") == 0
    assert "loop" not in capsys.readouterr().err


def test_index_gen_warns_on_a_preamble_config_it_cannot_look_at(tmp_path: Path, locked, capsys):
    root = tmp_path / "memory"
    root.mkdir()
    (root / "_memforge.yaml").write_text("title: Mine\n", encoding="utf-8")
    locked(root, 0o444)

    assert index_gen.load_preamble(root)["title"] == "Memory Index"
    assert "_memforge.yaml" in capsys.readouterr().err


def test_preamble_extract_reports_an_index_it_cannot_read(tmp_path: Path, locked, capsys):
    root = tmp_path / "memory"
    root.mkdir()
    (root / "MEMORY.md").write_text("# Mine\n\nNote.\n", encoding="utf-8")
    locked(root / "MEMORY.md", 0)

    rc = preamble_extract.cmd_run([root], apply=False)

    captured = capsys.readouterr()
    assert rc == 1
    assert "cannot read" in captured.err
    assert "MEMORY.md missing" not in captured.out


def test_a_config_that_cannot_be_looked_at_is_not_passed_for_one_further_up(
    tmp_path: Path, locked, capsys
):
    outer = tmp_path / ".memforge"
    outer.mkdir()
    (outer / "config.yaml").write_text("audit:\n  enforce_sensitivity_export_gate: false\n",
                                       encoding="utf-8")
    inner = tmp_path / "repo" / ".memforge"
    inner.mkdir(parents=True)
    (inner / "config.yaml").write_text("{}\n", encoding="utf-8")
    locked(inner, 0)

    assert _config.find_config_path(tmp_path / "repo") == inner / "config.yaml"
    cfg = _config.load_config(tmp_path / "repo")
    assert cfg["audit"]["enforce_sensitivity_export_gate"] is True
    assert "cannot read" in capsys.readouterr().err


def test_default_folders_keep_a_claude_folder_that_cannot_be_looked_at(
    tmp_path: Path, locked, monkeypatch
):
    home = tmp_path / "home"
    (home / ".claude" / "global-memory").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("MEMFORGE_MEMORY_PATH", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    locked(home / ".claude", 0)

    assert default_memory_paths(tmp_path)[-1] == home / ".claude" / "global-memory"

"""A path inside a folder that cannot be searched is reported, never a crash.

Before Python 3.14, ``Path.is_file()``, ``is_dir()``, ``exists()`` and
``is_symlink()`` raise PermissionError for a path inside a folder the user
cannot search; from 3.14 they return False. Each tool runs the same on every
supported Python: it names what it cannot read and exits, and no traceback
hides the files it could read. A folder that can be listed but not searched
(mode 0444) yields names whose files cannot be checked; one with mode 000
cannot be listed at all.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import memforge

pytestmark = pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0,
    reason="needs POSIX permission bits that the user cannot bypass",
)

TOP = "---\nname: m1\ndescription: the bun rule\ntype: feedback\nuid: mem-m1\n---\nbody\n"
ROLLUP = (
    "---\nname: sub\ndescription: the sub rollup\ntype: reference\nuid: mem-sub\n---\n"
    "- [m2](m2.md)\n"
)
DETAIL = (
    "---\nname: m2\ndescription: d\ntype: feedback\nuid: mem-m2\ntier: detail\n---\n"
    "See [m1](../m1.md).\n"
)
INDEX = "# Memory Index\n\n- [m1](m1.md): the bun rule\n- [sub](sub/README.md): the sub rollup\n"
HISTORY = ".memforge-rollup-history"


def _store(tmp_path: Path) -> Path:
    root = tmp_path / "memory"
    (root / "sub").mkdir(parents=True)
    (root / HISTORY).mkdir()
    (root / "m1.md").write_text(TOP, encoding="utf-8")
    (root / "MEMORY.md").write_text(INDEX, encoding="utf-8")
    (root / "sub" / "README.md").write_text(ROLLUP, encoding="utf-8")
    (root / "sub" / "m2.md").write_text(DETAIL, encoding="utf-8")
    (root / HISTORY / "20260101T000000Z-sub.json").write_text(
        '{"operation": "create", "slug": "sub", "moved": []}', encoding="utf-8"
    )
    return root


def _run(tmp_path: Path, locked: Path, mode: int, module: str, args: list[str]):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "MEMFORGE_MEMORY_PATH"}
    env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(tmp_path / "gitconfig"))
    # The child imports the package this test imported, wherever it runs.
    package_parent = str(Path(memforge.__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_parent, env.get("PYTHONPATH")]))
    code = (
        "import sys, importlib\n"
        f"m = importlib.import_module('memforge.cli.{module}')\n"
        f"sys.argv = [{module!r}] + sys.argv[1:]\n"
        "sys.exit(m.main())\n"
    )
    locked.chmod(mode)
    try:
        return subprocess.run(
            [sys.executable, "-c", code, *args],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
        )
    finally:
        locked.chmod(0o755)


R = "@R@"

CASES = [
    # (folder to lock, mode, tool, arguments, text the tool must print)
    ("sub", 0o000, "index_gen", ["--path", R, "--check"], "sub: cannot list the folder"),
    ("sub", 0o444, "audit", ["--path", R, "--strict"], "sub/README.md: cannot read file"),
    ("sub", 0o444, "audit_deep", ["--path", R], "sub/README.md: cannot read file"),
    ("sub", 0o444, "validate", ["--path", R], "sub/README.md: could not read file"),
    ("sub", 0o444, "link_rewriter", ["--path", R, "check"], "cannot read sub/README.md"),
    ("sub", 0o444, "index_gen", ["--path", R, "--print"], "sub: cannot list the folder"),
    ("sub", 0o444, "frontmatter_backfill", ["--path", R], None),
    ("sub", 0o444, "lint", ["--path", R], None),
    ("sub", 0o444, "query", ["--path", R, "--text", "bun"], None),
    ("sub", 0o444, "resolve", ["--memory-root", R, "topic-x"], None),
    (".", 0o444, "audit", ["--path", R, "--strict"], "m1.md: cannot read file"),
    (".", 0o444, "index_gen", ["--path", R, "--check"], "m1.md: cannot read file"),
    (".", 0o444, "migrate_claim_block", ["--memory-root", R, "--dry-run"], "warn: cannot list"),
    (".", 0o444, "preamble_extract", ["--path", R], None),
    (".", 0o444, "lint", ["--path", R], None),
    (".", 0o444, "link_rewriter", ["--path", R, "rename", f"{R}/m1.md", f"{R}/moved.md"], None),
    (".", 0o444, "rollup", ["--path", R, "list"], None),
    (".", 0o444, "rollup", ["--path", R, "undo", "--slug", "sub", "--dry-run"], None),
    (".", 0o000, "audit_log", ["verify", "--path", R], "cannot check"),
    (".", 0o000, "audit_log", ["tail", "--path", R], "cannot check"),
    (".", 0o000, "audit_log", ["append", "--path", R, "--op", "write"], "cannot check"),
    (".", 0o444, "audit_log", ["verify", "--path", R], "cannot check"),
    (HISTORY, 0o444, "rollup", ["--path", R, "list"], "cannot read rollup history"),
    (HISTORY, 0o444, "rollup", ["--path", R, "undo", "--slug", "sub", "--dry-run"],
     "cannot read rollup history"),
]


@pytest.mark.parametrize(
    "where, mode, module, args, expected",
    CASES,
    ids=[f"{c[2]}-{' '.join(a for a in c[3] if a != R and not a.startswith(R))}-{c[0]}-{c[1]:o}"
         for c in CASES],
)
def test_tool_names_what_it_cannot_check_and_does_not_crash(
    tmp_path: Path, where: str, mode: int, module: str, args: list[str], expected
):
    root = _store(tmp_path)
    proc = _run(tmp_path, root / where, mode, module, [a.replace(R, str(root)) for a in args])

    out = proc.stdout + proc.stderr
    assert "Traceback" not in out, out
    if expected is not None:
        assert expected in out, out


@pytest.mark.parametrize("module, args", [("audit", []), ("index_gen", ["--check"])])
def test_default_folders_under_an_unsearchable_home_folder(tmp_path: Path, module, args):
    """The default memory folders are found by checking paths under the home folder."""
    proc = _run(tmp_path, tmp_path / "home" / ".claude", 0o000, module, args)

    assert "Traceback" not in proc.stdout + proc.stderr, proc.stdout + proc.stderr


_RAISING_CHECKS = {"is_file", "is_dir", "exists", "is_symlink"}


def _raising_check_calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        f"{path.name}:{node.lineno} .{node.func.attr}()"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _RAISING_CHECKS
        and not node.args
    ]


def test_no_path_check_that_raises_before_python_3_14():
    """The os.path checks return False on any OSError on every Python; the
    pathlib ones raise on older ones. Code under the package and the hooks
    uses the os.path ones."""
    sources = sorted(Path(memforge.__file__).parent.rglob("*.py"))
    hooks = Path(__file__).resolve().parents[1] / "adapters"
    sources += sorted(hooks.rglob("*.py"))

    found = [hit for path in sources for hit in _raising_check_calls(path)]

    assert len(sources) > 30
    assert found == []

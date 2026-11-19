"""The 13 key-system commands removed in 1.0 are stubs: a notice, a non-zero
exit, and no key, registry, keyring or file operation."""

from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from memforge.cli import _dispatch, _removed

REMOVED_MODULES = {
    "registry",
    "revocation",
    "crypto",
    "identity",
    "agent_session",
    "sender_sequence",
    "recovery",
    "_security",
}
REPO_ROOT = Path(__file__).resolve().parents[1]


def _snapshot(*roots: Path) -> dict[str, str]:
    snap: dict[str, str] = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            key = f"{root.name}/{path.relative_to(root)}"
            snap[key] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
    return snap


@pytest.fixture
def store_and_keyring(tmp_path, monkeypatch):
    store = tmp_path / "store"
    (store / ".memforge").mkdir(parents=True)
    (store / "note.md").write_text("---\nname: n\n---\nbody\n", encoding="utf-8")
    (store / ".memforge" / "operator-registry.yaml").write_text("legacy: registry\n", encoding="utf-8")
    home = tmp_path / "home"
    (home / ".memforge" / "keys").mkdir(parents=True)
    (home / ".memforge" / "keys" / "operator.key").write_bytes(b"not a real key")
    (home / ".gnupg").mkdir()
    (home / ".gnupg" / "pubring.kbx").write_bytes(b"keyring bytes")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(store)
    return store, home


def test_thirteen_commands_listed():
    assert len(_removed.REMOVED_COMMANDS) == 13
    assert len(set(_removed.REMOVED_COMMANDS)) == 13


@pytest.mark.parametrize("name", _removed.REMOVED_COMMANDS)
@pytest.mark.parametrize("extra", [[], ["--force", "x"], ["add", "--memory-root", "."]])
def test_stub_prints_notice_exits_nonzero_and_changes_nothing(name, extra, store_and_keyring, capsys):
    store, home = store_and_keyring
    before = _snapshot(store, home)

    rc = _dispatch.main([name, *extra])

    err = capsys.readouterr().err
    assert rc != 0
    assert rc == _removed.EXIT_REMOVED
    assert f"memforge {name}: removed in 1.0" in err
    assert "docs/migrating-to-1.0.md" in err
    assert "[1.0.0]" in err and "CHANGELOG.md" in err
    assert _snapshot(store, home) == before


def test_stub_runs_as_a_real_process(store_and_keyring):
    store, home = store_and_keyring
    before = _snapshot(store, home)

    out = subprocess.run(
        [sys.executable, "-m", "memforge.cli._dispatch", "rotate-key", "--anything"],
        capture_output=True,
        text=True,
        cwd=store,
        timeout=60,
    )

    assert out.returncode == _removed.EXIT_REMOVED
    assert "removed in 1.0" in out.stderr
    assert _snapshot(store, home) == before


def test_help_lists_kept_and_removed_commands(capsys):
    with pytest.raises(SystemExit) as exc:
        _dispatch.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "init-store" in out
    for name in _removed.REMOVED_COMMANDS:
        assert name in out


def test_removed_modules_are_gone():
    src = REPO_ROOT / "src" / "memforge"
    for name in REMOVED_MODULES:
        assert not (src / f"{name}.py").exists(), name
    assert not list((src / "cli" / "v05").glob("*.py"))


def _python_files() -> list[Path]:
    files = []
    for top in ("src", "tests", "tools", "adapters"):
        base = REPO_ROOT / top
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix == ".py" or (path.suffix == "" and path.read_bytes()[:2] == b"#!"):
                files.append(path)
    return files


def _imported_names(tree: ast.AST) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = ("." * node.level) + (node.module or "")
            names.append(base)
            names += [f"{base}.{a.name}" for a in node.names]
    return names


def test_no_kept_module_imports_a_removed_module():
    offenders = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue  # a non-Python script with a shebang
        for name in _imported_names(tree):
            parts = name.lstrip(".").split(".")
            if parts[0] == "memforge" and len(parts) > 1 and parts[1] in REMOVED_MODULES:
                offenders.append((str(path), name))
            elif parts[:3] == ["memforge", "cli", "v05"]:
                offenders.append((str(path), name))
            elif name.startswith(".") and parts[0] in REMOVED_MODULES:
                offenders.append((str(path), name))
    assert offenders == []

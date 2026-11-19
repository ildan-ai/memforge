"""Shared builders for the `memforge sync` tests: real git repositories,
real ssh keys, signed commits, and a stub gitsign."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from memforge import sync as sync_mod

ALL_MODES = frozenset({"ssh", "gpg", "keyless"})
LIST_NAMES = {
    "ssh": "allowed_signers",
    "gpg": "allowed_gpg_fingerprints",
    "keyless": "allowed_identities",
}

needs_ssh = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None or shutil.which("git") is None,
    reason="needs git and ssh-keygen",
)

# A stand-in tool here is an sh script, which Windows cannot execute as a program.
needs_sh = pytest.mark.skipif(os.name == "nt", reason="fakes a tool with an sh script")


def git(cwd: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


@dataclass
class Key:
    kind: str                 # "ssh" or "gpg"
    ident: str                # ssh: private key path; gpg: fingerprint
    public: str = ""          # ssh: the public key line
    label: str = ""

    def signers_line(self, label: str | None = None) -> str:
        return f'{label or self.label} namespaces="git" {self.public}'

    def sign_config(self) -> list[str]:
        if self.kind == "ssh":
            return ["-c", "gpg.format=ssh", "-c", f"user.signingkey={self.ident}"]
        return ["-c", "gpg.format=openpgp", "-c", f"user.signingkey={self.ident}"]


def make_ssh_key(directory: Path, label: str) -> Key:
    path = directory / f"key-{label}"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", label, "-f", str(path)],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True)
    pub = (directory / f"key-{label}.pub").read_text().strip()
    return Key("ssh", str(path), pub, label)


def signers_text(*keys: Key) -> str:
    return "".join(key.signers_line() + "\n" for key in keys)


class Lab:
    """An origin, a writer clone and helpers to add commits and receivers."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.origin = root / "origin.git"
        self.writer = root / "writer"
        git(root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(root, "clone", "-q", str(self.origin), str(self.writer), check=False)
        git(self.writer, "checkout", "-q", "-B", "main")
        self.n = 0

    def commit(self, files: dict[str, str | None], key: Key | None = None,
               message: str | None = None, repo: Path | None = None, date: str | None = None) -> str:
        repo = repo or self.writer
        self.n += 1
        for name, text in files.items():
            path = repo / name
            if text is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode())     # the same bytes on every platform
        git(repo, "add", "-A")
        env = dict(os.environ)
        if date:
            env["GIT_COMMITTER_DATE"] = date
            env["GIT_AUTHOR_DATE"] = date
        sign = ([*key.sign_config(), "-c", "commit.gpgsign=true"] if key
                else ["-c", "commit.gpgsign=false"])
        proc = subprocess.run(
            ["git", *sign, "commit", "-q", "--allow-empty", "-m", message or f"commit {self.n}"],
            cwd=repo, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        assert proc.returncode == 0, proc.stderr
        return git(repo, "rev-parse", "HEAD")

    def push(self, force: bool = False) -> None:
        args = ["push", "-q", "-u", "origin", "main"]
        if force:
            args.insert(1, "--force")
        git(self.writer, *args)

    def start(self, mode_list: str = "ssh", text: str = "") -> str:
        """The base commit: an unsigned commit carrying the list. Pushed; returns its id."""
        name = f".memforge/{LIST_NAMES[mode_list]}"
        base = self.commit({name: text, "seed.md": "seed\n"})
        self.push()
        return base

    def receiver(self, name: str = "recv", *extra: str) -> Path:
        path = self.root / name
        git(self.root, "clone", "-q", *extra, str(self.origin), str(path))
        return path

    def tamper(self) -> str:
        """Rewrite the message of HEAD without touching its signature."""
        raw = subprocess.run(["git", "cat-file", "commit", "HEAD"], cwd=self.writer,
                             capture_output=True, check=True).stdout
        new = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=self.writer,
                             input=raw.replace(b"\n\ncommit", b"\n\ntampered commit"),
                             capture_output=True, check=True).stdout.decode().strip()
        git(self.writer, "reset", "-q", "--hard", new)
        return new


def run_sync(path: Path, **kwargs) -> int:
    return sync_mod.run_sync(path, **kwargs)


def verified_head(path: Path) -> str:
    return git(path, "config", "--local", "--get", "memforge.verifiedHead", check=False)


@pytest.fixture
def lab(tmp_path, monkeypatch) -> Lab:
    """A hermetic git environment: no personal, system or signing config."""
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("")
    for name, value in {
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_CONFIG_SYSTEM": str(empty),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "author@example.com",
        "GIT_COMMITTER_NAME": "Test Author",
        "GIT_COMMITTER_EMAIL": "author@example.com",
    }.items():
        monkeypatch.setenv(name, value)
    return Lab(tmp_path / "lab")


@dataclass
class StubGitsign:
    directory: Path
    log: Path
    passes: Path
    calls: list[str] = field(default_factory=list)

    def allow(self, commit: str, identity: str) -> None:
        with self.passes.open("a") as handle:
            handle.write(f"{commit} {identity}\n")

    def logged(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


def install_stub(tmp_path: Path, monkeypatch, body: str | None = None) -> StubGitsign:
    """A gitsign stand-in first on PATH: records its arguments, passes for listed pairs."""
    directory = tmp_path / "stubbin"
    directory.mkdir()
    log, passes = directory / "calls.log", directory / "passes.txt"
    passes.write_text("")
    script = body or f"""#!/bin/sh
[ "$1" = "--version" ] && {{ echo "gitsign stub"; exit 0; }}
echo "$*" >> "{log}"
[ "$1" = "verify" ] || {{ echo "unknown subcommand $1" >&2; exit 2; }}
id=""
for a in "$@"; do
  case "$a" in --certificate-identity=*) id="${{a#*=}}" ;; esac
  last="$a"
done
grep -qx "$last $id" "{passes}"
"""
    stub = directory / "gitsign"
    stub.write_text(script)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ.get('PATH', '')}")
    return StubGitsign(directory, log, passes)

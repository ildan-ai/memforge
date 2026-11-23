"""Process, tool and isolation plumbing for `memforge sync`.

Every external program runs from an argument list, never through a shell,
with a timeout and stdin closed. Verification runs in a throwaway bare git
directory that has no config of its own and reads the store's objects through
`objects/info/alternates`, so repo-local, global and system git config cannot
change a verdict.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# find_program is imported from here by other modules.
from memforge._programs import ProgramInFolder, find_program, find_program_outside, git_env  # noqa: F401

TIMEOUT_SECONDS = 120


class SyncError(Exception):
    """A failure that exits 1; `reason` is the name printed to the operator."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class Refused(Exception):
    """A refusal by verification, history or shallow rules; exits 2."""

    def __init__(self, reason: str, detail: str = "", commit: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.commit = commit


class Fail(Exception):
    """A per-commit verdict failure; the caller attaches the commit id."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Result:
    rc: int
    out: bytes
    err: str


def run(argv: list[str], *, env: dict[str, str], cwd: str | None = None,
        timeout: int = TIMEOUT_SECONDS) -> Result:
    try:
        proc = subprocess.run(
            argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=timeout, check=False,
        )
    except FileNotFoundError as exc:
        return Result(127, b"", str(exc))
    except subprocess.TimeoutExpired:
        return Result(124, b"", f"timed out after {timeout}s: {argv[0]}")
    except OSError as exc:
        return Result(126, b"", str(exc))
    return Result(proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace"))


@dataclass(frozen=True)
class Tool:
    name: str
    path: str | None
    version: str
    sha256: str

    @property
    def arg(self) -> str:
        """What goes into a `-c` pin: the resolved path, else the bare name so
        git reports the failure itself."""
        return self.path or self.name


def _plain_env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C"}


def _first_line(result: Result) -> str:
    text = result.out.decode("utf-8", "replace") or result.err
    lines = text.strip().splitlines()
    return lines[0].strip() if lines else "unknown"


def _version(name: str, path: str) -> str:
    env = _plain_env()
    if name == "ssh-keygen":
        # ssh-keygen has no version flag; the sibling ssh from the same install reports it.
        sibling = Path(path).with_name("ssh" + Path(path).suffix)
        if os.path.isfile(sibling):
            # OpenSSH for Windows exits silently without PROGRAMDATA; an empty folder is enough.
            with tempfile.TemporaryDirectory(prefix="memforge-sync-") as empty:
                env["PROGRAMDATA"] = empty
                return "sibling ssh: " + _first_line(run([str(sibling), "-V"], env=env, timeout=10))
        return "unknown"
    return _first_line(run([path, "--version"], env=env, timeout=10))


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_tools(store: Path) -> dict[str, Tool]:
    """Resolve each program once; these paths are the ones used and printed.

    A program inside the store is refused before it runs: anyone who can
    commit could have replaced it.
    """
    tools: dict[str, Tool] = {}
    for name in ("git", "ssh-keygen", "gpg", "gitsign"):
        try:
            path = find_program_outside(name, store)
        except ProgramInFolder as exc:
            raise SyncError("tool_in_store", f"{name} resolves to {exc.path}, inside the store") from None
        if path is None:
            tools[name] = Tool(name, None, "not found", "-")
            continue
        tools[name] = Tool(name, path, _version(name, path), _sha256(path))
    return tools


def tool_line(tool: Tool) -> str:
    return f"verifier {tool.name}: path={tool.path or 'not found'} version={tool.version} sha256={tool.sha256}"


class Store:
    """The store's own repository, driven with the receiver's normal environment
    minus the variables that would point git at another repository."""

    def __init__(self, git: str, root: Path) -> None:
        self.git_path = git
        self.root = root

    def git(self, *args: str) -> Result:
        return run([self.git_path, *args], env=git_env(), cwd=str(self.root))

    def must(self, *args: str) -> str:
        result = self.git(*args)
        if result.rc != 0:
            raise SyncError("git_failed", f"git {' '.join(args)}: {result.err.strip()}")
        return result.out.decode("utf-8", "replace").strip()


class Isolation:
    """A throwaway config-free bare git directory, removed on every exit path."""

    def __init__(self, git: str, objects_dir: str, shallow_file: str | None,
                 extra_env: dict[str, str] | None = None) -> None:
        self._git = git
        self._objects = objects_dir
        self._shallow = shallow_file
        self._extra = extra_env or {}
        self.root = ""
        self.gitdir = ""
        self._counter = 0

    def __enter__(self) -> "Isolation":
        self.root = tempfile.mkdtemp(prefix="memforge-sync-")
        try:
            root = Path(self.root)
            for sub in ("home", "tmp", "programdata"):
                (root / sub).mkdir()
            (root / "empty.conf").write_text("", encoding="utf-8")
            self.gitdir = str(root / "git")
            init = run([self._git, "init", "--bare", "-q", "--template=", self.gitdir],
                       env=self.env(), cwd=self.root)
            if init.rc != 0:
                raise SyncError("isolation_failed", init.err.strip())
            info = Path(self.gitdir) / "objects" / "info"
            info.mkdir(parents=True, exist_ok=True)
            # Bytes, not text: git reads a carriage return as part of the path.
            (info / "alternates").write_bytes((self._objects + "\n").encode("utf-8"))
            if self._shallow:
                shutil.copyfile(self._shallow, Path(self.gitdir) / "shallow")
        except BaseException:
            shutil.rmtree(self.root, ignore_errors=True)
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        root = Path(self.root)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(root / "home"),
            "TMPDIR": str(root / "tmp"),
            "LC_ALL": "C",
            "GIT_CONFIG_GLOBAL": str(root / "empty.conf"),
            "GIT_CONFIG_SYSTEM": str(root / "empty.conf"),
            "GIT_CONFIG_NOSYSTEM": "1",
            # OpenSSH for Windows exits silently without PROGRAMDATA. An empty
            # folder of our own satisfies it and keeps the machine's ssh config out.
            "PROGRAMDATA": str(root / "programdata"),
        }
        env.update(self._extra)
        env.update(extra or {})
        return env

    def git(self, *args: str, config: tuple[tuple[str, str], ...] = (),
            extra_env: dict[str, str] | None = None) -> Result:
        pins: list[str] = []
        for key, value in config:
            pins += ["-c", f"{key}={value}"]
        return run([self._git, f"--git-dir={self.gitdir}", *pins, *args],
                   env=self.env(extra_env), cwd=self.root)

    def write_temp(self, data: bytes) -> str:
        self._counter += 1
        path = Path(self.root) / "tmp" / f"list-{self._counter}"
        path.write_bytes(data)
        return str(path)

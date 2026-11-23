"""memory-dlp-scan must account for every input it is given.

A file the scanner cannot handle (undecodable bytes, unreadable, a symbolic
link, a name that looks like an option) is reported. It is never dropped from
the scan without a line of output, because a dropped file reads as "clean".
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from memforge.cli.dlp_scan import main, run_detect_secrets, scan_file

# Fixture credential shapes; none is a live secret.
_AWS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
_SECRET_LINE = f"aws key id {_AWS_KEY_ID}\n"


def _run(monkeypatch, capsys, *argv: str) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.argv", ["memory-dlp-scan", *argv])
    rc = main()
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


@pytest.fixture
def scratch_home(tmp_path: Path, monkeypatch) -> Path:
    """Keep config discovery and git away from the real home folder."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return home


# ---------- undecodable and unreadable files ----------


def test_invalid_utf8_byte_does_not_hide_a_secret(tmp_path, monkeypatch, capsys, scratch_home):
    target = tmp_path / "note.md"
    target.write_bytes(_SECRET_LINE.encode("utf-8") + b"\xff\n")

    rc, out, _ = _run(monkeypatch, capsys, "--paths", str(target), "--strict", "--no-detect-secrets")

    assert rc == 1
    assert "[aws_access_key_id]" in out
    assert "[invalid_utf8]" in out


def test_invalid_utf8_file_without_secret_is_reported(tmp_path, monkeypatch, capsys, scratch_home):
    target = tmp_path / "note.md"
    target.write_bytes(b"plain text\xff\n")

    rc, out, _ = _run(monkeypatch, capsys, "--paths", str(target), "--no-detect-secrets")

    assert rc == 0
    assert "[invalid_utf8]" in out
    assert "clean" not in out


def test_scan_file_reports_invalid_utf8_and_still_scans(tmp_path):
    target = tmp_path / "note.md"
    target.write_bytes(_SECRET_LINE.encode("utf-8") + b"\xff\n")

    patterns = {f.pattern for f in scan_file(target)}

    assert {"aws_access_key_id", "invalid_utf8"} <= patterns


# UTF-16 without a byte-order mark is valid UTF-8 (each ASCII letter is followed
# or preceded by a NUL), so it decodes without complaint and matches nothing.
_GENERIC_SECRET_LINE = "password=hunter2supersecret\n"


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
@pytest.mark.parametrize(
    ("line", "pattern", "gate"),
    [
        (_SECRET_LINE, "aws_access_key_id", "--strict"),
        (_GENERIC_SECRET_LINE, "generic_password_assignment", "--strict-major"),
    ],
)
def test_utf16_without_a_byte_order_mark_does_not_hide_a_secret(
    tmp_path, monkeypatch, capsys, scratch_home, encoding, line, pattern, gate
):
    target = tmp_path / "note.md"
    target.write_bytes(f"plain\n{line}".encode(encoding))

    rc, out, _ = _run(monkeypatch, capsys, "--paths", str(target), gate, "--no-detect-secrets")

    assert f"{target}:2  [{pattern}]" in out
    assert "[nul_bytes]" in out
    assert rc == 1


def test_utf16_with_a_byte_order_mark_does_not_hide_a_secret(tmp_path):
    target = tmp_path / "note.md"
    target.write_bytes(_SECRET_LINE.encode("utf-16"))

    patterns = {f.pattern for f in scan_file(target)}

    assert {"aws_access_key_id", "nul_bytes"} <= patterns


def test_nul_free_file_gets_no_nul_finding(tmp_path):
    target = tmp_path / "note.md"
    target.write_text(_SECRET_LINE, encoding="utf-8")

    patterns = [f.pattern for f in scan_file(target)]

    assert patterns == ["aws_access_key_id"]


def test_missing_path_fails_strict(tmp_path, monkeypatch, capsys, scratch_home):
    missing = tmp_path / "typo.md"

    rc, out, _ = _run(monkeypatch, capsys, "--paths", str(missing), "--strict", "--no-detect-secrets")

    assert rc == 1
    assert "[unreadable_file]" in out
    assert str(missing) in out


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permission bits")
def test_unreadable_file_fails_strict_and_other_files_are_still_scanned(
    tmp_path, monkeypatch, capsys, scratch_home
):
    locked = tmp_path / "locked.md"
    locked.write_text("anything\n", encoding="utf-8")
    locked.chmod(0o000)
    other = tmp_path / "other.md"
    other.write_text(_SECRET_LINE, encoding="utf-8")

    try:
        rc, out, _ = _run(
            monkeypatch, capsys, "--paths", str(locked), str(other), "--strict", "--no-detect-secrets"
        )
    finally:
        locked.chmod(0o600)

    assert rc == 1
    assert "[unreadable_file]" in out
    assert "[aws_access_key_id]" in out


# ---------- symbolic links ----------


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_symlink_is_reported_and_its_target_is_not_read(tmp_path, monkeypatch, capsys, scratch_home):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text(_SECRET_LINE, encoding="utf-8")
    folder = tmp_path / "memory"
    folder.mkdir()
    link = folder / "link.md"
    link.symlink_to(outside / "private.txt")

    rc, out, _ = _run(monkeypatch, capsys, "--paths", str(link), "--strict", "--no-detect-secrets")

    assert "[symlink_not_scanned]" in out
    assert "[aws_access_key_id]" not in out
    assert rc == 0


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_memory_folder_walk_reports_symlinks_without_reading_them(
    tmp_path, monkeypatch, capsys, scratch_home
):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.md").write_text(_SECRET_LINE, encoding="utf-8")
    folder = tmp_path / "memory"
    folder.mkdir()
    (folder / "file-link.md").symlink_to(outside / "private.md")
    (folder / "dir-link").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("MEMFORGE_MEMORY_PATH", str(folder))

    rc, out, _ = _run(monkeypatch, capsys, "--memory-folders", "--strict-major", "--no-detect-secrets")

    assert "[aws_access_key_id]" not in out
    assert "file-link.md" in out
    assert "[symlink_not_scanned]" in out
    assert rc == 1


# ---------- file names handed to detect-secrets ----------


def _stub_detect_secrets(tmp_path: Path, monkeypatch) -> Path:
    """Put a stand-in `detect-secrets` on PATH that records its arguments."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "detect-secrets-argv.json"
    stub = bin_dir / "detect-secrets"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"json.dump(sys.argv[1:], open({str(record)!r}, 'w'))\n"
        "print(json.dumps({'results': {}}))\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return record


@pytest.mark.skipif(os.name == "nt", reason="stub executable is a POSIX script")
def test_dash_leading_file_name_cannot_be_read_as_an_option(tmp_path, monkeypatch, scratch_home):
    record = _stub_detect_secrets(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    (work / "--plugin=evil.py").write_text("x = 1\n", encoding="utf-8")
    (work / "plain.md").write_text("plain\n", encoding="utf-8")
    monkeypatch.chdir(work)

    run_detect_secrets([Path("--plugin=evil.py"), Path("plain.md")])

    argv = json.loads(record.read_text(encoding="utf-8"))
    separator = argv.index("--")
    assert argv[:separator] == ["scan", "--all-files"]
    assert argv[separator + 1:] == ["--plugin=evil.py", "plain.md"]


# ---------- staged content ----------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch, scratch_home) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    monkeypatch.chdir(path)
    return path


def test_staged_scan_reads_the_staged_content_not_the_working_tree(repo, monkeypatch, capsys):
    target = repo / "note.md"
    target.write_text(_SECRET_LINE, encoding="utf-8")
    _git(repo, "add", "note.md")
    target.write_text("nothing to see\n", encoding="utf-8")

    rc, out, _ = _run(monkeypatch, capsys, "--staged", "--strict", "--no-detect-secrets")

    assert rc == 1
    assert "[aws_access_key_id]" in out


def test_staged_scan_covers_a_file_removed_from_the_working_tree(repo, monkeypatch, capsys):
    target = repo / "note.md"
    target.write_text(_SECRET_LINE, encoding="utf-8")
    _git(repo, "add", "note.md")
    target.unlink()

    rc, out, _ = _run(monkeypatch, capsys, "--staged", "--strict", "--no-detect-secrets")

    assert rc == 1
    assert "[aws_access_key_id]" in out


@pytest.mark.skipif(os.name == "nt", reason="file name is not valid on Windows")
@pytest.mark.parametrize("name", ["new\nline.md", "café \"quoted\".md"])
def test_staged_scan_covers_names_git_would_quote(repo, monkeypatch, capsys, name):
    (repo / name).write_text(_SECRET_LINE, encoding="utf-8")
    _git(repo, "add", "--", name)

    rc, out, _ = _run(monkeypatch, capsys, "--staged", "--strict", "--no-detect-secrets")

    assert rc == 1
    assert "[aws_access_key_id]" in out


def test_staged_scan_from_a_subdirectory_still_finds_the_file(repo, monkeypatch, capsys):
    (repo / "sub").mkdir()
    (repo / "note.md").write_text(_SECRET_LINE, encoding="utf-8")
    _git(repo, "add", "note.md")
    monkeypatch.chdir(repo / "sub")

    rc, out, _ = _run(monkeypatch, capsys, "--staged", "--strict", "--no-detect-secrets")

    assert rc == 1
    assert "[aws_access_key_id]" in out


@pytest.mark.skipif(os.name == "nt", reason="stub executable is a POSIX script")
def test_staged_scan_hands_detect_secrets_the_staged_content(repo, tmp_path, monkeypatch, capsys):
    record = _stub_detect_secrets(tmp_path, monkeypatch)
    seen = tmp_path / "detect-secrets-saw.txt"
    stub = tmp_path / "bin" / "detect-secrets"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"json.dump(sys.argv[1:], open({str(record)!r}, 'w'))\n"
        "path = sys.argv[-1]\n"
        f"open({str(seen)!r}, 'w').write(open(path).read())\n"
        "print(json.dumps({'results': {path: [{'type': 'Stub', 'line_number': 1}]}}))\n",
        encoding="utf-8",
    )
    target = repo / "note.md"
    target.write_text("staged content\n", encoding="utf-8")
    _git(repo, "add", "note.md")
    target.write_text("working tree content\n", encoding="utf-8")

    rc, out, _ = _run(monkeypatch, capsys, "--staged", "--strict")

    assert seen.read_text(encoding="utf-8") == "staged content\n"
    assert "note.md:1  [detect-secrets:Stub]" in out
    assert rc == 1


def test_staged_scan_outside_a_repository_is_an_error(tmp_path, monkeypatch, capsys, scratch_home):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    rc, _, err = _run(monkeypatch, capsys, "--staged", "--strict")

    assert rc == 2
    assert "staged" in err


# ---------- a path swapped for a link while it is being read ----------


def _swap_for_link_before_open(monkeypatch, path: Path, target: Path) -> None:
    """Replace `path` with a link to `target` just before it is opened, the
    latest point a concurrent writer can swap it and still change the read."""
    real_open = os.open
    swapped: list[bool] = []

    def swapping_open(opened, *args, **kwargs):
        if not swapped and os.fspath(opened) == os.fspath(path):
            swapped.append(True)
            os.unlink(path)
            os.symlink(target, path)
        return real_open(opened, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
@pytest.mark.parametrize("platform_has_no_follow", [True, False])
def test_file_swapped_for_a_link_after_the_check_is_not_read(
    tmp_path, monkeypatch, capsys, scratch_home, platform_has_no_follow
):
    outside = tmp_path / "outside.txt"
    outside.write_text(_SECRET_LINE, encoding="utf-8")
    target = tmp_path / "note.md"
    target.write_text("plain\n", encoding="utf-8")
    if not platform_has_no_follow:
        monkeypatch.delattr(os, "O_NOFOLLOW")
    _swap_for_link_before_open(monkeypatch, target, outside)

    _, out, _ = _run(monkeypatch, capsys, "--paths", str(target), "--no-detect-secrets")

    assert target.is_symlink()
    assert "[aws_access_key_id]" not in out
    assert "[symlink_not_scanned]" in out


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_named_pipe_is_an_unreadable_blocker_not_a_symlink(tmp_path, monkeypatch, capsys, scratch_home):
    # A refusal that is not a link must keep the BLOCKER: labelling it a
    # symlink would drop it to MAJOR and let a plain --strict run pass.
    pipe = tmp_path / "note.md"
    os.mkfifo(pipe)

    rc, out, _ = _run(monkeypatch, capsys, "--paths", str(pipe), "--strict", "--no-detect-secrets")

    assert "[unreadable_file]" in out
    assert "[symlink_not_scanned]" not in out
    assert rc == 1


# ---------- a folder the walk cannot list ----------


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permission bits")
def test_memory_folder_walk_reports_a_directory_it_cannot_list(
    tmp_path, monkeypatch, capsys, scratch_home
):
    folder = tmp_path / "memory"
    locked = folder / "locked"
    locked.mkdir(parents=True)
    (locked / "hidden.md").write_text(_SECRET_LINE, encoding="utf-8")
    locked.chmod(0o000)
    monkeypatch.setenv("MEMFORGE_MEMORY_PATH", str(folder))

    try:
        rc, out, _ = _run(monkeypatch, capsys, "--memory-folders", "--strict", "--no-detect-secrets")
    finally:
        locked.chmod(0o700)

    assert f"{locked}:0  [unreadable_file]" in out
    assert rc == 1


# ---------- the deep layer failing must not hide the other findings ----------


def test_staged_scan_keeps_its_findings_when_the_temporary_folder_cannot_be_made(
    repo, tmp_path, monkeypatch, capsys
):
    if os.name != "nt":
        _stub_detect_secrets(tmp_path, monkeypatch)
    else:
        # Only detect-secrets is made to look installed; git must still resolve.
        real_which = shutil.which
        monkeypatch.setattr(
            "shutil.which",
            lambda name, *a, **k: name if "detect-secrets" in name else real_which(name, *a, **k),
        )
    (repo / "note.md").write_text(_SECRET_LINE, encoding="utf-8")
    _git(repo, "add", "note.md")

    def no_temporary_folder(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr("tempfile.TemporaryDirectory", no_temporary_folder)

    rc, out, err = _run(monkeypatch, capsys, "--staged", "--strict")

    assert rc == 1
    assert "[aws_access_key_id]" in out
    assert "[deep_scan_failed]" in out
    assert "deep DLP layer skipped" in err


@pytest.mark.skipif(os.name == "nt", reason="stub executable is a POSIX script")
def test_staged_scan_keeps_its_findings_when_detect_secrets_fails(
    repo, tmp_path, monkeypatch, capsys
):
    _stub_detect_secrets(tmp_path, monkeypatch)
    (tmp_path / "bin" / "detect-secrets").write_text(
        f"#!{sys.executable}\nimport sys\nsys.exit(3)\n", encoding="utf-8"
    )
    (repo / "note.md").write_text(_SECRET_LINE, encoding="utf-8")
    _git(repo, "add", "note.md")

    rc, out, err = _run(monkeypatch, capsys, "--staged", "--strict")

    assert rc == 1
    assert "[aws_access_key_id]" in out
    assert "[deep_scan_failed]" in out
    assert "deep DLP layer skipped" in err


# ---------- --strict when the deep layer was asked for and failed ----------
#
# The deep layer is there for what the regex floor misses, so a run that asked
# for it and did not get it cannot pass --strict on a file the floor finds clean.


_FAILING_STUBS = {
    "cannot start": "#!/nonexistent/interpreter\n",
    "exits with no output": f"#!{sys.executable}\nimport sys\nsys.exit(3)\n",
    "prints something that is not JSON": f"#!{sys.executable}\nprint('not json')\n",
    "prints JSON that is not a report": f"#!{sys.executable}\nprint('[]')\n",
}

# A report that parses as JSON but has the wrong shape inside, the way a
# different detect-secrets version or a wrapper could print it.
_MALFORMED_REPORTS = {
    "has no results": {},
    "has results that are a list": {"results": []},
    "has results that are text": {"results": "none"},
    "has a file entry that is not a list": {"results": {"clean.md": {"type": "Stub"}}},
    "has an entry that is not an object": {"results": {"a": [1]}},
    "has a line number that is text": {"results": {"clean.md": [{"type": "Stub", "line_number": "one"}]}},
    "has a line number that is a list": {"results": {"clean.md": [{"type": "Stub", "line_number": [1]}]}},
    "has a line number that is a fraction": {"results": {"clean.md": [{"type": "Stub", "line_number": 1.5}]}},
    "has a type that is not text": {"results": {"clean.md": [{"type": {"a": 1}, "line_number": 1}]}},
}
_FAILING_STUBS.update(
    (f"prints a report that {shape}", f"#!{sys.executable}\nprint({json.dumps(report)!r})\n")
    for shape, report in _MALFORMED_REPORTS.items()
)


@pytest.mark.skipif(os.name == "nt", reason="stub executable is a POSIX script")
@pytest.mark.parametrize("failure", sorted(_FAILING_STUBS))
def test_strict_fails_when_detect_secrets_fails(
    tmp_path, monkeypatch, capsys, scratch_home, failure
):
    _stub_detect_secrets(tmp_path, monkeypatch)
    (tmp_path / "bin" / "detect-secrets").write_text(_FAILING_STUBS[failure], encoding="utf-8")
    clean = tmp_path / "work" / "clean.md"
    clean.parent.mkdir()
    clean.write_text("plain\n", encoding="utf-8")
    secret = tmp_path / "work" / "secret.md"
    secret.write_text(_SECRET_LINE, encoding="utf-8")

    rc, out, err = _run(monkeypatch, capsys, "--paths", str(clean), "--strict")

    assert rc == 1
    assert f"{clean.parent}:0  [deep_scan_failed]" in out
    assert "deep DLP layer skipped" in err

    rc, out, _ = _run(monkeypatch, capsys, "--paths", str(clean), str(secret), "--strict")

    assert rc == 1
    assert out.count("[deep_scan_failed]") == 1
    assert "[aws_access_key_id]" in out


@pytest.mark.skipif(os.name == "nt", reason="stub executable is a POSIX script")
def test_strict_fails_when_detect_secrets_times_out(tmp_path, monkeypatch, capsys, scratch_home):
    _stub_detect_secrets(tmp_path, monkeypatch)
    clean = tmp_path / "clean.md"
    clean.write_text("plain\n", encoding="utf-8")

    def time_out(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", time_out)

    rc, out, err = _run(monkeypatch, capsys, "--paths", str(clean), "--strict")

    assert rc == 1
    assert "[deep_scan_failed]" in out
    assert "timed out" in out
    assert "deep DLP layer skipped" in err


def test_detect_secrets_not_installed_stays_a_warning(tmp_path, monkeypatch, capsys, scratch_home):
    monkeypatch.setattr("shutil.which", lambda name: None)
    clean = tmp_path / "clean.md"
    clean.write_text("plain\n", encoding="utf-8")

    rc, out, err = _run(monkeypatch, capsys, "--paths", str(clean), "--strict")

    assert rc == 0
    assert "DLP: clean (0 findings)" in out
    assert "detect-secrets not on PATH" in err


@pytest.mark.skipif(os.name == "nt", reason="needs a symlink in the index")
def test_staged_scan_covers_a_link_replaced_by_a_file(repo, monkeypatch, capsys):
    # git reports a link that became a regular file as a type change (T), not
    # a modification, so a filter that leaves T out never reads the new file.
    (repo / "note.md").symlink_to("elsewhere.md")
    _git(repo, "add", "note.md")
    _git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "link")
    (repo / "note.md").unlink()
    (repo / "note.md").write_text(_SECRET_LINE, encoding="utf-8")
    _git(repo, "add", "note.md")

    rc, out, _ = _run(monkeypatch, capsys, "--staged", "--strict", "--no-detect-secrets")

    assert rc == 1
    assert "[aws_access_key_id]" in out

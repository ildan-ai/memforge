"""Frontmatter YAML is bounded: an oversized or alias-expanding block is
unparseable frontmatter for that one file, reported quickly, and the tools
carry on with the rest of the folder.

The hostile parses run in a child process under a timeout, so a regression
fails the test instead of hanging the suite.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from memforge import frontmatter

PARSE_TIMEOUT_SECONDS = 20
# Two bounded loads of the slowest shape take about 0.7 s on an idle machine
# and an unbounded one never finishes, so this leaves room for a loaded
# machine without letting a regression through.
PARSE_BUDGET_SECONDS = 5.0

GOOD = (
    "---\n"
    "name: good\n"
    "description: a well-formed neighbour\n"
    "type: reference\n"
    "---\n\n"
    "Body.\n"
)


def _alias_bomb(levels: int = 12) -> str:
    """Frontmatter of a few hundred bytes whose aliases expand to 9**levels leaves."""
    lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x]"]
    for i in range(1, levels + 1):
        refs = ", ".join([f"*a{i - 1}"] * 9)
        lines.append(f"a{i}: &a{i} [{refs}]")
    lines.append(f"tags: *a{levels}")
    return "---\nname: bomb\n" + "\n".join(lines) + "\n---\n\nBody.\n"


def _self_reference() -> str:
    return "---\nname: loop\ntags: &t [*t]\n---\n\nBody.\n"


def _deep_nesting() -> str:
    return "---\nname: deep\ntags: " + "[" * 5000 + "]" * 5000 + "\n---\n\nBody.\n"


def _oversized() -> str:
    filler = "".join(f"k{i}: {'v' * 60}\n" for i in range(2000))
    return "---\nname: big\n" + filler + "---\n\nBody.\n"


HOSTILE = {
    "alias_bomb": _alias_bomb,
    "self_reference": _self_reference,
    "deep_nesting": _deep_nesting,
    "oversized": _oversized,
}

_CHILD = """
import sys, time
from pathlib import Path
from memforge import frontmatter

text = Path(sys.argv[1]).read_text(encoding="utf-8")
start = time.monotonic()
fm, body = frontmatter.parse(text)
ok, reason = frontmatter.validate_frontmatter(text)
elapsed = time.monotonic() - start
print(f"fm={fm!r} body_is_text={body == text} ok={ok} elapsed={elapsed:.3f}")
print(f"reason={reason}")
"""


@pytest.mark.parametrize("shape", sorted(HOSTILE))
def test_hostile_frontmatter_is_rejected_quickly(shape: str, tmp_path: Path):
    hostile = tmp_path / "hostile.md"
    hostile.write_text(HOSTILE[shape](), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(hostile)],
        capture_output=True, text=True, timeout=PARSE_TIMEOUT_SECONDS,
    )

    assert proc.returncode == 0, proc.stderr[-2000:]
    summary, reason = proc.stdout.splitlines()[:2]
    assert "fm={} body_is_text=True ok=False" in summary
    assert float(summary.rsplit("elapsed=", 1)[1]) < PARSE_BUDGET_SECONDS
    assert "limit" in reason
    assert "COLON" not in reason


def test_alias_bomb_is_small_enough_to_be_a_real_threat():
    assert len(_alias_bomb().encode("utf-8")) < 1024


def test_anchors_within_the_limits_still_parse():
    text = "---\nname: ok\nbase: &b [one, two]\ntags: *b\n---\nBody.\n"

    fm, body = frontmatter.parse(text)

    assert fm["tags"] == ["one", "two"]
    assert body == "Body.\n"
    assert frontmatter.validate_frontmatter(text) == (True, None)


@pytest.mark.parametrize("line", [
    "created: 2024-13-45",
    "updated: 2024-01-01 99:00:00",
    'priority: !!int "high"',
])
def test_value_the_yaml_loader_cannot_build_is_unparseable_not_a_crash(line: str):
    text = f"---\nname: typo\n{line}\n---\nBody.\n"

    fm, body = frontmatter.parse(text)
    ok, reason = frontmatter.validate_frontmatter(text)

    assert (fm, body) == ({}, text)
    assert not ok
    assert "invalid value" in reason


def test_block_at_the_size_limit_parses_and_one_byte_over_does_not():
    head = "name: edge\npad: "
    at_limit = head + "v" * (frontmatter.MAX_FRONTMATTER_BYTES - len(head))

    fm, _ = frontmatter.parse(f"---\n{at_limit}\n---\nBody.\n")
    over, _ = frontmatter.parse(f"---\n{at_limit}v\n---\nBody.\n")

    assert fm["name"] == "edge"
    assert over == {}


def test_node_count_at_the_limit_parses_and_one_over_does_not():
    # A mapping of one key whose value is a list: the list and its items are
    # the nodes besides the root mapping, the key and nothing else.
    def block(items: int) -> str:
        return "tags: [" + ", ".join(["x"] * items) + "]"

    at_limit = frontmatter.MAX_FRONTMATTER_NODES - 3

    fm, _ = frontmatter.parse(f"---\n{block(at_limit)}\n---\nBody.\n")
    over, _ = frontmatter.parse(f"---\n{block(at_limit + 1)}\n---\nBody.\n")

    assert len(fm["tags"]) == at_limit
    assert over == {}


@pytest.fixture
def folder_with_bomb(tmp_path: Path) -> Path:
    (tmp_path / "good.md").write_text(GOOD, encoding="utf-8")
    (tmp_path / "bomb.md").write_text(_alias_bomb(), encoding="utf-8")
    return tmp_path


def _run_in_child(code: str, folder: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code, str(folder)],
        capture_output=True, text=True, timeout=PARSE_TIMEOUT_SECONDS,
    )


def test_audit_reports_the_bomb_and_audits_the_neighbour(folder_with_bomb: Path):
    proc = _run_in_child(
        "import sys; from memforge.cli import audit; "
        "sys.exit(audit.main(['--path', sys.argv[1]]))",
        folder_with_bomb,
    )

    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "bomb.md: frontmatter YAML failed to parse" in proc.stdout
    assert "Files: 2" in proc.stdout


def test_validate_reports_the_bomb_and_passes_the_neighbour(folder_with_bomb: Path):
    proc = _run_in_child(
        "import sys; from memforge.cli import validate; "
        "sys.exit(validate.main(['--path', sys.argv[1], '--json']))",
        folder_with_bomb,
    )

    assert proc.returncode == 1, proc.stderr[-2000:]
    assert '"files_checked": 2' in proc.stdout
    assert '"errors": 1' in proc.stdout
    assert "limit" in proc.stdout


def test_index_gen_indexes_the_neighbour_despite_the_bomb(folder_with_bomb: Path):
    proc = _run_in_child(
        "import sys; from memforge.cli import index_gen; "
        "sys.exit(index_gen.process(__import__('pathlib').Path(sys.argv[1]), 'print'))",
        folder_with_bomb,
    )

    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "[good](good.md)" in proc.stdout
    assert "bomb" not in proc.stdout


def test_recall_build_survives_an_alias_bomb_in_the_synonym_override(tmp_path: Path):
    (tmp_path / "good.md").write_text(GOOD, encoding="utf-8")
    (tmp_path / ".memforge").mkdir()
    bomb_block = _alias_bomb().split("---\n")[1]
    (tmp_path / ".memforge" / "recall-synonyms.yaml").write_text(
        bomb_block.replace("tags:", "map:\n  deploy:"), encoding="utf-8"
    )

    proc = _run_in_child(
        "import sys; from memforge import recall; "
        "print(recall.build_index(__import__('pathlib').Path(sys.argv[1]))['counts']['entries'])",
        tmp_path,
    )

    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip() == "1"


def test_index_gen_survives_an_alias_bomb_in_a_snooze_file(tmp_path: Path):
    member = (
        "---\nname: {name}\ndescription: fixture\ntype: feedback\nuid: {uid}\n"
        "status: active\ndecision_topic: the-topic\n---\n\nBody for {name}.\n"
    )
    (tmp_path / "a.md").write_text(member.format(name="a", uid="mem-a"), encoding="utf-8")
    (tmp_path / "b.md").write_text(member.format(name="b", uid="mem-b"), encoding="utf-8")
    snoozes = tmp_path / ".memforge" / "snoozes"
    snoozes.mkdir(parents=True)
    bomb_block = _alias_bomb().split("---\n")[1]
    (snoozes / "the-topic.yaml").write_text(
        'snoozed_until: "9999-12-31"\n' + bomb_block.replace("tags:", "snooze_reason:"),
        encoding="utf-8",
    )

    proc = _run_in_child(
        "import sys; from memforge.cli import index_gen; "
        "sys.exit(index_gen.process(__import__('pathlib').Path(sys.argv[1]), 'print'))",
        tmp_path,
    )

    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "state: competing" in proc.stdout


def test_index_gen_survives_an_alias_bomb_in_its_config(tmp_path: Path):
    """The same loader bounds the config file index-gen reads beside the memories."""
    (tmp_path / "good.md").write_text(GOOD, encoding="utf-8")
    bomb_block = _alias_bomb().split("---\n")[1]
    (tmp_path / "_memforge.yaml").write_text(
        bomb_block.replace("tags:", "title:"), encoding="utf-8"
    )

    proc = _run_in_child(
        "import sys; from memforge.cli import index_gen; "
        "sys.exit(index_gen.process(__import__('pathlib').Path(sys.argv[1]), 'print'))",
        tmp_path,
    )

    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.startswith("# Memory Index\n")
    assert "_memforge.yaml" in proc.stderr

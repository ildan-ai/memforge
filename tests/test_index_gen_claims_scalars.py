"""The competing-claims block stays readable YAML whatever a member holds.

The block is the reader-side contract for conflicts, so every member field
must read back with `yaml.safe_load` as the text the member holds, and a
member field that is not text must not stop memory-index-gen for the store.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
import yaml

from memforge.cli import index_gen
from memforge.cli.index_gen import _yaml_escape, render_competing_claims_block


def _member(folder: Path, stem: str, fields: str, body: str = "Body line.") -> Path:
    path = folder / f"{stem}.md"
    path.write_text(
        f"---\nname: {stem}\ndescription: d\ntype: feedback\nstatus: active\n"
        f"decision_topic: db\n{fields}---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _members(block: str) -> list[dict]:
    inner = [line for line in block.split("\n") if not line.startswith("# memforge:competing-claims:")]
    parsed = yaml.safe_load("\n".join(inner))
    return parsed[0]["members"]


# Characters that change how YAML reads a plain scalar, and characters that
# YAML or Python treat as a line break or refuse in a stream.
_SIGNIFICANT = "-?:,[]{}#&*!|>'\"%@`<=.~ +0eE\\"
LS = "\N{LINE SEPARATOR}"
PS = "\N{PARAGRAPH SEPARATOR}"
NONCHAR = chr(0xFFFE)
_BREAKS_AND_UNPRINTABLE = [LS, PS, "\x85", "\x0b", "\x0c", "\x1c", NONCHAR, chr(0xFFFF)]
HOSTILE = sorted(
    {"".join(p) for n in (1, 2) for p in itertools.product(_SIGNIFICANT, repeat=n)}
    | {"<<", "=", ".5e+3", "._5", "1_000", "-.inf", f"a{LS}- uid: forged", f"x{PS}y"}
    | {f"a{c}b" for c in _BREAKS_AND_UNPRINTABLE}
    | set(_BREAKS_AND_UNPRINTABLE)
)


def test_every_escaped_text_reads_back_as_the_same_text():
    wrong = []
    for value in HOSTILE:
        emitted = _yaml_escape(value)
        try:
            parsed = yaml.safe_load(f"k: {emitted}")
        except yaml.YAMLError as exc:
            wrong.append((value, emitted, type(exc).__name__))
            continue
        if parsed != {"k": value} or len(f"k: {emitted}".splitlines()) != 1:
            wrong.append((value, emitted, parsed))
    assert wrong == []


@pytest.mark.parametrize("owner", ["<<", "=", f"a{LS}- uid: forged", NONCHAR, f"x{PS}y", ".5e+3"])
def test_block_parses_with_a_hostile_owner(tmp_path, owner):
    _member(tmp_path, "a", "uid: mem-a\nowner: alice\nupdated: '2026-05-08'\n")
    _member(tmp_path, "b", f"uid: mem-b\nowner: {json.dumps(owner)}\nupdated: '2026-05-09'\n")

    block = render_competing_claims_block(tmp_path)

    assert {m["uid"]: m["owner"] for m in _members(block)} == {"mem-a": "alice", "mem-b": owner}
    assert len([line for line in block.splitlines() if line.lstrip().startswith("- uid:")]) == 2


@pytest.mark.parametrize("first_line", ["=", "<<", ".5e+3", f"a{NONCHAR}b"])
def test_block_parses_with_a_hostile_first_line(tmp_path, first_line):
    _member(tmp_path, "a", "uid: mem-a\nowner: alice\nupdated: '2026-05-08'\n")
    _member(tmp_path, "b", "uid: mem-b\nowner: bob\nupdated: '2026-05-09'\n", body=first_line)

    block = render_competing_claims_block(tmp_path)

    assert {m["uid"]: m["first_line"] for m in _members(block)}["mem-b"] == first_line


@pytest.mark.parametrize("field, value", [
    ("updated", "[1]"),
    ("updated", "5"),
    ("updated", "{a: 1}"),
    ("updated", "true"),
    ("uid", "5"),
    ("uid", "[x]"),
])
def test_member_field_that_is_not_text_is_named_and_the_index_is_built(tmp_path, capsys, field, value):
    fields = {"uid": "mem-b", "owner": "bob", "updated": "'2026-05-09'"}
    fields[field] = value
    _member(tmp_path, "a", "uid: mem-a\nowner: alice\nupdated: '2026-05-08'\n")
    bad = _member(tmp_path, "b", "".join(f"{k}: {v}\n" for k, v in fields.items()))

    rc = index_gen.process(tmp_path, "write")

    assert rc == 0
    text = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    block = text[text.index("# memforge:competing-claims:begin"):text.index("# memforge:competing-claims:end")]
    assert len(_members(block)) == 2
    err = capsys.readouterr().err
    assert str(bad) in err and f"field '{field}' must be text" in err

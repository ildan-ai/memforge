"""Helpers shared by the terminal-output tests.

Windows refuses a file or folder name that holds a control character below
0x20, so a hostile name made of them cannot be created there. Its C1 range
(0x80 to 0x9f) is allowed in a name, and a terminal acts on those too, so the
Windows variant of a hostile name swaps each C0 character for the C1
character with the same low bits and a line break for NEL.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_C1_FOR_C0 = {chr(code): chr(0x80 + code) for code in range(0x20)}
_C1_FOR_C0["\n"] = "\x85"


def hostile_name(text: str) -> str:
    """The name as given, except on Windows, where C0 characters become C1 ones."""
    if os.name != "nt":
        return text
    return "".join(_C1_FOR_C0.get(char, char) for char in text)


def lf(stream: bytes) -> bytes:
    """Captured output with the line ends the platform adds taken back out.

    A text stream on Windows turns each newline into CR LF below the guard
    that escapes control characters, so a clean run ends its lines with CR LF
    there. A CR on its own is kept so the checks still see it.
    """
    return stream.replace(b"\r\n", b"\n")


def symlink_or_skip(link: Path, target: Path) -> None:
    """Make a symbolic link, or skip where the account may not create one."""
    try:
        link.symlink_to(target)
    except OSError:
        if os.name != "nt":
            raise
        pytest.skip("symlink creation needs privileges on Windows")

"""Keep raw control characters out of what the command-line tools print.

A memory's file name, frontmatter and body are written by whoever can commit
to the store. Printed as they are, an escape sequence in them reaches the
terminal (a clipboard write, a window title, a screen clear). Two layers:

- :func:`visible` makes one store-derived field safe to place in a line.
- :func:`terminal_safe` wraps a tool's entry point so that nothing it writes
  to standard output or standard error carries a control character, which
  also covers text no call site thought to clean.

Newline and tab stay in the stream; every other C0 character, DEL, and the C1
range become a visible escape such as ``\\x1b``.
"""

from __future__ import annotations

import functools
import re
import sys
from typing import Any, Callable, TypeVar

_STREAM_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f  ]")
_FIELD_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def _escape(match: "re.Match[str]") -> str:
    code = ord(match.group())
    return f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}"


def visible(value: Any) -> str:
    """One field as text with every control character, newline and tab
    included, shown as an escape, so it cannot end or forge a line."""
    return _FIELD_UNSAFE.sub(_escape, str(value))


def stream_safe(text: str) -> str:
    return _STREAM_UNSAFE.sub(_escape, text)


class _SafeStream:
    """A text stream that escapes control characters on the way out."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def write(self, text: str) -> int:
        self._inner.write(stream_safe(text))
        return len(text)

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


F = TypeVar("F", bound=Callable[..., Any])


def terminal_safe(func: F) -> F:
    """Run a tool's entry point with its standard output and error guarded."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        out, err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _SafeStream(out), _SafeStream(err)
        try:
            return func(*args, **kwargs)
        finally:
            sys.stdout, sys.stderr = out, err

    return wrapper  # type: ignore[return-value]

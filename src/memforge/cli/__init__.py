"""MemForge CLI entry-point modules.

Each `memforge.cli.<name>` module exposes a `main()` function that is
both the body of the corresponding `tools/<name>` script and the
console_scripts entry point declared in `pyproject.toml`. The two
invocation paths share the same code.
"""

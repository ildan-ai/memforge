# Shared config loader for `.memforge/config.yaml`.
#
# Spec ref: §"Config file (`.memforge/config.yaml`)" and §"Sensitivity
# enforcement (v0.4.0+)". The file is optional; defaults below match the spec.

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


TIER_ORDER = ["public", "internal", "restricted", "privileged"]


def tier_rank(tier: Optional[str]) -> int:
    if tier is None:
        return TIER_ORDER.index("internal")
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return -1


DEFAULTS: dict[str, Any] = {
    "spec_version": "0.4.0",
    "audit": {
        "stale_collision_days": 7,
        "snooze_horizon_days": 14,
        "snooze_cap_per_author": 10,
        "decision_bearing_tags": [],
        "audit_window_days": 30,
        "default_export_tier": None,
        "enforce_sensitivity_export_gate": True,
    },
    "dlp": {
        "enforce_sensitivity_cross_check": True,
    },
    "conformance": {
        "enforce_sensitivity_fixtures": True,
    },
}


def _merge_defaults(loaded: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in DEFAULTS.items():
        if isinstance(v, dict):
            sub = dict(v)
            loaded_sub = loaded.get(k) or {}
            if isinstance(loaded_sub, dict):
                sub.update({sk: sv for sk, sv in loaded_sub.items() if sv is not None or sk == "default_export_tier"})
            out[k] = sub
        else:
            out[k] = loaded.get(k, v)
    for k, v in loaded.items():
        if k not in out:
            out[k] = v
    return out


def find_config_path(start: Optional[Path] = None) -> Optional[Path]:
    """Walk upward from `start` (or cwd) looking for `.memforge/config.yaml`.

    Returns the path if found, else None.
    """
    here = (start or Path.cwd()).resolve()
    for directory in [here, *here.parents]:
        candidate = directory / ".memforge" / "config.yaml"
        if candidate.is_file():
            return candidate
    return None


def load_config(start: Optional[Path] = None) -> dict[str, Any]:
    """Load `.memforge/config.yaml` (auto-discovered) and merge with defaults.

    Returns the defaults verbatim when no config file is found or YAML
    parsing fails. Never raises.
    """
    if yaml is None:
        return _merge_defaults({})
    path = find_config_path(start)
    if path is None:
        return _merge_defaults({})
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, Exception):  # pragma: no cover
        return _merge_defaults({})
    if not isinstance(raw, dict):
        return _merge_defaults({})
    return _merge_defaults(raw)


def parse_frontmatter_sensitivity(text: str) -> tuple[bool, str]:
    """Lightweight frontmatter parse for sensitivity-aware tooling.

    Returns ``(is_memforge_file, declared_sensitivity)``:

    - ``is_memforge_file`` is True when the file has YAML frontmatter
      containing both ``name:`` and ``type:`` keys (the MemForge minimum).
    - ``declared_sensitivity`` is the literal value, or ``"internal"`` when
      absent or unrecognized (matches §"Sensitivity classification" default).
    """
    import re as _re

    if not text.startswith("---\n"):
        return (False, "internal")
    end = text.find("\n---", 4)
    if end < 0:
        return (False, "internal")
    fm_block = text[4:end]
    has_name = _re.search(r"(?m)^name\s*:", fm_block) is not None
    has_type = _re.search(r"(?m)^type\s*:", fm_block) is not None
    is_memforge = has_name and has_type

    sens = "internal"
    sens_match = _re.search(r"(?m)^sensitivity\s*:\s*(\S+)\s*$", fm_block)
    if sens_match:
        candidate = sens_match.group(1).strip()
        if candidate in TIER_ORDER:
            sens = candidate
    return (is_memforge, sens)

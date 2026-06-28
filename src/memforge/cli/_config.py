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


def _default_spec_version() -> str:
    """Default value for the config ``spec_version`` field.

    Reports the installed PACKAGE version (importlib.metadata), with a literal
    fallback for an uninstalled source tree. The package and the spec version
    are released on independent SemVer tracks (package 0.7.0 ships the spec-0.6.1
    surface this cycle), so this reports the package the operator is running.

    Caveat (config-01): this is the PACKAGE version, not a value read from
    ``spec/VERSION`` -- the spec directory is not shipped as wheel package-data,
    so it cannot be read at runtime from an installed deploy. On a package-only
    patch release (e.g. 0.6.2 with no spec change) this default would report the
    package version, which may be one patch ahead of the unchanged spec. The
    field name is retained for config-schema stability; the value tracks the
    package, which is the only version reliably available at runtime. Sourced
    from the package (not a hardcoded literal) so the default cannot drift
    STALE behind the shipping package (closes config-03).
    """
    fallback = "0.9.0"
    try:
        from importlib.metadata import PackageNotFoundError, version as _pkg_version

        try:
            return _pkg_version("ildan-memforge")
        except PackageNotFoundError:
            return fallback
    except Exception:  # pragma: no cover - importlib always present on 3.8+
        return fallback


def tier_rank(tier: Optional[str]) -> int:
    if tier is None:
        return TIER_ORDER.index("internal")
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return -1


# Sub-keys whose explicit ``null`` in config.yaml must be PRESERVED rather than
# dropped back to the DEFAULTS value. For these, ``null`` is a meaningful state
# (e.g. default_export_tier: null == "no export-tier gate"). Every OTHER sub-key
# set to null reverts to its default (fail-safe for the security flags, which
# default True). Named + commented here so the null-preservation contract is
# discoverable and extensible, not buried as a magic string in the merge
# comprehension (config-merge-01).
NULLABLE_KEYS: frozenset[str] = frozenset({"default_export_tier"})


DEFAULTS: dict[str, Any] = {
    "spec_version": _default_spec_version(),
    "audit": {
        "stale_collision_days": 7,
        "snooze_horizon_days": 14,
        "snooze_cap_per_author": 10,
        "decision_bearing_tags": [],
        "audit_window_days": 30,
        "default_export_tier": None,
        "enforce_sensitivity_export_gate": True,
    },
    "recall": {
        # Advisory always-set budget (v0.6.1). The always-set is the per-query
        # recall cost paid on every query, so the spec asks operators to keep it
        # small and bounded (§"Recall operation"). These are WARN thresholds,
        # never BLOCKERs: an existing repo over budget must not fail on upgrade.
        "max_always_count": 8,
        "max_always_description_chars": 600,
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
            loaded_raw = loaded.get(k)
            loaded_sub = loaded_raw or {}
            if isinstance(loaded_sub, dict):
                sub.update({
                    sk: sv
                    for sk, sv in loaded_sub.items()
                    if sv is not None or sk in NULLABLE_KEYS
                })
            elif k in loaded and loaded_raw is not None:
                # Operator set a dict-typed section (e.g. `audit:`) to a scalar
                # or list. The whole section silently reverts to defaults, which
                # is a foot-gun for the security-relevant flags it carries (e.g.
                # enforce_sensitivity_export_gate). Surface it so the operator
                # does not believe a mistyped flag is in effect (closes
                # config-02). load_config still never raises.
                import sys as _sys

                _sys.stderr.write(
                    f"warning: config section '{k}' is not a mapping "
                    f"(got {type(loaded_raw).__name__}); ignoring it and using "
                    f"defaults for that section\n"
                )
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

    Note (sec-egress/config-01): SPEC §"Sensitivity enforcement" makes setting
    either ``enforce_sensitivity_export_gate`` or ``dlp.
    enforce_sensitivity_cross_check`` to ``false`` a BLOCKER WHEN a privileged-
    labeled memory is present (the floor takes precedence over the disable). The
    loader does NOT scan the folder, so it cannot perform that content-aware
    validation here; the privileged hard-floor is asserted at scan time
    (dlp_scan._privileged_floor_engaged). That floor is presently DORMANT because
    no DLP PATTERN implies the ``privileged`` tier (all are restricted/internal),
    so a config disable is honored today with no privileged content possible to
    protect. When a privileged-class pattern lands, the scan-time floor activates
    and re-enables the cross-check regardless of the config disable.
    """
    if yaml is None:
        return _merge_defaults({})
    path = find_config_path(start)
    if path is None:
        return _merge_defaults({})
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):  # pragma: no cover
        return _merge_defaults({})
    if not isinstance(raw, dict):
        return _merge_defaults({})
    return _merge_defaults(raw)


def parse_frontmatter_sensitivity(text: str) -> tuple[bool, str]:
    """Frontmatter parse for sensitivity-aware tooling (DLP cross-check).

    Returns ``(is_memforge_file, declared_sensitivity)``:

    - ``is_memforge_file`` is True when the file has YAML frontmatter
      containing both ``name`` and ``type`` keys (the MemForge minimum).
    - ``declared_sensitivity`` is the declared value when recognized, or
      ``"internal"`` when absent or unrecognized (matches §"Sensitivity
      classification" default).

    Uses the canonical ``memforge.frontmatter.parse`` (yaml.safe_load) so the
    DLP cross-check and the ``memory-audit`` export-tier gate read sensitivity
    through the SAME parser. A hand-rolled regex previously diverged from the
    canonical parser on flow-style (`{name: x, type: user}`) and quoted
    (`sensitivity: "restricted"`) frontmatter, opening a
    ``sensitivity_label_mismatch`` evasion (closes config-01).
    """
    from memforge.frontmatter import parse as _mf_parse

    fm, _ = _mf_parse(text)
    if not isinstance(fm, dict) or not fm:
        return (False, "internal")

    is_memforge = ("name" in fm) and ("type" in fm)

    sens = "internal"
    declared = fm.get("sensitivity")
    if isinstance(declared, str) and declared in TIER_ORDER:
        sens = declared
    return (is_memforge, sens)

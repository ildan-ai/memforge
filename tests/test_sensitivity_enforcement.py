"""Unit tests for v0.4 sensitivity enforcement (spec §"Sensitivity enforcement").

Covers:
- Pattern -> implied tier mapping.
- DLP cross-check label vs implied tier.
- Cross-check disable + privileged hard-floor.
- Export-tier gate at each level.
- Privileged hard-floor for the export gate.
- Config loader defaults + overrides.
- Frontmatter parser absent/explicit/invalid sensitivity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memforge.cli._config import (
    DEFAULTS,
    TIER_ORDER,
    load_config,
    parse_frontmatter_sensitivity,
    tier_rank,
)
from memforge.cli.audit import _export_tier_gate
from memforge.cli.dlp_scan import (
    PATTERN_TIER_BY_NAME,
    Finding,
    _cross_check_enabled,
    _privileged_floor_engaged,
    check_sensitivity_mismatch,
)


# ---------------------------------------------------------------------------
# tier ordering + frontmatter parser
# ---------------------------------------------------------------------------


def test_tier_order_is_canonical():
    assert TIER_ORDER == ["public", "internal", "restricted", "privileged"]


def test_tier_rank_unknown_returns_negative():
    assert tier_rank("unknown") == -1


def test_tier_rank_none_treated_as_internal():
    assert tier_rank(None) == TIER_ORDER.index("internal")


def test_parse_frontmatter_no_frontmatter():
    is_mf, sens = parse_frontmatter_sensitivity("just a body, no fence")
    assert (is_mf, sens) == (False, "internal")


def test_parse_frontmatter_unterminated_fence():
    is_mf, sens = parse_frontmatter_sensitivity("---\nname: foo\nbody without close")
    assert (is_mf, sens) == (False, "internal")


def test_parse_frontmatter_minimal_memforge():
    text = "---\nname: foo\ntype: feedback\n---\n\nbody"
    is_mf, sens = parse_frontmatter_sensitivity(text)
    assert (is_mf, sens) == (True, "internal")


def test_parse_frontmatter_explicit_sensitivity():
    text = "---\nname: foo\ntype: feedback\nsensitivity: restricted\n---\n\nbody"
    is_mf, sens = parse_frontmatter_sensitivity(text)
    assert (is_mf, sens) == (True, "restricted")


def test_parse_frontmatter_invalid_sensitivity_value():
    text = "---\nname: foo\ntype: feedback\nsensitivity: bogus\n---\n\nbody"
    is_mf, sens = parse_frontmatter_sensitivity(text)
    assert (is_mf, sens) == (True, "internal")


# ---------------------------------------------------------------------------
# pattern->tier mapping
# ---------------------------------------------------------------------------


def test_pattern_tier_table_has_all_known_patterns():
    expected_names = {
        "aws_access_key_id", "aws_secret_access_key", "aws_arn",
        "github_pat", "github_oauth", "github_app_token", "github_refresh_token",
        "openai_api_key", "anthropic_api_key", "xai_api_key", "google_api_key",
        "slack_bot_token", "slack_webhook",
        "private_key_pem", "ssh_private_key_body",
        "jwt_token", "ssn_us", "credit_card_visa_mc",
        "docusign_api_account", "stripe_secret_key", "twilio_auth_token",
        "generic_password_assignment",
        "high_entropy_near_keyword",
    }
    assert expected_names.issubset(PATTERN_TIER_BY_NAME.keys())


def test_pattern_tier_values_are_known_levels():
    for name, tier in PATTERN_TIER_BY_NAME.items():
        assert tier in TIER_ORDER, f"{name} -> {tier} not in {TIER_ORDER}"


# ---------------------------------------------------------------------------
# DLP cross-check
# ---------------------------------------------------------------------------


def _finding(pattern: str, severity: str = "MAJOR") -> Finding:
    return Finding(
        file=Path("dummy.md"),
        line_no=1,
        pattern=pattern,
        severity=severity,
        excerpt="[REDACTED]",
    )


def test_cross_check_no_findings_returns_none():
    text = "---\nname: x\ntype: feedback\nsensitivity: public\n---\n\nbody"
    assert check_sensitivity_mismatch(text, Path("x"), []) is None


def test_cross_check_non_memforge_file_returns_none():
    # Plain markdown, no frontmatter — should not emit mismatch even with findings
    text = "Just markdown with arn:aws:iam::1:role/x"
    assert check_sensitivity_mismatch(text, Path("x"), [_finding("aws_arn")]) is None


def test_cross_check_public_with_arn_emits_blocker():
    text = "---\nname: x\ntype: feedback\nsensitivity: public\n---\n\nbody"
    result = check_sensitivity_mismatch(text, Path("x"), [_finding("aws_arn")])
    assert result is not None
    assert result.severity == "BLOCKER"
    assert result.pattern == "sensitivity_label_mismatch"
    assert "public" in result.excerpt
    assert "restricted" in result.excerpt


def test_cross_check_internal_with_jwt_emits_blocker():
    text = "---\nname: x\ntype: feedback\nsensitivity: internal\n---\n\nbody"
    result = check_sensitivity_mismatch(text, Path("x"), [_finding("jwt_token")])
    assert result is not None
    assert result.pattern == "sensitivity_label_mismatch"


def test_cross_check_restricted_with_arn_no_mismatch():
    text = "---\nname: x\ntype: feedback\nsensitivity: restricted\n---\n\nbody"
    assert check_sensitivity_mismatch(text, Path("x"), [_finding("aws_arn")]) is None


def test_cross_check_privileged_covers_everything():
    text = "---\nname: x\ntype: feedback\nsensitivity: privileged\n---\n\nbody"
    findings = [_finding("aws_arn"), _finding("jwt_token"), _finding("ssn_us")]
    assert check_sensitivity_mismatch(text, Path("x"), findings) is None


def test_cross_check_picks_highest_implied_across_findings():
    text = "---\nname: x\ntype: feedback\nsensitivity: public\n---\n\nbody"
    findings = [_finding("high_entropy_near_keyword"), _finding("aws_arn")]
    result = check_sensitivity_mismatch(text, Path("x"), findings)
    assert result is not None
    assert "restricted" in result.excerpt


def test_cross_check_unknown_pattern_skipped():
    text = "---\nname: x\ntype: feedback\nsensitivity: public\n---\n\nbody"
    assert check_sensitivity_mismatch(text, Path("x"), [_finding("__unknown__")]) is None


# ---------------------------------------------------------------------------
# Cross-check enable resolution + privileged floor
# ---------------------------------------------------------------------------


def test_cross_check_enable_cli_disable_overrides_config():
    assert _cross_check_enabled(cli_disabled=True, config_enabled=True) is False


def test_cross_check_enable_config_disable_honored():
    assert _cross_check_enabled(cli_disabled=False, config_enabled=False) is False


def test_cross_check_enable_default_path():
    assert _cross_check_enabled(cli_disabled=False, config_enabled=True) is True


def test_privileged_floor_engaged_with_no_privileged_finding():
    findings = [_finding("aws_arn"), _finding("jwt_token")]
    assert _privileged_floor_engaged(findings) is False


def test_privileged_floor_engaged_unknown_pattern_safe():
    findings = [_finding("__unknown__")]
    assert _privileged_floor_engaged(findings) is False


# ---------------------------------------------------------------------------
# Audit export-tier gate
# ---------------------------------------------------------------------------


def test_export_gate_no_tier_set_returns_none():
    assert _export_tier_gate("restricted", export_tier=None, enforce=True) is None


def test_export_gate_at_or_below_tier_returns_none():
    assert _export_tier_gate("public", "internal", True) is None
    assert _export_tier_gate("internal", "internal", True) is None


def test_export_gate_exceeds_tier_with_enforce():
    msg = _export_tier_gate("restricted", "internal", True)
    assert msg is not None
    assert "restricted" in msg and "internal" in msg


def test_export_gate_disabled_skips_non_privileged():
    assert _export_tier_gate("restricted", "internal", enforce=False) is None
    assert _export_tier_gate("internal", "public", enforce=False) is None


def test_export_gate_privileged_blocks_even_when_disabled():
    msg = _export_tier_gate("privileged", "restricted", enforce=False)
    assert msg is not None
    assert "privileged hard-floor" in msg


def test_export_gate_privileged_not_blocked_at_privileged_tier():
    assert _export_tier_gate("privileged", "privileged", enforce=False) is None


def test_export_gate_empty_sensitivity_treated_as_internal():
    assert _export_tier_gate("", "internal", True) is None
    msg = _export_tier_gate("", "public", True)
    assert msg is not None and "internal" in msg


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def test_load_config_defaults_when_no_file(tmp_path):
    cfg = load_config(start=tmp_path)
    assert cfg["audit"]["enforce_sensitivity_export_gate"] is True
    assert cfg["dlp"]["enforce_sensitivity_cross_check"] is True
    assert cfg["audit"]["default_export_tier"] is None
    assert cfg["audit"]["audit_window_days"] == 30


def test_load_config_overrides(tmp_path):
    cfg_dir = tmp_path / ".memforge"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        "audit:\n"
        "  default_export_tier: internal\n"
        "  enforce_sensitivity_export_gate: false\n"
        "dlp:\n"
        "  enforce_sensitivity_cross_check: false\n",
        encoding="utf-8",
    )
    cfg = load_config(start=tmp_path)
    assert cfg["audit"]["default_export_tier"] == "internal"
    assert cfg["audit"]["enforce_sensitivity_export_gate"] is False
    assert cfg["dlp"]["enforce_sensitivity_cross_check"] is False


def test_load_config_preserves_other_keys(tmp_path):
    cfg_dir = tmp_path / ".memforge"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        "audit:\n  audit_window_days: 60\n",
        encoding="utf-8",
    )
    cfg = load_config(start=tmp_path)
    assert cfg["audit"]["audit_window_days"] == 60
    assert cfg["audit"]["enforce_sensitivity_export_gate"] is True


def test_defaults_are_a_dict():
    assert isinstance(DEFAULTS, dict)
    assert "audit" in DEFAULTS and "dlp" in DEFAULTS

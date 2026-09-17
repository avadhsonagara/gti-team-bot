"""
Regression tests for the GTI List Alerts filter — same mechanism as the
canonical gti-ms-team-bot/gcp/rs-alerts implementation exactly, including its
strict `>` cursor clause (an alert sharing its exact audit.update_time with
another one is a real, accepted edge case there — this port keeps the same
behavior rather than diverging from it).
"""
import pytest

from app.config import Settings
from app.gti_client import _level_filter_clause, build_filter


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, gti_rsa_project="test-project", **overrides)


def test_build_filter_with_cursor_and_defaults():
    filt = build_filter("2026-09-17T10:00:00Z", _settings())

    assert 'audit.update_time > "2026-09-17T10:00:00Z"' in filt
    assert "update_time >=" not in filt, "must be strict >, matching the GCP canonical script exactly"
    assert '(severity_analysis.severity_level = "SEVERITY_LEVEL_MEDIUM" OR severity_analysis.severity_level = "SEVERITY_LEVEL_HIGH")' in filt
    assert '(priority_analysis.priority_level = "PRIORITY_LEVEL_MEDIUM" OR priority_analysis.priority_level = "PRIORITY_LEVEL_HIGH" OR priority_analysis.priority_level = "PRIORITY_LEVEL_CRITICAL")' in filt
    assert '(relevance_analysis.relevance_level = "RELEVANCE_LEVEL_MEDIUM" OR relevance_analysis.relevance_level = "RELEVANCE_LEVEL_HIGH")' in filt
    assert '(relevance_analysis.confidence = "CONFIDENCE_LEVEL_MEDIUM" OR relevance_analysis.confidence = "CONFIDENCE_LEVEL_HIGH")' in filt
    # AND-joined across dimensions, cursor clause first.
    assert filt.startswith('audit.update_time > "2026-09-17T10:00:00Z" AND (severity_analysis')


def test_build_filter_first_run_has_no_cursor_clause():
    filt = build_filter(None, _settings())
    assert "audit.update_time" not in filt


def test_single_value_dimension_is_not_parenthesized():
    """A dimension resolving to exactly one value must be bare, not wrapped in parens."""
    filt = build_filter(None, _settings(filter_severity_level="HIGH"))
    assert 'severity_analysis.severity_level = "SEVERITY_LEVEL_HIGH"' in filt
    assert '(severity_analysis.severity_level = "SEVERITY_LEVEL_HIGH")' not in filt


def test_empty_filter_dimension_raises_not_disables():
    """
    Matches the canonical GCP behavior exactly: an empty dimension is a
    configuration error, not "pass everything on this dimension" — the
    opposite of what the older azure/rs-alerts port silently did.
    """
    with pytest.raises(RuntimeError, match="filter_severity_level resolved to no values"):
        build_filter(None, _settings(filter_severity_level=""))


def test_invalid_filter_value_raises():
    with pytest.raises(RuntimeError, match="filter_severity_level='NOTALEVEL' is invalid"):
        build_filter(None, _settings(filter_severity_level="NOTALEVEL"))


def test_value_without_prefix_is_normalized_and_deduplicated():
    """Bare 'high' (no SEVERITY_LEVEL_ prefix, mixed case) normalizes and de-dupes against the prefixed form."""
    clause = _level_filter_clause(
        "severity_analysis.severity_level", "filter_severity_level", "SEVERITY_LEVEL_",
        ("LOW", "MEDIUM", "HIGH"), _settings(filter_severity_level="high, SEVERITY_LEVEL_HIGH, high"),
    )
    assert clause == 'severity_analysis.severity_level = "SEVERITY_LEVEL_HIGH"'

"""Unit tests for GTI List Alerts query filter construction and validation."""
import pytest

from app.config import Settings
from app.gti_client import _level_filter_clause, build_filter


def _settings(**overrides) -> Settings:
    """Helper creating a Settings test instance with default project."""
    return Settings(_env_file=None, gti_rsa_project="test-project", **overrides)


def test_build_filter_with_cursor_and_defaults():
    """Verify filter generation with a cursor timestamp and standard default level clauses."""
    filt = build_filter("2026-09-17T10:00:00Z", _settings())

    assert 'audit.update_time > "2026-09-17T10:00:00Z"' in filt
    assert "update_time >=" not in filt
    assert '(severity_analysis.severity_level = "SEVERITY_LEVEL_MEDIUM" OR severity_analysis.severity_level = "SEVERITY_LEVEL_HIGH")' in filt
    assert '(priority_analysis.priority_level = "PRIORITY_LEVEL_MEDIUM" OR priority_analysis.priority_level = "PRIORITY_LEVEL_HIGH" OR priority_analysis.priority_level = "PRIORITY_LEVEL_CRITICAL")' in filt
    assert '(relevance_analysis.relevance_level = "RELEVANCE_LEVEL_MEDIUM" OR relevance_analysis.relevance_level = "RELEVANCE_LEVEL_HIGH")' in filt
    assert '(relevance_analysis.confidence = "CONFIDENCE_LEVEL_MEDIUM" OR relevance_analysis.confidence = "CONFIDENCE_LEVEL_HIGH")' in filt
    assert filt.startswith('audit.update_time > "2026-09-17T10:00:00Z" AND (severity_analysis')


def test_build_filter_first_run_has_no_cursor_clause():
    """Verify first-run filter omitted cursor timestamp clause when cursor is None."""
    filt = build_filter(None, _settings())
    assert "audit.update_time" not in filt


def test_single_value_dimension_is_not_parenthesized():
    """Verify single-value dimensions are rendered without outer parentheses."""
    filt = build_filter(None, _settings(filter_severity_level="HIGH"))
    assert 'severity_analysis.severity_level = "SEVERITY_LEVEL_HIGH"' in filt
    assert '(severity_analysis.severity_level = "SEVERITY_LEVEL_HIGH")' not in filt


def test_empty_filter_dimension_raises_not_disables():
    """Verify empty filter level string raises RuntimeError rather than disabling filter."""
    with pytest.raises(RuntimeError, match="filter_severity_level resolved to no values"):
        build_filter(None, _settings(filter_severity_level=""))


def test_invalid_filter_value_raises():
    """Verify unrecognized filter level strings raise RuntimeError."""
    with pytest.raises(RuntimeError, match="filter_severity_level='NOTALEVEL' is invalid"):
        build_filter(None, _settings(filter_severity_level="NOTALEVEL"))


def test_value_without_prefix_is_normalized_and_deduplicated():
    """Verify bare level names normalize to prefixed format and deduplicate identical values."""
    clause = _level_filter_clause(
        "severity_analysis.severity_level", "filter_severity_level", "SEVERITY_LEVEL_",
        ("LOW", "MEDIUM", "HIGH"), _settings(filter_severity_level="high, SEVERITY_LEVEL_HIGH, high"),
    )
    assert clause == 'severity_analysis.severity_level = "SEVERITY_LEVEL_HIGH"'


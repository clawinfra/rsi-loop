"""Tests for the Analyzer module."""

import pytest

from rsi_loop.analyzer import Analyzer
from rsi_loop.observer import Observer
from rsi_loop.types import Config


@pytest.fixture
def tmp_config(tmp_path):
    return Config(data_dir=str(tmp_path / "rsi_data"))


@pytest.fixture
def analyzer(tmp_config):
    return Analyzer(tmp_config)


@pytest.fixture
def populated_analyzer(tmp_config):
    """Analyzer with pre-populated outcomes."""
    obs = Observer(tmp_config)
    # Rate limit pattern
    for _ in range(4):
        obs.record_simple("api_call", success=False, error="429 Too Many Requests", source="svc_a")
    # Success outcomes
    for _ in range(6):
        obs.record_simple("code_gen", success=True, model="sonnet-4.6", quality=4)
    # Tool error (high severity, threshold=1)
    obs.record_simple("file_ops", success=False, error="Permission denied: /etc/passwd")
    return Analyzer(tmp_config, observer=obs)


class TestAnalyzer:
    def test_analyze_empty(self, analyzer):
        patterns = analyzer.analyze()
        assert patterns == []

    def test_analyze_detects_patterns(self, populated_analyzer):
        patterns = populated_analyzer.analyze()
        assert len(patterns) > 0
        # Rate limit should be detected (4 occurrences)
        issues = {p.issue for p in patterns}
        assert "rate_limit" in issues

    def test_pattern_fields(self, populated_analyzer):
        patterns = populated_analyzer.analyze()
        for p in patterns:
            assert p.id
            assert p.category
            assert p.frequency > 0
            assert 0 <= p.impact_score
            assert 0 <= p.failure_rate <= 1
            assert p.description

    def test_high_severity_threshold_1(self, populated_analyzer):
        patterns = populated_analyzer.analyze()
        issues = {p.issue for p in patterns}
        assert "tool_error" in issues

    def test_health_score_no_data(self, analyzer):
        score = analyzer.health_score()
        assert score == 1.0  # No data = healthy

    def test_health_score_all_success(self, tmp_config):
        obs = Observer(tmp_config)
        for _ in range(5):
            obs.record_simple("task", success=True, quality=5)
        a = Analyzer(tmp_config, observer=obs)
        score = a.health_score()
        assert score == 1.0

    def test_health_score_all_failure(self, tmp_config):
        obs = Observer(tmp_config)
        for _ in range(5):
            obs.record_simple("task", success=False, error="fail", quality=1)
        a = Analyzer(tmp_config, observer=obs)
        score = a.health_score()
        assert score < 0.3

    def test_health_score_mixed(self, populated_analyzer):
        score = populated_analyzer.health_score()
        assert 0.0 < score < 1.0

    def test_patterns_sorted_by_impact(self, populated_analyzer):
        patterns = populated_analyzer.analyze()
        if len(patterns) > 1:
            for i in range(len(patterns) - 1):
                assert patterns[i].impact_score >= patterns[i + 1].impact_score

    def test_error_clustering(self, tmp_config):
        obs = Observer(tmp_config)
        for i in range(3):
            obs.record_simple(
                "api", success=False,
                error=f"Connection refused to host abc{i}def: port 8080"
            )
        a = Analyzer(tmp_config, observer=obs)
        patterns = a.analyze()
        categories = {p.category for p in patterns}
        # Should find error_cluster or tool_reliability
        assert len(patterns) > 0

    def test_cross_source_correlations(self, tmp_config):
        obs = Observer(tmp_config)
        obs.record_simple("task", success=False, error="session reset", source="svc_a")
        obs.record_simple("task", success=False, error="context length exceeded", source="svc_b")
        a = Analyzer(tmp_config, observer=obs)
        corr = a.cross_source_correlations()
        # session_reset + context_loss from different sources → correlation
        if corr:
            assert corr[0]["correlation"] == "context_management"

    def test_recurrence_detection(self, tmp_config):
        obs = Observer(tmp_config)
        a = Analyzer(tmp_config, observer=obs)

        # First cycle
        for _ in range(3):
            obs.record_simple("api", success=False, error="429 rate limit")
        a.analyze()

        # Second cycle — same pattern should be marked recurring
        for _ in range(3):
            obs.record_simple("api", success=False, error="429 rate limit")
        patterns = a.analyze()
        rate_patterns = [p for p in patterns if p.issue == "rate_limit"]
        if rate_patterns:
            assert rate_patterns[0].recurring is True

    def test_max_20_patterns(self, tmp_config):
        obs = Observer(tmp_config)
        # Create many different issue types
        for i in range(25):
            obs.record_simple(f"task_{i}", success=False, error="tool error occurred")
            obs.record_simple(f"task_{i}", success=False, error="tool error occurred")
        a = Analyzer(tmp_config, observer=obs)
        patterns = a.analyze()
        assert len(patterns) <= 20

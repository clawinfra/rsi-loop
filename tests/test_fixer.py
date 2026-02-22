"""Tests for the Fixer module."""

import pytest

from rsi_loop.fixer import Fixer
from rsi_loop.types import Config, Pattern


@pytest.fixture
def tmp_config(tmp_path):
    return Config(data_dir=str(tmp_path / "rsi_data"))


@pytest.fixture
def fixer(tmp_config):
    return Fixer(tmp_config)


def _make_pattern(issue: str = "rate_limit", **kwargs) -> Pattern:
    defaults = {
        "id": "test-pattern-1",
        "category": "model_routing",
        "task_type": "api_call",
        "issue": issue,
        "frequency": 5,
        "impact_score": 0.8,
        "failure_rate": 0.9,
        "description": f"Test pattern: {issue}",
    }
    defaults.update(kwargs)
    return Pattern(**defaults)


class TestFixer:
    def test_propose_rate_limit(self, fixer):
        fix = fixer.propose(_make_pattern("rate_limit"))
        assert fix.pattern_id == "test-pattern-1"
        assert fix.safe_category == "retry_logic"
        assert fix.type == "auto"
        assert len(fix.changes) > 0

    def test_propose_model_fallback(self, fixer):
        fix = fixer.propose(_make_pattern("model_fallback"))
        assert fix.safe_category == "routing_config"
        assert fix.type == "auto"

    def test_propose_slow_response(self, fixer):
        fix = fixer.propose(_make_pattern("slow_response"))
        assert fix.safe_category == "threshold_tuning"

    def test_propose_session_reset_not_safe(self, fixer):
        fix = fixer.propose(_make_pattern("session_reset"))
        assert fix.safe_category == ""
        assert fix.type == "manual"
        assert fix.status == "draft"

    def test_propose_unknown_issue(self, fixer):
        fix = fixer.propose(_make_pattern("some_new_issue"))
        assert fix.type == "manual"
        assert fix.description

    def test_apply_if_safe_auto(self, fixer):
        fix = fixer.propose(_make_pattern("rate_limit"))
        applied = fixer.apply_if_safe(fix)
        assert applied is True
        assert fix.status == "applied"

    def test_apply_if_safe_manual(self, fixer):
        fix = fixer.propose(_make_pattern("session_reset"))
        applied = fixer.apply_if_safe(fix)
        assert applied is False
        assert fix.status == "draft"

    def test_propose_and_apply(self, fixer):
        fix = fixer.propose_and_apply(_make_pattern("rate_limit"))
        assert fix.status == "applied"

    def test_save_and_load_proposals(self, fixer):
        fixer.propose_and_apply(_make_pattern("rate_limit"))
        fixer.propose_and_apply(_make_pattern("model_fallback", id="test-2"))
        fixer.propose_and_apply(_make_pattern("session_reset", id="test-3"))

        proposals = fixer.load_proposals()
        assert len(proposals) == 3

    def test_load_proposals_empty(self, fixer):
        assert fixer.load_proposals() == []

    def test_custom_safe_categories(self, tmp_path):
        config = Config(
            data_dir=str(tmp_path / "rsi"),
            safe_categories=["investigation"],
        )
        fixer = Fixer(config)
        fix = fixer.propose(_make_pattern("session_reset"))
        applied = fixer.apply_if_safe(fix)
        assert applied is True

    def test_fix_has_description(self, fixer):
        fix = fixer.propose(_make_pattern("empty_response"))
        assert "empty" in fix.description.lower() or "response" in fix.description.lower()

    def test_fix_changes_detail(self, fixer):
        fix = fixer.propose(_make_pattern("rate_limit", frequency=10))
        assert any("10" in c.get("detail", "") for c in fix.changes)

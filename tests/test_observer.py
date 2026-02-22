"""Tests for the Observer module."""

import json
import tempfile
from pathlib import Path

import pytest

from rsi_loop.observer import Observer
from rsi_loop.types import Config, Outcome


@pytest.fixture
def tmp_config(tmp_path):
    return Config(data_dir=str(tmp_path / "rsi_data"))


@pytest.fixture
def observer(tmp_config):
    return Observer(tmp_config)


class TestObserver:
    def test_record_simple_success(self, observer):
        o = observer.record_simple("code_gen", success=True, model="sonnet-4.6")
        assert o.success is True
        assert o.task_type == "code_gen"
        assert o.model == "sonnet-4.6"
        assert o.quality == 3

    def test_record_simple_failure(self, observer):
        o = observer.record_simple("api_call", success=False, error="Connection refused")
        assert o.success is False
        assert o.quality <= 2
        assert "tool_error" in o.issues

    def test_record_simple_rate_limit(self, observer):
        o = observer.record_simple("api_call", success=False, error="429 Too Many Requests")
        assert "rate_limit" in o.issues

    def test_record_simple_timeout(self, observer):
        o = observer.record_simple("search", success=False, error="Request timed out after 30s")
        assert "timeout" in o.issues

    def test_record_simple_empty_response(self, observer):
        o = observer.record_simple("code_gen", success=False, error="Got empty response from model")
        assert "empty_response" in o.issues

    def test_record_simple_context_loss(self, observer):
        o = observer.record_simple("chat", success=False, error="Context length exceeded limit")
        assert "context_loss" in o.issues

    def test_record_simple_unknown_error(self, observer):
        o = observer.record_simple("task", success=False, error="Something weird happened")
        assert "other" in o.issues

    def test_record_full_outcome(self, observer):
        outcome = Outcome(
            source="openclaw",
            task_type="code_review",
            success=True,
            quality=5,
            model="opus-4",
            duration_ms=1500,
            notes="Great review",
            tags=["python"],
        )
        recorded = observer.record(outcome)
        assert recorded.id == outcome.id
        assert recorded.quality == 5

    def test_quality_clamping(self, observer):
        o = observer.record_simple("task", success=True, quality=10)
        assert o.quality == 5
        o2 = observer.record_simple("task", success=True, quality=-1)
        # Failure path clamps to min(quality, 2), but success doesn't
        # The record method clamps to 1-5
        loaded = observer.load_outcomes(days=365)
        for outcome in loaded:
            assert 1 <= outcome.quality <= 5

    def test_load_outcomes(self, observer):
        observer.record_simple("task1", success=True)
        observer.record_simple("task2", success=False, error="timeout")
        outcomes = observer.load_outcomes(days=1)
        assert len(outcomes) == 2

    def test_load_outcomes_empty(self, observer):
        outcomes = observer.load_outcomes()
        assert outcomes == []

    def test_persistence(self, observer, tmp_config):
        observer.record_simple("task1", success=True)
        # Create new observer with same config
        observer2 = Observer(tmp_config)
        outcomes = observer2.load_outcomes(days=1)
        assert len(outcomes) == 1

    def test_recurrences(self, observer):
        for _ in range(4):
            observer.record_simple("api", success=False, error="429 rate limit hit")
        recur = observer.recurrences(threshold=3)
        assert "rate_limit" in recur
        assert recur["rate_limit"] >= 3

    def test_recurrences_below_threshold(self, observer):
        observer.record_simple("api", success=False, error="429 rate limit")
        recur = observer.recurrences(threshold=3)
        assert recur == {}

    def test_normalize_error(self):
        norm = Observer.normalize_error("Error at request abc123def456: timeout after 30s")
        assert "<ID>" in norm
        assert "<N>" in norm
        # Placeholders are uppercase (<ID>, <N>), rest is lowered
        assert "<ID>" in norm
        assert "<N>" in norm

    def test_classify_error_multiple(self):
        # An error could match multiple classifiers
        issues = Observer._classify_error("Model not found, got 404")
        assert "missing_tool" in issues

    def test_record_with_tags_and_metadata(self, observer):
        outcome = Outcome(
            task_type="analysis",
            success=True,
            tags=["important", "weekly"],
            metadata={"run_id": "abc123"},
        )
        recorded = observer.record(outcome)
        loaded = observer.load_outcomes(days=1)
        assert loaded[0].tags == ["important", "weekly"]

    def test_auto_classify_on_record(self, observer):
        outcome = Outcome(
            task_type="api_call",
            success=False,
            error_message="429 Too Many Requests",
        )
        recorded = observer.record(outcome)
        assert "rate_limit" in recorded.issues

    def test_no_overwrite_existing_issues(self, observer):
        outcome = Outcome(
            task_type="api_call",
            success=False,
            error_message="Some error",
            issues=["custom_issue"],
        )
        recorded = observer.record(outcome)
        assert recorded.issues == ["custom_issue"]

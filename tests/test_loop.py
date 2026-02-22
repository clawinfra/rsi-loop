"""Tests for the RSILoop module."""

import time

import pytest

from rsi_loop.loop import RSILoop
from rsi_loop.types import Config


@pytest.fixture
def tmp_config(tmp_path):
    return Config(data_dir=str(tmp_path / "rsi_data"))


@pytest.fixture
def loop(tmp_config):
    return RSILoop(tmp_config)


class TestRSILoop:
    def test_default_init(self, tmp_path):
        loop = RSILoop(Config(data_dir=str(tmp_path / "d")))
        assert loop.observer is not None
        assert loop.analyzer is not None
        assert loop.fixer is not None

    def test_init_no_config(self):
        loop = RSILoop()
        assert loop.config.data_dir == "./rsi_data"

    def test_run_cycle_empty(self, loop):
        patterns = loop.run_cycle()
        assert patterns == []

    def test_run_cycle_with_data(self, loop):
        for _ in range(3):
            loop.observer.record_simple("api", success=False, error="429 rate limit")
        loop.observer.record_simple("code", success=True, quality=5)

        patterns = loop.run_cycle()
        assert len(patterns) > 0

    def test_health_score_fresh(self, loop):
        assert loop.health_score() == 1.0

    def test_health_score_after_failures(self, loop):
        for _ in range(5):
            loop.observer.record_simple("task", success=False, error="fail")
        assert loop.health_score() < 0.5

    def test_patterns(self, loop):
        for _ in range(3):
            loop.observer.record_simple("api", success=False, error="timeout")
        pats = loop.patterns()
        assert len(pats) > 0

    def test_fixes_empty(self, loop):
        assert loop.fixes() == []

    def test_fixes_after_cycle(self, loop):
        for _ in range(3):
            loop.observer.record_simple("api", success=False, error="429 rate limit")
        loop.run_cycle()
        fixes = loop.fixes()
        assert len(fixes) > 0

    def test_background_loop(self, loop):
        loop.observer.record_simple("task", success=True)
        loop.start_background(interval_seconds=1)
        time.sleep(0.5)
        # Should not crash
        loop.stop_background()

    def test_background_loop_idempotent_start(self, loop):
        loop.start_background(interval_seconds=60)
        loop.start_background(interval_seconds=60)  # Should not create second thread
        loop.stop_background()

    def test_stop_without_start(self, loop):
        loop.stop_background()  # Should not crash

    def test_full_workflow(self, loop):
        """End-to-end: record → analyze → fix → verify health."""
        # Record mixed outcomes
        loop.observer.record_simple("code_gen", success=True, model="sonnet-4.6", quality=5)
        loop.observer.record_simple("code_gen", success=True, model="sonnet-4.6", quality=4)
        loop.observer.record_simple("api_call", success=False, error="429 rate limit")
        loop.observer.record_simple("api_call", success=False, error="429 rate limit")
        loop.observer.record_simple("api_call", success=False, error="429 rate limit")

        # Run cycle
        patterns = loop.run_cycle()
        assert any(p.issue == "rate_limit" for p in patterns)

        # Health should be between 0 and 1
        health = loop.health_score()
        assert 0.0 < health < 1.0

        # Fixes should exist
        fixes = loop.fixes()
        assert len(fixes) > 0

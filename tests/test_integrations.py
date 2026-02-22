"""Tests for integration adapters."""

import json

from rsi_loop.integrations.claude_code import ClaudeCodeAdapter
from rsi_loop.integrations.generic import GenericAdapter


class TestClaudeCodeAdapter:
    def test_on_task_complete(self, tmp_path):
        adapter = ClaudeCodeAdapter(data_dir=str(tmp_path / "rsi"))
        o = adapter.on_task_complete("code_review", success=True, model="sonnet-4.6")
        assert o.source == "openclaw"
        assert o.success is True

    def test_on_task_failure(self, tmp_path):
        adapter = ClaudeCodeAdapter(data_dir=str(tmp_path / "rsi"))
        o = adapter.on_task_complete("api_call", success=False, error="timeout")
        assert o.success is False
        assert o.issues

    def test_on_session_reset(self, tmp_path):
        adapter = ClaudeCodeAdapter(data_dir=str(tmp_path / "rsi"))
        o = adapter.on_session_reset(notes="Context overflow")
        assert o.success is False
        assert "session_reset" in o.issues

    def test_on_model_fallback(self, tmp_path):
        adapter = ClaudeCodeAdapter(data_dir=str(tmp_path / "rsi"))
        o = adapter.on_model_fallback("opus-4", "sonnet-4.6", "rate_limit")
        assert "fallback" in o.error_message.lower()
        assert o.model == "sonnet-4.6"

    def test_on_cron_failure(self, tmp_path):
        adapter = ClaudeCodeAdapter(data_dir=str(tmp_path / "rsi"))
        o = adapter.on_cron_failure("daily-check", "Script crashed")
        assert o.success is False
        assert "daily-check" in o.notes

    def test_on_subagent_failure(self, tmp_path):
        adapter = ClaudeCodeAdapter(data_dir=str(tmp_path / "rsi"))
        o = adapter.on_subagent_failure("code-review-agent", "OOM", model="llama-70b")
        assert o.success is False
        assert o.model == "llama-70b"

    def test_run_cycle(self, tmp_path):
        adapter = ClaudeCodeAdapter(data_dir=str(tmp_path / "rsi"))
        for _ in range(3):
            adapter.on_task_complete("api", success=False, error="429 rate limit")
        patterns = adapter.run_cycle()
        assert len(patterns) > 0

    def test_health_score(self, tmp_path):
        adapter = ClaudeCodeAdapter(data_dir=str(tmp_path / "rsi"))
        assert adapter.health_score() == 1.0


class TestGenericAdapter:
    def test_poll_empty(self, tmp_path):
        adapter = GenericAdapter(
            watch_dir=str(tmp_path / "inbox"),
            data_dir=str(tmp_path / "rsi"),
        )
        assert adapter.poll() == []

    def test_poll_ingests_files(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        # Write outcome file
        (inbox / "outcome1.json").write_text(json.dumps({
            "task": "search",
            "success": False,
            "error": "timeout after 30s",
        }))
        (inbox / "outcome2.json").write_text(json.dumps({
            "task": "code_gen",
            "success": True,
            "quality": 5,
        }))

        adapter = GenericAdapter(
            watch_dir=str(inbox),
            data_dir=str(tmp_path / "rsi"),
        )
        outcomes = adapter.poll()
        assert len(outcomes) == 2

        # Files should be moved to .processed
        assert not (inbox / "outcome1.json").exists()
        assert (inbox / ".processed" / "outcome1.json").exists()

    def test_poll_skips_bad_json(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "bad.json").write_text("not json{{{")
        (inbox / "good.json").write_text(json.dumps({"task": "ok", "success": True}))

        adapter = GenericAdapter(
            watch_dir=str(inbox),
            data_dir=str(tmp_path / "rsi"),
        )
        outcomes = adapter.poll()
        assert len(outcomes) == 1

    def test_poll_skips_dotfiles(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / ".hidden.json").write_text(json.dumps({"task": "x", "success": True}))

        adapter = GenericAdapter(
            watch_dir=str(inbox),
            data_dir=str(tmp_path / "rsi"),
        )
        assert adapter.poll() == []

    def test_run_cycle(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        for i in range(3):
            (inbox / f"out{i}.json").write_text(json.dumps({
                "task": "api", "success": False, "error": "429 rate limit",
            }))

        adapter = GenericAdapter(
            watch_dir=str(inbox),
            data_dir=str(tmp_path / "rsi"),
        )
        patterns = adapter.run_cycle()
        assert len(patterns) > 0

    def test_health_score(self, tmp_path):
        adapter = GenericAdapter(
            watch_dir=str(tmp_path / "inbox"),
            data_dir=str(tmp_path / "rsi"),
        )
        assert adapter.health_score() == 1.0

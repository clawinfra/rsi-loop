"""Claude Code / OpenClaw adapter for RSI Loop."""

from __future__ import annotations

from rsi_loop.loop import RSILoop
from rsi_loop.types import Config, Outcome


class ClaudeCodeAdapter:
    """Wraps RSI Loop for Claude Code and OpenClaw agents.

    Provides high-level methods matching common agent lifecycle events:
    task completion, session resets, model fallbacks, etc.

    Usage::

        adapter = ClaudeCodeAdapter(data_dir="./rsi_data")
        adapter.on_task_complete("code_review", success=True, model="sonnet-4.6")
        adapter.on_model_fallback("opus-4", "sonnet-4.6", "rate_limit")
        adapter.on_session_reset()
    """

    def __init__(self, data_dir: str = "./rsi_data", **config_kwargs) -> None:
        self.loop = RSILoop(Config(data_dir=data_dir, **config_kwargs))

    def on_task_complete(
        self,
        task: str,
        success: bool = True,
        error: str | None = None,
        model: str | None = None,
        duration_ms: int | None = None,
        quality: int = 3,
        notes: str = "",
    ) -> Outcome:
        """Record a completed task outcome."""
        return self.loop.observer.record_simple(
            task=task,
            success=success,
            error=error,
            model=model,
            duration_ms=duration_ms,
            quality=quality,
            source="openclaw",
            notes=notes,
        )

    def on_session_reset(self, notes: str = "") -> Outcome:
        """Record a session reset event."""
        return self.loop.observer.record_simple(
            task="session_management",
            success=False,
            error="Session reset — context lost",
            source="openclaw",
            quality=1,
            notes=notes,
        )

    def on_model_fallback(
        self,
        from_model: str,
        to_model: str,
        reason: str = "unavailable",
    ) -> Outcome:
        """Record a model fallback event."""
        return self.loop.observer.record_simple(
            task="model_routing",
            success=True,
            error=f"Model fallback: {from_model} → {to_model} ({reason})",
            model=to_model,
            source="openclaw",
            quality=2,
            notes=f"Fell back from {from_model} to {to_model}: {reason}",
        )

    def on_cron_failure(self, job_name: str, error: str) -> Outcome:
        """Record a cron job failure."""
        return self.loop.observer.record_simple(
            task="cron_management",
            success=False,
            error=error,
            source="openclaw",
            quality=1,
            notes=f"Cron job '{job_name}' failed",
        )

    def on_subagent_failure(self, label: str, error: str, model: str = "") -> Outcome:
        """Record a sub-agent failure."""
        return self.loop.observer.record_simple(
            task="subagent_management",
            success=False,
            error=error,
            model=model,
            source="openclaw",
            quality=1,
            notes=f"Sub-agent '{label}' failed",
        )

    def run_cycle(self):
        """Run an improvement cycle."""
        return self.loop.run_cycle()

    def health_score(self) -> float:
        """Get current health score."""
        return self.loop.health_score()

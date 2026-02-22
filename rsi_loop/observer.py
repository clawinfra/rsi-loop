"""Observer — Record task outcomes and auto-classify issues."""

from __future__ import annotations

import json
import re
from pathlib import Path

from rsi_loop.types import ERROR_CLASSIFIERS, Config, Outcome


class Observer:
    """Records agent task outcomes to a JSONL store.

    Framework-agnostic: works with any agent that can call ``record()``
    or ``record_simple()`` after each task.
    """

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self._data_dir = Path(self.config.data_dir)
        self._outcomes_file = self._data_dir / "outcomes.jsonl"

    def record(self, outcome: Outcome) -> Outcome:
        """Record a full Outcome object. Auto-classifies issues from error_message."""
        if outcome.error_message and not outcome.issues:
            outcome.issues = self._classify_error(outcome.error_message)
        outcome.quality = max(1, min(5, outcome.quality))

        self._data_dir.mkdir(parents=True, exist_ok=True)
        with open(self._outcomes_file, "a") as f:
            f.write(json.dumps(outcome.to_dict()) + "\n")
        return outcome

    def record_simple(
        self,
        task: str,
        success: bool = True,
        error: str | None = None,
        model: str | None = None,
        duration_ms: int | None = None,
        quality: int = 3,
        source: str = "generic",
        tags: list[str] | None = None,
        notes: str = "",
    ) -> Outcome:
        """Convenience method — record an outcome with minimal arguments."""
        issues: list[str] = []
        if error:
            issues = self._classify_error(error)
        if not success and not issues:
            issues = ["other"]

        outcome = Outcome(
            source=source,
            task_type=task,
            success=success,
            quality=quality if success else min(quality, 2),
            issues=issues,
            error_message=error or "",
            model=model or "",
            duration_ms=duration_ms or 0,
            notes=notes,
            tags=tags or [],
        )
        return self.record(outcome)

    def load_outcomes(self, days: int | None = None) -> list[Outcome]:
        """Load outcomes from the JSONL store, optionally filtered by recency."""
        if not self._outcomes_file.exists():
            return []

        import time
        from datetime import datetime

        cutoff = time.time() - ((days or self.config.analysis_window_days) * 86400)
        outcomes: list[Outcome] = []

        with open(self._outcomes_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    ts_str = data.get("ts", data.get("timestamp", ""))
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str).timestamp()
                        if ts < cutoff:
                            continue
                    outcomes.append(Outcome.from_dict(data))
                except (json.JSONDecodeError, ValueError):
                    continue
        return outcomes

    def recurrences(self, threshold: int | None = None) -> dict[str, int]:
        """Return issues that recur >= threshold times in the analysis window."""
        thresh = threshold or self.config.recurrence_threshold
        outcomes = self.load_outcomes()
        counts: dict[str, int] = {}
        for o in outcomes:
            for issue in o.issues:
                counts[issue] = counts.get(issue, 0) + 1
        return {issue: count for issue, count in counts.items() if count >= thresh}

    @staticmethod
    def _classify_error(error: str) -> list[str]:
        """Auto-classify an error message into issue types."""
        lower = error.lower()
        issues: list[str] = []
        for keywords, issue_type in ERROR_CLASSIFIERS:
            if any(kw in lower for kw in keywords):
                issues.append(issue_type)
        return issues or ["other"]

    @staticmethod
    def normalize_error(error: str) -> str:
        """Normalize an error message for clustering (strip IDs, numbers)."""
        normalized = error.lower().strip()
        normalized = re.sub(r"[0-9a-f]{8,}", "<ID>", normalized)
        normalized = re.sub(r"\d+", "<N>", normalized)
        return normalized[:120]

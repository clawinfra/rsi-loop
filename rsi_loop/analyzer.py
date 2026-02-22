"""Analyzer — Detect improvement patterns from recorded outcomes."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from rsi_loop.observer import Observer
from rsi_loop.types import HIGH_SEVERITY_ISSUES, Config, Outcome, Pattern

# Category classification by issue
_ISSUE_CATEGORIES: dict[str, str] = {
    "skill_gap": "skill_gap", "missing_tool": "skill_gap", "wrong_output": "skill_gap",
    "rate_limit": "model_routing", "model_fallback": "model_routing",
    "wrong_model_tier": "model_routing", "slow_response": "model_routing",
    "context_loss": "memory_continuity", "memory_miss": "memory_continuity",
    "compaction_lost_context": "memory_continuity",
    "repeated_mistake": "behavior_pattern", "over_confirmation": "behavior_pattern",
    "bad_routing": "behavior_pattern",
    "tool_error": "tool_reliability", "timeout": "tool_reliability",
    "empty_response": "tool_reliability",
}

# Suggested actions by category
_CATEGORY_ACTIONS: dict[str, str] = {
    "skill_gap": "Create or improve relevant skill/tool",
    "model_routing": "Update model routing configuration",
    "memory_continuity": "Improve memory and context protocols",
    "behavior_pattern": "Update agent behavior rules",
    "tool_reliability": "Add retry logic or fallback tools",
}

# Cross-source correlation pairs
_CORRELATED_ISSUES: dict[frozenset[str], str] = {
    frozenset({"session_reset", "context_loss"}): "context_management",
    frozenset({"cost_overrun", "wrong_model_tier"}): "model_routing",
    frozenset({"empty_response", "tool_error"}): "tool_reliability",
    frozenset({"hydration_fail", "context_loss"}): "session_recovery",
}


class Analyzer:
    """Detects improvement patterns from recorded outcomes."""

    def __init__(self, config: Config | None = None, observer: Observer | None = None) -> None:
        self.config = config or Config()
        self.observer = observer or Observer(self.config)
        self._data_dir = Path(self.config.data_dir)
        self._patterns_file = self._data_dir / "patterns.json"

    def analyze(self, days: int | None = None) -> list[Pattern]:
        """Scan outcomes and detect patterns. Returns ranked list by impact."""
        window = days or self.config.analysis_window_days
        outcomes = self.observer.load_outcomes(days=window)
        if not outcomes:
            return []

        patterns: list[Pattern] = []
        total = len(outcomes)

        # ── Group by (task_type, issue) ────────────────────────────────────────
        groups: dict[tuple[str, str], list[Outcome]] = defaultdict(list)
        for o in outcomes:
            issues = o.issues or ["none"]
            for issue in issues:
                groups[(o.task_type, issue)].append(o)

        for (task, issue), group in groups.items():
            n = len(group)
            min_threshold = 1 if issue in HIGH_SEVERITY_ISSUES else 2
            if n < min_threshold:
                continue

            failures = [o for o in group if not o.success]
            failure_rate = len(failures) / n
            avg_quality = sum(o.quality for o in group) / n
            quality_deficit = 5.0 - avg_quality
            impact = (n / total) * quality_deficit

            category = _ISSUE_CATEGORIES.get(issue, "other")
            action = _CATEGORY_ACTIONS.get(category, "Investigate and address")
            sources = sorted(set(o.source for o in group))
            errors = [o.error_message for o in group if o.error_message][:3]
            timestamps = sorted(o.timestamp for o in group)

            patterns.append(Pattern(
                id=f"{task[:8]}-{issue[:8]}-{n}",
                category=category,
                task_type=task,
                issue=issue,
                frequency=n,
                impact_score=impact,
                failure_rate=failure_rate,
                description=f"In '{task}' tasks, '{issue}' occurs {n}x "
                            f"with {failure_rate:.0%} failure rate",
                sample_errors=errors,
                suggested_action=action,
                sources=sources,
                first_seen=timestamps[0] if timestamps else "",
                last_seen=timestamps[-1] if timestamps else "",
            ))

        # ── Error message clustering ──────────────────────────────────────────
        error_groups: dict[str, list[Outcome]] = defaultdict(list)
        for o in outcomes:
            if o.error_message:
                norm = Observer.normalize_error(o.error_message)
                error_groups[norm].append(o)

        for norm_err, err_outcomes in error_groups.items():
            if len(err_outcomes) < 2:
                continue
            n = len(err_outcomes)
            failures = [o for o in err_outcomes if not o.success]
            avg_quality = sum(o.quality for o in err_outcomes) / n
            quality_deficit = 5.0 - avg_quality
            impact = (n / total) * quality_deficit
            sources = sorted(set(o.source for o in err_outcomes))

            patterns.append(Pattern(
                id=f"err-{hash(norm_err) % 99999:05d}-{n}",
                category="error_cluster",
                task_type="mixed",
                issue="error_cluster",
                frequency=n,
                impact_score=impact,
                failure_rate=len(failures) / n,
                description=f"Error cluster ({n}x): {norm_err[:60]}",
                sample_errors=[o.error_message for o in err_outcomes[:3]],
                suggested_action="Investigate common error pattern",
                sources=sources,
            ))

        # ── Recurrence detection ──────────────────────────────────────────────
        prev_patterns = self._load_previous_patterns()
        for p in patterns:
            key = (p.task_type, p.issue)
            if key in prev_patterns:
                p.recurring = True
                prev_freq = prev_patterns[key].get("frequency", 0)
                p.trend = "increasing" if p.frequency > prev_freq else "stable"

        # Sort by impact
        patterns.sort(key=lambda p: -p.impact_score)
        patterns = patterns[:20]

        # Save for next cycle's recurrence detection
        self._save_patterns(patterns)
        return patterns

    def health_score(self, days: int | None = None) -> float:
        """Compute overall health score: 0.0 (broken) to 1.0 (healthy)."""
        outcomes = self.observer.load_outcomes(days=days or self.config.analysis_window_days)
        if not outcomes:
            return 1.0  # No data = assume healthy

        total_success = sum(1 for o in outcomes if o.success)
        avg_quality = sum(o.quality for o in outcomes) / len(outcomes)
        return round((total_success / len(outcomes)) * (avg_quality / 5.0), 3)

    def cross_source_correlations(self, days: int | None = None) -> list[dict]:
        """Detect correlated issues appearing across different sources."""
        outcomes = self.observer.load_outcomes(days=days or self.config.analysis_window_days)
        issue_sources: dict[str, set[str]] = defaultdict(set)
        for o in outcomes:
            for issue in o.issues:
                issue_sources[issue].add(o.source)

        active = set(issue_sources.keys())
        correlations = []
        for pair, name in _CORRELATED_ISSUES.items():
            if pair.issubset(active):
                all_sources: set[str] = set()
                for iss in pair:
                    all_sources.update(issue_sources[iss])
                if len(all_sources) > 1:
                    correlations.append({
                        "issues": sorted(pair),
                        "correlation": name,
                        "sources": sorted(all_sources),
                    })
        return correlations

    def _load_previous_patterns(self) -> dict[tuple[str, str], dict]:
        if not self._patterns_file.exists():
            return {}
        try:
            with open(self._patterns_file) as f:
                data = json.load(f)
            return {
                (p.get("task_type", ""), p.get("issue", "")): p
                for p in data.get("patterns", [])
            }
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_patterns(self, patterns: list[Pattern]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        data = {"patterns": [p.to_dict() for p in patterns]}
        with open(self._patterns_file, "w") as f:
            json.dump(data, f, indent=2)

"""Fixer — Generate and apply fixes for detected patterns."""

from __future__ import annotations

import json
from pathlib import Path

from rsi_loop.types import Config, Fix, Pattern

# Issue → fix template mapping
_FIX_TEMPLATES: dict[str, dict[str, str]] = {
    "rate_limit": {
        "type": "retry_logic",
        "description": "Add/increase retry backoff for rate-limited endpoints",
    },
    "model_fallback": {
        "type": "routing_config",
        "description": "Update model fallback chain configuration",
    },
    "wrong_model_tier": {
        "type": "routing_config",
        "description": "Adjust tier classification thresholds",
    },
    "cost_overrun": {
        "type": "routing_config",
        "description": "Lower cost ceiling or adjust model routing",
    },
    "slow_response": {
        "type": "threshold_tuning",
        "description": "Increase timeout thresholds or add circuit breaker",
    },
    "timeout": {
        "type": "threshold_tuning",
        "description": "Increase timeout thresholds or add circuit breaker",
    },
    "empty_response": {
        "type": "retry_logic",
        "description": "Add empty-response detection and retry",
    },
    "session_reset": {
        "type": "investigation",
        "description": "Investigate context management protocols",
    },
}


class Fixer:
    """Generates fix proposals for detected patterns.

    Safe categories are auto-applicable; everything else produces a proposal
    saved to disk for human review.
    """

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self._data_dir = Path(self.config.data_dir)
        self._proposals_dir = self._data_dir / "proposals"

    def propose(self, pattern: Pattern) -> Fix:
        """Generate a fix proposal for a detected pattern."""
        template = _FIX_TEMPLATES.get(pattern.issue, {})
        fix_type = template.get("type", "investigation")
        description = template.get(
            "description",
            f"Address '{pattern.issue}' in '{pattern.task_type}' tasks",
        )

        is_safe = fix_type in self.config.safe_categories

        changes: list[dict[str, str]] = []
        if fix_type == "routing_config":
            changes.append({
                "target": "model routing config",
                "action": "Review and adjust routing thresholds",
                "detail": f"Pattern '{pattern.issue}' suggests routing misconfiguration",
            })
        elif fix_type == "retry_logic":
            changes.append({
                "target": "relevant integration code",
                "action": f"Add retry with backoff for '{pattern.issue}' errors",
                "detail": f"Detected {pattern.frequency}x occurrences",
            })
        elif fix_type == "threshold_tuning":
            changes.append({
                "target": "relevant config",
                "action": f"Adjust thresholds to reduce '{pattern.issue}'",
                "detail": f"Current failure rate: {pattern.failure_rate:.0%}",
            })

        return Fix(
            pattern_id=pattern.id,
            type="auto" if is_safe else "manual",
            status="proposed" if is_safe else "draft",
            target=changes[0]["target"] if changes else "",
            changes=changes,
            safe_category=fix_type if is_safe else "",
            description=description,
        )

    def apply_if_safe(self, fix: Fix) -> bool:
        """Auto-apply if the fix is in a safe category. Returns True if applied.

        Note: Even "safe" fixes only get their status updated and saved as a
        proposal. Actual code/config changes require agent-specific implementation.
        """
        if fix.safe_category and fix.safe_category in self.config.safe_categories:
            fix.status = "applied"
            self._save_proposal(fix)
            return True

        fix.status = "draft"
        self._save_proposal(fix)
        return False

    def propose_and_apply(self, pattern: Pattern) -> Fix:
        """Convenience: propose a fix and auto-apply if safe."""
        fix = self.propose(pattern)
        self.apply_if_safe(fix)
        return fix

    def load_proposals(self) -> list[Fix]:
        """Load all saved fix proposals."""
        if not self._proposals_dir.exists():
            return []
        proposals: list[Fix] = []
        for path in sorted(self._proposals_dir.glob("*.json")):
            try:
                with open(path) as f:
                    proposals.append(Fix.from_dict(json.load(f)))
            except (json.JSONDecodeError, OSError):
                continue
        return proposals

    def _save_proposal(self, fix: Fix) -> Path:
        self._proposals_dir.mkdir(parents=True, exist_ok=True)
        path = self._proposals_dir / f"{fix.id}.json"
        with open(path, "w") as f:
            json.dump(fix.to_dict(), f, indent=2)
        return path

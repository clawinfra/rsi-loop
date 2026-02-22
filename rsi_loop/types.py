"""Core data types for RSI Loop."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id() -> str:
    return str(uuid.uuid4())[:8]


# ── Issue taxonomy ─────────────────────────────────────────────────────────────

ISSUE_TYPES: set[str] = {
    # Model / routing
    "rate_limit", "model_fallback", "wrong_model_tier", "cost_overrun",
    "bad_routing", "slow_response",
    # Tool / execution
    "tool_error", "empty_response", "missing_tool", "incomplete_task",
    # Output quality
    "wrong_output",
    # Memory / context
    "context_loss", "memory_miss", "compaction_lost_context", "session_reset",
    "hydration_fail",
    # Self-governance
    "over_confirmation", "repeated_mistake", "skill_gap", "wal_miss",
    # Timeout
    "timeout",
    # Catch-all
    "other",
}

HIGH_SEVERITY_ISSUES: set[str] = {
    "tool_error", "wrong_output", "empty_response",
    "session_reset", "cost_overrun", "wal_miss",
}

# ── Error keyword → issue mapping for auto-classification ─────────────────────

ERROR_CLASSIFIERS: list[tuple[list[str], str]] = [
    (["rate limit", "429", "too many requests", "quota exceeded"], "rate_limit"),
    (["timeout", "timed out", "deadline exceeded"], "timeout"),
    (["empty response", "empty reply", "no output", "null response"], "empty_response"),
    (["context length", "token limit", "context window", "too long"], "context_loss"),
    (["session reset", "session expired", "compaction"], "session_reset"),
    (["model unavailable", "model not found", "fallback"], "model_fallback"),
    (["permission denied", "unauthorized", "forbidden", "403"], "tool_error"),
    (["not found", "404", "missing"], "missing_tool"),
    (["connection refused", "connection reset", "network error"], "tool_error"),
]


@dataclass
class Outcome:
    """A single task outcome recorded by an agent."""

    id: str = field(default_factory=_short_id)
    timestamp: str = field(default_factory=_utcnow)
    source: str = "generic"
    task_type: str = "unknown"
    success: bool = True
    quality: int = 3  # 1-5
    issues: list[str] = field(default_factory=list)
    error_message: str = ""
    model: str = ""
    duration_ms: int = 0
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.timestamp,
            "source": self.source,
            "task_type": self.task_type,
            "success": self.success,
            "quality": max(1, min(5, self.quality)),
            "issues": self.issues,
            "error_message": self.error_message,
            "model": self.model,
            "duration_ms": self.duration_ms,
            "notes": self.notes,
            "tags": self.tags,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Outcome:
        return cls(
            id=data.get("id", _short_id()),
            timestamp=data.get("ts", data.get("timestamp", _utcnow())),
            source=data.get("source", "generic"),
            task_type=data.get("task_type", data.get("task", "unknown")),
            success=data.get("success", True),
            quality=data.get("quality", 3),
            issues=data.get("issues", []),
            error_message=data.get("error_message", data.get("error_msg", data.get("error", ""))),
            model=data.get("model", ""),
            duration_ms=data.get("duration_ms", 0),
            notes=data.get("notes", ""),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Pattern:
    """A detected improvement pattern from analyzed outcomes."""

    id: str = field(default_factory=_short_id)
    category: str = "other"
    task_type: str = "unknown"
    issue: str = "other"
    frequency: int = 0
    impact_score: float = 0.0
    failure_rate: float = 0.0
    description: str = ""
    sample_errors: list[str] = field(default_factory=list)
    suggested_action: str = ""
    sources: list[str] = field(default_factory=list)
    first_seen: str = field(default_factory=_utcnow)
    last_seen: str = field(default_factory=_utcnow)
    recurring: bool = False
    trend: str = "new"  # new, stable, increasing, decreasing

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "task_type": self.task_type,
            "issue": self.issue,
            "frequency": self.frequency,
            "impact_score": round(self.impact_score, 4),
            "failure_rate": round(self.failure_rate, 3),
            "description": self.description,
            "sample_errors": self.sample_errors,
            "suggested_action": self.suggested_action,
            "sources": self.sources,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "recurring": self.recurring,
            "trend": self.trend,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Pattern:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Fix:
    """A fix proposal or applied fix for a detected pattern."""

    id: str = field(default_factory=_short_id)
    pattern_id: str = ""
    type: str = "manual"  # auto or manual
    status: str = "draft"  # draft, proposed, applied, rejected
    target: str = ""  # what to fix (file, config, etc.)
    changes: list[dict[str, str]] = field(default_factory=list)
    safe_category: str = ""
    description: str = ""
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pattern_id": self.pattern_id,
            "type": self.type,
            "status": self.status,
            "target": self.target,
            "changes": self.changes,
            "safe_category": self.safe_category,
            "description": self.description,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fix:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Config:
    """Configuration for RSI Loop."""

    data_dir: str = "./rsi_data"
    analysis_window_days: int = 7
    recurrence_threshold: int = 3
    auto_fix_enabled: bool = True
    safe_categories: list[str] = field(
        default_factory=lambda: ["routing_config", "threshold_tuning", "retry_logic"]
    )

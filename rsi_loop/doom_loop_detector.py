"""
doom_loop_detector.py — Pre-flight tool-call pattern guard for RSI-Loop.

Detects four pathological patterns in agent tool-call histories:
  - "loop"            : same tool + identical args repeated ≥ N times
  - "soft_loop"       : same tool + similar args (Jaccard > threshold) ≥ N times
  - "flailing"        : N consecutive errors from the same tool
  - "emergency_abort" : context budget <20% AND last 5 calls all errored

Inspired by huggingface/ml-intern's Doom Loop Detector (agent/core/agent_loop.py).

Usage:
    from rsi_loop.doom_loop_detector import check_before_tool_call

    result = check_before_tool_call(
        session_id="sess-123",
        planned_tool="read_file",
        planned_args={"path": "/tmp/foo.txt"},
        recent_history=[...],           # list of ToolCallRecord dicts
        context_used_tokens=8000,
        context_total_tokens=10000,
    )
    if result["should_abort"]:
        raise RuntimeError(result["corrective_message"])
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: Dict[str, Any] = {
    "window_size": 10,
    "identical_threshold": 3,
    "similar_threshold": 4,
    "jaccard_threshold": 0.85,
    "consecutive_error_threshold": 3,
    "context_emergency_pct": 0.20,
    "abort_after_k_detections": 2,
}

_CONFIG_PATH = Path(os.environ.get("DOOM_LOOP_CONFIG", "")) or Path(
    Path(__file__).resolve().parents[2] / "memory" / "doom-loop-config.json"
)


def load_config() -> Dict[str, Any]:
    """Load config from doom-loop-config.json, falling back to defaults."""
    cfg = dict(_DEFAULT_CONFIG)
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH) as f:
                overrides = json.load(f)
            cfg.update(overrides)
        except Exception:
            pass
    return cfg


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ToolCallRecord:
    """Represents one tool call in history."""
    tool: str
    args: Dict[str, Any]
    errored: bool = False
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ToolCallRecord":
        return cls(
            tool=d.get("tool", d.get("tool_name", "")),
            args=d.get("args", d.get("arguments", d.get("input", {}))),
            errored=bool(d.get("errored", d.get("error", d.get("is_error", False)))),
            timestamp=float(d.get("timestamp", time.time())),
        )


# Type alias for the return value
DetectionResult = Dict[str, Any]

# ---------------------------------------------------------------------------
# Jaccard similarity helpers
# ---------------------------------------------------------------------------

def _args_to_token_set(args: Dict[str, Any]) -> set:
    """
    Flatten args dict into a set of "key=value" tokens for Jaccard comparison.
    Values are stringified; nested dicts/lists are JSON-serialised.
    """
    tokens: set = set()
    for k, v in args.items():
        if isinstance(v, (dict, list)):
            v_str = json.dumps(v, sort_keys=True)
        else:
            v_str = str(v)
        tokens.add(f"{k}={v_str}")
    return tokens


def jaccard(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Jaccard similarity between two args dicts."""
    sa = _args_to_token_set(a)
    sb = _args_to_token_set(b)
    if not sa and not sb:
        return 1.0
    intersection = len(sa & sb)
    union = len(sa | sb)
    return intersection / union if union else 0.0


# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------

def _detect(
    planned_tool: str,
    planned_args: Dict[str, Any],
    window: List[ToolCallRecord],
    context_used: Optional[int],
    context_total: Optional[int],
    cfg: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Core detection engine. Returns a pattern dict or None.
    Pattern dict: {"pattern": str, "severity": str, "count": int}
    """

    # --- Pattern 1: emergency_abort — check first (highest priority) ---
    if context_used is not None and context_total is not None and context_total > 0:
        remaining_pct = 1.0 - (context_used / context_total)
        if remaining_pct < cfg["context_emergency_pct"]:
            # Check if last 5 calls all errored
            last5 = window[-5:]
            if len(last5) >= 5 and all(r.errored for r in last5):
                return {
                    "pattern": "emergency_abort",
                    "severity": "abort",
                    "count": 5,
                    "extra": {"remaining_pct": remaining_pct},
                }

    # --- Pattern 2: flailing — consecutive errors from same tool ---
    consec_errors = 0
    for rec in reversed(window):
        if rec.tool == planned_tool and rec.errored:
            consec_errors += 1
        else:
            break
    if consec_errors >= cfg["consecutive_error_threshold"]:
        return {
            "pattern": "flailing",
            "severity": "abort",
            "count": consec_errors,
            "extra": {},
        }

    # Filter window to same tool only for loop checks
    same_tool_calls = [r for r in window if r.tool == planned_tool]

    # --- Pattern 3: identical loop ---
    identical_count = sum(
        1 for r in same_tool_calls if r.args == planned_args
    )
    if identical_count >= cfg["identical_threshold"]:
        return {
            "pattern": "loop",
            "severity": "abort",
            "count": identical_count,
            "extra": {},
        }

    # --- Pattern 4: soft_loop (similar args) ---
    similar_count = sum(
        1 for r in same_tool_calls
        if jaccard(r.args, planned_args) > cfg["jaccard_threshold"]
    )
    if similar_count >= cfg["similar_threshold"]:
        return {
            "pattern": "soft_loop",
            "severity": "warning",
            "count": similar_count,
            "extra": {"jaccard_threshold": cfg["jaccard_threshold"]},
        }

    return None


# ---------------------------------------------------------------------------
# Corrective message templates
# ---------------------------------------------------------------------------

_CORRECTIVE_TEMPLATES: Dict[str, str] = {
    "loop": (
        "🔁 **Doom Loop detected** — you've called `{tool}` with identical args "
        "{count} times (threshold: {threshold}). "
        "Try: (a) read the tool's docs and verify the correct args, "
        "(b) use a sibling/alternative tool, "
        "(c) surface the blocker to the user instead of retrying."
    ),
    "soft_loop": (
        "⚠️ **Soft Loop detected** — you've called `{tool}` with similar args "
        "{count} times (Jaccard > {jaccard_threshold}). "
        "Try: (a) check if a different parameter set is needed, "
        "(b) use a sibling tool, "
        "(c) stop and ask the user for clarification."
    ),
    "flailing": (
        "🚨 **Flailing detected** — `{tool}` has errored {count} consecutive times. "
        "Stop retrying. Try: (a) read the error message carefully, "
        "(b) check tool docs for correct usage, "
        "(c) try an alternative approach or surface the error to the user."
    ),
    "emergency_abort": (
        "🆘 **Emergency abort** — context budget is critically low ({remaining_pct:.0%} remaining) "
        "AND the last 5 tool calls all errored. "
        "Abort the current approach immediately. Surface the situation to the user "
        "and request guidance rather than continuing to consume tokens."
    ),
}


def _format_corrective(pattern: str, tool: str, detection: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    template = _CORRECTIVE_TEMPLATES.get(pattern, "Doom loop pattern `{pattern}` detected for `{tool}`.")
    extra = detection.get("extra", {})
    return template.format(
        tool=tool,
        pattern=pattern,
        count=detection.get("count", 0),
        threshold=cfg.get("identical_threshold", 3),
        jaccard_threshold=cfg.get("jaccard_threshold", 0.85),
        remaining_pct=extra.get("remaining_pct", 0.0),
    )


# ---------------------------------------------------------------------------
# Per-session consecutive detection counter (in-memory)
# ---------------------------------------------------------------------------

_session_detection_counts: Dict[str, int] = {}


# ---------------------------------------------------------------------------
# WAL integration
# ---------------------------------------------------------------------------

def _read_wal_history(session_id: str, window_size: int) -> List[ToolCallRecord]:
    """
    Read recent tool-call records from agent-self-governance WAL storage.
    WAL log files live at memory/wal-<session_id>.jsonl (one JSON obj per line).
    Each record is expected to have: tool, args, errored, timestamp fields.
    Falls back to empty list if WAL is unavailable.
    """
    workspace = Path(__file__).resolve().parents[2]
    # Try multiple WAL path conventions
    candidates = [
        workspace / "memory" / f"wal-{session_id}.jsonl",
        workspace / "memory" / f"wal.jsonl",
        workspace / "memory" / "wal" / f"{session_id}.jsonl",
    ]

    for wal_path in candidates:
        if wal_path.exists():
            records: List[ToolCallRecord] = []
            try:
                with open(wal_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            # Only include tool-call events
                            if obj.get("type") in ("tool_call", "tool_result", "tool_use", None):
                                if "tool" in obj or "tool_name" in obj:
                                    records.append(ToolCallRecord.from_dict(obj))
                        except json.JSONDecodeError:
                            pass
            except OSError:
                pass
            return records[-window_size:]

    return []


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

def _emit_telemetry(session_id: str, pattern: str, tool: str, count: int) -> None:
    """Emit doom_loop_detected event to clawmemory API (fire-and-forget)."""
    content = (
        f"doom_loop_detected: session={session_id} pattern={pattern} "
        f"tool={tool} count={count} ts={int(time.time())}"
    )
    payload = json.dumps({
        "content": content,
        "tags": ["doom_loop", "rsi", f"pattern:{pattern}", f"tool:{tool}"],
    })
    try:
        subprocess.run(
            ["curl", "-s", "-X", "POST",
             "http://localhost:7437/api/v1/facts",
             "-H", "Content-Type: application/json",
             "-d", payload],
            timeout=3,
            capture_output=True,
        )
    except Exception:
        pass  # telemetry is best-effort


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_before_tool_call(
    session_id: str,
    planned_tool: str,
    planned_args: Dict[str, Any],
    recent_history: Optional[List[Any]] = None,
    *,
    context_used_tokens: Optional[int] = None,
    context_total_tokens: Optional[int] = None,
    _cfg_override: Optional[Dict[str, Any]] = None,
) -> DetectionResult:
    """
    Pre-flight check before executing a tool call.

    Args:
        session_id:           Unique identifier for the current agent session.
        planned_tool:         Name of the tool about to be called.
        planned_args:         Arguments dict for the planned tool call.
        recent_history:       Optional list of recent tool-call records (dicts or
                              ToolCallRecord objects). If None, WAL is read automatically.
        context_used_tokens:  Tokens consumed so far in the context window.
        context_total_tokens: Total context window capacity.
        _cfg_override:        Optional config dict override (for testing).

    Returns:
        DetectionResult dict:
            {
                "detected": bool,
                "pattern": str | None,       # "loop"|"soft_loop"|"flailing"|"emergency_abort"
                "severity": str,             # "info"|"warning"|"abort"
                "corrective_message": str | None,
                "should_abort": bool,
            }
    """
    cfg = _cfg_override if _cfg_override is not None else load_config()

    # Build the window of recent tool calls
    if recent_history is None:
        window_records = _read_wal_history(session_id, cfg["window_size"])
    else:
        window_records = []
        for item in recent_history:
            if isinstance(item, ToolCallRecord):
                window_records.append(item)
            elif isinstance(item, dict):
                window_records.append(ToolCallRecord.from_dict(item))
        window_records = window_records[-cfg["window_size"]:]

    # Run detection
    detection = _detect(
        planned_tool=planned_tool,
        planned_args=planned_args,
        window=window_records,
        context_used=context_used_tokens,
        context_total=context_total_tokens,
        cfg=cfg,
    )

    if detection is None:
        return {
            "detected": False,
            "pattern": None,
            "severity": "info",
            "corrective_message": None,
            "should_abort": False,
        }

    pattern = detection["pattern"]
    corrective = _format_corrective(pattern, planned_tool, detection, cfg)

    # Track consecutive detections per session for k-abort
    _session_detection_counts[session_id] = _session_detection_counts.get(session_id, 0) + 1
    consec = _session_detection_counts[session_id]

    severity = detection["severity"]
    should_abort = severity == "abort"

    # Hard stop after k consecutive detections regardless of severity
    if consec >= cfg["abort_after_k_detections"]:
        should_abort = True
        if severity != "abort":
            severity = "abort"
            corrective = (
                f"🛑 **Hard stop** — doom loop detector has fired {consec} times "
                f"this session (limit: {cfg['abort_after_k_detections']}). "
                f"Last pattern: `{pattern}` on `{planned_tool}`. "
                "Abort current approach and surface situation to user."
            )

    # Emit telemetry (fire and forget)
    _emit_telemetry(session_id, pattern, planned_tool, detection["count"])

    return {
        "detected": True,
        "pattern": pattern,
        "severity": severity,
        "corrective_message": corrective,
        "should_abort": should_abort,
    }


def reset_session_counter(session_id: str) -> None:
    """Reset the per-session detection counter (useful after a successful recovery)."""
    _session_detection_counts.pop(session_id, None)

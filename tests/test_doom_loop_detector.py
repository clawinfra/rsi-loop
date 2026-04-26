"""
Unit tests for doom_loop_detector.py

Coverage:
  - Identical-arg loop detection
  - Similar-arg Jaccard detection (true positive + false positive)
  - Consecutive-error flailing detection
  - Emergency-abort token-budget trigger
  - K-consecutive-detection hard stop
  - Jaccard similarity edge cases
  - No-detection baseline
  - WAL record parsing helpers
"""

import pytest


from rsi_loop.doom_loop_detector import (
    ToolCallRecord,
    check_before_tool_call,
    jaccard,
    reset_session_counter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_CFG = {
    "window_size": 10,
    "identical_threshold": 3,
    "similar_threshold": 4,
    "jaccard_threshold": 0.85,
    "consecutive_error_threshold": 3,
    "context_emergency_pct": 0.20,
    "abort_after_k_detections": 2,
}


def _rec(tool: str, args: dict, errored: bool = False) -> ToolCallRecord:
    return ToolCallRecord(tool=tool, args=args, errored=errored)


def _check(session_id, tool, args, history, *, ctx_used=None, ctx_total=None, cfg=None):
    """Convenience wrapper that resets counter before each check."""
    reset_session_counter(session_id)
    return check_before_tool_call(
        session_id=session_id,
        planned_tool=tool,
        planned_args=args,
        recent_history=history,
        context_used_tokens=ctx_used,
        context_total_tokens=ctx_total,
        _cfg_override=cfg or _BASE_CFG,
    )


# ---------------------------------------------------------------------------
# 1. No-detection baseline
# ---------------------------------------------------------------------------

class TestNoDetection:
    def test_empty_history(self):
        result = _check("s1", "read_file", {"path": "/tmp/a"}, [])
        assert result["detected"] is False
        assert result["pattern"] is None
        assert result["should_abort"] is False
        assert result["severity"] == "info"

    def test_single_call_same_tool(self):
        history = [_rec("read_file", {"path": "/tmp/a"})]
        result = _check("s2", "read_file", {"path": "/tmp/a"}, history)
        assert result["detected"] is False

    def test_two_calls_same_tool_same_args(self):
        # threshold is 3; 2 in history + 1 planned = 3, but history has only 2 identical
        history = [
            _rec("read_file", {"path": "/tmp/a"}),
            _rec("read_file", {"path": "/tmp/a"}),
        ]
        result = _check("s3", "read_file", {"path": "/tmp/a"}, history)
        # 2 identical in history < threshold(3) → no detection
        assert result["detected"] is False

    def test_different_tools_no_detection(self):
        history = [
            _rec("read_file", {"path": "/tmp/a"}),
            _rec("write_file", {"path": "/tmp/a"}),
            _rec("exec", {"cmd": "ls"}),
        ]
        result = _check("s4", "read_file", {"path": "/tmp/b"}, history)
        assert result["detected"] is False


# ---------------------------------------------------------------------------
# 2. Identical-arg loop detection
# ---------------------------------------------------------------------------

class TestIdenticalLoop:
    def test_exact_loop_at_threshold(self):
        """3 identical calls in history → detect loop."""
        history = [_rec("read_file", {"path": "/tmp/foo"}) for _ in range(3)]
        result = _check("loop1", "read_file", {"path": "/tmp/foo"}, history)
        assert result["detected"] is True
        assert result["pattern"] == "loop"
        assert result["should_abort"] is True
        assert result["severity"] == "abort"
        assert "read_file" in result["corrective_message"]

    def test_exact_loop_above_threshold(self):
        """5 identical calls in history → still loop."""
        history = [_rec("exec", {"cmd": "ls /tmp"}) for _ in range(5)]
        result = _check("loop2", "exec", {"cmd": "ls /tmp"}, history)
        assert result["detected"] is True
        assert result["pattern"] == "loop"

    def test_just_below_threshold_no_loop(self):
        """2 identical calls in history → no detection (threshold=3)."""
        history = [_rec("read_file", {"path": "/tmp/foo"}) for _ in range(2)]
        result = _check("loop3", "read_file", {"path": "/tmp/foo"}, history)
        assert result["detected"] is False

    def test_loop_with_interspersed_different_args(self):
        """Mixed args: only 3 identical → detect loop."""
        history = [
            _rec("read_file", {"path": "/tmp/foo"}),
            _rec("read_file", {"path": "/tmp/bar"}),
            _rec("read_file", {"path": "/tmp/foo"}),
            _rec("read_file", {"path": "/tmp/baz"}),
            _rec("read_file", {"path": "/tmp/foo"}),
        ]
        result = _check("loop4", "read_file", {"path": "/tmp/foo"}, history)
        assert result["detected"] is True
        assert result["pattern"] == "loop"

    def test_loop_corrective_message_content(self):
        history = [_rec("web_fetch", {"url": "https://example.com"}) for _ in range(3)]
        result = _check("loop5", "web_fetch", {"url": "https://example.com"}, history)
        msg = result["corrective_message"]
        assert "web_fetch" in msg
        assert "identical" in msg.lower() or "doom loop" in msg.lower()


# ---------------------------------------------------------------------------
# 3. Similar-arg Jaccard detection (true positive + false positive)
# ---------------------------------------------------------------------------

class TestSoftLoop:
    def test_soft_loop_true_positive(self):
        """
        4 calls with highly similar args (Jaccard ~0.9 > 0.85 threshold) → soft_loop.
        Args differ only in one small value.
        """
        base = {"path": "/tmp/foo", "mode": "r", "encoding": "utf-8"}
        # Each record has Jaccard ≈ 2/3 vs base? Let's compute carefully.
        # planned: {path=/tmp/foo, mode=r, encoding=utf-8}
        # history: {path=/tmp/foo, mode=r, encoding=utf-8, offset=N}  → 3/4 = 0.75
        # That's below 0.85. Use args that are actually similar.
        # planned: {path=/tmp/foo, mode=r}
        # history: {path=/tmp/foo, mode=r} → identical → Jaccard = 1.0 > 0.85 ✓
        # But identical args would trigger loop first if count≥3.
        # Use: planned args have 5 keys; history args differ by 1 key value each time.
        planned = {"path": "/tmp/foo", "a": "1", "b": "2", "c": "3", "d": "4"}
        # Slightly different: swap one value → 4/5 tokens match = 0.80... still < 0.85
        # Need Jaccard > 0.85. With 6 tokens: 5 match → 5/7 = 0.71. Not enough.
        # Best approach: use nearly identical args with one extra key.
        # planned = {k1=v1, k2=v2, k3=v3, k4=v4, k5=v5}  → 5 tokens
        # hist    = {k1=v1, k2=v2, k3=v3, k4=v4, k5=v5X} → 1 differs, union=6, inter=4
        # Jaccard = 4/6 = 0.67. Still not > 0.85.
        # Use large matching overlap: 10 matching + 1 differing.
        # planned = {a=1, b=2, c=3, d=4, e=5, f=6, g=7, h=8, i=9, j=10}  10 tokens
        # hist    = {a=1, b=2, c=3, d=4, e=5, f=6, g=7, h=8, i=9, j=X}   9 match, 1 diff
        # intersection=9, union=11, J=9/11=0.818. Still not > 0.85.
        # Use 20 matching + 1 differing: J=20/21=0.952 ✓
        base_args = {str(i): str(i) for i in range(20)}
        planned2 = dict(base_args)
        history2 = []
        for _ in range(4):
            slightly_diff = dict(base_args)
            slightly_diff["0"] = "DIFFERENT"  # 1 token differs
            # intersection=19, union=21, J=19/21 ≈ 0.905 > 0.85 ✓
            history2.append(_rec("exec", slightly_diff))

        result = _check("soft1", "exec", planned2, history2)
        assert result["detected"] is True
        assert result["pattern"] == "soft_loop"
        assert result["severity"] in ("warning", "abort")

    def test_soft_loop_false_positive_avoided(self):
        """
        Args with Jaccard < threshold should NOT trigger soft_loop.
        4 calls but Jaccard ≈ 0.33 (only 1/3 tokens match).
        """
        planned = {"path": "/tmp/foo", "a": "alpha", "b": "beta"}
        # Completely different args except one key
        history = []
        for i in range(4):
            history.append(_rec("exec", {"path": "/tmp/other", "x": str(i), "y": "gamma"}))
        # planned tokens: {path=/tmp/foo, a=alpha, b=beta}
        # history tokens: {path=/tmp/other, x=N, y=gamma} → 0 tokens match
        # Jaccard = 0 < 0.85 → should NOT detect soft_loop
        result = _check("soft2", "exec", planned, history)
        assert result["detected"] is False or result["pattern"] != "soft_loop"

    def test_soft_loop_below_count_threshold(self):
        """Similar args but only 3 times (threshold=4) → no soft_loop."""
        base_args = {str(i): str(i) for i in range(20)}
        planned = dict(base_args)
        history = []
        for _ in range(3):
            diff = dict(base_args)
            diff["0"] = "DIFFERENT"
            history.append(_rec("exec", diff))
        result = _check("soft3", "exec", planned, history)
        # 3 similar < threshold(4) → no soft_loop
        # (might still be no detection)
        if result["detected"]:
            assert result["pattern"] != "soft_loop"

    def test_jaccard_identical_args(self):
        assert jaccard({"a": "1"}, {"a": "1"}) == 1.0

    def test_jaccard_disjoint_args(self):
        assert jaccard({"a": "1"}, {"b": "2"}) == 0.0

    def test_jaccard_partial_overlap(self):
        # {a=1, b=2} vs {a=1, c=3}: intersection={a=1}, union={a=1,b=2,c=3} → 1/3
        score = jaccard({"a": "1", "b": "2"}, {"a": "1", "c": "3"})
        assert abs(score - 1/3) < 0.01

    def test_jaccard_empty_both(self):
        assert jaccard({}, {}) == 1.0

    def test_jaccard_one_empty(self):
        assert jaccard({"a": "1"}, {}) == 0.0


# ---------------------------------------------------------------------------
# 4. Consecutive-error flailing detection
# ---------------------------------------------------------------------------

class TestFlailing:
    def test_flailing_at_threshold(self):
        """3 consecutive errors from same tool in window tail → flailing."""
        history = [
            _rec("web_fetch", {"url": "https://example.com"}, errored=True),
            _rec("web_fetch", {"url": "https://example.com"}, errored=True),
            _rec("web_fetch", {"url": "https://example.com"}, errored=True),
        ]
        result = _check("flail1", "web_fetch", {"url": "https://example.com"}, history)
        assert result["detected"] is True
        assert result["pattern"] == "flailing"
        assert result["should_abort"] is True

    def test_flailing_interrupted_by_success(self):
        """Consecutive error count resets on success."""
        history = [
            _rec("web_fetch", {"url": "https://a.com"}, errored=True),
            _rec("web_fetch", {"url": "https://a.com"}, errored=False),  # success breaks run
            _rec("web_fetch", {"url": "https://a.com"}, errored=True),
            _rec("web_fetch", {"url": "https://a.com"}, errored=True),
        ]
        # Only 2 consecutive errors at the end → no flailing
        result = _check("flail2", "web_fetch", {"url": "https://a.com"}, history)
        assert not (result["detected"] and result["pattern"] == "flailing")

    def test_flailing_different_tool_breaks_run(self):
        """Consecutive errors from different tool don't count for current tool."""
        history = [
            _rec("web_fetch", {"url": "https://a.com"}, errored=True),
            _rec("exec", {"cmd": "ls"}, errored=True),  # different tool
            _rec("web_fetch", {"url": "https://a.com"}, errored=True),
            _rec("web_fetch", {"url": "https://a.com"}, errored=True),
        ]
        # Reversed scan for web_fetch: last 2 are errors but then exec interrupts
        result = _check("flail3", "web_fetch", {"url": "https://a.com"}, history)
        assert not (result["detected"] and result["pattern"] == "flailing")

    def test_flailing_corrective_message(self):
        history = [_rec("exec", {"cmd": "bad"}, errored=True) for _ in range(3)]
        result = _check("flail4", "exec", {"cmd": "bad"}, history)
        assert result["detected"] and result["pattern"] == "flailing"
        msg = result["corrective_message"]
        assert "exec" in msg
        assert "error" in msg.lower() or "flailing" in msg.lower() or "consecutive" in msg.lower()


# ---------------------------------------------------------------------------
# 5. Emergency-abort token-budget trigger
# ---------------------------------------------------------------------------

class TestEmergencyAbort:
    def test_emergency_abort_triggered(self):
        """Context <20% remaining AND last 5 calls errored → emergency_abort."""
        history = [
            _rec("any_tool", {"x": str(i)}, errored=True) for i in range(5)
        ]
        result = _check(
            "emerg1", "any_tool", {"x": "6"},
            history,
            ctx_used=9000, ctx_total=10000,  # 10% remaining < 20% threshold
        )
        assert result["detected"] is True
        assert result["pattern"] == "emergency_abort"
        assert result["should_abort"] is True

    def test_emergency_abort_not_triggered_context_ok(self):
        """Context >20% remaining → emergency_abort not triggered (even with errors)."""
        history = [
            _rec("any_tool", {"x": str(i)}, errored=True) for i in range(5)
        ]
        result = _check(
            "emerg2", "any_tool", {"x": "6"},
            history,
            ctx_used=5000, ctx_total=10000,  # 50% remaining > 20% threshold
        )
        assert not (result["detected"] and result["pattern"] == "emergency_abort")

    def test_emergency_abort_not_triggered_not_all_errored(self):
        """Context low but last 5 calls not all errored → no emergency_abort."""
        history = [
            _rec("any_tool", {"x": "1"}, errored=True),
            _rec("any_tool", {"x": "2"}, errored=True),
            _rec("any_tool", {"x": "3"}, errored=False),  # success
            _rec("any_tool", {"x": "4"}, errored=True),
            _rec("any_tool", {"x": "5"}, errored=True),
        ]
        result = _check(
            "emerg3", "any_tool", {"x": "6"},
            history,
            ctx_used=9000, ctx_total=10000,
        )
        assert not (result["detected"] and result["pattern"] == "emergency_abort")

    def test_emergency_abort_not_triggered_fewer_than_5_calls(self):
        """Only 4 errored calls in history (need 5) → no emergency_abort."""
        history = [
            _rec("any_tool", {"x": str(i)}, errored=True) for i in range(4)
        ]
        result = _check(
            "emerg4", "any_tool", {"x": "5"},
            history,
            ctx_used=9500, ctx_total=10000,
        )
        assert not (result["detected"] and result["pattern"] == "emergency_abort")


# ---------------------------------------------------------------------------
# 6. K-consecutive-detection hard stop
# ---------------------------------------------------------------------------

class TestKConsecutiveDetectionHardStop:
    def test_hard_stop_after_k_detections(self):
        """
        After abort_after_k_detections (=2) fires, severity escalates to abort
        even for soft_loop (which is normally only warning).
        """
        session_id = "kstop1"
        reset_session_counter(session_id)

        # Set up: 4 similar-arg calls for soft_loop (warning severity)
        base_args = {str(i): str(i) for i in range(20)}
        planned = dict(base_args)
        history = []
        for _ in range(4):
            diff = dict(base_args)
            diff["0"] = "DIFFERENT"
            history.append(_rec("exec", diff))

        cfg = dict(_BASE_CFG)

        # First detection — soft_loop → warning, should_abort=False (1 detection so far)
        result1 = check_before_tool_call(
            session_id=session_id,
            planned_tool="exec",
            planned_args=planned,
            recent_history=history,
            _cfg_override=cfg,
        )
        assert result1["detected"] is True
        assert result1["pattern"] == "soft_loop"
        # After first detection: counter=1 < k=2, severity stays warning
        assert result1["severity"] == "warning"
        assert result1["should_abort"] is False

        # Second detection — counter hits k=2 → hard stop
        result2 = check_before_tool_call(
            session_id=session_id,
            planned_tool="exec",
            planned_args=planned,
            recent_history=history,
            _cfg_override=cfg,
        )
        assert result2["detected"] is True
        assert result2["should_abort"] is True
        assert result2["severity"] == "abort"
        assert "hard stop" in result2["corrective_message"].lower() or \
               "abort" in result2["corrective_message"].lower()

    def test_reset_counter_clears_k_count(self):
        """reset_session_counter allows detections to start fresh."""
        session_id = "kstop2"
        reset_session_counter(session_id)

        base_args = {str(i): str(i) for i in range(20)}
        planned = dict(base_args)
        history = []
        for _ in range(4):
            diff = dict(base_args)
            diff["0"] = "DIFFERENT"
            history.append(_rec("exec", diff))

        # Trigger twice to hit k=2
        for _ in range(2):
            check_before_tool_call(
                session_id=session_id,
                planned_tool="exec",
                planned_args=planned,
                recent_history=history,
                _cfg_override=dict(_BASE_CFG),
            )

        # Reset and verify next check goes back to normal severity
        reset_session_counter(session_id)
        result = check_before_tool_call(
            session_id=session_id,
            planned_tool="exec",
            planned_args=planned,
            recent_history=history,
            _cfg_override=dict(_BASE_CFG),
        )
        assert result["detected"] is True
        # Counter was reset so this is the first detection again
        assert result["severity"] == "warning"  # soft_loop = warning
        assert result["should_abort"] is False


# ---------------------------------------------------------------------------
# 7. ToolCallRecord.from_dict parsing
# ---------------------------------------------------------------------------

class TestToolCallRecordParsing:
    def test_from_dict_standard(self):
        r = ToolCallRecord.from_dict({"tool": "read_file", "args": {"path": "/tmp"}, "errored": True})
        assert r.tool == "read_file"
        assert r.args == {"path": "/tmp"}
        assert r.errored is True

    def test_from_dict_alternate_keys(self):
        r = ToolCallRecord.from_dict({
            "tool_name": "exec",
            "arguments": {"cmd": "ls"},
            "error": "some error",
        })
        assert r.tool == "exec"
        assert r.args == {"cmd": "ls"}
        assert r.errored is True

    def test_from_dict_input_key(self):
        r = ToolCallRecord.from_dict({"tool": "browse", "input": {"url": "https://x.com"}})
        assert r.args == {"url": "https://x.com"}
        assert r.errored is False

    def test_from_dict_is_error_key(self):
        r = ToolCallRecord.from_dict({"tool": "t", "args": {}, "is_error": True})
        assert r.errored is True


# ---------------------------------------------------------------------------
# 8. Priority order: emergency_abort > flailing > loop > soft_loop
# ---------------------------------------------------------------------------

class TestDetectionPriority:
    def test_emergency_abort_beats_loop(self):
        """When both emergency_abort and loop conditions are met, emergency_abort wins."""
        history = [
            _rec("read_file", {"path": "/tmp/a"}, errored=True),
            _rec("read_file", {"path": "/tmp/a"}, errored=True),
            _rec("read_file", {"path": "/tmp/a"}, errored=True),
            _rec("read_file", {"path": "/tmp/a"}, errored=True),
            _rec("read_file", {"path": "/tmp/a"}, errored=True),
        ]
        # 5 identical calls (loop) + 5 consecutive errors (flailing) + low context (emergency)
        result = _check(
            "prio1", "read_file", {"path": "/tmp/a"},
            history,
            ctx_used=9500, ctx_total=10000,
        )
        assert result["detected"] is True
        assert result["pattern"] == "emergency_abort"

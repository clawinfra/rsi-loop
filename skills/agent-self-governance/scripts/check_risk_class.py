#!/usr/bin/env python3
"""
check_risk_class.py — CLI tool to determine the approval level for a given tool.

Usage:
    python check_risk_class.py <tool_name> [--action <action>] [--amount <usd>]
                               [--context <main|sub>] [--session-key <key>]
                               [--platform <platform_name>]

Exit codes:
    0 — ALLOW   (auto-approved)
    1 — PROMPT  (ask the user, non-blocking)
    2 — CONFIRM (require explicit confirmation before proceeding)

Outputs one of ALLOW / PROMPT / CONFIRM to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent          # skills/agent-self-governance/
RISK_CLASSES_FILE = SKILL_DIR / "risk-classes.json"
RISK_POLICY_FILE = SKILL_DIR / "risk-policy.json"
PLATFORM_POLICY_DIR = SKILL_DIR / "platform-policies"

# ---------------------------------------------------------------------------
# Output constants
# ---------------------------------------------------------------------------

ALLOW = ("ALLOW", 0)
PROMPT = ("PROMPT", 1)
CONFIRM = ("CONFIRM", 2)

_LEVEL_MAP = {
    "auto": ALLOW,
    "prompt": PROMPT,
    "confirm": CONFIRM,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_json(path: Path) -> dict:
    try:
        with path.open() as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        print(f"[check_risk_class] WARNING: could not parse {path}: {exc}", file=sys.stderr)
        return {}


def resolve_risk_class(
    tool: str,
    action: Optional[str],
    risk_classes: dict,
) -> Optional[str]:
    """
    Return the first matching risk class for (tool, action), respecting
    classification_precedence order (highest risk first).
    """
    classes = risk_classes.get("classes", {})
    precedence = risk_classes.get(
        "classification_precedence",
        ["financial", "destructive", "external_write", "mutate", "read"],
    )

    for class_name in precedence:
        cls = classes.get(class_name, {})

        # Direct tool match
        if tool in cls.get("tools", []):
            return class_name

        # Pattern matches
        for pattern in cls.get("tool_patterns", []):
            if pattern.get("tool") != tool:
                continue
            # Action-based patterns
            pattern_actions = pattern.get("actions")
            if pattern_actions is not None:
                if action and action in pattern_actions:
                    return class_name
            else:
                # No action constraint — matches any invocation of this tool
                return class_name

    return None


def resolve_platform_policy(platform: Optional[str]) -> tuple[str, int]:
    """
    Look up a per-platform policy JSON file.
    Returns (output, exit_code).
    Falls back to CONFIRM if the file is missing or malformed.
    """
    if not platform:
        return CONFIRM

    policy_file = PLATFORM_POLICY_DIR / f"{platform}.json"
    data = load_json(policy_file)
    if not data:
        # Graceful fallback
        return CONFIRM

    level = data.get("level", "confirm").lower()
    return _LEVEL_MAP.get(level, CONFIRM)


def apply_session_key_override(
    session_key: Optional[str],
    risk_class: str,
    policy_data: dict,
    base_result: tuple[str, int],
    hardcoded_confirms: list[str],
) -> tuple[str, int]:
    """
    Apply session-key override if present, unless the class is hardcoded-confirm.
    """
    if risk_class in hardcoded_confirms:
        return base_result  # Cannot be overridden

    if not session_key:
        # Also check environment variable
        session_key = os.environ.get(
            policy_data.get("session_key_overrides", {}).get("env_var", "RSI_SESSION_KEY")
        )

    if not session_key:
        return base_result

    overrides_schema = policy_data.get("session_key_overrides", {})
    # In a real deployment the session-key would be looked up in a secure store.
    # Here we load from an optional override file for testability.
    override_file = SKILL_DIR / "session-overrides.json"
    overrides_data = load_json(override_file)

    session_entry = overrides_data.get(session_key)
    if not session_entry:
        return base_result

    class_override = session_entry.get("overrides", {}).get(risk_class)
    if not class_override:
        return base_result

    # Check expiry
    expires_at = class_override.get("expires_at")
    if expires_at:
        from datetime import datetime, timezone
        try:
            expiry = datetime.fromisoformat(expires_at)
            now = datetime.now(timezone.utc)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if now > expiry:
                print(
                    f"[check_risk_class] Session key override for '{risk_class}' has expired.",
                    file=sys.stderr,
                )
                return base_result
        except ValueError:
            pass

    level = class_override.get("level", "").lower()
    return _LEVEL_MAP.get(level, base_result)


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def determine_result(
    tool: str,
    action: Optional[str],
    amount_usd: Optional[float],
    context: str,
    session_key: Optional[str],
    platform: Optional[str],
) -> tuple[str, int]:
    risk_classes = load_json(RISK_CLASSES_FILE)
    policy_data = load_json(RISK_POLICY_FILE)

    policies = policy_data.get("policies", {})
    hardcoded_confirms = policy_data.get("hardcoded_confirms", ["destructive"])

    # 1. Resolve risk class
    risk_class = resolve_risk_class(tool, action, risk_classes)

    if risk_class is None:
        # Unknown tool — default to PROMPT (conservative but not blocking)
        print(
            f"[check_risk_class] WARNING: tool '{tool}' not found in risk-classes.json; defaulting to PROMPT.",
            file=sys.stderr,
        )
        return apply_session_key_override(
            session_key, "unknown", policy_data, PROMPT, hardcoded_confirms
        )

    # 2. Get class policy
    class_policy = policies.get(risk_class, {})
    level = class_policy.get("level", "confirm").lower()

    # 3. Class-specific resolution
    if risk_class == "read":
        base = ALLOW

    elif risk_class == "mutate":
        if level == "prompt":
            # context-sensitive
            context_rules = class_policy.get("context_rules", {})
            effective = context_rules.get(context, "prompt").lower()
            base = _LEVEL_MAP.get(effective, PROMPT)
        else:
            base = _LEVEL_MAP.get(level, PROMPT)

    elif risk_class == "destructive":
        # Hardcoded — always CONFIRM, no override possible
        return CONFIRM

    elif risk_class == "external_write":
        platform_result = resolve_platform_policy(platform)
        base = platform_result

    elif risk_class == "financial":
        if amount_usd is not None:
            threshold = class_policy.get("auto_threshold_usd", 500)
            if amount_usd < threshold:
                base = ALLOW
            else:
                base = CONFIRM
        else:
            # No amount provided — default to CONFIRM for safety
            base = CONFIRM

    else:
        base = _LEVEL_MAP.get(level, CONFIRM)

    # 4. Apply session-key overrides (destructive is skipped above)
    return apply_session_key_override(
        session_key, risk_class, policy_data, base, hardcoded_confirms
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Determine the approval level for a tool invocation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("tool", help="Tool name (e.g. exec, write, message)")
    p.add_argument(
        "--action",
        default=None,
        help="Sub-action for the tool (e.g. send, edit, delete, kill)",
    )
    p.add_argument(
        "--amount",
        type=float,
        default=None,
        metavar="USD",
        help="Transaction amount in USD (relevant for financial class)",
    )
    p.add_argument(
        "--context",
        choices=["main", "sub"],
        default="sub",
        help="Agent context: 'main' (top-level) or 'sub' (sub-agent). Default: sub",
    )
    p.add_argument(
        "--session-key",
        default=None,
        help="Session key for per-session policy overrides (also read from RSI_SESSION_KEY env var)",
    )
    p.add_argument(
        "--platform",
        default=None,
        help="Platform name for external_write tools (e.g. whatsapp, slack, email)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output JSON with full details instead of plain text",
    )
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    output, exit_code = determine_result(
        tool=args.tool,
        action=args.action,
        amount_usd=args.amount,
        context=args.context,
        session_key=args.session_key,
        platform=args.platform,
    )

    if args.json_output:
        import json as _json
        result = {
            "tool": args.tool,
            "action": args.action,
            "context": args.context,
            "platform": args.platform,
            "amount_usd": args.amount,
            "result": output,
            "exit_code": exit_code,
        }
        print(_json.dumps(result, indent=2))
    else:
        print(output)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
```
---
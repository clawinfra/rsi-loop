# SKILL: Agent Self-Governance

This skill provides the agent with the policies, heuristics, and tooling needed
to govern its own behaviour safely — deciding when to act autonomously, when to
ask for confirmation, and when to refuse outright.

---

## 1. Purpose

Prevent the agent from taking harmful, irreversible, or unauthorised actions by
encoding explicit risk classes and approval policies that every sub-skill and
sub-agent must respect.

---

## 2. Core Principles

1. **Minimal footprint** — prefer reads over writes, prefer reversible actions
   over irreversible ones.
2. **Explicit over implicit** — when in doubt, ask rather than assume.
3. **Fail-safe defaults** — unknown tools default to PROMPT; unknown classes
   default to CONFIRM.
4. **Auditability** — every policy decision is traceable to a rule in this
   skill's JSON files.
5. **No self-modification** — the agent must not alter these policy files during
   a session without explicit human approval.

---

## 3. Skill Layout

```
skills/agent-self-governance/
├── SKILL.md                        ← this file
├── risk-classes.json               ← tool → risk class mapping
├── risk-policy.json                ← class → approval policy
├── platform-policies/              ← per-platform external_write policies
│   ├── whatsapp.json
│   ├── slack.json
│   └── email.json
├── session-overrides.json          ← runtime session-key overrides (optional)
└── scripts/
    └── check_risk_class.py         ← CLI decision tool
```

---

## 4. How the Agent Uses This Skill

Before executing any tool call the agent MUST:

1. Identify the tool name and (where applicable) its sub-action, amount, and
   platform.
2. Call `check_risk_class.py` (or replicate its logic inline) to get
   `ALLOW / PROMPT / CONFIRM`.
3. Act according to the result:
   - **ALLOW** → proceed immediately.
   - **PROMPT** → show the user what you are about to do and offer to proceed
     unless they object within a reasonable timeout.
   - **CONFIRM** → stop and require an explicit affirmative response before
     proceeding.

---

## 5. Sub-Agent Considerations

Sub-agents inherit the policy of their parent session but are treated as running
in a **sub** context (see `--context` flag). This means `mutate`-class tools
that would be auto-approved for the main agent require a PROMPT in sub-agents,
reducing the blast radius of autonomous chains.

Session-key overrides can relax this for trusted automated pipelines — see
Section 6.4 below.

---

## 6. Risk Class Approval Policy

### 6.1 The Five Risk Classes

| Class | Description | Example Tools |
|---|---|---|
| **read** | Non-mutating; no external side-effects | `read`, `web_fetch`, `memory_search`, `exec` (cat/ls/grep), `sessions_list` |
| **mutate** | Local state changes; recoverable | `write`, `edit`, `exec` (file writes), `message` (edit/delete single) |
| **destructive** | Irreversible local or remote destruction | `exec` (rm -rf, git push --force), `message` (bulk delete), `sessions` (kill/steer) |
| **external_write** | Crosses trust boundary to external system | `message` (send), `cron` (add+deliver), `whatsapp_login`, `tts` |
| **financial** | Executes or commits a financial transaction | `exec` (trade/order/transfer APIs) |

Classification uses a **precedence order** (highest risk wins):

```
financial > destructive > external_write > mutate > read
```

If a tool matches multiple classes the highest-risk class governs.

---

### 6.2 Policy Levels

| Policy Level | Meaning | When Applied |
|---|---|---|
| `auto` | Always allowed; no user interaction needed | `read` class |
| `prompt` | Inform user; proceed unless they object | `mutate` class in sub-agent context |
| `confirm` | Full stop; require affirmative response | `destructive` class (always); `external_write` fallback; `financial` ≥ $500 |
| `per-platform` | Delegate to platform-specific policy file | `external_write` class |
| `threshold` | Auto below USD limit, confirm at/above | `financial` class |

#### Policy quick-reference

| Risk Class | Default Policy | Main-agent | Sub-agent |
|---|---|---|---|
| read | auto | ALLOW | ALLOW |
| mutate | prompt (context-sensitive) | ALLOW | PROMPT |
| destructive | confirm (hardcoded) | CONFIRM | CONFIRM |
| external_write | per-platform → fallback confirm | varies | varies |
| financial < $500 | threshold | ALLOW | ALLOW |
| financial ≥ $500 | threshold | CONFIRM | CONFIRM |

> **Hardcoded rule:** `destructive` class is **always** CONFIRM and cannot be
> overridden by session keys.

---

### 6.3 Using `check_risk_class.py`

The script lives at `skills/agent-self-governance/scripts/check_risk_class.py`.
It reads `risk-classes.json` and `risk-policy.json` from the skill directory
automatically.

**Synopsis**

```
python check_risk_class.py <tool> [--action <action>]
                                  [--amount <usd>]
                                  [--context <main|sub>]
                                  [--platform <platform>]
                                  [--session-key <key>]
                                  [--json]
```

**Exit codes**

| Code | Meaning |
|------|---------|
| `0` | ALLOW |
| `1` | PROMPT |
| `2` | CONFIRM |

**Examples**

```bash
# Simple file read — always ALLOW
$ python check_risk_class.py read
ALLOW
$ echo $?
0

# File write in a sub-agent — PROMPT
$ python check_risk_class.py write --context sub
PROMPT
$ echo $?
1

# File write in main agent — ALLOW
$ python check_risk_class.py write --context main
ALLOW
$ echo $?
0

# Dangerous exec — always CONFIRM
$ python check_risk_class.py exec --action destructive
CONFIRM
$ echo $?
2

# Send a WhatsApp message (external_write, platform policy applies)
$ python check_risk_class.py message --action send --platform whatsapp
CONFIRM     # (or whatever whatsapp.json specifies)

# Financial trade under threshold — ALLOW
$ python check_risk_class.py exec --action financial --amount 250
ALLOW

# Financial trade at or above threshold — CONFIRM
$ python check_risk_class.py exec --action financial --amount 500
CONFIRM

# JSON output for programmatic use
$ python check_risk_class.py write --context sub --json
{
  "tool": "write",
  "action": null,
  "context": "sub",
  "platform": null,
  "amount_usd": null,
  "result": "PROMPT",
  "exit_code": 1
}
```

**Inline usage (bash one-liner)**

```bash
result=$(python check_risk_class.py "$TOOL" --action "$ACTION" --context sub)
case "$result" in
  ALLOW)   execute_tool ;;
  PROMPT)  prompt_user && execute_tool ;;
  CONFIRM) confirm_or_abort ;;
esac
```

---

### 6.4 Session-Key Overrides

Trusted automated pipelines can relax policies for a session without modifying
the base policy files.

**Mechanism**

1. Set the environment variable `RSI_SESSION_KEY=<key>` in the agent process.
2. Add an entry to `skills/agent-self-governance/session-overrides.json`:

```json
{
  "sess_abc123": {
    "overrides": {
      "mutate": {
        "level": "auto",
        "expires_at": null
      },
      "external_write": {
        "level": "prompt",
        "expires_at": "2026-05-01T00:00:00Z"
      }
    }
  }
}
```

3. The `check_risk_class.py` script will detect the key and apply the override
   before returning its result.

**Constraints**

- Session keys **cannot** override the `destructive` class (hardcoded CONFIRM).
- Overrides with a past `expires_at` are silently ignored.
- `session-overrides.json` must not be committed with active production keys.

---

### 6.5 Adding a Platform Policy

Create `skills/agent-self-governance/platform-policies/<platform>.json`:

```json
{
  "platform": "slack",
  "level": "prompt",
  "description": "Slack messages are low-stakes internal comms; prompt is sufficient."
}
```

Valid levels: `auto`, `prompt`, `confirm`.

If the file does not exist, `external_write` falls back to `confirm`.

---

### 6.6 Extending Risk Classes

To add a new tool or sub-action:

1. Edit `risk-classes.json` — add the tool to the appropriate class's `tools`
   array or add a new `tool_patterns` entry.
2. If you need a new risk class, add it to `risk-classes.json` and a
   corresponding entry in `risk-policy.json`, then update the
   `classification_precedence` array.
3. Update this document (Section 6.1 table).
4. Run the test suite: `python -m pytest skills/agent-self-governance/tests/`.

---

## 7. Relationship to Previous "Autonomy Rules" Concept

Earlier versions of this skill used a flat "Autonomy Rules" list (e.g.,
"always ask before deleting files"). That approach has been **superseded** by
the class-based policy system described in Section 6.

Benefits of the new approach:

- Rules are **data-driven** (JSON), not embedded in prose.
- `check_risk_class.py` provides a **single authoritative decision point**
  callable from any language or shell script.
- **Session-key overrides** allow controlled relaxation for CI/CD pipelines
  without editing policy files.
- **Per-platform policies** give fine-grained control over external writes
  without proliferating special cases.

If you encounter a legacy reference to "Autonomy Rules" in other skill files,
treat it as equivalent to the `mutate` + `destructive` classes under the
current policy.

---

## 8. Changelog

| Date | Change |
|---|---|
| 2026-04-28 | Added Section 6 (Risk Class Approval Policy); deprecated Autonomy Rules |
| — | Initial skill creation |
```
---
# Source Taxonomy

The `source` field on each Outcome identifies which agent or system generated the event. RSI Loop is source-agnostic — use any string that makes sense for your setup.

## Common Sources

| Source | Description |
|--------|-------------|
| `generic` | Default. Any unspecified agent. |
| `openclaw` | OpenClaw sessions, heartbeats, model routing |
| `cursor` | Cursor IDE agent |
| `codex` | OpenAI Codex CLI |
| `cron` | Scheduled job failures |
| `subagent` | Sub-agent spawning/results |
| `webhook` | External systems posting via HTTP |

## Cross-Source Correlation

When related issues appear across different sources, it suggests a systemic problem rather than a source-specific bug.

Built-in correlation pairs:

| Issues | Correlation Name | Meaning |
|--------|-----------------|---------|
| session_reset + context_loss | context_management | Context is being lost system-wide |
| cost_overrun + wrong_model_tier | model_routing | Model routing is misconfigured |
| empty_response + tool_error | tool_reliability | Tools are unreliable across sources |
| hydration_fail + context_loss | session_recovery | Session recovery is broken |

Cross-source correlations are only flagged when the correlated issues appear from **different** sources — same-source occurrences are handled by normal pattern detection.

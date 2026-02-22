# Claude Code / OpenClaw Setup

Add RSI Loop to your Claude Code or OpenClaw agent for automatic self-improvement.

## Install

```bash
uv add rsi-loop
```

## Integration

Add to your agent's workspace (e.g., in a skill or startup script):

```python
from rsi_loop.integrations.claude_code import ClaudeCodeAdapter

adapter = ClaudeCodeAdapter(data_dir="./rsi_data")

# After each task
adapter.on_task_complete("code_review", success=True, model="sonnet-4.6", quality=4)

# On failures
adapter.on_task_complete("api_call", success=False, error="429 Too Many Requests")

# On session resets
adapter.on_session_reset(notes="Context overflow after 200k tokens")

# On model fallbacks
adapter.on_model_fallback("opus-4", "sonnet-4.6", "rate_limit")

# On cron failures
adapter.on_cron_failure("daily-check", "Script timeout")

# On sub-agent failures
adapter.on_subagent_failure("code-reviewer", "OOM killed", model="llama-70b")
```

## Periodic Analysis

Run improvement cycles in your heartbeat or cron:

```python
# In heartbeat handler
patterns = adapter.run_cycle()
if patterns:
    print(f"Health: {adapter.health_score():.0%}")
    for p in patterns:
        print(f"  [{p.category}] {p.description} → {p.suggested_action}")
```

## AGENTS.md Integration

Add to your `AGENTS.md`:

```markdown
## RSI Loop
- Record outcomes after significant tasks
- Run `adapter.run_cycle()` during heartbeats
- Check `rsi_data/proposals/` for fix suggestions
```

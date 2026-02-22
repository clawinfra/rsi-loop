# Auto-Fix

RSI Loop can automatically apply fixes for "safe" categories — changes that are low-risk and well-understood.

## Safe Categories

By default, three fix types are considered safe for auto-application:

| Category | Description | Example |
|----------|-------------|---------|
| `routing_config` | Model routing changes | Adjusting fallback chains |
| `threshold_tuning` | Timeout/threshold adjustments | Increasing request timeouts |
| `retry_logic` | Adding/adjusting retry behavior | Backoff for rate-limited endpoints |

Configure via `Config.safe_categories`:

```python
from rsi_loop import RSILoop, Config

loop = RSILoop(Config(
    safe_categories=["routing_config", "retry_logic", "threshold_tuning", "investigation"]
))
```

## Fix Templates

Each issue type maps to a fix template:

| Issue | Fix Type | Action |
|-------|----------|--------|
| rate_limit | retry_logic | Add/increase retry backoff |
| model_fallback | routing_config | Update fallback chain |
| wrong_model_tier | routing_config | Adjust tier thresholds |
| cost_overrun | routing_config | Lower cost ceiling |
| slow_response | threshold_tuning | Increase timeouts |
| timeout | threshold_tuning | Increase timeouts |
| empty_response | retry_logic | Add empty-response detection and retry |
| session_reset | investigation | Investigate (not auto-fixable by default) |

## How It Works

1. `Fixer.propose(pattern)` generates a `Fix` with changes and a `safe_category`
2. `Fixer.apply_if_safe(fix)` checks if `safe_category` is in `Config.safe_categories`
3. If safe → status set to "applied", proposal saved
4. If not safe → status stays "draft", proposal saved for human review

**Important**: Even "applied" fixes only update their status in the proposal file. Actual code/config changes require agent-specific implementation — RSI Loop tells you *what* to fix, your agent decides *how*.

## Proposals

All fix proposals are saved as JSON files in `{data_dir}/proposals/`:

```json
{
  "id": "abc12345",
  "pattern_id": "api_call-rate_lim-5",
  "type": "auto",
  "status": "applied",
  "target": "relevant integration code",
  "changes": [
    {
      "target": "relevant integration code",
      "action": "Add retry with backoff for 'rate_limit' errors",
      "detail": "Detected 5x occurrences"
    }
  ],
  "safe_category": "retry_logic",
  "description": "Add/increase retry backoff for rate-limited endpoints"
}
```

## Disabling Auto-Fix

```python
loop = RSILoop(Config(auto_fix_enabled=False))
# Or set safe_categories to empty:
loop = RSILoop(Config(safe_categories=[]))
```

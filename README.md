# 🔄 RSI Loop

[![CI](https://github.com/clawinfra/rsi-loop/actions/workflows/ci.yml/badge.svg)](https://github.com/clawinfra/rsi-loop/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)

**Every AI agent makes mistakes. RSI Loop makes them learn.**

---

## The Problem

AI agents repeat the same failures. They hit rate limits, return empty responses, lose context, pick the wrong model — and do it all again next session. There's no feedback loop. No memory of what went wrong. No automatic improvement.

RSI Loop is the missing primitive: a universal **recursive self-improvement** loop that works with *any* agent framework.

## How It Works

```
    ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
    │ OBSERVE  │────▶│ ANALYZE  │────▶│   FIX    │────▶│  VERIFY  │
    │          │     │          │     │          │     │          │
    │ Record   │     │ Detect   │     │ Generate │     │ Check    │
    │ outcomes │     │ patterns │     │ & apply  │     │ health   │
    └──────────┘     └──────────┘     └──────────┘     └────┬─────┘
         ▲                                                   │
         └───────────────────────────────────────────────────┘
```

1. **Observe** — Record task outcomes: success/failure, quality, errors, model used, duration
2. **Analyze** — Detect patterns: recurring failures, error clusters, cross-source correlations
3. **Fix** — Auto-fix safe categories (routing, retries, thresholds); propose fixes for the rest
4. **Verify** — Track health score over time; confirm fixes reduced failure rates

## Quick Start

```bash
uv add rsi-loop
```

> **Not using uv?** `pip install rsi-loop` works too.

```python
from rsi_loop import RSILoop

loop = RSILoop()
loop.observer.record_simple("code_generation", success=True, model="sonnet-4.6")
loop.observer.record_simple("api_call", success=False, error="429 Too Many Requests")
loop.observer.record_simple("api_call", success=False, error="429 Too Many Requests")
loop.observer.record_simple("api_call", success=False, error="429 Too Many Requests")

# Run improvement cycle
patterns = loop.run_cycle()
print(f"Health: {loop.health_score():.0%}")
print(f"Patterns found: {len(patterns)}")
```

## Framework Integrations

### Claude Code / OpenClaw

```python
from rsi_loop.integrations.claude_code import ClaudeCodeAdapter

adapter = ClaudeCodeAdapter(data_dir="./rsi_data")
adapter.on_task_complete("code_review", success=True, model="claude-sonnet-4")
adapter.on_model_fallback("claude-opus-4", "claude-sonnet-4", "rate_limit")
adapter.on_session_reset()
```

### Generic (File-Based)

Drop JSON files into a watch directory — works with any agent:

```python
from rsi_loop.integrations.generic import GenericAdapter

adapter = GenericAdapter(watch_dir="./rsi_inbox")
# Your agent writes: {"task": "search", "success": false, "error": "timeout"}
# RSI picks it up automatically
outcomes = adapter.poll()
```

### Webhook

```python
from rsi_loop.integrations.webhook import WebhookAdapter

app = WebhookAdapter(data_dir="./rsi_data").create_app()
# POST /observe {"task": "code_gen", "success": true, "quality": 4}
# GET /health
# GET /patterns
```

## Features

- **Auto-classification** — Categorizes errors automatically: rate_limit, empty_response, timeout, context_loss, etc.
- **Recurrence detection** — Flags issues that repeat 3+ times within the analysis window
- **Health scoring** — 0.0 (broken) to 1.0 (healthy), recency-weighted
- **Cross-source correlation** — Detects related issues across different agent sources
- **Error clustering** — Groups similar error messages even with different IDs/numbers
- **Safe auto-fix** — Automatically applies fixes for safe categories (routing, retries, thresholds)
- **Fix proposals** — Generates detailed proposals for unsafe categories, saved for human review
- **Background loop** — Run continuous improvement cycles in a background thread
- **Framework-agnostic** — Works with Claude Code, Cursor, Codex, or any custom agent
- **Zero dependencies** — Core package has no external dependencies (integrations optional)

## Documentation

- [Architecture](docs/architecture.md) — How RSI Loop works internally
- [Source Taxonomy](docs/sources.md) — Source classification and cross-source correlation
- [Auto-Fix](docs/auto_fix.md) — How auto-fix works, safe categories, and fix templates

## Examples

- [Claude Code Setup](examples/claude_code_setup.md)
- [Cursor Setup](examples/cursor_setup.md)
- [Generic Agent Setup](examples/generic_agent_setup.md)
- [Quick Start Script](examples/quick_start.py)

---

Built by [ClawInfra](https://github.com/clawinfra) — infrastructure for autonomous agents.

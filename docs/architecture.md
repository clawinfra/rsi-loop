# Architecture

RSI Loop implements a four-phase recursive self-improvement cycle.

## Components

### Observer
Records task outcomes to a JSONL store. Each outcome captures: task type, success/failure, quality (1-5), error messages, model used, duration, and arbitrary tags/metadata.

**Auto-classification**: Error messages are automatically classified into issue types (rate_limit, timeout, empty_response, context_loss, etc.) using keyword matching.

**Storage**: Outcomes are appended to `{data_dir}/outcomes.jsonl` — one JSON object per line.

### Analyzer
Scans recorded outcomes and detects improvement patterns.

**Pattern detection** works two ways:
1. **Task/issue grouping** — Groups outcomes by `(task_type, issue)` pairs. Patterns with ≥2 occurrences (or ≥1 for high-severity issues) are surfaced.
2. **Error clustering** — Normalizes error messages (strips IDs, numbers) and groups similar errors. Catches patterns that span multiple task types.

**Health score**: `success_rate × (avg_quality / 5.0)`, computed over the analysis window. Range: 0.0 (broken) to 1.0 (healthy).

**Cross-source correlation**: Detects when related issues (e.g., session_reset + context_loss) appear across different agent sources, suggesting systemic problems.

**Recurrence detection**: Compares current patterns against the previous analysis cycle. Recurring patterns are flagged with trend direction (increasing/stable).

### Fixer
Generates fix proposals for detected patterns.

**Safe categories** (configurable, default: routing_config, threshold_tuning, retry_logic) get auto-applied status. Everything else produces a draft proposal saved to `{data_dir}/proposals/` for human review.

### RSILoop
The main orchestrator. Ties Observer, Analyzer, and Fixer together. Provides:
- `run_cycle()` — One full observe → analyze → fix pass
- `health_score()` — Current health
- `start_background(interval)` — Background improvement loop in a daemon thread

## Data Flow

```
Agent task completes
    │
    ▼
Observer.record_simple(task, success, error, model)
    │ auto-classify issues from error message
    │ append to outcomes.jsonl
    ▼
Analyzer.analyze()
    │ load outcomes from window
    │ group by (task_type, issue)
    │ cluster similar errors
    │ detect recurrences
    │ compute health score
    ▼
Fixer.propose_and_apply(pattern)
    │ generate fix proposal
    │ auto-apply if safe category
    │ save proposal to proposals/
    ▼
Health score improves (or doesn't → next cycle catches it)
```

## Storage Layout

```
{data_dir}/
├── outcomes.jsonl    # Raw outcome records (append-only)
├── patterns.json     # Latest analysis results (overwritten each cycle)
└── proposals/        # Fix proposals (one JSON file per fix)
    ├── abc12345.json
    └── def67890.json
```

#!/usr/bin/env python3
"""Quick start example for RSI Loop.

Run with: uv run python examples/quick_start.py
"""

from rsi_loop import RSILoop

# Create a loop (defaults to ./rsi_data for storage)
loop = RSILoop()

# Record some outcomes as your agent works
loop.observer.record_simple("code_generation", success=True, model="sonnet-4.6", quality=5)
loop.observer.record_simple("code_generation", success=True, model="sonnet-4.6", quality=4)
loop.observer.record_simple("api_call", success=False, error="429 Too Many Requests")
loop.observer.record_simple("api_call", success=False, error="429 Too Many Requests")
loop.observer.record_simple("api_call", success=False, error="429 Too Many Requests")
loop.observer.record_simple("file_search", success=False, error="Request timed out after 30s")

# Run an improvement cycle
patterns = loop.run_cycle()

# Print results
print(f"Health score: {loop.health_score():.0%}")
print(f"Patterns detected: {len(patterns)}")
for p in patterns:
    print(f"  [{p.category}] {p.description}")
    print(f"    Impact: {p.impact_score:.4f} | Action: {p.suggested_action}")

# Check recurring issues
recurrences = loop.observer.recurrences(threshold=2)
if recurrences:
    print(f"\nRecurring issues: {recurrences}")

# Check fixes
fixes = loop.fixes()
if fixes:
    print(f"\nFix proposals: {len(fixes)}")
    for f in fixes:
        print(f"  [{f.status}] {f.description}")

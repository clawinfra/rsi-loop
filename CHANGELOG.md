# Changelog

## [0.1.0] - 2026-02-22

### Added
- Initial release
- Core modules: Observer, Analyzer, Fixer, RSILoop
- Type definitions: Outcome, Pattern, Fix, Config
- Auto-classification of error messages
- Recurrence detection (3+ occurrences in window)
- Health scoring (0.0–1.0, recency-weighted)
- Cross-source correlation for related issues
- Error message clustering with normalization
- Safe auto-fix for configurable categories
- Fix proposal generation for unsafe categories
- Background loop with configurable interval
- Integrations: Claude Code adapter, generic file-based adapter, webhook adapter
- Full test suite with 85%+ coverage
- CI with Python 3.10/3.11/3.12, pytest, ruff

"""Generic file-based adapter — works with any agent."""

from __future__ import annotations

import json
from pathlib import Path

from rsi_loop.loop import RSILoop
from rsi_loop.types import Config, Outcome


class GenericAdapter:
    """File-based adapter. Drop JSON files into a watch directory and RSI picks them up.

    Any agent can write a JSON file like::

        {"task": "search", "success": false, "error": "timeout after 30s"}

    Then call ``adapter.poll()`` to ingest new files.

    Usage::

        adapter = GenericAdapter(watch_dir="./rsi_inbox")
        # Agent drops JSON files into ./rsi_inbox/
        outcomes = adapter.poll()
    """

    def __init__(
        self,
        watch_dir: str = "./rsi_inbox",
        data_dir: str = "./rsi_data",
        **config_kwargs,
    ) -> None:
        self._watch_dir = Path(watch_dir)
        self.loop = RSILoop(Config(data_dir=data_dir, **config_kwargs))
        self._processed_dir = self._watch_dir / ".processed"

    def poll(self) -> list[Outcome]:
        """Scan watch_dir for new JSON files, ingest them, move to .processed/."""
        if not self._watch_dir.exists():
            return []

        self._processed_dir.mkdir(parents=True, exist_ok=True)
        outcomes: list[Outcome] = []

        for path in sorted(self._watch_dir.glob("*.json")):
            if path.name.startswith("."):
                continue
            try:
                with open(path) as f:
                    data = json.load(f)
                outcome = Outcome.from_dict(data)
                self.loop.observer.record(outcome)
                outcomes.append(outcome)
                path.rename(self._processed_dir / path.name)
            except (json.JSONDecodeError, OSError):
                continue

        return outcomes

    def run_cycle(self):
        """Poll + analyze + fix."""
        self.poll()
        return self.loop.run_cycle()

    def health_score(self) -> float:
        return self.loop.health_score()

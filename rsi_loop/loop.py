"""RSILoop — The main recursive self-improvement loop."""

from __future__ import annotations

import threading

from rsi_loop.analyzer import Analyzer
from rsi_loop.fixer import Fixer
from rsi_loop.observer import Observer
from rsi_loop.types import Config, Fix, Pattern


class RSILoop:
    """Universal self-improvement loop: observe → analyze → fix → verify.

    Usage::

        loop = RSILoop()
        loop.observer.record_simple("code_gen", success=True, model="sonnet-4.6")
        patterns = loop.run_cycle()
        print(loop.health_score())
    """

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self.observer = Observer(self.config)
        self.analyzer = Analyzer(self.config, observer=self.observer)
        self.fixer = Fixer(self.config)
        self._background_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def run_cycle(self) -> list[Pattern]:
        """Run one observe → analyze → fix cycle. Returns detected patterns."""
        patterns = self.analyzer.analyze()
        for pattern in patterns:
            self.fixer.propose_and_apply(pattern)
        return patterns

    def health_score(self) -> float:
        """Current health score: 0.0 (broken) to 1.0 (healthy)."""
        return self.analyzer.health_score()

    def patterns(self) -> list[Pattern]:
        """Run analysis and return current patterns."""
        return self.analyzer.analyze()

    def fixes(self) -> list[Fix]:
        """Return all saved fix proposals."""
        return self.fixer.load_proposals()

    def start_background(self, interval_seconds: int = 3600) -> None:
        """Start the improvement loop in a background thread."""
        if self._background_thread and self._background_thread.is_alive():
            return
        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.is_set():
                self.run_cycle()
                self._stop_event.wait(timeout=interval_seconds)

        self._background_thread = threading.Thread(target=_loop, daemon=True)
        self._background_thread.start()

    def stop_background(self) -> None:
        """Stop the background loop."""
        self._stop_event.set()
        if self._background_thread:
            self._background_thread.join(timeout=5)
            self._background_thread = None

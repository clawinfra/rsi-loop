"""HTTP webhook adapter — POST outcomes to RSI Loop."""

from __future__ import annotations

from rsi_loop.loop import RSILoop
from rsi_loop.types import Config, Outcome


class WebhookAdapter:
    """HTTP endpoint adapter for RSI Loop.

    Creates a Flask app with endpoints::

        POST /observe   — Record an outcome (JSON body)
        GET  /health    — Get current health score
        GET  /patterns  — Get detected patterns

    Usage::

        from rsi_loop.integrations.webhook import WebhookAdapter
        app = WebhookAdapter(data_dir="./rsi_data").create_app()
        app.run(port=8900)

    Requires ``flask`` (install with ``uv add rsi-loop[webhook]``).
    """

    def __init__(self, data_dir: str = "./rsi_data", **config_kwargs) -> None:
        self.loop = RSILoop(Config(data_dir=data_dir, **config_kwargs))

    def create_app(self):
        """Create and return a Flask app with RSI Loop endpoints."""
        try:
            from flask import Flask, jsonify, request
        except ImportError:
            raise ImportError(
                "Flask is required for the webhook adapter. "
                "Install with: uv add rsi-loop[webhook]"
            )

        app = Flask("rsi_loop_webhook")

        @app.post("/observe")
        def observe():
            data = request.get_json(force=True)
            outcome = Outcome.from_dict(data)
            recorded = self.loop.observer.record(outcome)
            return jsonify({"id": recorded.id, "issues": recorded.issues}), 201

        @app.get("/health")
        def health():
            score = self.loop.health_score()
            return jsonify({"health_score": score})

        @app.get("/patterns")
        def patterns():
            pats = self.loop.run_cycle()
            return jsonify({
                "health_score": self.loop.health_score(),
                "patterns": [p.to_dict() for p in pats],
            })

        return app

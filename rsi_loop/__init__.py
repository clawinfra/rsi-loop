"""RSI Loop — Universal self-improvement loop for AI agents."""

from rsi_loop.analyzer import Analyzer
from rsi_loop.fixer import Fixer
from rsi_loop.loop import RSILoop
from rsi_loop.observer import Observer
from rsi_loop.types import Config, Fix, Outcome, Pattern

__version__ = "0.1.0"
__all__ = ["RSILoop", "Observer", "Analyzer", "Fixer", "Config", "Outcome", "Pattern", "Fix"]

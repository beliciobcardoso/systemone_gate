"""
SystemOne Gate
High-speed, zero-cost, local decision gating & triage engine for AI coding agents.
Powered by Ollama System One (Jev-compatible models: Nimble 9B & Tev1).
"""

__version__ = "0.1.0"

from .client import SystemOneClient
from .rubrics import (
    RUBRIC_DIFF_RISK,
    RUBRIC_ERROR_TRIAGE,
    RUBRIC_COMMAND_SAFETY,
    RUBRIC_AGENT_ROUTING,
)

__all__ = [
    "SystemOneClient",
    "RUBRIC_DIFF_RISK",
    "RUBRIC_ERROR_TRIAGE",
    "RUBRIC_COMMAND_SAFETY",
    "RUBRIC_AGENT_ROUTING",
]

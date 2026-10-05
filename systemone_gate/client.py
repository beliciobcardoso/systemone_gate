"""
Client library for communicating with Ollama System One API.
Supports Nimble (9B), Tev1 (0.8B, 4B) and Jev-compatible decision endpoints.
"""

import copy
import json
import os
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

from .guard_rules import evaluate_command
from .rubrics import (
    RUBRIC_DIFF_RISK,
    RUBRIC_ERROR_TRIAGE,
    RUBRIC_COMMAND_SAFETY,
    RUBRIC_AGENT_ROUTING,
)

RULES_VERDICT_ANSWERS = {
    "is_destructive": {
        "type": "choice",
        "choice": "destructive_or_risky",
        "probabilities": {"safe": 0.0, "destructive_or_risky": 1.0},
        "confidence": 1.0,
    },
    "danger_score": {
        "type": "score",
        "score": 2.0,
        "probabilities": {"0": 0.0, "1": 0.0, "2": 1.0},
        "confidence": 1.0,
    },
}

DEFAULT_ENDPOINT = os.environ.get("OLLAMA_SYSTEMONE_URL", "http://localhost:11434/v1/systemone")

class SystemOneClient:
    """
    High-level Python client for Ollama System One.
    Provides methods for error triage, diff assessment, and command safety guarding.
    """

    def __init__(self, endpoint: str = DEFAULT_ENDPOINT, default_model: str = "nimble", fast_model: str = "tev1:0.8b"):
        self.endpoint = endpoint
        self.default_model = default_model
        self.fast_model = fast_model

    def evaluate(self, state: str, questions: Dict[str, Any], model: Optional[str] = None, timeout: int = 30) -> Dict[str, Any]:
        """
        Sends an evaluation request to the System One endpoint.
        Returns the parsed response dictionary containing 'answers' and 'usage'.
        """
        selected_model = model or self.default_model
        payload = json.dumps({
            "model": selected_model,
            "state": state,
            "questions": questions
        }).encode("utf-8")

        req = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except urllib.error.URLError as e:
            return {
                "error": f"Failed to connect to Ollama at {self.endpoint}: {e}",
                "model": selected_model
            }
        except Exception as e:
            return {
                "error": f"Unexpected error during evaluation: {e}",
                "model": selected_model
            }

    def triage_error(self, error_text: str, model: Optional[str] = None) -> Dict[str, Any]:
        """Triages build, linker, or runtime errors using Nimble (9B)."""
        return self.evaluate(
            state=error_text,
            questions=RUBRIC_ERROR_TRIAGE,
            model=model or self.default_model
        )

    def review_diff(self, diff_text: str, model: Optional[str] = None) -> Dict[str, Any]:
        """Evaluates architectural risk and breaking changes in code diffs."""
        return self.evaluate(
            state=diff_text,
            questions=RUBRIC_DIFF_RISK,
            model=model or self.default_model
        )

    def guard_command(self, command: str, model: Optional[str] = None) -> Dict[str, Any]:
        """
        Checks if a bash command is destructive. Deterministic rules run first
        and short-circuit (no network); otherwise the fast model is consulted,
        whose verdict is a heuristic warning, not a security barrier.
        """
        match = evaluate_command(command)
        if match is not None:
            return {
                "model": "rules",
                "source": "rules",
                "rule": {"id": match.rule_id, "reason": match.reason},
                "answers": copy.deepcopy(RULES_VERDICT_ANSWERS),
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        result = self.evaluate(
            state=command,
            questions=RUBRIC_COMMAND_SAFETY,
            model=model or self.fast_model
        )
        if isinstance(result, dict) and "error" not in result:
            return {**result, "source": "model"}
        return result

    def route_task(self, prompt: str, model: Optional[str] = None) -> Dict[str, Any]:
        """Routes a user prompt to the most suitable subagent role."""
        return self.evaluate(
            state=prompt,
            questions=RUBRIC_AGENT_ROUTING,
            model=model or self.default_model
        )

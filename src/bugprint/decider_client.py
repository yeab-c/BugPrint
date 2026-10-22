"""Decider HTTP client.

Thin wrapper around POST /v1/systemone. This is the only file that knows
the exact Decider response shape.

Confirmed from installed strands_decider.schema (v0.1.0):
  - ChoiceAnswer.choice  (NOT "selected" — the spec §7.2 was wrong)
  - ChoiceAnswer.probabilities  dict[str, float]
  - ChoiceAnswer.confidence  float
  - Top-level: model, answers, usage, latency_ms (added by server)
"""

from __future__ import annotations

import time
from typing import Any

import httpx


class DeciderError(Exception):
    """Raised when the Decider server returns an error or is unreachable."""


class DeciderClient:
    """HTTP client for the local Decider server.

    Args:
        base_url: Base URL of the Decider server, e.g. 'http://127.0.0.1:8099'.
        model: Model field sent in the request body.
        timeout: HTTP timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8099",
        model: str = "strands-decider-latest",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        """GET /health — returns server info dict or raises DeciderError."""
        try:
            resp = httpx.get(f"{self.base_url}/health", timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError as exc:
            raise DeciderError(
                f"Cannot connect to Decider at {self.base_url}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise DeciderError(f"Decider /health error: {exc}") from exc

    def is_healthy(self) -> bool:
        """Return True if the server responds to /health, False otherwise."""
        try:
            self.health()
            return True
        except DeciderError:
            return False

    def ask(
        self,
        state: str,
        questions: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """POST /v1/systemone and return the raw response dict.

        Args:
            state: The state text (the full context paragraph).
            questions: Map of question_id -> question dict with 'type',
                       'instructions', and 'criteria'.

        Returns:
            Raw response dict with keys: model, answers, usage, latency_ms.

        Raises:
            DeciderError: On network error or non-2xx response.
        """
        payload = {
            "model": self.model,
            "state": state,
            "questions": questions,
        }
        t0 = time.perf_counter()
        try:
            resp = httpx.post(
                f"{self.base_url}/v1/systemone",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise DeciderError(
                f"Cannot connect to Decider at {self.base_url}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise DeciderError(
                f"Decider returned {exc.response.status_code}: {exc.response.text}"
            ) from exc
        elapsed_ms = (time.perf_counter() - t0) * 1000
        result = resp.json()
        # Prefer the server-reported latency; fall back to our measurement.
        if "latency_ms" not in result:
            result["latency_ms"] = round(elapsed_ms, 2)
        return result

    def choice(
        self,
        state: str,
        question_id: str,
        instructions: str,
        criteria: dict[str, str],
    ) -> tuple[str, dict[str, float], float, float]:
        """Ask one choice question; return (chosen, probs, confidence, latency_ms).

        Args:
            state: Context paragraph.
            question_id: Key name for the question (e.g. 'next_tool').
            instructions: The question text.
            criteria: Dict mapping option name to description.

        Returns:
            Tuple of (chosen option name, probabilities dict,
                      confidence float, latency_ms float).

        Raises:
            DeciderError: On any error.
            KeyError: If the response shape is unexpected.
        """
        raw = self.ask(
            state=state,
            questions={
                question_id: {
                    "type": "choice",
                    "instructions": instructions,
                    "criteria": criteria,
                }
            },
        )
        answer = raw["answers"][question_id]
        # Real field name is "choice" (confirmed from schema.py ChoiceAnswer).
        chosen: str = answer["choice"]
        probs: dict[str, float] = answer["probabilities"]
        confidence: float = answer["confidence"]
        latency_ms: float = raw.get("latency_ms", 0.0)
        return chosen, probs, confidence, latency_ms

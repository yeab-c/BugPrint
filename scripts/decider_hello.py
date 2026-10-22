#!/usr/bin/env python3
"""Phase 0 hello-world: send one real choice question to Decider and print the response.

Run with:
    .venv/Scripts/python scripts/decider_hello.py

Requires the Decider server running on localhost:8099.
"""

import json
import sys
import time

import httpx


DECIDER_URL = "http://127.0.0.1:8099"


def check_health() -> dict:
    """GET /health and return the response."""
    resp = httpx.get(f"{DECIDER_URL}/health", timeout=10)
    resp.raise_for_status()
    return resp.json()


def ask_choice() -> dict:
    """Send a choice question to POST /v1/systemone and return the response."""
    payload = {
        "state": (
            "EVENT: test_failed\n"
            "TEST: tests/test_stats.py::test_average_empty\n"
            "ERROR: ZeroDivisionError: division by zero (stats.py line 8)\n"
            "FILES CHANGED SINCE LAST RUN: stats.py\n"
            "SECONDS SINCE PREVIOUS EVENT: 95\n"
            "PREVIOUS RESULTS FOR THIS TEST: failed, failed\n"
            "RESULT FLIPS WITHOUT CODE CHANGE: no\n"
            "ENVIRONMENT CLUE: no\n"
            "OPEN DEFECTS: 0\n"
        ),
        "questions": {
            "next_tool": {
                "type": "choice",
                "instructions": "Which tool should Bugprint call next?",
                "criteria": {
                    "record_possible_defect": (
                        "A test failed and the error suggests a real problem worth recording."
                    ),
                    "record_interruption": (
                        "The developer was idle, away, or interrupted. "
                        "This is not a test result."
                    ),
                    "ask_for_confirmation": (
                        "The evidence is unclear, missing, or contradictory, "
                        "so a person should decide."
                    ),
                },
            }
        },
    }

    start = time.perf_counter()
    resp = httpx.post(
        f"{DECIDER_URL}/v1/systemone",
        json=payload,
        timeout=30,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    resp.raise_for_status()
    result = resp.json()
    result["_our_measured_latency_ms"] = round(elapsed_ms, 2)
    return result


def main() -> None:
    print("=" * 60)
    print("BugPrint Phase 0: Decider Hello World")
    print("=" * 60)

    # Step 1: Health check
    print("\n1. Checking /health ...")
    try:
        health = check_health()
        print(json.dumps(health, indent=2))
    except httpx.ConnectError:
        print(f"ERROR: Cannot connect to {DECIDER_URL}")
        print("Start the Decider server first:")
        print(
            "  strands-decider serve StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 --port 8099"
        )
        sys.exit(1)

    # Step 2: Choice question
    print("\n2. Sending choice question to /v1/systemone ...")
    result = ask_choice()
    print(json.dumps(result, indent=2))

    # Step 3: Save response
    output_path = "docs/decider-response-sample.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n3. Saved response to {output_path}")

    # Step 4: Extract key fields
    # NOTE: The choice answer field is "choice" (not "selected" — spec §7.2 was wrong).
    answer = result.get("answers", {}).get("next_tool", {})
    print("\n--- Key fields ---")
    print(f"  Model:       {result.get('model')}")
    print(f"  Choice:      {answer.get('choice')}")        # real field name
    print(f"  Confidence:  {answer.get('confidence')}")
    print(f"  Probs:       {answer.get('probabilities')}")
    print(f"  Latency:     {result.get('latency_ms')} ms (server)")
    print(f"  Latency:     {result.get('_our_measured_latency_ms')} ms (measured)")


if __name__ == "__main__":
    main()

"""Router: asks Decider a typed choice question and applies the guard.

Decider makes the tool choice. The threshold and compatibility guard
live here in plain Python — they never run inside a model.

Decision flow (spec §9.4):
    1. Build the state text from Evidence (spec §10).
    2. Call Decider with the allowed options for this event type.
    3. Apply the Guard:
       - If Decider chose 'ask_for_confirmation' -> keep it.
       - If confidence < ACT_THRESHOLD -> override to 'ask_for_confirmation'
         with reason 'low_confidence'.
       - If chosen option is incompatible with the event type -> override
         with reason 'incompatible_with_event'.
       - If Decider chose 'link_fix_to_defect' but no open defects exist ->
         option was not offered (removed before the call), so this is
         handled by option filtering, not the guard.
    4. Return a Decision (not yet stored — caller does that).
"""

from __future__ import annotations

from .decider_client import DeciderClient, DeciderError
from .models import Decision, Evidence

# ---------------------------------------------------------------------------
# Spec §10: State text template
# ---------------------------------------------------------------------------

_NEXT_TOOL_INSTRUCTIONS = (
    "Given this development event and its evidence, "
    "which tool should Bugprint call next?"
)

_NEXT_TOOL_CRITERIA = {
    "record_possible_defect": (
        "A test failed and the error suggests a real problem worth recording."
    ),
    "link_fix_to_defect": (
        "A test that failed earlier now passes, and an open defect in the list "
        "matches it."
    ),
    "record_interruption": (
        "The developer was idle, away, or interrupted. This is not a test result."
    ),
    "ask_for_confirmation": (
        "The evidence is unclear, missing, or contradictory, "
        "so a person should decide."
    ),
}

_FAILURE_KIND_INSTRUCTIONS = (
    "What kind of failure is this test failure?"
)

_FAILURE_KIND_CRITERIA = {
    "real_defect": "The code under test is wrong.",
    "flaky_test": (
        "The result changes without code changes, often due to timing or randomness."
    ),
    "environment_problem": (
        "The failure is caused by a missing package, network issue, "
        "permissions, or configuration."
    ),
    "unclear": "The evidence is not sufficient to classify the failure kind.",
}


# ---------------------------------------------------------------------------
# Compatibility table (spec §9.4)
# ---------------------------------------------------------------------------

_COMPATIBLE: dict[str, set[str]] = {
    "record_possible_defect": {"test_failed"},
    "link_fix_to_defect": {"test_passed"},
    "record_interruption": {"idle_gap", "focus_lost"},
    "ask_for_confirmation": {"test_failed", "test_passed", "idle_gap", "focus_lost"},
}


def _is_compatible(chosen: str, event_type: str) -> bool:
    allowed = _COMPATIBLE.get(chosen, set())
    return event_type in allowed


# ---------------------------------------------------------------------------
# State text builder (spec §10)
# ---------------------------------------------------------------------------


def build_state_text(ev: Evidence) -> str:
    """Render Evidence into the fixed-format state text for Decider."""
    lines: list[str] = []
    lines.append(f"EVENT: {ev.event_type}")
    if ev.test_name:
        lines.append(f"TEST: {ev.test_name}")
    if ev.error_excerpt:
        lines.append(f"ERROR: {ev.error_excerpt[:500]}")
    files = ", ".join(ev.files_changed) if ev.files_changed else "none"
    lines.append(f"FILES CHANGED SINCE LAST RUN: {files}")
    if ev.seconds_since_previous_event is not None:
        lines.append(f"SECONDS SINCE PREVIOUS EVENT: {ev.seconds_since_previous_event}")
    if ev.previous_results_for_test:
        results_str = ", ".join(ev.previous_results_for_test)
        lines.append(f"PREVIOUS RESULTS FOR THIS TEST: {results_str}")
    lines.append(
        f"RESULT FLIPS WITHOUT CODE CHANGE: "
        f"{'yes' if ev.alternating_without_code_change else 'no'}"
    )
    lines.append(f"ENVIRONMENT CLUE: {'yes' if ev.environment_hint else 'no'}")
    lines.append(f"OPEN DEFECTS: {len(ev.open_defects)}")
    for d in ev.open_defects:
        overlap_str = ", ".join(d.files_overlap) if d.files_overlap else "none"
        lines.append(
            f"- defect_{d.id}: {d.test_name} | {d.summary} | "
            f"open {d.minutes_open} min | shares file {overlap_str}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fallback heuristic (labeled, excluded from eval/demo)
# ---------------------------------------------------------------------------


def _fallback_choice(ev: Evidence) -> tuple[str, dict[str, float], float]:
    """Simple keyword heuristic used ONLY when Decider is down and fallback is enabled.

    All fallback decisions are stored with is_fallback=True and MUST NOT
    appear in eval, demo, or published numbers.
    """
    if ev.event_type in ("idle_gap", "focus_lost"):
        chosen = "record_interruption"
    elif ev.event_type == "test_failed":
        chosen = "record_possible_defect"
    else:
        chosen = "ask_for_confirmation"
    # Fake uniform probability so the schema is valid.
    options = list(_NEXT_TOOL_CRITERIA.keys())
    prob = 1.0 / len(options)
    probs = {o: prob for o in options}
    probs[chosen] = prob  # not inflated — it's a heuristic, not a real score
    return chosen, probs, 0.0  # confidence 0.0 = always ask for confirmation


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


def _apply_guard(
    chosen: str,
    confidence: float,
    event_type: str,
    act_threshold: float,
    has_open_defects: bool,
) -> tuple[str, str | None]:
    """Apply threshold and compatibility rules.

    Returns (final_action, overridden_reason | None).
    """
    if chosen == "ask_for_confirmation":
        return "ask_for_confirmation", None

    if confidence < act_threshold:
        return "ask_for_confirmation", "low_confidence"

    if not _is_compatible(chosen, event_type):
        return "ask_for_confirmation", "incompatible_with_event"

    return chosen, None


# ---------------------------------------------------------------------------
# Public router function
# ---------------------------------------------------------------------------


def route_event(
    ev: Evidence,
    client: DeciderClient,
    act_threshold: float = 0.70,
    allow_fallback: bool = False,
    question_id: str = "next_tool",
) -> Decision:
    """Ask Decider what to do with this event and return a Decision.

    The caller is responsible for storing the Decision in the database.

    Args:
        ev: Evidence object for the event.
        client: DeciderClient instance (or a fake for tests).
        act_threshold: Minimum confidence to act without asking.
        allow_fallback: If True, use heuristic when Decider is down.
        question_id: Override question id (useful for testing).

    Returns:
        Decision with final_action set after the guard is applied.
    """
    # Build option set — remove link_fix_to_defect if no open defects (spec §9.4).
    criteria = dict(_NEXT_TOOL_CRITERIA)
    if not ev.open_defects:
        criteria.pop("link_fix_to_defect", None)
    options = list(criteria.keys())

    state = build_state_text(ev)
    is_fallback = False
    latency_ms: float = 0.0

    try:
        chosen, probs, confidence, latency_ms = client.choice(
            state=state,
            question_id=question_id,
            instructions=_NEXT_TOOL_INSTRUCTIONS,
            criteria=criteria,
        )
    except DeciderError:
        if not allow_fallback:
            raise
        chosen, probs, confidence = _fallback_choice(ev)
        is_fallback = True

    final_action, overridden_reason = _apply_guard(
        chosen=chosen,
        confidence=confidence,
        event_type=ev.event_type,
        act_threshold=act_threshold,
        has_open_defects=bool(ev.open_defects),
    )

    return Decision(
        event_id=ev.event_id,
        question_id=question_id,
        options=options,
        chosen=chosen,
        probs=probs,
        confidence=confidence,
        threshold=act_threshold,
        final_action=final_action,
        overridden_reason=overridden_reason,
        latency_ms=latency_ms,
        is_fallback=is_fallback,
    )


def route_failure_kind(
    ev: Evidence,
    client: DeciderClient,
    act_threshold: float = 0.70,
) -> Decision:
    """Ask Decider to classify the failure kind (question 2).

    Called after route_event chose 'record_possible_defect'.
    Returns a Decision with final_action set to one of the failure_kind options
    or 'ask_for_confirmation' if Decider is unclear or confidence is low.
    """
    state = build_state_text(ev)
    criteria = dict(_FAILURE_KIND_CRITERIA)
    options = list(criteria.keys())

    chosen, probs, confidence, latency_ms = client.choice(
        state=state,
        question_id="failure_kind",
        instructions=_FAILURE_KIND_INSTRUCTIONS,
        criteria=criteria,
    )

    # 'unclear' maps to confirmation (spec §9.4)
    if chosen == "unclear":
        final_action = "ask_for_confirmation"
        overridden_reason = None
    elif confidence < act_threshold:
        final_action = "ask_for_confirmation"
        overridden_reason = "low_confidence"
    else:
        final_action = chosen
        overridden_reason = None

    return Decision(
        event_id=ev.event_id,
        question_id="failure_kind",
        options=options,
        chosen=chosen,
        probs=probs,
        confidence=confidence,
        threshold=act_threshold,
        final_action=final_action,
        overridden_reason=overridden_reason,
        latency_ms=latency_ms,
        is_fallback=False,
    )


def route_fix_match(
    ev: Evidence,
    client: DeciderClient,
    act_threshold: float = 0.70,
) -> Decision:
    """Ask Decider which open defect this fix matches (question 3).

    Called after route_event chose 'link_fix_to_defect'.
    Options: one per open defect + 'none_of_these'.
    'none_of_these' creates a confirmation (spec §9.4).
    """
    state = build_state_text(ev)
    criteria: dict[str, str] = {
        f"defect_{d.id}": d.summary for d in ev.open_defects
    }
    criteria["none_of_these"] = (
        "None of the listed open defects matches this fix."
    )
    options = list(criteria.keys())

    chosen, probs, confidence, latency_ms = client.choice(
        state=state,
        question_id="fix_match",
        instructions="Which open defect does this fix resolve?",
        criteria=criteria,
    )

    if chosen == "none_of_these":
        final_action = "ask_for_confirmation"
        overridden_reason = None
    elif confidence < act_threshold:
        final_action = "ask_for_confirmation"
        overridden_reason = "low_confidence"
    else:
        final_action = chosen
        overridden_reason = None

    return Decision(
        event_id=ev.event_id,
        question_id="fix_match",
        options=options,
        chosen=chosen,
        probs=probs,
        confidence=confidence,
        threshold=act_threshold,
        final_action=final_action,
        overridden_reason=overridden_reason,
        latency_ms=latency_ms,
        is_fallback=False,
    )

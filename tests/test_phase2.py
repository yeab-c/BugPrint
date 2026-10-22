"""Phase 2 tests: evidence.py, decider_client.py, router.py.

All Decider calls use a FakeDeciderClient — no real server required.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bugprint.db import get_conn
from bugprint.decider_client import DeciderError
from bugprint.evidence import (
    _alternating_without_change,
    _extract_error_type,
    _is_env_hint,
    build_evidence,
)
from bugprint.models import Evidence, OpenDefectRef
from bugprint.router import (
    _apply_guard,
    build_state_text,
    route_event,
    route_failure_kind,
)

# ---------------------------------------------------------------------------
# Fake Decider client
# ---------------------------------------------------------------------------


class FakeDeciderClient:
    """Fake DeciderClient for tests. Returns pre-configured responses."""

    def __init__(
        self,
        chosen: str = "record_possible_defect",
        confidence: float = 0.90,
        probs: dict[str, float] | None = None,
        latency_ms: float = 10.0,
        raise_error: bool = False,
    ) -> None:
        self.chosen = chosen
        self.confidence = confidence
        self.probs = probs or {chosen: confidence}
        self.latency_ms = latency_ms
        self.raise_error = raise_error
        self.calls: list[dict[str, Any]] = []

    def choice(
        self,
        state: str,
        question_id: str,
        instructions: str,
        criteria: dict[str, str],
    ) -> tuple[str, dict[str, float], float, float]:
        self.calls.append(
            {
                "state": state,
                "question_id": question_id,
                "criteria": criteria,
            }
        )
        if self.raise_error:
            raise DeciderError("Fake server down")
        return self.chosen, self.probs, self.confidence, self.latency_ms


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture()
def seeded_db(tmp_db: Path):
    """DB with one session, one test_failed event, and one open defect."""
    with get_conn(tmp_db) as conn:
        conn.execute(
            "INSERT INTO sessions(id, started_at) VALUES (?, ?)",
            ("s1", "2024-01-01T10:00:00+00:00"),
        )
        conn.execute(
            """INSERT INTO events
               (session_id, ts, event_type, test_name, result, error_text,
                files_changed, duration_ms, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "s1",
                "2024-01-01T10:00:10+00:00",
                "test_failed",
                "tests/test_stats.py::test_avg",
                "failed",
                "ZeroDivisionError: division by zero",
                json.dumps(["stats.py"]),
                500,
                "pending",
            ),
        )
        # Second event to test build_evidence with a previous result
        conn.execute(
            """INSERT INTO events
               (session_id, ts, event_type, test_name, result, error_text,
                files_changed, duration_ms, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "s1",
                "2024-01-01T10:01:00+00:00",
                "test_failed",
                "tests/test_stats.py::test_avg",
                "failed",
                "ZeroDivisionError: division by zero (again)",
                json.dumps(["stats.py"]),
                400,
                "pending",
            ),
        )
        # Open defect referencing event 1
        conn.execute(
            """INSERT INTO defects
               (session_id, test_name, summary, failure_kind, status,
                confirmed_by_user, opened_event_id, opened_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "s1",
                "tests/test_stats.py::test_avg",
                "ZeroDivisionError: division by zero",
                "real_defect",
                "open",
                0,
                1,
                "2024-01-01T10:00:10+00:00",
            ),
        )
    return tmp_db


# ---------------------------------------------------------------------------
# evidence.py unit tests
# ---------------------------------------------------------------------------


class TestExtractErrorType:
    def test_simple(self) -> None:
        assert _extract_error_type("ZeroDivisionError: division by zero") == "ZeroDivisionError"

    def test_multiline(self) -> None:
        tb = "Traceback:\n  File foo.py\nValueError: bad value"
        assert _extract_error_type(tb) == "ValueError"

    def test_none(self) -> None:
        assert _extract_error_type(None) is None

    def test_no_match(self) -> None:
        assert _extract_error_type("something went wrong") is None


class TestIsEnvHint:
    def test_module_not_found(self) -> None:
        assert _is_env_hint("ModuleNotFoundError: No module named 'foo'")

    def test_connection_error(self) -> None:
        assert _is_env_hint("ConnectionError: timed out")

    def test_not_env(self) -> None:
        assert not _is_env_hint("ZeroDivisionError: division by zero")

    def test_none(self) -> None:
        assert not _is_env_hint(None)


class TestAlternatingWithoutChange:
    def test_alternating_no_files(self) -> None:
        assert _alternating_without_change(["passed", "failed", "passed"], [])

    def test_not_alternating(self) -> None:
        assert not _alternating_without_change(["failed", "failed", "passed"], [])

    def test_has_files_changed(self) -> None:
        assert not _alternating_without_change(
            ["passed", "failed", "passed"], ["a.py"]
        )

    def test_too_few_results(self) -> None:
        assert not _alternating_without_change(["passed", "failed"], [])


class TestBuildEvidence:
    def test_basic_fields(self, seeded_db: Path) -> None:
        with get_conn(seeded_db) as conn:
            ev = build_evidence(conn, 2)  # second event
        assert ev.event_id == 2
        assert ev.event_type == "test_failed"
        assert ev.test_name == "tests/test_stats.py::test_avg"
        assert ev.error_type == "ZeroDivisionError"
        assert "ZeroDivisionError" in ev.error_excerpt
        assert ev.files_changed == ["stats.py"]

    def test_previous_results(self, seeded_db: Path) -> None:
        with get_conn(seeded_db) as conn:
            ev = build_evidence(conn, 2)
        # Event 1 (same test, same session) should appear in previous_results
        assert "failed" in ev.previous_results_for_test

    def test_open_defects(self, seeded_db: Path) -> None:
        with get_conn(seeded_db) as conn:
            ev = build_evidence(conn, 2)
        assert len(ev.open_defects) == 1
        assert ev.open_defects[0].test_name == "tests/test_stats.py::test_avg"
        assert "stats.py" in ev.open_defects[0].files_overlap

    def test_not_found(self, seeded_db: Path) -> None:
        with get_conn(seeded_db) as conn:
            with pytest.raises(ValueError, match="not found"):
                build_evidence(conn, 9999)

    def test_seconds_since_previous(self, seeded_db: Path) -> None:
        with get_conn(seeded_db) as conn:
            ev = build_evidence(conn, 2)
        assert ev.seconds_since_previous_event == 50  # 10:01:00 - 10:00:10 = 50s


# ---------------------------------------------------------------------------
# router.py — state text
# ---------------------------------------------------------------------------


class TestBuildStateText:
    def test_basic(self) -> None:
        ev = Evidence(
            event_id=1,
            event_type="test_failed",
            test_name="tests/test_foo.py::test_bar",
            error_excerpt="ValueError: bad",
            files_changed=["foo.py"],
            seconds_since_previous_event=60,
            previous_results_for_test=["failed"],
            alternating_without_code_change=False,
            environment_hint=False,
            open_defects=[],
        )
        text = build_state_text(ev)
        assert "EVENT: test_failed" in text
        assert "TEST: tests/test_foo.py::test_bar" in text
        assert "ERROR: ValueError: bad" in text
        assert "FILES CHANGED SINCE LAST RUN: foo.py" in text
        assert "SECONDS SINCE PREVIOUS EVENT: 60" in text
        assert "OPEN DEFECTS: 0" in text

    def test_no_test_name(self) -> None:
        ev = Evidence(event_id=1, event_type="idle_gap")
        text = build_state_text(ev)
        assert "EVENT: idle_gap" in text
        assert "TEST:" not in text

    def test_defect_listed(self) -> None:
        ev = Evidence(
            event_id=1,
            event_type="test_passed",
            open_defects=[
                OpenDefectRef(
                    id=3,
                    test_name="tests/t.py::test_x",
                    summary="ValueError",
                    minutes_open=15,
                    files_overlap=["a.py"],
                )
            ],
        )
        text = build_state_text(ev)
        assert "defect_3" in text
        assert "15 min" in text


# ---------------------------------------------------------------------------
# router.py — guard
# ---------------------------------------------------------------------------


class TestApplyGuard:
    def test_act_when_confident(self) -> None:
        action, reason = _apply_guard(
            "record_possible_defect", 0.90, "test_failed", 0.70, False
        )
        assert action == "record_possible_defect"
        assert reason is None

    def test_low_confidence_overrides(self) -> None:
        action, reason = _apply_guard(
            "record_possible_defect", 0.50, "test_failed", 0.70, False
        )
        assert action == "ask_for_confirmation"
        assert reason == "low_confidence"

    def test_incompatible_overrides(self) -> None:
        # record_possible_defect is only for test_failed, not idle_gap
        action, reason = _apply_guard(
            "record_possible_defect", 0.95, "idle_gap", 0.70, False
        )
        assert action == "ask_for_confirmation"
        assert reason == "incompatible_with_event"

    def test_ask_for_confirmation_always_passes(self) -> None:
        # confidence=0 but it's ask_for_confirmation — no override
        action, reason = _apply_guard(
            "ask_for_confirmation", 0.0, "test_failed", 0.70, False
        )
        assert action == "ask_for_confirmation"
        assert reason is None

    def test_threshold_boundary_exact(self) -> None:
        # Exactly at threshold should act (>= threshold)
        action, reason = _apply_guard(
            "record_possible_defect", 0.70, "test_failed", 0.70, False
        )
        assert action == "record_possible_defect"

    def test_just_below_threshold(self) -> None:
        action, reason = _apply_guard(
            "record_possible_defect", 0.699, "test_failed", 0.70, False
        )
        assert action == "ask_for_confirmation"
        assert reason == "low_confidence"


# ---------------------------------------------------------------------------
# router.py — route_event with FakeDeciderClient
# ---------------------------------------------------------------------------


class TestRouteEvent:
    def test_confident_decision(self) -> None:
        ev = Evidence(event_id=1, event_type="test_failed")
        client = FakeDeciderClient(
            chosen="record_possible_defect", confidence=0.92
        )
        decision = route_event(ev, client, act_threshold=0.70)
        assert decision.final_action == "record_possible_defect"
        assert decision.overridden_reason is None
        assert decision.is_fallback is False

    def test_low_confidence_asks(self) -> None:
        ev = Evidence(event_id=2, event_type="test_failed")
        client = FakeDeciderClient(
            chosen="record_possible_defect", confidence=0.50
        )
        decision = route_event(ev, client, act_threshold=0.70)
        assert decision.final_action == "ask_for_confirmation"
        assert decision.overridden_reason == "low_confidence"

    def test_incompatible_asks(self) -> None:
        ev = Evidence(event_id=3, event_type="idle_gap")
        client = FakeDeciderClient(
            chosen="record_possible_defect", confidence=0.95
        )
        decision = route_event(ev, client, act_threshold=0.70)
        assert decision.final_action == "ask_for_confirmation"
        assert decision.overridden_reason == "incompatible_with_event"

    def test_link_fix_removed_when_no_defects(self) -> None:
        ev = Evidence(event_id=4, event_type="test_passed", open_defects=[])
        client = FakeDeciderClient(chosen="ask_for_confirmation", confidence=0.80)
        decision = route_event(ev, client)
        # link_fix_to_defect should not be in options offered
        assert "link_fix_to_defect" not in decision.options

    def test_link_fix_present_when_defects_exist(self) -> None:
        ev = Evidence(
            event_id=5,
            event_type="test_passed",
            open_defects=[
                OpenDefectRef(id=1, test_name="t", summary="e", minutes_open=5)
            ],
        )
        client = FakeDeciderClient(chosen="link_fix_to_defect", confidence=0.88)
        decision = route_event(ev, client)
        assert "link_fix_to_defect" in decision.options

    def test_decider_error_no_fallback_raises(self) -> None:
        ev = Evidence(event_id=6, event_type="test_failed")
        client = FakeDeciderClient(raise_error=True)
        with pytest.raises(DeciderError):
            route_event(ev, client, allow_fallback=False)

    def test_decider_error_with_fallback(self) -> None:
        ev = Evidence(event_id=7, event_type="test_failed")
        client = FakeDeciderClient(raise_error=True)
        decision = route_event(ev, client, allow_fallback=True)
        assert decision.is_fallback is True
        # fallback for test_failed => ask_for_confirmation (confidence 0.0 < threshold)
        assert decision.final_action == "ask_for_confirmation"

    def test_state_text_sent_to_client(self) -> None:
        ev = Evidence(
            event_id=8,
            event_type="test_failed",
            test_name="tests/test_x.py::test_y",
        )
        client = FakeDeciderClient(chosen="record_possible_defect", confidence=0.85)
        route_event(ev, client)
        assert len(client.calls) == 1
        assert "EVENT: test_failed" in client.calls[0]["state"]
        assert "tests/test_x.py::test_y" in client.calls[0]["state"]

    def test_decision_stores_all_fields(self) -> None:
        ev = Evidence(event_id=9, event_type="test_failed")
        probs = {
            "record_possible_defect": 0.80,
            "record_interruption": 0.10,
            "ask_for_confirmation": 0.10,
        }
        client = FakeDeciderClient(
            chosen="record_possible_defect", confidence=0.80, probs=probs, latency_ms=123.4
        )
        d = route_event(ev, client, act_threshold=0.70)
        assert d.event_id == 9
        assert d.chosen == "record_possible_defect"
        assert abs(d.confidence - 0.80) < 1e-9
        assert d.threshold == 0.70
        assert abs(d.latency_ms - 123.4) < 1e-9
        assert d.probs == probs


# ---------------------------------------------------------------------------
# router.py — route_failure_kind
# ---------------------------------------------------------------------------


class TestRouteFailureKind:
    def test_clear_real_defect(self) -> None:
        ev = Evidence(event_id=1, event_type="test_failed")
        client = FakeDeciderClient(chosen="real_defect", confidence=0.92)
        d = route_failure_kind(ev, client, act_threshold=0.70)
        assert d.final_action == "real_defect"

    def test_unclear_maps_to_confirmation(self) -> None:
        ev = Evidence(event_id=1, event_type="test_failed")
        client = FakeDeciderClient(chosen="unclear", confidence=0.80)
        d = route_failure_kind(ev, client, act_threshold=0.70)
        assert d.final_action == "ask_for_confirmation"

    def test_low_confidence_maps_to_confirmation(self) -> None:
        ev = Evidence(event_id=1, event_type="test_failed")
        client = FakeDeciderClient(chosen="flaky_test", confidence=0.40)
        d = route_failure_kind(ev, client, act_threshold=0.70)
        assert d.final_action == "ask_for_confirmation"
        assert d.overridden_reason == "low_confidence"

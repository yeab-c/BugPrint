"""Pydantic models for BugPrint domain objects.

These mirror the SQLite schema but are used for validation, serialisation,
and passing data between layers.  IDs are Optional[int] so that objects can
be created before they are inserted into the database.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class Session(BaseModel):
    id: str                        # uuid4
    started_at: str = Field(default_factory=_utcnow)
    ended_at: str | None = None
    project_path: str | None = None
    git_commit: str | None = None


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------

EventType = Literal["test_failed", "test_passed", "idle_gap", "focus_lost"]
EventStatus = Literal["pending", "processed", "skipped", "error"]


class Event(BaseModel):
    id: int | None = None
    session_id: str
    ts: str = Field(default_factory=_utcnow)
    event_type: EventType
    test_name: str | None = None
    result: Literal["failed", "passed"] | None = None
    error_text: str | None = None          # already trimmed & redacted
    files_changed: list[str] = Field(default_factory=list)
    duration_ms: int | None = None
    gap_seconds: int | None = None
    status: EventStatus = "pending"

    @field_validator("error_text")
    @classmethod
    def _trim_error(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 2000:
            return v[-2000:]
        return v

    def files_changed_json(self) -> str:
        return json.dumps(self.files_changed)

    @classmethod
    def from_row(cls, row: Any) -> Event:
        d = dict(row)
        d["files_changed"] = json.loads(d.get("files_changed") or "[]")
        return cls(**d)


# ---------------------------------------------------------------------------
# Evidence (not stored in DB, built at route time)
# ---------------------------------------------------------------------------


class OpenDefectRef(BaseModel):
    id: int
    test_name: str
    summary: str
    minutes_open: int
    files_overlap: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    event_id: int
    event_type: EventType
    test_name: str | None = None
    error_type: str | None = None
    error_excerpt: str | None = None       # max 500 chars
    files_changed: list[str] = Field(default_factory=list)
    seconds_since_previous_event: int | None = None
    previous_results_for_test: list[str] = Field(default_factory=list)
    alternating_without_code_change: bool = False
    environment_hint: bool = False
    open_defects: list[OpenDefectRef] = Field(default_factory=list)

    @field_validator("error_excerpt")
    @classmethod
    def _trim_excerpt(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 500:
            return v[:500]
        return v


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

OverriddenReason = Literal[
    "low_confidence", "incompatible_with_event", "no_open_defects"
]


class Decision(BaseModel):
    id: int | None = None
    event_id: int
    question_id: str
    options: list[str]
    chosen: str
    probs: dict[str, float]
    confidence: float
    threshold: float | None = None
    final_action: str
    overridden_reason: OverriddenReason | None = None
    latency_ms: float | None = None
    is_fallback: bool = False
    created_at: str = Field(default_factory=_utcnow)

    def options_json(self) -> str:
        return json.dumps(self.options)

    def probs_json(self) -> str:
        return json.dumps(self.probs)

    @classmethod
    def from_row(cls, row: Any) -> Decision:
        d = dict(row)
        d["options"] = json.loads(d["options"])
        d["probs"] = json.loads(d["probs"])
        d["is_fallback"] = bool(d["is_fallback"])
        return cls(**d)


# ---------------------------------------------------------------------------
# Defect
# ---------------------------------------------------------------------------

FailureKind = Literal["real_defect", "flaky_test", "environment_problem", "unknown"]
DefectStatus = Literal["open", "fixed", "dismissed"]


class Defect(BaseModel):
    id: int | None = None
    session_id: str
    test_name: str
    summary: str
    failure_kind: FailureKind | None = None
    status: DefectStatus = "open"
    confirmed_by_user: bool = False
    opened_event_id: int
    fixed_event_id: int | None = None
    opened_at: str = Field(default_factory=_utcnow)
    fixed_at: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> Defect:
        d = dict(row)
        d["confirmed_by_user"] = bool(d["confirmed_by_user"])
        return cls(**d)


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------

ConfirmationStatus = Literal["pending", "accepted", "rejected"]


class Confirmation(BaseModel):
    id: int | None = None
    event_id: int
    decision_id: int
    proposed_action: str
    proposed_args: dict[str, Any] | None = None
    reason: str
    status: ConfirmationStatus = "pending"
    created_at: str = Field(default_factory=_utcnow)
    resolved_at: str | None = None

    def proposed_args_json(self) -> str | None:
        if self.proposed_args is None:
            return None
        return json.dumps(self.proposed_args)

    @classmethod
    def from_row(cls, row: Any) -> Confirmation:
        d = dict(row)
        raw = d.get("proposed_args")
        d["proposed_args"] = json.loads(raw) if raw else None
        return cls(**d)


# ---------------------------------------------------------------------------
# Interruption
# ---------------------------------------------------------------------------


class Interruption(BaseModel):
    id: int | None = None
    event_id: int
    started_at: str = Field(default_factory=_utcnow)
    seconds: int
    reason: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> Interruption:
        return cls(**dict(row))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


class Claim(BaseModel):
    text: str
    event_ids: list[int]


class Report(BaseModel):
    id: int | None = None
    session_id: str
    created_at: str = Field(default_factory=_utcnow)
    model_id: str
    input_stats: dict[str, Any]
    summary: str
    suggestion: str
    claims: list[Claim]
    validated: bool = False

    def input_stats_json(self) -> str:
        return json.dumps(self.input_stats)

    def claims_json(self) -> str:
        return json.dumps([c.model_dump() for c in self.claims])

    @classmethod
    def from_row(cls, row: Any) -> Report:
        d = dict(row)
        d["input_stats"] = json.loads(d["input_stats"])
        d["claims"] = [Claim(**c) for c in json.loads(d["claims"])]
        d["validated"] = bool(d["validated"])
        return cls(**d)

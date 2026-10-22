"""Evidence builder.

Collects facts about a pending event from the database and returns
an Evidence object. No decisions are made here — only facts.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime

from .models import Evidence, OpenDefectRef

# Patterns that suggest the failure is environmental rather than a code defect.
_ENV_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ModuleNotFoundError", re.IGNORECASE),
    re.compile(r"ImportError", re.IGNORECASE),
    re.compile(r"ConnectionError", re.IGNORECASE),
    re.compile(r"\bTimeout\b", re.IGNORECASE),
    re.compile(r"PermissionError", re.IGNORECASE),
    re.compile(r"FileNotFoundError", re.IGNORECASE),
]


def _extract_error_type(error_text: str | None) -> str | None:
    """Return the exception class name from the first line of a traceback."""
    if not error_text:
        return None
    for line in reversed(error_text.splitlines()):
        line = line.strip()
        if not line:
            continue
        # Match e.g. "ZeroDivisionError: division by zero"
        m = re.match(r"^([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*):", line)
        if m:
            return m.group(1)
    return None


def _is_env_hint(error_text: str | None) -> bool:
    if not error_text:
        return False
    return any(p.search(error_text) for p in _ENV_PATTERNS)


def _minutes_open(opened_at: str) -> int:
    """Return minutes elapsed since opened_at (UTC ISO-8601 string)."""
    try:
        opened = datetime.fromisoformat(opened_at)
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=UTC)
        delta = datetime.now(UTC) - opened
        return max(0, int(delta.total_seconds() / 60))
    except ValueError:
        return 0


def _previous_results(
    conn: sqlite3.Connection,
    session_id: str,
    test_name: str,
    before_event_id: int,
    limit: int = 5,
) -> list[str]:
    """Return the last `limit` results for a test in this session, oldest first."""
    rows = conn.execute(
        """
        SELECT result FROM events
        WHERE session_id = ?
          AND test_name = ?
          AND id < ?
          AND result IS NOT NULL
        ORDER BY id DESC
        LIMIT ?
        """,
        (session_id, test_name, before_event_id, limit),
    ).fetchall()
    return [r["result"] for r in reversed(rows)]


def _alternating_without_change(
    results: list[str], files_changed: list[str]
) -> bool:
    """True if the last 3 results flip between passed/failed with no file changes."""
    if len(results) < 3 or files_changed:
        return False
    last3 = results[-3:]
    # Check for alternating pattern
    return (
        last3[0] != last3[1]
        and last3[1] != last3[2]
    )


def _seconds_since_previous(
    conn: sqlite3.Connection,
    session_id: str,
    before_event_id: int,
    current_ts: str,
) -> int | None:
    """Return seconds between the current event and the previous one."""
    row = conn.execute(
        """
        SELECT ts FROM events
        WHERE session_id = ?
          AND id < ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (session_id, before_event_id),
    ).fetchone()
    if not row:
        return None
    try:
        t_prev = datetime.fromisoformat(row["ts"])
        t_curr = datetime.fromisoformat(current_ts)
        for t in (t_prev, t_curr):
            if t.tzinfo is None:
                t = t.replace(tzinfo=UTC)
        t_prev = t_prev if t_prev.tzinfo else t_prev.replace(tzinfo=UTC)
        t_curr = t_curr if t_curr.tzinfo else t_curr.replace(tzinfo=UTC)
        return max(0, int((t_curr - t_prev).total_seconds()))
    except ValueError:
        return None


def _open_defects(
    conn: sqlite3.Connection,
    session_id: str,
    event_files: list[str],
    limit: int = 5,
) -> list[OpenDefectRef]:
    """Return up to `limit` open defects, most recent first."""
    rows = conn.execute(
        """
        SELECT d.id, d.test_name, d.summary, d.opened_at,
               e.files_changed as opening_files
        FROM defects d
        JOIN events e ON e.id = d.opened_event_id
        WHERE d.session_id = ?
          AND d.status = 'open'
        ORDER BY d.id DESC
        LIMIT ?
        """,
        (session_id, limit),
    ).fetchall()

    result = []
    for row in rows:
        import json

        opening_files: list[str] = json.loads(row["opening_files"] or "[]")
        overlap = list(set(event_files) & set(opening_files))
        result.append(
            OpenDefectRef(
                id=row["id"],
                test_name=row["test_name"],
                summary=row["summary"],
                minutes_open=_minutes_open(row["opened_at"]),
                files_overlap=overlap,
            )
        )
    return result


def build_evidence(
    conn: sqlite3.Connection,
    event_id: int,
) -> Evidence:
    """Build an Evidence object for a pending event.

    Reads the event from the DB and collects supporting facts.
    Raises ValueError if the event is not found.
    """
    row = conn.execute(
        "SELECT * FROM events WHERE id = ?", (event_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Event {event_id} not found")

    import json

    event_files: list[str] = json.loads(row["files_changed"] or "[]")
    error_text: str | None = row["error_text"]
    prev_results = _previous_results(
        conn, row["session_id"], row["test_name"] or "", event_id
    )

    raw_excerpt = error_text[-500:] if error_text else None

    return Evidence(
        event_id=event_id,
        event_type=row["event_type"],
        test_name=row["test_name"],
        error_type=_extract_error_type(error_text),
        error_excerpt=raw_excerpt,
        files_changed=event_files,
        seconds_since_previous_event=_seconds_since_previous(
            conn, row["session_id"], event_id, row["ts"]
        ),
        previous_results_for_test=prev_results,
        alternating_without_code_change=_alternating_without_change(
            prev_results, event_files
        ),
        environment_hint=_is_env_hint(error_text),
        open_defects=_open_defects(conn, row["session_id"], event_files),
    )

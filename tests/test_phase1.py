"""Phase 1 tests: schema.sql, db.py, models.py, config.py, redact.py."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from bugprint.config import load
from bugprint.db import get_conn
from bugprint.models import (
    Claim,
    Confirmation,
    Decision,
    Defect,
    Event,
    Evidence,
    OpenDefectRef,
    Report,
    Session,
)
from bugprint.redact import redact

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    """Return a path to a fresh temporary SQLite database."""
    return tmp_path / "test.db"


# ---------------------------------------------------------------------------
# db.py — connection and schema bootstrap
# ---------------------------------------------------------------------------


class TestDb:
    def test_creates_db_and_tables(self, tmp_db: Path) -> None:
        with get_conn(tmp_db) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        expected = {
            "sessions",
            "events",
            "decisions",
            "defects",
            "confirmations",
            "interruptions",
            "reports",
        }
        assert expected <= tables

    def test_wal_mode(self, tmp_db: Path) -> None:
        with get_conn(tmp_db) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_foreign_keys_on(self, tmp_db: Path) -> None:
        with get_conn(tmp_db) as conn:
            fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1

    def test_idempotent_schema(self, tmp_db: Path) -> None:
        """Opening the same DB twice must not raise (IF NOT EXISTS)."""
        with get_conn(tmp_db):
            pass
        with get_conn(tmp_db):
            pass

    def test_rollback_on_error(self, tmp_db: Path) -> None:
        with get_conn(tmp_db) as conn:
            conn.execute(
                "INSERT INTO sessions(id, started_at) VALUES (?, ?)",
                ("s1", "2024-01-01T00:00:00+00:00"),
            )

        with pytest.raises(sqlite3.IntegrityError):
            with get_conn(tmp_db) as conn:
                # Duplicate PK — should rollback
                conn.execute(
                    "INSERT INTO sessions(id, started_at) VALUES (?, ?)",
                    ("s1", "2024-01-02T00:00:00+00:00"),
                )

        # Session s1 should still exist from the first insert
        with get_conn(tmp_db) as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE id='s1'"
            ).fetchone()
        assert row is not None

    def test_parent_dir_created(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "c" / "test.db"
        with get_conn(nested) as conn:
            conn.execute("SELECT 1")
        assert nested.exists()


# ---------------------------------------------------------------------------
# models.py
# ---------------------------------------------------------------------------


class TestSession:
    def test_defaults(self) -> None:
        s = Session(id="abc-123")
        assert s.started_at  # non-empty
        assert s.ended_at is None

    def test_fields(self) -> None:
        s = Session(
            id="x",
            started_at="2024-01-01T00:00:00+00:00",
            project_path="/home/dev/proj",
            git_commit="deadbeef",
        )
        assert s.project_path == "/home/dev/proj"


class TestEvent:
    def test_error_text_trimmed(self) -> None:
        long_text = "x" * 3000
        e = Event(session_id="s", event_type="test_failed", error_text=long_text)
        assert len(e.error_text) == 2000

    def test_files_changed_json(self) -> None:
        e = Event(
            session_id="s",
            event_type="test_failed",
            files_changed=["a.py", "b.py"],
        )
        assert json.loads(e.files_changed_json()) == ["a.py", "b.py"]

    def test_from_row(self, tmp_db: Path) -> None:
        with get_conn(tmp_db) as conn:
            conn.execute(
                "INSERT INTO sessions(id, started_at) VALUES (?, ?)",
                ("s1", "2024-01-01T00:00:00+00:00"),
            )
            conn.execute(
                """INSERT INTO events
                   (session_id, ts, event_type, files_changed, status)
                   VALUES (?, ?, ?, ?, ?)""",
                ("s1", "2024-01-01T00:00:01+00:00", "test_failed", '["x.py"]', "pending"),
            )
            row = conn.execute("SELECT * FROM events LIMIT 1").fetchone()
            ev = Event.from_row(row)
        assert ev.files_changed == ["x.py"]
        assert ev.event_type == "test_failed"


class TestEvidence:
    def test_excerpt_trimmed(self) -> None:
        e = Evidence(
            event_id=1,
            event_type="test_failed",
            error_excerpt="A" * 600,
        )
        assert len(e.error_excerpt) == 500

    def test_open_defect_ref(self) -> None:
        ref = OpenDefectRef(
            id=3,
            test_name="test_foo",
            summary="ValueError",
            minutes_open=10,
            files_overlap=["a.py"],
        )
        assert ref.files_overlap == ["a.py"]


class TestDecision:
    def test_json_helpers(self) -> None:
        d = Decision(
            event_id=1,
            question_id="next_tool",
            options=["a", "b"],
            chosen="a",
            probs={"a": 0.8, "b": 0.2},
            confidence=0.75,
            final_action="record_possible_defect",
        )
        assert json.loads(d.options_json()) == ["a", "b"]
        probs = json.loads(d.probs_json())
        assert abs(probs["a"] - 0.8) < 1e-9

    def test_is_fallback_default_false(self) -> None:
        d = Decision(
            event_id=1,
            question_id="next_tool",
            options=["a"],
            chosen="a",
            probs={"a": 1.0},
            confidence=1.0,
            final_action="a",
        )
        assert d.is_fallback is False


class TestDefect:
    def test_status_default(self) -> None:
        d = Defect(
            session_id="s",
            test_name="t",
            summary="err",
            opened_event_id=1,
        )
        assert d.status == "open"
        assert d.confirmed_by_user is False


class TestConfirmation:
    def test_proposed_args_json_none(self) -> None:
        c = Confirmation(
            event_id=1,
            decision_id=1,
            proposed_action="record_possible_defect",
            reason="not sure",
        )
        assert c.proposed_args_json() is None

    def test_proposed_args_json_dict(self) -> None:
        c = Confirmation(
            event_id=1,
            decision_id=1,
            proposed_action="link_fix_to_defect",
            proposed_args={"defect_id": 5},
            reason="possible fix",
        )
        assert json.loads(c.proposed_args_json()) == {"defect_id": 5}


class TestReport:
    def test_claims_json(self) -> None:
        r = Report(
            session_id="s",
            model_id="claude-3",
            input_stats={"failures": 3},
            summary="three failures",
            suggestion="write more tests",
            claims=[Claim(text="3 failures occurred", event_ids=[1, 2, 3])],
        )
        claims = json.loads(r.claims_json())
        assert claims[0]["event_ids"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# config.py
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Remove any env overrides
        for key in ("BUGPRINT_DB", "DECIDER_URL", "ACT_THRESHOLD", "ALLOW_FALLBACK"):
            monkeypatch.delenv(key, raising=False)
        cfg = load(toml_path=Path("nonexistent_config.toml"))
        assert cfg.db == ".bugprint/bugprint.db"
        assert cfg.decider_url == "http://127.0.0.1:8099"
        assert cfg.act_threshold == 0.70
        assert cfg.allow_fallback is False

    def test_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BUGPRINT_DB", "/tmp/test.db")
        monkeypatch.setenv("ACT_THRESHOLD", "0.85")
        monkeypatch.setenv("ALLOW_FALLBACK", "true")
        cfg = load(toml_path=Path("nonexistent_config.toml"))
        assert cfg.db == "/tmp/test.db"
        assert cfg.act_threshold == 0.85
        assert cfg.allow_fallback is True

    def test_toml_override(self, tmp_path: Path) -> None:
        toml = tmp_path / "bugprint.toml"
        toml.write_text(
            '[decider]\nact_threshold = 0.60\nurl = "http://localhost:9000"\n',
            encoding="utf-8",
        )
        cfg = load(toml_path=toml)
        assert cfg.act_threshold == 0.60
        assert cfg.decider_url == "http://localhost:9000"

    def test_env_beats_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        toml = tmp_path / "bugprint.toml"
        toml.write_text("[decider]\nact_threshold = 0.60\n", encoding="utf-8")
        monkeypatch.setenv("ACT_THRESHOLD", "0.95")
        cfg = load(toml_path=toml)
        assert cfg.act_threshold == 0.95


# ---------------------------------------------------------------------------
# redact.py
# ---------------------------------------------------------------------------


class TestRedact:
    def test_no_secrets(self) -> None:
        text = "ZeroDivisionError: division by zero at line 8"
        assert redact(text) == text

    def test_password_equals(self) -> None:
        result = redact("password=hunter2")
        assert "hunter2" not in result
        assert "[REDACTED]" in result

    def test_password_colon(self) -> None:
        result = redact("password: s3cr3t!")
        assert "s3cr3t!" not in result
        assert "[REDACTED]" in result

    def test_token_key(self) -> None:
        result = redact("token=eyJhbGciOiJIUzI1NiJ9.payload.sig")
        assert "eyJhbGciOiJIUzI1NiJ9" not in result

    def test_api_key(self) -> None:
        result = redact("api_key=abcdefghij1234567890")
        assert "abcdefghij" not in result

    def test_aws_access_key(self) -> None:
        result = redact("key: AKIAIOSFODNN7EXAMPLE rest of text")
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_bearer_token(self) -> None:
        result = redact("Authorization: Bearer mySecretToken123abc")
        assert "mySecretToken123abc" not in result

    def test_idempotent(self) -> None:
        text = "password=secret123"
        once = redact(text)
        twice = redact(once)
        assert once == twice

    def test_preserves_normal_error_context(self) -> None:
        text = "File 'main.py', line 42, in process\n  result = db.query(user_id)"
        assert redact(text) == text

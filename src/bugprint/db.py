"""Database connection and schema bootstrap.

Single responsibility: open the SQLite connection (WAL mode), apply the
schema on first use, and provide a thin context manager.  Nothing else goes here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

_SCHEMA = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")


def _open(db_path: str | Path) -> sqlite3.Connection:
    """Open a WAL-mode connection with row_factory set to Row."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


@contextmanager
def get_conn(db_path: str | Path) -> Generator[sqlite3.Connection, None, None]:
    """Context manager: yields an open connection, commits on success, rolls back on error."""
    conn = _open(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

"""Dual-mode SQLite connections: write connection vs strict read-only agent connection."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().with_name("schema.sql")


def get_write_conn(db_path: Path) -> sqlite3.Connection:
    """Open a read-write SQLite connection for offline ingestion, seed loading, and review_flags."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: Path) -> None:
    """Initialize SQLite schema and FTS5 triggers idempotently."""
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = get_write_conn(db_path)
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()


def set_readonly_deadline(conn: sqlite3.Connection, timeout_ms: int = 1500) -> None:
    """Attach a per-query wall-clock deadline progress handler to a read-only SQLite connection."""
    if timeout_ms <= 0:
        conn.set_progress_handler(None, 0)
        return
    deadline = time.monotonic() + (timeout_ms / 1000.0)

    def _abort_if_timed_out() -> int:
        return 1 if time.monotonic() > deadline else 0

    conn.set_progress_handler(_abort_if_timed_out, 5000)


def get_readonly_agent_conn(db_path: Path, timeout_ms: int = 1500) -> sqlite3.Connection:
    """
    Open a strictly read-only SQLite connection (`mode=ro` + `PRAGMA query_only=ON`).
    Used exclusively by agent tools (`search_disclosures`, `get_disclosure`, `run_sql`).
    """
    resolved = Path(db_path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"SQLite database does not exist: {resolved}")

    readonly_uri = f"{resolved.as_uri()}?mode=ro"
    conn = sqlite3.connect(readonly_uri, uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    set_readonly_deadline(conn, timeout_ms=timeout_ms)
    return conn

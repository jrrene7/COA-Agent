"""Durable, run_id-scoped storage for pipeline runs.

Uses stdlib sqlite3 so the project gains no new dependency while still getting
indexed queries for KPI aggregation. Everything is keyed by `run_id`, the UUID4
the Orchestrator mints per lead, so a run can be reconstructed or audited after
the process exits.
"""

import json
import logging
import os
import sqlite3
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from lib.logging_config import redact_text

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent / "runs" / "runs.db"

DIRECTION_OUTBOUND = "outbound"
DIRECTION_INBOUND = "inbound"

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_INCOMPLETE = "incomplete"
STATUS_FAILED = "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id             TEXT PRIMARY KEY,
    company            TEXT NOT NULL,
    url                TEXT,
    status             TEXT NOT NULL,
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    duration_seconds   REAL,
    report_path        TEXT,
    error              TEXT,
    llm_calls          INTEGER NOT NULL DEFAULT 0,
    prompt_tokens      INTEGER NOT NULL DEFAULT 0,
    completion_tokens  INTEGER NOT NULL DEFAULT 0,
    total_tokens       INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL    NOT NULL DEFAULT 0,
    step_retries       INTEGER NOT NULL DEFAULT 0,
    feedback_loops     INTEGER NOT NULL DEFAULT 0,
    escalations        INTEGER NOT NULL DEFAULT 0,
    direction          TEXT    NOT NULL DEFAULT 'outbound',
    queue              TEXT,
    priority           TEXT,
    owner              TEXT,
    sentiment          TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    step_id     TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    state_json  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS step_metrics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL,
    step             TEXT NOT NULL,
    status           TEXT NOT NULL,
    duration_seconds REAL,
    recorded_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_run ON snapshots(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_step_metrics_run ON step_metrics(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    """SQLite-backed store for run state, snapshots, and per-step metrics."""

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._lock = threading.Lock()

        if self.db_path.parent != Path(":memory:"):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()
        self._restrict_permissions()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created.

        CREATE TABLE IF NOT EXISTS does nothing to an existing table, so a store
        written by an older build would be missing newer columns entirely. Only
        additive migrations are supported — see docs/deployment.md.
        """
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        additions = {
            "escalations": "INTEGER NOT NULL DEFAULT 0",
            "direction": "TEXT NOT NULL DEFAULT 'outbound'",
            "queue": "TEXT",
            "priority": "TEXT",
            "owner": "TEXT",
            "sentiment": "TEXT",
        }
        for column, ddl in additions.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {ddl}")
                logger.info("run_store_migrated added_column=%s", column)

    def _restrict_permissions(self) -> None:
        """Runs hold scraped lead data — keep the database owner-only."""
        try:
            os.chmod(self.db_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            logger.warning("Could not restrict permissions on %s", self.db_path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "RunStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # Write path
    def start_run(
        self,
        run_id: str,
        company: str,
        url: str = "",
        direction: str = DIRECTION_OUTBOUND,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO runs "
                "(run_id, company, url, status, started_at, direction) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, company, url, STATUS_RUNNING, _now(), direction),
            )
            self._conn.commit()

    def record_snapshot(self, run_id: str, seq: int, step_id: str, state_update: dict) -> None:
        """Persist one node's state update.

        Secret-shaped strings are scrubbed on the way in; PII is deliberately
        left intact so a stored run still reconstructs faithfully. The database
        file is owner-only for that reason.
        """
        payload = redact_text(json.dumps(state_update, default=str))
        with self._lock:
            self._conn.execute(
                "INSERT INTO snapshots (run_id, seq, step_id, recorded_at, state_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, seq, step_id, _now(), payload),
            )
            self._conn.commit()

    def record_step_metric(
        self,
        run_id: str,
        step: str,
        status: str,
        duration_seconds: Optional[float] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO step_metrics (run_id, step, status, duration_seconds, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, step, status, duration_seconds, _now()),
            )
            self._conn.commit()

    def finish_run(
        self,
        run_id: str,
        status: str,
        report_path: str = "",
        error: str = "",
        duration_seconds: Optional[float] = None,
        totals: Optional[dict] = None,
    ) -> None:
        totals = totals or {}
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET status = ?, finished_at = ?, duration_seconds = ?, "
                "report_path = ?, error = ?, llm_calls = ?, prompt_tokens = ?, "
                "completion_tokens = ?, total_tokens = ?, estimated_cost_usd = ?, "
                "step_retries = ?, feedback_loops = ?, escalations = ?, "
                "queue = ?, priority = ?, owner = ?, sentiment = ? WHERE run_id = ?",
                (
                    status,
                    _now(),
                    duration_seconds,
                    report_path,
                    redact_text(error) if error else "",
                    totals.get("llm_calls", 0),
                    totals.get("prompt_tokens", 0),
                    totals.get("completion_tokens", 0),
                    totals.get("total_tokens", 0),
                    totals.get("estimated_cost_usd", 0.0),
                    totals.get("step_retries", 0),
                    totals.get("feedback_loops", 0),
                    totals.get("escalations", 0),
                    totals.get("queue") or None,
                    totals.get("priority") or None,
                    totals.get("owner") or None,
                    totals.get("sentiment") or None,
                    run_id,
                ),
            )
            self._conn.commit()

    # Read path
    def get_run(self, run_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_snapshots(self, run_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, step_id, recorded_at, state_json FROM snapshots "
                "WHERE run_id = ? ORDER BY seq",
                (run_id,),
            ).fetchall()
        return [
            {
                "seq": r["seq"],
                "step_id": r["step_id"],
                "recorded_at": r["recorded_at"],
                "state": json.loads(r["state_json"]),
            }
            for r in rows
        ]

    def get_step_metrics(self, run_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT step, status, duration_seconds, recorded_at "
                "FROM step_metrics WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def reconstruct_state(self, run_id: str) -> dict:
        """Replay stored snapshots in order into the final merged state."""
        state: dict[str, Any] = {}
        for snap in self.get_snapshots(run_id):
            state.update(snap["state"])
        return state

    def recent_runs(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

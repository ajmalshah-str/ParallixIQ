import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT_DIR / "parallix_audit.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditDatabase:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self._init_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_tables(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id TEXT NOT NULL,
                    dataset_version TEXT NOT NULL,
                    total_cases INTEGER NOT NULL,
                    exact_match_rate REAL NOT NULL,
                    field_precision REAL NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evaluation_logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER,
                    test_id TEXT NOT NULL,
                    language TEXT NOT NULL,
                    category TEXT NOT NULL,
                    user_input TEXT NOT NULL,
                    model_output_json TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    score REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS gateway_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_input TEXT NOT NULL,
                    proposed_action_json TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    risk_score REAL NOT NULL,
                    reasons_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS failure_incidents (
                    incident_id TEXT PRIMARY KEY,
                    engine_source TEXT NOT NULL,
                    user_input TEXT NOT NULL,
                    expected_summary TEXT NOT NULL,
                    actual_summary TEXT NOT NULL,
                    failure_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    explanation TEXT NOT NULL,
                    recommendation TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    resolved INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tenant_usage (
                    tenant_id TEXT PRIMARY KEY,
                    call_count INTEGER NOT NULL DEFAULT 0,
                    last_called_at TEXT NOT NULL
                );
                """
            )

    def log_benchmark_run(
        self,
        model_id: str,
        dataset_version: str,
        total_cases: int,
        exact_match_rate: float,
        field_precision: float,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO benchmark_runs
                (model_id, dataset_version, total_cases, exact_match_rate, field_precision, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (model_id, dataset_version, total_cases, exact_match_rate, field_precision, _now_iso()),
            )
            return int(cur.lastrowid)

    def get_recent_runs(self, model_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM benchmark_runs
                WHERE model_id = ?
                ORDER BY run_id DESC
                LIMIT ?
                """,
                (model_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def log_gateway_decision(
        self,
        session_id: str,
        user_input: str,
        proposed_action: dict[str, Any],
        decision: str,
        risk_score: float,
        reasons: list[str],
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO gateway_audit
                (session_id, user_input, proposed_action_json, decision, risk_score, reasons_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    user_input,
                    json.dumps(proposed_action, ensure_ascii=False),
                    decision,
                    risk_score,
                    json.dumps(reasons, ensure_ascii=False),
                    _now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def log_failure_incident(self, report_dict: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO failure_incidents
                (incident_id, engine_source, user_input, expected_summary, actual_summary,
                 failure_type, severity, confidence, explanation, recommendation, evidence_json, resolved, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_dict["incident_id"],
                    report_dict["engine_source"],
                    report_dict["user_input"],
                    report_dict["expected_summary"],
                    report_dict["actual_summary"],
                    report_dict["failure_type"],
                    report_dict["severity"],
                    report_dict["confidence"],
                    report_dict["explanation"],
                    report_dict["recommendation"],
                    json.dumps(report_dict.get("evidence", {}), ensure_ascii=False),
                    0,
                    _now_iso(),
                ),
            )

    def get_unresolved_incidents(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM failure_incidents
                WHERE resolved = 0
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_summary_counts(self) -> dict[str, int]:
        tables = ["benchmark_runs", "evaluation_logs", "gateway_audit", "failure_incidents"]
        counts = {}
        with self._connect() as conn:
            for tbl in tables:
                row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {tbl}").fetchone()
                counts[tbl] = int(row["cnt"])
        return counts

    def increment_tenant_usage(self, tenant_id: str = "demo_tenant") -> int:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tenant_usage (tenant_id, call_count, last_called_at)
                VALUES (?, 1, ?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    call_count = call_count + 1,
                    last_called_at = excluded.last_called_at
                """,
                (tenant_id, _now_iso()),
            )
            row = conn.execute(
                "SELECT call_count FROM tenant_usage WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            return int(row["call_count"]) if row else 1

    def get_tenant_usage(self, tenant_id: str = "demo_tenant") -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT call_count FROM tenant_usage WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            return int(row["call_count"]) if row else 0
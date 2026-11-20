import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT_DIR / "parallix_audit.db"
GENESIS_HASH = "0" * 64


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_audit_hash(
    prev_hash: str,
    session_id: str,
    user_input: str,
    proposed_action_json: str,
    decision: str,
    risk_score: float,
    reasons_json: str,
    created_at: str,
) -> str:
    canonical = json.dumps(
        {
            "prev_hash": prev_hash,
            "session_id": session_id,
            "user_input": user_input,
            "proposed_action_json": proposed_action_json,
            "decision": decision,
            "risk_score": round(float(risk_score), 6),
            "reasons_json": reasons_json,
            "created_at": created_at,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditDatabase:
    def __init__(self, db_path: str | Path | None = None):
        env_path = os.getenv("PARALLIX_DB_PATH", "").strip()
        resolved = db_path if db_path is not None else (env_path if env_path else DEFAULT_DB_PATH)
        self.db_path = Path(resolved)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def get_persistence_status(self) -> dict[str, Any]:
        external_db = bool(os.getenv("DATABASE_URL", "").strip())
        is_cloud_run = bool(os.getenv("K_SERVICE", "").strip())
        chain = self.verify_audit_chain()
        return {
            "storage_backend": "sqlite_local",
            "db_path": str(self.db_path),
            "durable": False,
            "cloud_run_environment": is_cloud_run,
            "external_database_configured": external_db,
            "target_production_backend": "cloud_sql_postgresql",
            "migration_status": "pending_external_db_provisioning",
            "audit_chain_valid": chain["valid"],
            "audit_chain_records": chain["total_records"],
            "audit_chain_head": chain["head_hash"],
            "warning": (
                "Local SQLite storage is ephemeral on Cloud Run and isolated per container instance. "
                "Provision Cloud SQL (PostgreSQL) via DATABASE_URL for durable multi-instance persistence."
            ),
        }

    def _init_tables(self) -> None:
        with closing(self._connect()) as conn:
            with conn:
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
                        created_at TEXT NOT NULL,
                        prev_hash TEXT NOT NULL DEFAULT '',
                        record_hash TEXT NOT NULL DEFAULT ''
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
                cols = {row["name"] for row in conn.execute("PRAGMA table_info(gateway_audit)").fetchall()}
                if "prev_hash" not in cols:
                    conn.execute("ALTER TABLE gateway_audit ADD COLUMN prev_hash TEXT NOT NULL DEFAULT ''")
                if "record_hash" not in cols:
                    conn.execute("ALTER TABLE gateway_audit ADD COLUMN record_hash TEXT NOT NULL DEFAULT ''")

                # Backfill any legacy rows that lack hashes so existing databases stay chain-valid
                rows = conn.execute(
                    "SELECT * FROM gateway_audit ORDER BY audit_id ASC"
                ).fetchall()
                prev = GENESIS_HASH
                for row in rows:
                    if not row["record_hash"]:
                        rec_hash = _compute_audit_hash(
                            prev_hash=prev,
                            session_id=row["session_id"],
                            user_input=row["user_input"],
                            proposed_action_json=row["proposed_action_json"],
                            decision=row["decision"],
                            risk_score=row["risk_score"],
                            reasons_json=row["reasons_json"],
                            created_at=row["created_at"],
                        )
                        conn.execute(
                            "UPDATE gateway_audit SET prev_hash = ?, record_hash = ? WHERE audit_id = ?",
                            (prev, rec_hash, row["audit_id"]),
                        )
                        prev = rec_hash
                    else:
                        prev = row["record_hash"]

    def log_benchmark_run(
        self,
        model_id: str,
        dataset_version: str,
        total_cases: int,
        exact_match_rate: float,
        field_precision: float,
    ) -> int:
        with closing(self._connect()) as conn:
            with conn:
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
        with closing(self._connect()) as conn:
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
        action_json = json.dumps(proposed_action, ensure_ascii=False)
        reasons_json = json.dumps(reasons, ensure_ascii=False)
        created_at = _now_iso()
        with closing(self._connect()) as conn:
            with conn:
                last = conn.execute(
                    "SELECT record_hash FROM gateway_audit ORDER BY audit_id DESC LIMIT 1"
                ).fetchone()
                prev_hash = last["record_hash"] if (last and last["record_hash"]) else GENESIS_HASH
                rec_hash = _compute_audit_hash(
                    prev_hash=prev_hash,
                    session_id=session_id,
                    user_input=user_input,
                    proposed_action_json=action_json,
                    decision=decision,
                    risk_score=risk_score,
                    reasons_json=reasons_json,
                    created_at=created_at,
                )
                cur = conn.execute(
                    """
                    INSERT INTO gateway_audit
                    (session_id, user_input, proposed_action_json, decision, risk_score, reasons_json, created_at, prev_hash, record_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        user_input,
                        action_json,
                        decision,
                        risk_score,
                        reasons_json,
                        created_at,
                        prev_hash,
                        rec_hash,
                    ),
                )
                return int(cur.lastrowid)

    def verify_audit_chain(self) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM gateway_audit ORDER BY audit_id ASC"
            ).fetchall()

        expected_prev = GENESIS_HASH
        for row in rows:
            if row["prev_hash"] != expected_prev:
                return {
                    "valid": False,
                    "total_records": len(rows),
                    "head_hash": expected_prev,
                    "broken_audit_id": int(row["audit_id"]),
                    "reason": f"prev_hash mismatch at audit_id={row['audit_id']}",
                }
            recomputed = _compute_audit_hash(
                prev_hash=row["prev_hash"],
                session_id=row["session_id"],
                user_input=row["user_input"],
                proposed_action_json=row["proposed_action_json"],
                decision=row["decision"],
                risk_score=row["risk_score"],
                reasons_json=row["reasons_json"],
                created_at=row["created_at"],
            )
            if row["record_hash"] != recomputed:
                return {
                    "valid": False,
                    "total_records": len(rows),
                    "head_hash": expected_prev,
                    "broken_audit_id": int(row["audit_id"]),
                    "reason": f"record_hash tamper detected at audit_id={row['audit_id']}",
                }
            expected_prev = row["record_hash"]

        return {
            "valid": True,
            "total_records": len(rows),
            "head_hash": expected_prev,
            "broken_audit_id": None,
            "reason": None,
        }

    def get_verified_audit_logs(self, limit: int = 20) -> dict[str, Any]:
        integrity = self.verify_audit_chain()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM gateway_audit ORDER BY audit_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return {
            "integrity": integrity,
            "records": [dict(r) for r in rows],
        }

    def log_failure_incident(self, report_dict: dict[str, Any]) -> None:
        with closing(self._connect()) as conn:
            with conn:
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
        with closing(self._connect()) as conn:
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
        with closing(self._connect()) as conn:
            for tbl in tables:
                row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {tbl}").fetchone()
                counts[tbl] = int(row["cnt"])
        return counts

    def increment_tenant_usage(self, tenant_id: str = "demo_tenant") -> int:
        with closing(self._connect()) as conn:
            with conn:
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
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT call_count FROM tenant_usage WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            return int(row["call_count"]) if row else 0

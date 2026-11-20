import os
import tempfile
import threading
import unittest
from pathlib import Path

from storage.audit_db import AuditDatabase


class TestDatabasePersistenceAndConcurrency(unittest.TestCase):
    def test_persistent_disk_survives_reinitialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_file = Path(tmp_dir) / "persistent_audit.db"
            db1 = AuditDatabase(db_path=db_file)
            db1.log_gateway_decision(
                session_id="sess_persist",
                user_input="Naale Rahulinu 500 roopa ayakkaruthu",
                proposed_action={"intent": "transfer", "amount": 500},
                decision="BLOCK",
                risk_score=0.95,
                reasons=["Negative polarity detected"],
            )
            count1 = db1.increment_tenant_usage("tenant_alpha")
            self.assertEqual(count1, 1)

            # Simulate process restart against same persistent volume path
            db2 = AuditDatabase(db_path=db_file)
            self.assertEqual(db2.get_summary_counts()["gateway_audit"], 1)
            self.assertEqual(db2.get_tenant_usage("tenant_alpha"), 1)

    def test_ephemeral_container_wipe_resets_local_sqlite(self) -> None:
        # Simulates Cloud Run container recycle where local filesystem is recreated
        with tempfile.TemporaryDirectory() as container_fs_1:
            db1 = AuditDatabase(db_path=Path(container_fs_1) / "parallix_audit.db")
            db1.increment_tenant_usage("tenant_alpha")
            self.assertEqual(db1.get_tenant_usage("tenant_alpha"), 1)

        with tempfile.TemporaryDirectory() as container_fs_2:
            db2 = AuditDatabase(db_path=Path(container_fs_2) / "parallix_audit.db")
            self.assertEqual(db2.get_tenant_usage("tenant_alpha"), 0)
            status = db2.get_persistence_status()
            self.assertFalse(status["durable"])
            self.assertEqual(status["target_production_backend"], "cloud_sql_postgresql")

    def test_concurrent_writes_and_metered_increments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = AuditDatabase(db_path=Path(tmp_dir) / "concurrent_audit.db")
            errors: list[Exception] = []

            def worker(idx: int) -> None:
                try:
                    db.log_gateway_decision(
                        session_id=f"worker_{idx}",
                        user_input=f"Send {idx * 10} to Rahul",
                        proposed_action={"amount": idx * 10},
                        decision="PASS",
                        risk_score=0.05,
                        reasons=["ok"],
                    )
                    db.increment_tenant_usage("concurrent_tenant")
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            self.assertEqual(db.get_summary_counts()["gateway_audit"], 20)
            self.assertEqual(db.get_tenant_usage("concurrent_tenant"), 20)

    def test_postgres_migration_schema_contains_all_tables(self) -> None:
        sql = Path("storage/postgres_schema.sql").read_text(encoding="utf-8")
        for table in ("benchmark_runs", "evaluation_logs", "gateway_audit", "failure_incidents", "tenant_usage"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", sql)


if __name__ == "__main__":
    unittest.main()

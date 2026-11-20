import json
import os
import sqlite3
import tempfile
import unittest

import dashboard
from core.benchmark_audit import run_reproducible_audit
from engines.adaptive_gen import AdaptiveTestGenerator
from engines.explainability import ExplainabilityEngine
from engines.hallucination import HallucinationDetector
from gateway.interceptor import ActionInterceptionGateway
from gateway.simulated_bank import SimulatedBankApp
from storage.audit_db import AuditDatabase
from storage.schemas import ExtractedAction, GatewayDecision


class TestPlatformCorrectionsAndHardening(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.tmp_dir.name, "corrections_test.db")
        self.db = AuditDatabase(db_path=self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_benchmark_baseline_consistency_and_pipeline_labels(self) -> None:
        report = run_reproducible_audit(use_live_gemini=False)
        self.assertEqual(report["overall"]["exact_match_accuracy"], 4.0)
        self.assertEqual(report["overall"]["slot_agreement_rate"], 59.5)

        html = dashboard.HTML_PAGE
        self.assertNotIn("7.41%", html)
        self.assertNotIn("Stage 2 • Engine 6", html)
        self.assertIn("Stage 2 • Structured Extraction (Pydantic)", html)
        self.assertIn("59.50%", html)
        self.assertIn("93.83% (563/600 slots)", html)
        self.assertIn("SIMULATED SANDBOX DATA", html)
        self.assertIn("ILLUSTRATIVE PRICING", html)

    def test_gateway_bypass_unauthorized_duplicates_missing_fields_and_db_failure(self) -> None:
        gw = ActionInterceptionGateway(db=self.db)
        bank = SimulatedBankApp(gateway=gw, starting_balance=50000.0)
        valid_act = ExtractedAction(intent="transfer", recipient="Rahul", amount=1000.0, currency="INR", polarity="positive")

        forged = GatewayDecision(decision="PASS", risk_score=0.0, reasons=["forged"], requires_human_review=False)
        with self.assertRaises(PermissionError):
            bank.execute_verified_transfer(valid_act, forged)
        self.assertEqual(bank.balances["user_wallet"], 50000.0)

        real_pass = gw.inspect_action("Send 1000 to Rahul", valid_act)
        first_exec = bank.execute_verified_transfer(valid_act, real_pass)
        self.assertEqual(first_exec["execution_status"], "EXECUTED")
        self.assertEqual(bank.balances["user_wallet"], 49000.0)
        with self.assertRaises(PermissionError):
            bank.execute_verified_transfer(valid_act, real_pass)
        self.assertEqual(bank.balances["user_wallet"], 49000.0)

        with self.assertRaises(PermissionError):
            gw.authorize_human_override("Send 60000 to Rahul", valid_act, supervisor_id="")

        dup_act = ExtractedAction(intent="transfer", recipient="Rajan", amount=500.0, currency="INR", polarity="positive")
        r1 = bank.process_proposed_action("Send 500 to Rajan", dup_act, idempotency_key="idem-dup-1")
        r2 = bank.process_proposed_action("Send 500 to Rajan", dup_act, idempotency_key="idem-dup-1")
        self.assertEqual(r1["execution_status"], "EXECUTED")
        self.assertTrue(r2.get("duplicate_prevented"))
        self.assertEqual(bank.balances["user_wallet"], 48500.0)

        for bad_act in (
            ExtractedAction(intent="transfer", recipient=None, amount=500.0, currency="INR", polarity="positive"),
            ExtractedAction(intent="transfer", recipient="Rahul", amount=None, currency="INR", polarity="positive"),
            ExtractedAction(intent="transfer", recipient="Rahul", amount=500.0, currency="BTC", polarity="positive"),
        ):
            res = bank.process_proposed_action("Transfer funds", bad_act)
            self.assertEqual(res["gateway_decision"], "BLOCK")
            self.assertEqual(res["execution_status"], "BLOCKED")
            self.assertEqual(bank.balances["user_wallet"], 48500.0)

        hold_act = ExtractedAction(intent="transfer", recipient="Rahul", amount=60000.0, currency="INR", polarity="positive")
        hold_res = bank.process_proposed_action("Send 60000 to Rahul", hold_act)
        self.assertEqual(hold_res["gateway_decision"], "HOLD")
        self.assertEqual(hold_res["execution_status"], "HELD_FOR_APPROVAL")
        self.assertEqual(bank.balances["user_wallet"], 48500.0)

        def broken_db_log(*args, **kwargs):
            raise sqlite3.OperationalError("Simulated disk I/O failure")

        self.db.log_gateway_decision = broken_db_log
        db_fail_res = bank.process_proposed_action("Send 200 to Rahul", dup_act)
        self.assertEqual(db_fail_res["gateway_decision"], "BLOCK")
        self.assertEqual(bank.balances["user_wallet"], 48500.0)

    def test_hash_chained_audit_log_verification_and_tamper_detection(self) -> None:
        gw = ActionInterceptionGateway(db=self.db)
        act1 = ExtractedAction(intent="transfer", recipient="Rajan", amount=500.0, currency="INR", polarity="positive")
        act2 = ExtractedAction(intent="transfer", recipient="Rahul", amount=500.0, currency="INR", polarity="negative")
        gw.inspect_action("Rajan 500 ayakk", act1, session_id="chain_test")
        gw.inspect_action("Naale Rahulinu 500 roopa ayakkaruthu", act2, session_id="chain_test")

        valid_check = self.db.verify_audit_chain()
        self.assertTrue(valid_check["valid"])
        self.assertEqual(valid_check["total_records"], 2)
        self.assertIsNone(valid_check["broken_audit_id"])

        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE gateway_audit SET decision = 'PASS' WHERE audit_id = 2")
        conn.commit()
        conn.close()

        tamper_check = self.db.verify_audit_chain()
        self.assertFalse(tamper_check["valid"])
        self.assertEqual(tamper_check["broken_audit_id"], 2)

    def test_engine2_four_way_claim_classification(self) -> None:
        det = HallucinationDetector()
        evidence = {"account_balance": 2500, "daily_limit": 50000}
        reply = (
            "Your account balance is 2500 rupees. "
            "Your daily limit is 100000 rupees. "
            "RBI changed UPI rules yesterday. "
            "I think you should save more."
        )
        res = det.evaluate(reply, evidence)
        statuses = [c.status for c in res.claims]
        self.assertEqual(statuses, ["VERIFIED", "CONTRADICTED", "UNSUPPORTED", "UNVERIFIABLE"])
        self.assertEqual(res.hallucination_risk, "high")

    def test_engine8_end_to_end_failure_to_versioned_regression_suite(self) -> None:
        gw = ActionInterceptionGateway(db=self.db)
        explainer = ExplainabilityEngine(db=self.db)
        bad_act = ExtractedAction(intent="transfer", recipient="Rahul", amount=500.0, currency="INR", polarity="positive")
        dec = gw.inspect_action(
            "Naale Rahulinu 500 roopa ayakkaruthu",
            ExtractedAction(intent="transfer", recipient="Rahul", amount=500.0, currency="INR", polarity="negative"),
        )
        inc = explainer.explain_gateway_block("Naale Rahulinu 500 roopa ayakkaruthu", bad_act, dec)

        gen = AdaptiveTestGenerator(db=self.db, datasets_dir=self.tmp_dir.name)
        cases = gen.generate_from_failures(max_cases=3)
        self.assertGreaterEqual(len(cases), 1)
        self.assertEqual(cases[0].source_incident_id, inc.incident_id)
        prompts = [c.input for c in cases]
        self.assertEqual(len(prompts), len(set(prompts)))

        saved_path = gen.save_versioned_dataset(cases, ["gateway_block"])
        self.assertTrue(saved_path.exists())
        saved_json = json.loads(saved_path.read_text(encoding="utf-8"))
        self.assertEqual(saved_json["dataset_version"], "adaptive_v1")
        self.assertEqual(saved_json["total_cases"], len(cases))


if __name__ == "__main__":
    unittest.main()

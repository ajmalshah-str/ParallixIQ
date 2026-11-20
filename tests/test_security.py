import http.client
import json
import os
import tempfile
import threading
import unittest

import dashboard
from gateway.interceptor import ActionInterceptionGateway
from gateway.security import ApiKeyManager, SlidingWindowRateLimiter, redact_secrets, sanitize_text
from gateway.simulated_bank import SimulatedBankApp
from storage.audit_db import AuditDatabase
from storage.schemas import ExtractedAction, GatewayDecision


class TestSecurityAndGatewayHardening(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.tmp_dir.name, "sec_test.db")
        self.db = AuditDatabase(db_path=self.db_path)

    def tearDown(self) -> None:
        for attr in ("conn", "_conn", "connection"):
            handle = getattr(self.db, attr, None)
            if handle is not None and hasattr(handle, "close"):
                try:
                    handle.close()
                except Exception:
                    pass
        self.tmp_dir.cleanup()

    def test_api_key_manager_revokes_legacy_and_validates_configured(self) -> None:
        mgr = ApiKeyManager(configured_keys={"acme_bank": "secret_acme_token_2026"})
        ok, tenant, _ = mgr.validate_key(None)
        self.assertFalse(ok)
        self.assertIsNone(tenant)

        ok, tenant, reason = mgr.validate_key("plx_live_growth_9482")
        self.assertFalse(ok)
        self.assertIn("revoked", reason.lower())

        ok, tenant, _ = mgr.validate_key("wrong_token")
        self.assertFalse(ok)

        ok, tenant, _ = mgr.validate_key("secret_acme_token_2026")
        self.assertTrue(ok)
        self.assertEqual(tenant.tenant_id, "acme_bank")

    def test_api_key_manager_fails_closed_when_unconfigured(self) -> None:
        mgr = ApiKeyManager(configured_keys={})
        ok, tenant, reason = mgr.validate_key("any_random_key")
        self.assertFalse(ok)
        self.assertIsNone(tenant)
        self.assertIn("fail-closed", reason.lower())

    def test_secret_redaction_and_input_sanitization(self) -> None:
        raw = "Call with AIzaSyD1234567890abcdefghijklmno and plx_live_growth_9482"
        scrubbed = redact_secrets(raw)
        self.assertNotIn("AIzaSyD1234567890abcdefghijklmno", scrubbed)
        self.assertNotIn("plx_live_growth_9482", scrubbed)
        self.assertIn("[REDACTED_CREDENTIAL]", scrubbed)

        mal = "നാളെ രാഹുലിന് 500 രൂപ\x00 അയക്കരുത്"
        self.assertEqual(sanitize_text(mal), "നാളെ രാഹുലിന് 500 രൂപ അയക്കരുത്")

        with self.assertRaises(ValueError):
            sanitize_text("x" * 5000)

    def test_gateway_bypass_and_fail_closed_protection(self) -> None:
        gw = ActionInterceptionGateway(db=self.db)
        bank = SimulatedBankApp(gateway=gw, starting_balance=10000.0)
        act = ExtractedAction(intent="transfer", recipient="Rahul", amount=500.0, currency="INR", polarity="positive")

        forged_pass = GatewayDecision(decision="PASS", risk_score=0.0, reasons=["forged"], requires_human_review=False)
        with self.assertRaises(PermissionError):
            bank.execute_verified_transfer(act, forged_pass)
        self.assertEqual(bank.balances["user_wallet"], 10000.0)

        def broken_eval(*args, **kwargs):
            raise RuntimeError("Simulated rule engine crash")

        gw._evaluate_action = broken_eval
        res = bank.process_proposed_action("Send 500 to Rahul", act)
        self.assertEqual(res["gateway_decision"], "BLOCK")
        self.assertEqual(bank.balances["user_wallet"], 10000.0)

    def test_simulated_bank_idempotency_prevents_double_spend(self) -> None:
        gw = ActionInterceptionGateway(db=self.db)
        bank = SimulatedBankApp(gateway=gw, starting_balance=10000.0)
        act = ExtractedAction(intent="transfer", recipient="Rahul", amount=500.0, currency="INR", polarity="positive")

        first = bank.process_proposed_action("Send 500 to Rahul", act, idempotency_key="tx-001")
        self.assertEqual(first["execution_status"], "EXECUTED")
        self.assertEqual(bank.balances["user_wallet"], 9500.0)

        second = bank.process_proposed_action("Send 500 to Rahul", act, idempotency_key="tx-001")
        self.assertTrue(second.get("duplicate_prevented"))
        self.assertEqual(bank.balances["user_wallet"], 9500.0)

        different_act = ExtractedAction(intent="transfer", recipient="Rahul", amount=1500.0, currency="INR", polarity="positive")
        conflict = bank.process_proposed_action("Send 1500 to Rahul", different_act, idempotency_key="tx-001")
        self.assertEqual(conflict["execution_status"], "REJECTED_IDEMPOTENCY_CONFLICT")
        self.assertEqual(bank.balances["user_wallet"], 9500.0)

    def test_http_endpoints_auth_validation_and_rate_limiting(self) -> None:
        orig_mgr = dashboard.CENTER.api_key_manager
        orig_limiter = dashboard.CommandCenterHandler.rate_limiter
        orig_adapter = dashboard.CENTER.adapter
        dashboard.CENTER.api_key_manager = ApiKeyManager(configured_keys={"test_client": "unit_test_secret_key"})
        dashboard.CommandCenterHandler.rate_limiter = SlidingWindowRateLimiter(max_requests=20, window_seconds=60)
        dashboard.CENTER.adapter = None

        server = dashboard.HTTPServer(("127.0.0.1", 0), dashboard.CommandCenterHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/health")
            r = conn.getresponse()
            body = json.loads(r.read().decode("utf-8"))
            self.assertEqual(r.status, 200)
            self.assertEqual(body["status"], "ok")
            conn.close()

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/openapi.json")
            r = conn.getresponse()
            spec = json.loads(r.read().decode("utf-8"))
            self.assertEqual(r.status, 200)
            self.assertIn("/v1/audit", spec["paths"])
            conn.close()

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/v1/audit", body=json.dumps({"text": "Send 500 to Rahul"}), headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            r.read()
            self.assertEqual(r.status, 401)
            conn.close()

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/v1/audit",
                body=json.dumps({"text": "Send 500 to Rahul"}),
                headers={"Content-Type": "application/json", "X-Parallix-Key": "plx_live_growth_9482"},
            )
            r = conn.getresponse()
            r.read()
            self.assertEqual(r.status, 401)
            conn.close()

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/v1/audit",
                body="{bad-json",
                headers={"Content-Type": "application/json", "X-Parallix-Key": "unit_test_secret_key"},
            )
            r = conn.getresponse()
            r.read()
            self.assertEqual(r.status, 400)
            conn.close()

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/v1/audit",
                body=json.dumps({"text": "Naale Rahulinu 500 roopa ayakkaruthu", "profile": "banking_upi"}),
                headers={"Content-Type": "application/json", "X-Parallix-Key": "unit_test_secret_key"},
            )
            r = conn.getresponse()
            raw_resp = r.read().decode("utf-8")
            parsed = json.loads(raw_resp)
            self.assertEqual(r.status, 200)
            self.assertEqual(parsed["decision"], "BLOCK")
            self.assertEqual(parsed["tenant_id"], "test_client")
            self.assertNotIn("unit_test_secret_key", raw_resp)
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            dashboard.CENTER.api_key_manager = orig_mgr
            dashboard.CommandCenterHandler.rate_limiter = orig_limiter
            dashboard.CENTER.adapter = orig_adapter


if __name__ == "__main__":
    unittest.main()

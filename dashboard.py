import json
import os
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from adapters.gemini_adapter import GeminiAdapter
from adapters.planned_adapters import build_comparison_table
from engines.adaptive_gen import AdaptiveTestGenerator
from engines.counterfactual import CounterfactualEngine
from engines.drift_monitor import DriftMonitor
from engines.explainability import ExplainabilityEngine
from engines.hallucination import HallucinationDetector
from engines.intent_integrity import IntentIntegrityEngine
from gateway.interceptor import ActionInterceptionGateway
from gateway.security import (
    ApiKeyManager,
    IdempotencyStore,
    MAX_BODY_BYTES,
    SlidingWindowRateLimiter,
    redact_secrets,
    sanitize_text,
)
from gateway.simulated_bank import SimulatedBankApp
from storage.audit_db import AuditDatabase
from storage.schemas import ExtractedAction, ExtractionBundle


class ParallixCommandCenter:
    def __init__(self) -> None:
        self.db = AuditDatabase()
        self.intent_engine = IntentIntegrityEngine()
        self.hallucination_engine = HallucinationDetector()
        self.counterfactual_engine = CounterfactualEngine()
        self.gateway = ActionInterceptionGateway(db=self.db)
        self.bank = SimulatedBankApp(gateway=self.gateway, starting_balance=100000.0)
        self.drift_monitor = DriftMonitor(db=self.db, drop_threshold=3.0)
        self.explainer = ExplainabilityEngine(db=self.db)
        self.adaptive_gen = AdaptiveTestGenerator(db=self.db)
        self.activity_feed: list[dict] = []
        self.current_profile: str = "banking_upi"
        self.api_key_manager = ApiKeyManager()
        self.idempotency_store = IdempotencyStore()

        self.adapter: GeminiAdapter | None = None
        try:
            self.adapter = GeminiAdapter(model_id="gemini-flash-lite-latest")
        except Exception:
            self.adapter = None

    def _local_fallback_parse(self, text: str) -> ExtractionBundle:
        lowered = text.lower()
        amt_match = re.search(r"-?\d+(?:\.\d+)?", text)
        amount = float(amt_match.group(0)) if amt_match else None

        recipient = None
        for name in ("Rajan", "Rahul", "Suresh", "Anu", "Hacker"):
            if name.lower() in lowered:
                recipient = name
                break

        polarity = "positive"
        condition = None
        if any(w in lowered for w in ("ayakkaruth", "mat bhejna", "do not", "don't", "never")):
            polarity = "negative"
        elif any(w in lowered for w in ("maathram", "mathram", "only if", "agar")):
            polarity = "conditional"
            condition = "conditional trigger detected"
        elif any(w in lowered for w in ("owes me", "tharanund")):
            polarity = "statement"

        date = "tomorrow" if any(w in lowered for w in ("naale", "tomorrow", "kal")) else None
        intent = None if polarity == "statement" else "transfer"

        return ExtractionBundle(
            actions=[
                ExtractedAction(
                    intent=intent,
                    recipient=recipient,
                    amount=amount,
                    currency="INR",
                    date=date,
                    polarity=polarity,
                    condition=condition,
                )
            ]
        )

    def evaluate_instruction(self, text: str, profile: str = "banking_upi") -> dict:
        active_profile = profile or self.current_profile or "banking_upi"
        self.current_profile = active_profile
        source = "Offline Deterministic Parser"
        latency_ms = 0.0
        bundle = ExtractionBundle(actions=[])

        if self.adapter is not None:
            bundle, latency_ms = self.adapter.extract_actions(text)
            if bundle.actions:
                source = f"Google Gemini ({self.adapter.model_id})"

        if not bundle.actions:
            bundle = self._local_fallback_parse(text)

        primary = bundle.actions[0]
        intent_check = self.intent_engine.audit_runtime(text, bundle)
        bank_result = self.bank.process_proposed_action(
            user_input=text,
            action=primary,
            intent_check=intent_check,
            session_id="buildathon_stage",
            profile=active_profile,
        )

        incident_data = None
        if bank_result["gateway_decision"] in ("BLOCK", "HOLD"):
            dec = self.gateway.inspect_action(
                user_input=text,
                action=primary,
                intent_check=intent_check,
                session_id="buildathon_incident",
                profile=active_profile,
            )
            inc = self.explainer.explain_gateway_block(text, primary, dec)
            incident_data = inc.model_dump(mode="json")

        self.activity_feed.insert(
            0,
            {
                "input": text,
                "decision": bank_result["gateway_decision"],
                "status": bank_result["execution_status"],
                "recipient": primary.recipient or "—",
                "amount": primary.amount,
                "currency": primary.currency,
                "profile": active_profile,
            },
        )
        self.activity_feed = self.activity_feed[:8]
        metered_calls = self.db.increment_tenant_usage("growth_tenant")

        return {
            "source": source,
            "latency_ms": latency_ms,
            "actions": [a.model_dump() for a in bundle.actions],
            "intent_check": intent_check.model_dump(),
            "gateway": bank_result,
            "incident": incident_data,
            "feed": self.activity_feed,
            "held_count": len(self.bank.held_queue),
            "db_counts": self.db.get_summary_counts(),
            "metered_usage": metered_calls,
            "profile": active_profile,
        }

    def audit_api_request(
        self,
        payload: dict,
        api_key: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[int, dict]:
        if not isinstance(payload, dict):
            return 400, {"error": "JSON request body must be an object.", "code": "INVALID_PAYLOAD"}

        is_valid, tenant, auth_msg = self.api_key_manager.validate_key(api_key)
        if not is_valid or tenant is None:
            return 401, {"error": auth_msg, "code": "UNAUTHORIZED"}

        tenant_id = tenant.tenant_id
        raw_text = payload.get("text") if "text" in payload else payload.get("input", "")
        try:
            text = sanitize_text(raw_text)
        except ValueError as exc:
            return 400, {"error": str(exc), "code": "INPUT_TOO_LONG"}

        if not text and "action" not in payload:
            return 400, {"error": "Field text (or action) is required.", "code": "MISSING_INPUT"}

        profile = sanitize_text(payload.get("profile") or "banking_upi", max_length=64).lower()
        if profile not in {"banking_upi", "healthcare_rx", "ecommerce_refund"}:
            return 400, {
                "error": f"Unsupported profile {profile}. Allowed: banking_upi, healthcare_rx, ecommerce_refund.",
                "code": "INVALID_PROFILE",
            }

        idem_status, cached_resp = self.idempotency_store.lookup(tenant_id, idempotency_key, payload)
        if idem_status == "CONFLICT":
            return 409, {
                "error": "X-Idempotency-Key reused with a different request payload.",
                "code": "IDEMPOTENCY_CONFLICT",
            }
        if idem_status == "HIT" and cached_resp is not None:
            replay = dict(cached_resp)
            replay["duplicate_prevented"] = True
            return 200, replay

        if "action" in payload:
            if not isinstance(payload["action"], dict):
                return 400, {"error": "Field action must be a JSON object.", "code": "INVALID_ACTION"}
            try:
                action = ExtractedAction(**payload["action"])
            except Exception as exc:
                return 400, {"error": f"Invalid action schema: {exc}", "code": "INVALID_ACTION_SCHEMA"}
            bundle = ExtractionBundle(actions=[action])
        else:
            bundle = ExtractionBundle(actions=[])
            if self.adapter is not None:
                bundle, _ = self.adapter.extract_actions(text)
            if not bundle.actions:
                bundle = self._local_fallback_parse(text)
            action = bundle.actions[0] if bundle.actions else ExtractedAction()

        intent_check = self.intent_engine.audit_runtime(text, bundle)
        decision = self.gateway.inspect_action(
            user_input=text,
            action=action,
            intent_check=intent_check,
            session_id=f"api_{tenant_id}",
            profile=profile,
        )

        incident_id = None
        if decision.decision in ("BLOCK", "HOLD") or intent_check.polarity_reversed:
            inc = self.explainer.explain_gateway_block(
                user_input=text,
                proposed_action=action,
                decision=decision,
            )
            incident_id = inc.incident_id

        metered_usage = self.db.increment_tenant_usage(tenant_id)

        result = {
            "decision": decision.decision,
            "risk_score": decision.risk_score,
            "extracted_action": action.model_dump(),
            "incident_id": incident_id,
            "metered_usage": metered_usage,
            "profile": profile,
            "reasons": decision.reasons,
            "tenant_id": tenant_id,
            "storage_mode": "ephemeral_sqlite_beta",
        }
        self.idempotency_store.store(tenant_id, idempotency_key, payload, result)
        return 200, result

    def approve_latest_hold(self) -> dict:
        if not self.bank.held_queue:
            return {
                "approved": False,
                "message": "No held transactions waiting in queue.",
                "wallet_balance": self.bank.balances["user_wallet"],
                "held_count": 0,
                "feed": self.activity_feed,
            }

        ticket = self.bank.held_queue.pop(0)
        act = ExtractedAction(**ticket["action"])
        amount = float(act.amount or 0.0)
        recipient = act.recipient or "Unknown"

        if self.bank.balances["user_wallet"] >= amount:
            self.bank.balances["user_wallet"] -= amount
            self.bank.balances[recipient] = self.bank.balances.get(recipient, 0.0) + amount
            self.activity_feed.insert(
                0,
                {
                    "input": f"[Human Approved] {ticket['user_input']}",
                    "decision": "PASS",
                    "status": "EXECUTED_AFTER_REVIEW",
                    "recipient": recipient,
                    "amount": amount,
                    "currency": act.currency,
                },
            )
            self.activity_feed = self.activity_feed[:8]

        return {
            "approved": True,
            "message": f"Human supervisor approved INR {amount:.2f} transfer to {recipient}.",
            "wallet_balance": self.bank.balances["user_wallet"],
            "held_count": len(self.bank.held_queue),
            "feed": self.activity_feed,
        }

    def check_hallucination(self, ai_reply: str) -> dict:
        evidence = {"account_balance": 2500, "daily_limit": 50000}
        res = self.hallucination_engine.evaluate(ai_reply, evidence)
        return {
            "evidence": evidence,
            "result": res.model_dump(),
        }

    def generate_counterfactuals(self, sentence: str) -> dict:
        base_bundle = self._local_fallback_parse(sentence)
        variants = self.counterfactual_engine.generate_variants(sentence)
        evaluated = []
        for v in variants:
            mod_bundle = self._local_fallback_parse(v.modified_input)
            cf_res = self.counterfactual_engine.evaluate_pair(v, base_bundle, mod_bundle)
            evaluated.append(
                {
                    "perturbation_type": v.perturbation_type,
                    "modified_input": v.modified_input,
                    "expected_change": v.expected_change,
                    "observed_change": cf_res.observed_change,
                    "passed": cf_res.passed,
                }
            )
        return {
            "base": sentence,
            "variants": evaluated,
        }

    def check_drift_and_models(self, simulate_drop: bool = False) -> dict:
        alert_info = None
        if simulate_drop:
            alert_info = {
                "simulated": True,
                "label": "SIMULATED REGRESSION DEMONSTRATION — Not an actual model degradation",
                "model_id": "gemini-flash-lite-latest",
                "baseline_accuracy": 74.0,
                "current_accuracy": 61.5,
                "delta": -12.5,
                "drift_detected": True,
                "alert_message": (
                    "[SIMULATED DRIFT DEMO] Accuracy drop from 74.00% to 61.50% (-12.50%) "
                    "exceeds -3.00% threshold."
                ),
            }

        trend = self.drift_monitor.get_trend_summary("gemini-flash-lite-latest")
        models = build_comparison_table(
            gemini_metrics={
                "intent_accuracy": 74.00,
                "slot_accuracy": 93.83,
                "safety_pass_rate": 100.0,
                "avg_latency_ms": 620.0,
            }
        )
        return {
            "alert": alert_info,
            "trend": trend,
            "models": [m.model_dump() for m in models],
            "db_counts": self.db.get_summary_counts(),
        }

    def generate_adaptive_suite(self) -> dict:
        weak = self.adaptive_gen.analyze_weaknesses()
        cases = self.adaptive_gen.generate_from_failures(max_cases=4)
        saved = self.adaptive_gen.save_versioned_dataset(
            cases, list(weak["top_failure_types"].keys())
        )
        return {
            "weaknesses": weak["top_failure_types"],
            "saved_file": saved.name,
            "generated_cases": [c.model_dump() for c in cases],
            "db_counts": self.db.get_summary_counts(),
        }


CENTER = ParallixCommandCenter()

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>PARALLIX IQ — AI Reliability & Safety Infrastructure</title>
<style>
  :root {
    --bg: #090d16;
    --panel: #111827;
    --panel-alt: #161f33;
    --border: rgba(148, 163, 184, 0.16);
    --text: #f8fafc;
    --muted: #94a3b8;
    --blue: #3b82f6;
    --indigo: #6366f1;
    --pass: #10b981;
    --pass-soft: rgba(16, 185, 129, 0.14);
    --hold: #f59e0b;
    --hold-soft: rgba(245, 158, 11, 0.14);
    --block: #ef4444;
    --block-soft: rgba(239, 68, 68, 0.14);
  }

  * {
    box-sizing: border-box;
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }

  body {
    margin: 0;
    background: radial-gradient(circle at top right, #172554 0%, var(--bg) 42%);
    color: var(--text);
    min-height: 100vh;
  }

  .shell {
    max-width: 1240px;
    margin: 0 auto;
    padding: 22px 26px 50px;
  }

  /* HERO HEADER */
  .hero {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 18px;
    background: linear-gradient(135deg, rgba(30, 41, 59, 0.85), rgba(15, 23, 42, 0.92));
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 20px 26px;
    box-shadow: 0 20px 45px rgba(0, 0, 0, 0.35);
    margin-bottom: 20px;
  }

  .brand-row {
    display: flex;
    align-items: center;
    gap: 14px;
  }

  .shield-icon {
    width: 44px;
    height: 44px;
    border-radius: 12px;
    background: linear-gradient(135deg, #2563eb, #4f46e5);
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 800;
    font-size: 18px;
    color: #fff;
    box-shadow: 0 8px 20px rgba(37, 99, 235, 0.4);
  }

  .brand-title {
    margin: 0;
    font-size: 22px;
    font-weight: 800;
    letter-spacing: -0.02em;
  }

  .brand-badge {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    background: rgba(59, 130, 246, 0.18);
    color: #93c5fd;
    border: 1px solid rgba(59, 130, 246, 0.35);
    padding: 3px 9px;
    border-radius: 999px;
    margin-left: 8px;
    vertical-align: middle;
  }

  .brand-sub {
    margin: 4px 0 0;
    font-size: 13px;
    color: var(--muted);
  }

  .kpi-row {
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
  }

  .kpi-card {
    background: rgba(15, 23, 42, 0.85);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 10px 16px;
    min-width: 145px;
  }

  .kpi-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
  }

  .kpi-val {
    font-size: 18px;
    font-weight: 700;
    margin-top: 2px;
    font-variant-numeric: tabular-nums;
  }

  .kpi-val.green { color: #34d399; }
  .kpi-val.blue { color: #60a5fa; }

  /* NAVIGATION */
  .nav {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 20px;
  }

  .nav-pill {
    background: rgba(17, 24, 39, 0.85);
    color: var(--muted);
    border: 1px solid var(--border);
    padding: 10px 16px;
    border-radius: 10px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .nav-pill:hover {
    color: #fff;
    border-color: rgba(148, 163, 184, 0.4);
  }

  .nav-pill.active {
    background: linear-gradient(135deg, #2563eb, #4f46e5);
    color: #fff;
    border-color: transparent;
    box-shadow: 0 6px 18px rgba(37, 99, 235, 0.35);
  }

  .screen { display: none; }
  .screen.active { display: block; animation: fadeIn 0.2s ease; }

  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: translateY(0); }
  }

  /* PIPELINE STRIP */
  .pipeline {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 10px;
    margin-bottom: 18px;
  }

  @media (max-width: 860px) {
    .pipeline { grid-template-columns: 1fr; }
  }

  .pipe-node {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 12px 14px;
    position: relative;
  }

  .pipe-step {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #60a5fa;
  }

  .pipe-name {
    font-size: 13.5px;
    font-weight: 700;
    margin-top: 3px;
  }

  .pipe-status {
    font-size: 12px;
    color: var(--muted);
    margin-top: 4px;
  }

  .pipe-node.state-PASS { border-color: var(--pass); background: linear-gradient(180deg, var(--pass-soft), var(--panel)); }
  .pipe-node.state-HOLD { border-color: var(--hold); background: linear-gradient(180deg, var(--hold-soft), var(--panel)); }
  .pipe-node.state-BLOCK { border-color: var(--block); background: linear-gradient(180deg, var(--block-soft), var(--panel)); }

  /* TWO-COLUMN WORKSPACE */
  .split-grid {
    display: grid;
    grid-template-columns: 1.45fr 1fr;
    gap: 18px;
  }

  @media (max-width: 920px) {
    .split-grid { grid-template-columns: 1fr; }
  }

  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 20px 22px;
    margin-bottom: 18px;
  }

  .card-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
  }

  .card-title {
    margin: 0;
    font-size: 16px;
    font-weight: 700;
  }

  .preset-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 8px;
    margin-bottom: 14px;
  }

  .preset {
    text-align: left;
    background: var(--panel-alt);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 10px 12px;
    border-radius: 10px;
    cursor: pointer;
    font-size: 12.5px;
    transition: border-color 0.15s ease;
  }

  .preset:hover { border-color: #60a5fa; }
  .preset small { display: block; color: var(--muted); margin-top: 2px; font-size: 11px; }

  .input-bar {
    display: flex;
    gap: 10px;
  }

  input[type="text"] {
    flex: 1;
    background: #0b1120;
    border: 1px solid rgba(148, 163, 184, 0.28);
    color: #fff;
    padding: 12px 14px;
    border-radius: 10px;
    font-size: 14px;
  }

  input[type="text"]:focus {
    outline: none;
    border-color: #3b82f6;
    box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
  }

  .btn {
    background: linear-gradient(135deg, #2563eb, #4f46e5);
    color: #fff;
    border: none;
    padding: 12px 18px;
    border-radius: 10px;
    font-weight: 700;
    font-size: 13.5px;
    cursor: pointer;
    white-space: nowrap;
  }

  .btn-secondary {
    background: var(--panel-alt);
    color: #e2e8f0;
    border: 1px solid var(--border);
    padding: 9px 14px;
    border-radius: 8px;
    font-weight: 600;
    font-size: 12.5px;
    cursor: pointer;
  }

  .verdict-card {
    margin-top: 16px;
    border-radius: 12px;
    padding: 16px;
    border: 1px solid var(--border);
  }

  .verdict-PASS { background: var(--pass-soft); border-color: rgba(16, 185, 129, 0.45); }
  .verdict-HOLD { background: var(--hold-soft); border-color: rgba(245, 158, 11, 0.45); }
  .verdict-BLOCK { background: var(--block-soft); border-color: rgba(239, 68, 68, 0.45); }

  .pill {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 999px;
    font-weight: 800;
    font-size: 12px;
    letter-spacing: 0.05em;
    margin-right: 8px;
  }

  .pill-PASS { background: var(--pass); color: #022c22; }
  .pill-HOLD { background: var(--hold); color: #271300; }
  .pill-BLOCK { background: var(--block); color: #fff; }

  .slots {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 8px;
    margin-top: 14px;
  }

  @media (max-width: 640px) { .slots { grid-template-columns: repeat(2, 1fr); } }

  .slot {
    background: rgba(9, 13, 22, 0.7);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 9px 10px;
  }

  .slot-k { font-size: 10px; text-transform: uppercase; color: var(--muted); }
  .slot-v { font-size: 13.5px; font-weight: 700; margin-top: 3px; color: #fff; }

  .feed-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    background: var(--panel-alt);
    border: 1px solid var(--border);
    border-radius: 10px;
    margin-bottom: 8px;
    font-size: 12.5px;
  }

  /* BENCHMARK BARS */
  .bar-row { margin-bottom: 12px; }
  .bar-meta { display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 5px; }
  .bar-track { height: 10px; background: #0b1120; border-radius: 999px; overflow: hidden; border: 1px solid var(--border); }
  .bar-fill { height: 100%; border-radius: 999px; background: linear-gradient(90deg, #3b82f6, #10b981); }

  table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 12px;
    font-size: 13px;
  }

  th {
    text-align: left;
    padding: 10px 12px;
    background: var(--panel-alt);
    color: var(--muted);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  td {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
  }

  code {
    background: rgba(15, 23, 42, 0.9);
    border: 1px solid var(--border);
    padding: 2px 6px;
    border-radius: 5px;
    font-size: 12px;
    color: #93c5fd;
  }

  /* SAAS TENANT & INDUSTRY PROFILE BAR */
  .saas-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    background: linear-gradient(135deg, rgba(30, 58, 138, 0.45), rgba(15, 23, 42, 0.95));
    border: 1px solid rgba(59, 130, 246, 0.4);
    border-radius: 14px;
    padding: 12px 20px;
    margin-bottom: 12px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
  }

  .saas-left {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .tenant-badge {
    background: rgba(59, 130, 246, 0.2);
    color: #93c5fd;
    border: 1px solid rgba(59, 130, 246, 0.4);
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 11.5px;
    font-weight: 700;
  }

  .tier-badge {
    background: rgba(16, 185, 129, 0.18);
    color: #6ee7b7;
    border: 1px solid rgba(16, 185, 129, 0.35);
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 11.5px;
    font-weight: 700;
  }

  .saas-center {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
  }

  .saas-center select {
    background: #0f172a;
    color: #f8fafc;
    border: 1px solid #3b82f6;
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    outline: none;
  }

  .saas-right {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .usage-label {
    font-size: 11px;
    color: var(--muted);
    font-weight: 700;
    letter-spacing: 0.05em;
  }

  .usage-val {
    font-size: 18px;
    font-weight: 800;
    color: #38bdf8;
    font-variant-numeric: tabular-nums;
  }

  .curl-strip {
    background: #0b1120;
    border: 1px solid rgba(148, 163, 184, 0.2);
    border-radius: 10px;
    padding: 10px 16px;
    margin-bottom: 20px;
  }

  .curl-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 6px;
  }

  .curl-strip pre {
    margin: 0;
    color: #a5b4fc;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-all;
  }

  .btn-copy-curl {
    background: rgba(99, 102, 241, 0.25);
    color: #c7d2fe;
    border: 1px solid rgba(99, 102, 241, 0.45);
    padding: 5px 12px;
    border-radius: 6px;
    font-size: 11.5px;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .btn-copy-curl:hover {
    background: rgba(99, 102, 241, 0.45);
    color: #fff;
  }
</style>
</head>
<body>

<div class="shell">
  <!-- TOP SAAS TENANT & INDUSTRY PROFILE BAR -->
  <div class="saas-bar">
    <div class="saas-left">
      <span class="tenant-badge">Demo Tenant — Sample Enterprise Profile (SIMULATED)</span>
      <span class="tier-badge">Illustrative Enterprise Pricing — ₹1.5 Lakh/mo (Not an Actual Subscription)</span>
    </div>

    <div class="saas-center">
      <label for="industryProfileSelect"><strong>Industry Profile:</strong></label>
      <select id="industryProfileSelect" onchange="changeProfile(this.value)">
        <option value="banking_upi" selected>🏦 Banking UPI (Default)</option>
        <option value="healthcare_rx">🏥 Healthcare Rx (Prescriptions)</option>
        <option value="ecommerce_refund">🛒 E-Commerce Refund (Returns)</option>
      </select>
    </div>

    <div class="saas-right">
      <span class="usage-label">METERED API CALLS:</span>
      <span class="usage-val"><span id="saasMeteredCalls">0</span></span>
      <button class="btn-copy-curl" onclick="copyCurlSnippet()">📋 Copy cURL</button>
    </div>
  </div>

  <!-- LIVE COPYABLE CURL SNIPPET -->
  <div class="curl-strip" id="curlSnippetContainer">
    <div class="curl-head">
      <span><strong>Live REST API Endpoint:</strong> <code>POST /v1/audit</code> • Header: <code>X-Parallix-Key</code> (Cloud Run & B2B Ready)</span>
      <button class="btn-copy-curl" id="btnCopyCurl" onclick="copyCurlSnippet()">Copy cURL</button>
    </div>
    <pre id="curlSnippet">curl -X POST https://parallix-iq-927980553219.asia-south1.run.app/v1/audit \
  -H "Content-Type: application/json" \
  -H "X-Parallix-Key: YOUR_API_KEY" \
  -d '{"text": "Naale Rahulinu 500 roopa ayakkaruthu", "profile": "banking_upi"}'</pre>
  </div>

  <!-- EXECUTIVE HEADER -->
  <div class="hero">
    <div class="brand-row">
      <div class="shield-icon">PQ</div>
      <div>
        <h1 class="brand-title">PARALLIX IQ <span class="brand-badge">8-Engine Safety Infrastructure</span></h1>
        <p class="brand-sub">Independent Multilingual Evaluation Lab & Runtime Action Firewall — AI Never Grades Itself</p>
      </div>
    </div>

    <div class="kpi-row">
      <div class="kpi-card">
        <div class="kpi-label">Simulated Bank Wallet (DEMO — No Real Money)</div>
        <div class="kpi-val green">₹<span id="walletBal">100,000.00</span></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Slot-Level Accuracy</div>
        <div class="kpi-val blue">93.83%</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">SQLite Audit Records (EPHEMERAL BETA)</div>
        <div class="kpi-val" id="dbAuditBadge">—</div>
      </div>
    </div>
  </div>

  <!-- 4-SYSTEM ARCHITECTURE OVERVIEW -->
  <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:12px; margin-bottom:18px;">
    <div style="background:rgba(15,23,42,0.85); border:1px solid rgba(59,130,246,0.3); border-radius:12px; padding:12px 14px;">
      <div style="font-size:11px; font-weight:800; letter-spacing:0.08em; color:#60a5fa;">1. UNDERSTAND <span style="background:rgba(16,185,129,0.18); color:#6ee7b7; padding:2px 6px; border-radius:4px; margin-left:4px;">LIVE</span></div>
      <div style="font-size:13px; font-weight:700; margin-top:4px;">Engine 1: Multilingual Intent Integrity</div>
      <div style="font-size:12px; color:#94a3b8; margin-top:2px;">Detects negation reversals, conditions, and Indic suffix mismatches across English, Malayalam, Manglish, Hindi &amp; Hinglish.</div>
    </div>
    <div style="background:rgba(15,23,42,0.85); border:1px solid rgba(99,102,241,0.3); border-radius:12px; padding:12px 14px;">
      <div style="font-size:11px; font-weight:800; letter-spacing:0.08em; color:#a5b4fc;">2. VERIFY <span style="background:rgba(59,130,246,0.18); color:#93c5fd; padding:2px 6px; border-radius:4px; margin-left:4px;">BETA / PLANNED</span></div>
      <div style="font-size:13px; font-weight:700; margin-top:4px;">Engines 2, 3 &amp; 6: Hallucination, Counterfactual &amp; Arena</div>
      <div style="font-size:12px; color:#94a3b8; margin-top:2px;">Evidence claim verification, 1-variable counterfactual mutations, Gemini consensus (OpenAI/Anthropic/Ollama: PLANNED).</div>
    </div>
    <div style="background:rgba(15,23,42,0.85); border:1px solid rgba(16,185,129,0.35); border-radius:12px; padding:12px 14px;">
      <div style="font-size:11px; font-weight:800; letter-spacing:0.08em; color:#34d399;">3. PROTECT <span style="background:rgba(16,185,129,0.18); color:#6ee7b7; padding:2px 6px; border-radius:4px; margin-left:4px;">LIVE</span></div>
      <div style="font-size:13px; font-weight:700; margin-top:4px;">Engine 4: AI Action Interception Gateway</div>
      <div style="font-size:12px; color:#94a3b8; margin-top:2px;">Fail-closed PASS / HOLD / BLOCK firewall sitting between AI outputs and downstream execution tools.</div>
    </div>
    <div style="background:rgba(15,23,42,0.85); border:1px solid rgba(245,158,11,0.3); border-radius:12px; padding:12px 14px;">
      <div style="font-size:11px; font-weight:800; letter-spacing:0.08em; color:#fbbf24;">4. IMPROVE <span style="background:rgba(245,158,11,0.18); color:#fcd34d; padding:2px 6px; border-radius:4px; margin-left:4px;">BETA</span></div>
      <div style="font-size:13px; font-weight:700; margin-top:4px;">Engines 5, 7 &amp; 8: Drift, Explainability &amp; Adaptive Tests</div>
      <div style="font-size:12px; color:#94a3b8; margin-top:2px;">Historical regression alerts, root-cause JSON/Markdown failure reports, and failure-driven test generation.</div>
    </div>
  </div>

  <!-- NAVIGATION -->
  <div class="nav">
    <button class="nav-pill active" onclick="openScreen('s1', this)">Live Action Firewall (Engines 1, 4 & 7)</button>
    <button class="nav-pill" onclick="openScreen('s2', this)">Hallucination Auditor (Engine 2)</button>
    <button class="nav-pill" onclick="openScreen('s3', this)">Counterfactual Stress Lab (Engine 3)</button>
    <button class="nav-pill" onclick="openScreen('s4', this)">Drift & Multi-Model Arena (Engines 5 & 6)</button>
    <button class="nav-pill" onclick="openScreen('s5', this)">Self-Healing Adaptive Loop (Engine 8)</button>
    <button class="nav-pill" onclick="openScreen('s6', this)">100-Point Indic Research Metrics</button>
  </div>

  <!-- SCREEN 1: LIVE ACTION FIREWALL -->
  <div id="s1" class="screen active">
    <div class="pipeline" id="pipelineStrip">
      <div class="pipe-node" id="node1">
        <div class="pipe-step">Stage 1</div>
        <div class="pipe-name">Multilingual Input</div>
        <div class="pipe-status" id="node1Sub">Waiting for prompt</div>
      </div>
      <div class="pipe-node" id="node2">
        <div class="pipe-step">Stage 2 • Extraction</div>
        <div class="pipe-name">Structured AI Parse</div>
        <div class="pipe-status" id="node2Sub">Pydantic schema enforced</div>
      </div>
      <div class="pipe-node" id="node3">
        <div class="pipe-step">Stage 3 • Engine 1</div>
        <div class="pipe-name">Intent Integrity Audit</div>
        <div class="pipe-status" id="node3Sub">Independent rule check</div>
      </div>
      <div class="pipe-node" id="node4">
        <div class="pipe-step">Stage 4 • Engine 4</div>
        <div class="pipe-name">Interception Firewall</div>
        <div class="pipe-status" id="node4Sub">PASS / HOLD / BLOCK</div>
      </div>
      <div class="pipe-node" id="node5">
        <div class="pipe-step">Stage 5 • Engine 7</div>
        <div class="pipe-name">Bank & SQLite Ledger</div>
        <div class="pipe-status" id="node5Sub">Append-only SQLite audit log (ephemeral)</div>
      </div>
    </div>

    <div class="split-grid">
      <div class="card">
        <div class="card-head">
          <h2 class="card-title">Interactive Firewall & Bank Simulation</h2>
          <span style="font-size:12px; color:#94a3b8;" id="parserTag">Ready</span>
        </div>

        <div class="preset-grid" id="presetGridContainer" style="grid-template-columns: repeat(3, 1fr);">
          <button class="preset" onclick="runPreset('Rajan 500 ayakk')">
            <strong>Manglish • Valid Transfer</strong>
            <small>"Rajan 500 ayakk" -> PASS</small>
          </button>
          <button class="preset" onclick="runPreset('Naale Rahulinu 500 roopa ayakkaruthu.')">
            <strong>Manglish • Negation Trap</strong>
            <small>"...ayakkaruthu" -> BLOCK</small>
          </button>
          <button class="preset" onclick="runPreset('Kal Vikram ko 1000 rupaye mat bhejna.')">
            <strong>Hinglish • Negation Trap</strong>
            <small>"...mat bhejna" -> BLOCK</small>
          </button>
          <button class="preset" onclick="runPreset('Agar Rahul call kare tabhi 500 bhejna.')">
            <strong>Hinglish • Conditional</strong>
            <small>"Agar ... tabhi" -> HOLD</small>
          </button>
          <button class="preset" onclick="runPreset('कल महेश को 1200 रुपये मत भेजना।')">
            <strong>Hindi • Devanagari Block</strong>
            <small>"कल महेश को ... मत भेजना" -> BLOCK</small>
          </button>
          <button class="preset" onclick="runPreset('നാളെ അരുണിന് 2000 രൂപ അയക്കരുത്.')">
            <strong>Malayalam • Unicode Block</strong>
            <small>"നാളെ അരുണിന് ... അയക്കരുത്" -> BLOCK</small>
          </button>
        </div>

        <div class="input-bar">
          <input id="promptInput" type="text" value="Naale Rahulinu 500 roopa ayakkaruthu." placeholder="Type Malayalam, Manglish, Hindi, Hinglish, or English..." />
          <button class="btn" onclick="runFirewall()">Intercept & Audit</button>
        </div>

        <div id="firewallResult"></div>
      </div>

      <div class="card">
        <div class="card-head">
          <h2 class="card-title">Live Bank & Audit Feed</h2>
          <button class="btn-secondary" id="approveHoldBtn" onclick="approveHold()" style="display:none;">Approve Held Transfer</button>
        </div>
        <p style="font-size:12.5px; color:var(--muted); margin-top:0;">
          Proposed actions are recorded to <code>parallix_audit.db</code> (ephemeral container storage in beta) before simulated wallet updates.
        </p>
        <div id="liveFeed">
          <div style="color:var(--muted); font-size:13px; padding:12px 0;">No transactions evaluated in this session yet.</div>
        </div>
      </div>
    </div>
  </div>

  <!-- SCREEN 2: HALLUCINATION DETECTOR -->
  <div id="s2" class="screen">
    <div class="card">
      <div class="card-head">
        <h2 class="card-title">Engine 2 — Evidence-Based Hallucination & Fabrication Detector</h2>
        <code>Trusted Record: Balance = ₹2,500 | Limit = ₹50,000</code>
      </div>
      <p style="font-size:13.5px; color:var(--muted); margin-top:0;">
        Separates verifiable facts, assumptions, and opinions. Claims lacking supplied evidence stay marked <strong>unverified</strong>—never falsely declared wrong.
      </p>
      <div class="input-bar">
        <input id="hallucInput" type="text" value="Your account balance is 2500 rupees. Your daily limit is 100000 rupees. RBI changed UPI rules yesterday. I think you should save more." />
        <button class="btn" onclick="runHallucination()">Verify Claims</button>
      </div>
      <div id="hallucOutput"></div>
    </div>
  </div>

  <!-- SCREEN 3: COUNTERFACTUAL LAB -->
  <div id="s3" class="screen">
    <div class="card">
      <div class="card-head">
        <h2 class="card-title">Engine 3 — Single-Variable Counterfactual Stress Lab</h2>
        <code>1-Variable Perturbation Engine</code>
      </div>
      <p style="font-size:13.5px; color:var(--muted); margin-top:0;">
        Automatically mutates one variable at a time (negation flip, 100x amount shift, recipient swap, date shift, conditional injection, polite wrapper) to detect stubborn AI behavior.
      </p>
      <div class="input-bar">
        <input id="cfInput" type="text" value="Naale Rahulinu 500 roopa ayakkanam." />
        <button class="btn" onclick="runCounterfactual()">Generate 6 Counterfactuals</button>
      </div>
      <div id="cfOutput"></div>
    </div>
  </div>

  <!-- SCREEN 4: DRIFT & MULTI-MODEL ARENA -->
  <div id="s4" class="screen">
    <div class="card">
      <div class="card-head">
        <h2 class="card-title">Engines 5 & 6 — Drift Monitor & Provider Comparison Matrix</h2>
        <div style="display:flex; gap:8px;">
          <button class="btn-secondary" onclick="loadDrift(false)">Refresh SQLite Baseline</button>
          <button class="btn" onclick="loadDrift(true)">Simulate 12.5% Drop (Demo)</button>
        </div>
      </div>
      <div id="driftOutput"></div>
    </div>
  </div>

  <!-- SCREEN 5: ADAPTIVE TEST GENERATOR -->
  <div id="s5" class="screen">
    <div class="card">
      <div class="card-head">
        <h2 class="card-title">Engine 8 — Self-Healing Adaptive Test Generator</h2>
        <button class="btn" onclick="runAdaptive()">Synthesize New Benchmark from Failures</button>
      </div>
      <p style="font-size:13.5px; color:var(--muted); margin-top:0;">
        Reads real unresolved failure incidents from <code>parallix_audit.db</code>, generates deduplicated multilingual & adversarial tests, validates labels via Engine 1, and saves versioned datasets.
      </p>
      <div id="adaptiveOutput"></div>
    </div>
  </div>

  <!-- SCREEN 6: BENCHMARK RESEARCH SHOWCASE -->
  <div id="s6" class="screen">
    <div class="split-grid">
      <div class="card">
        <h2 class="card-title">Internal Benchmark Results — 100 Test Cases (audit-v2.0)</h2>
        <div style="font-size:12px; color:#fbbf24; background:rgba(245,158,11,0.12); border:1px solid rgba(245,158,11,0.3); border-radius:8px; padding:8px 10px; margin-bottom:10px;">
          <strong>Methodology &amp; Split Disclosure:</strong> Internal benchmark curated by the founder (100 unique prompts: 53 Development-Overlap + 47 Held-Out Expansion). Gemini snapshot (<code>gemini-flash-lite-latest</code>): 74.00% exact-match, 93.83% slot-level precision. Offline deterministic baseline: 4.00% exact-match, 91.48% non-null field precision. Not yet externally audited.
        </div>
        <p style="font-size:13px; color:var(--muted);">
          Evaluated across 600 semantic slots (Intent, Recipient, Amount, Currency, Date, Polarity) with rate-limit backoff resilience.
        </p>
        <div class="bar-row">
          <div class="bar-meta"><span><strong>PARALLIX IQ + Gemini Slot Accuracy (600 Slots)</strong></span><span><strong>93.83% (563/600)</strong></span></div>
          <div class="bar-track"><div class="bar-fill" style="width:93.83%;"></div></div>
        </div>
        <div class="bar-row">
          <div class="bar-meta"><span><strong>PARALLIX IQ + Gemini Exact Match</strong></span><span><strong>74.00% (74/100)</strong></span></div>
          <div class="bar-track"><div class="bar-fill" style="width:74%;"></div></div>
        </div>
        <div class="bar-row">
          <div class="bar-meta"><span>Legacy Regex / Rule-Based Baseline</span><span>7.41%</span></div>
          <div class="bar-track"><div class="bar-fill" style="width:7.41%; background:#ef4444;"></div></div>
        </div>

        <h3 style="font-size:14px; margin-top:20px;">Exact Match Breakdown by Dialect</h3>
        <div class="bar-row">
          <div class="bar-meta"><span>Hinglish (Code-Switched Latin)</span><span>88.46%</span></div>
          <div class="bar-track"><div class="bar-fill" style="width:88.46%;"></div></div>
        </div>
        <div class="bar-row">
          <div class="bar-meta"><span>English (Standard & Adversarial)</span><span>80.95%</span></div>
          <div class="bar-track"><div class="bar-fill" style="width:80.95%;"></div></div>
        </div>
        <div class="bar-row">
          <div class="bar-meta"><span>Malayalam (Native Unicode)</span><span>75.00%</span></div>
          <div class="bar-track"><div class="bar-fill" style="width:75%;"></div></div>
        </div>
        <div class="bar-row">
          <div class="bar-meta"><span>Manglish (Agglutinative Latin)</span><span>70.00%</span></div>
          <div class="bar-track"><div class="bar-fill" style="width:70%;"></div></div>
        </div>
        <div class="bar-row">
          <div class="bar-meta"><span>Hindi (Devanagari Postpositions)</span><span>36.36%</span></div>
          <div class="bar-track"><div class="bar-fill" style="width:36.36%; background:#f59e0b;"></div></div>
        </div>
      </div>

      <div class="card">
        <h2 class="card-title">3 Key Research Insights for Judges</h2>
        <div style="margin-bottom:14px; padding:12px; background:var(--panel-alt); border-radius:10px; border:1px solid var(--border);">
          <strong>1. The Transliteration Dividend</strong>
          <p style="margin:5px 0 0; font-size:13px; color:var(--muted);">Hinglish (88.46%) beats Hindi (36.36%) because chat users naturally space postpositions (<code>Rahul ko</code>), while native scripts fuse suffixes onto entity tokens.</p>
        </div>
        <div style="margin-bottom:14px; padding:12px; background:var(--panel-alt); border-radius:10px; border:1px solid var(--border);">
          <strong>2. Cultural Honorific Preservation</strong>
          <p style="margin:5px 0 0; font-size:13px; color:var(--muted);">Retains kinship titles (<em>Amit Chettan</em>, <em>Kozhikode Ikka</em>, <em>Pitaji</em>) rather than stripping them—preventing ambiguous contact-list transfers.</p>
        </div>
        <div style="padding:12px; background:var(--panel-alt); border-radius:10px; border:1px solid var(--border);">
          <strong>3. Temporal Prudence Under Omission</strong>
          <p style="margin:5px 0 0; font-size:13px; color:var(--muted);">When no date word is spoken, outputs <code>date: null</code> instead of hallucinating <code>"today"</code>, preventing premature scheduled payments.</p>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
let currentProfile = 'banking_upi';

const PROFILE_PRESETS = {
  banking_upi: [
    {title: "Manglish • Valid Transfer", text: "Rajan 500 ayakk", note: '"Rajan 500 ayakk" -> PASS'},
    {title: "Manglish • Negation Trap", text: "Naale Rahulinu 500 roopa ayakkaruthu.", note: '"...ayakkaruthu" -> BLOCK'},
    {title: "Hinglish • Negation Trap", text: "Kal Vikram ko 1000 rupaye mat bhejna.", note: '"...mat bhejna" -> BLOCK'},
    {title: "Hinglish • Conditional", text: "Agar Rahul call kare tabhi 500 bhejna.", note: '"Agar ... tabhi" -> HOLD'},
    {title: "Hindi • Devanagari Block", text: "कल महेश को 1200 रुपये मत भेजना।", note: '"कल महेश को ... मत भेजना" -> BLOCK'},
    {title: "Malayalam • Unicode Block", text: "നാളെ അരുണിന് 2000 രൂപ അയക്കരുത്.", note: '"നാളെ അരുണിന് ... അയക്കരുത്" -> BLOCK'},
  ],
  healthcare_rx: [
    {title: "Healthcare • Negated Dosage", text: "Do not dispense 50mg morphine to patient.", note: '"Do not dispense morphine" -> BLOCK'},
    {title: "Healthcare • High-Risk Drug", text: "Administer morphine injection to patient.", note: 'High-risk narcotic -> HOLD'},
    {title: "Healthcare • Safe Vitamin", text: "Administer 100mg Vitamin C to patient.", note: 'Safe dosage -> PASS'},
    {title: "Healthcare • Prohibited Insulin", text: "Stop giving insulin to patient immediately.", note: '"Stop giving insulin" -> BLOCK'},
    {title: "Healthcare • Sedative Order", text: "Dispense fentanyl patch for patient.", note: 'Controlled substance -> HOLD'},
    {title: "Healthcare • Standard Antibiotic", text: "Dispense 500mg amoxicillin to patient.", note: 'Standard clinical order -> PASS'},
  ],
  ecommerce_refund: [
    {title: "E-Com • High-Value Refund", text: "Process refund of 15000 INR for order #4891.", note: 'Refund >= ₹10,000 -> HOLD'},
    {title: "E-Com • Negated Cancel", text: "Do not cancel order #9821.", note: '"Do not cancel" -> BLOCK'},
    {title: "E-Com • Standard Refund", text: "Refund 1200 INR to customer for return.", note: 'Safe refund -> PASS'},
    {title: "E-Com • High Replacement", text: "Refund 25000 INR to customer account.", note: 'High value -> HOLD'},
    {title: "E-Com • Cancel Negation (Hi)", text: "Mera order cancel mat karna please.", note: '"cancel mat karna" -> BLOCK'},
    {title: "E-Com • Small Item Refund", text: "Refund 450 INR for damaged delivery.", note: 'Within limits -> PASS'},
  ]
};

function renderPresets(profile) {
  const container = document.getElementById('presetGridContainer');
  if (!container) return;
  const list = PROFILE_PRESETS[profile] || PROFILE_PRESETS.banking_upi;
  container.innerHTML = list.map(p => `
    <button class="preset" onclick="runPreset('${p.text.replace(/'/g, "\\'")}')">
      <strong>${p.title}</strong>
      <small>${p.note}</small>
    </button>
  `).join('');
}

function updateCurlSnippet(profile) {
  currentProfile = profile || currentProfile;
  let sampleText = "Naale Rahulinu 500 roopa ayakkaruthu";
  if (currentProfile === 'healthcare_rx') {
    sampleText = "Do not dispense 50mg morphine to patient";
  } else if (currentProfile === 'ecommerce_refund') {
    sampleText = "Process refund of 15000 INR for order #4891";
  }
  const apiBase = (window.location && window.location.origin && window.location.origin.startsWith('http'))
    ? window.location.origin
    : 'https://parallix-iq-927980553219.asia-south1.run.app';
  const cmd = `curl -X POST ${apiBase}/v1/audit \\
  -H "Content-Type: application/json" \\
  -H "X-Parallix-Key: YOUR_API_KEY" \\
  -d '{"text": "${sampleText}", "profile": "${currentProfile}"}'`;
  const el = document.getElementById('curlSnippet');
  if (el) el.innerText = cmd;
}

function copyCurlSnippet() {
  const code = document.getElementById('curlSnippet').innerText;
  navigator.clipboard.writeText(code);
  const btn = document.getElementById('btnCopyCurl');
  if (btn) {
    const oldText = btn.innerText;
    btn.innerText = 'Copied!';
    setTimeout(() => { btn.innerText = oldText; }, 1500);
  }
}

function changeProfile(val) {
  currentProfile = val;
  updateCurlSnippet(val);
  renderPresets(val);
  const firstPreset = (PROFILE_PRESETS[val] || [])[0];
  if (firstPreset) {
    document.getElementById('promptInput').value = firstPreset.text;
  }
  runFirewall();
}

function openScreen(id, btn) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-pill').forEach(p => p.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
  if (id === 's4') loadDrift(false);
}

function runPreset(txt) {
  document.getElementById('promptInput').value = txt;
  runFirewall();
}

function updateHeader(wallet, counts) {
  if (wallet !== undefined && wallet !== null) {
    document.getElementById('walletBal').innerText = Number(wallet).toLocaleString('en-IN', {minimumFractionDigits: 2});
  }
  if (counts && counts.gateway_audit !== undefined) {
    document.getElementById('dbAuditBadge').innerText = counts.gateway_audit + ' Audits';
  }
}

function renderFeed(feed, heldCount) {
  const btn = document.getElementById('approveHoldBtn');
  btn.style.display = heldCount > 0 ? 'inline-block' : 'none';
  if (heldCount > 0) btn.innerText = `Approve Held (${heldCount})`;

  if (!feed || feed.length === 0) return;
  document.getElementById('liveFeed').innerHTML = feed.map(item => `
    <div class="feed-item">
      <div style="max-width:68%;">
        <div style="font-weight:600; color:#fff; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${item.input}</div>
        <div style="color:var(--muted); font-size:11.5px;">Target: ${item.recipient} | Amount: ${item.amount !== null ? item.amount + ' ' + item.currency : '—'}</div>
      </div>
      <span class="pill pill-${item.decision}">${item.decision}</span>
    </div>
  `).join('');
}

async function runFirewall() {
  const text = document.getElementById('promptInput').value;
  const out = document.getElementById('firewallResult');
  document.getElementById('parserTag').innerText = 'Auditing...';

  const res = await fetch('/api/evaluate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text, profile: currentProfile})
  });
  const data = await res.json();
  const gw = data.gateway;
  const act = data.actions[0] || {};
  const dec = gw.gateway_decision;

  if (data.metered_usage !== undefined) {
    const el = document.getElementById('saasMeteredCalls');
    if (el) el.innerText = data.metered_usage;
  }

  updateHeader(gw.wallet_balance, data.db_counts);
  renderFeed(data.feed, data.held_count);
  document.getElementById('parserTag').innerText = `${data.source} (${data.latency_ms} ms)`;

  // Light up pipeline nodes
  ['node1', 'node2', 'node3', 'node4', 'node5'].forEach(id => {
    document.getElementById(id).className = `pipe-node state-${dec}`;
  });
  document.getElementById('node1Sub').innerText = 'Instruction captured';
  document.getElementById('node2Sub').innerText = `Polarity: ${act.polarity}`;
  document.getElementById('node3Sub').innerText = `Score: ${(data.intent_check.integrity_score * 100).toFixed(0)}%`;
  document.getElementById('node4Sub').innerText = `Verdict: ${dec}`;
  document.getElementById('node5Sub').innerText = gw.execution_status;

  const reasonsHtml = (gw.reasons || []).map(r => `<li style="margin-bottom:4px;">${r}</li>`).join('');
  const incidentHtml = data.incident
    ? `<div style="margin-top:12px; padding:11px 14px; background:rgba(9,13,22,0.65); border-radius:8px; border:1px solid var(--border); font-size:12.5px;">
         <strong>Engine 7 Incident Ticket (${data.incident.incident_id}):</strong> ${data.incident.explanation}<br/>
         <span style="color:#93c5fd;">Guardrail Fix: ${data.incident.recommendation}</span>
       </div>`
    : '';

  out.innerHTML = `
    <div class="verdict-card verdict-${dec}">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
        <div>
          <span class="pill pill-${dec}">${dec}</span>
          <strong>${gw.execution_status}</strong>
        </div>
        <span style="font-size:12px; color:#cbd5e1;">Risk Score: ${(gw.risk_score * 100).toFixed(0)}%</span>
      </div>
      <ul style="margin:10px 0 0; padding-left:18px; font-size:13.5px;">${reasonsHtml}</ul>
      <div class="slots">
        <div class="slot"><div class="slot-k">Intent</div><div class="slot-v">${act.intent || 'null'}</div></div>
        <div class="slot"><div class="slot-k">Recipient</div><div class="slot-v">${act.recipient || 'Missing'}</div></div>
        <div class="slot"><div class="slot-k">Amount</div><div class="slot-v">${act.amount !== null ? act.amount + ' ' + act.currency : 'Missing'}</div></div>
        <div class="slot"><div class="slot-k">Date</div><div class="slot-v">${act.date || 'Unspecified'}</div></div>
        <div class="slot"><div class="slot-k">Polarity</div><div class="slot-v">${act.polarity}</div></div>
      </div>
      ${incidentHtml}
    </div>
  `;
}

async function approveHold() {
  const res = await fetch('/api/approve_hold', {method: 'POST'});
  const data = await res.json();
  updateHeader(data.wallet_balance, null);
  renderFeed(data.feed, data.held_count);
  document.getElementById('firewallResult').innerHTML = `
    <div class="verdict-card verdict-PASS">
      <span class="pill pill-PASS">HUMAN APPROVED</span>
      <strong>${data.message}</strong>
    </div>
  `;
}

async function runHallucination() {
  const text = document.getElementById('hallucInput').value;
  const res = await fetch('/api/hallucination', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text})
  });
  const data = await res.json();
  const risk = data.result.hallucination_risk;
  const mapClass = risk === 'high' ? 'BLOCK' : (risk === 'medium' ? 'HOLD' : 'PASS');

  const rows = data.result.claims.map(c => `
    <tr>
      <td>${c.claim}</td>
      <td><code>${c.category}</code></td>
      <td><span class="pill pill-${c.status === 'confirmed' ? 'PASS' : (c.status === 'contradicted' ? 'BLOCK' : 'HOLD')}">${c.status.toUpperCase()}</span></td>
      <td>${c.evidence_ref || 'Evidence unavailable (kept unverified)'}</td>
    </tr>
  `).join('');

  document.getElementById('hallucOutput').innerHTML = `
    <div class="verdict-card verdict-${mapClass}">
      <span class="pill pill-${mapClass}">RISK: ${risk.toUpperCase()}</span>
      <strong>Independent Claim Audit Complete</strong>
      <table>
        <thead><tr><th>AI Claim</th><th>Type</th><th>Verdict</th><th>Trusted Evidence</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

async function runCounterfactual() {
  const text = document.getElementById('cfInput').value;
  const res = await fetch('/api/counterfactual', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text})
  });
  const data = await res.json();
  const rows = data.variants.map(v => `
    <tr>
      <td><code>${v.perturbation_type}</code></td>
      <td>${v.modified_input}</td>
      <td><code>${v.expected_change}</code></td>
    </tr>
  `).join('');

  document.getElementById('cfOutput').innerHTML = `
    <table>
      <thead><tr><th>Perturbation Type</th><th>Generated Mutation</th><th>Expected Model Shift</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function loadDrift(simulateDrop) {
  const res = await fetch('/api/drift', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({simulate_drop: simulateDrop})
  });
  const data = await res.json();
  updateHeader(null, data.db_counts);

  const banner = data.alert && data.alert.drift_detected
    ? `<div class="verdict-card verdict-BLOCK"><span class="pill pill-BLOCK">REGRESSION ALERT</span><strong>${data.alert.alert_message}</strong></div>`
    : `<div class="verdict-card verdict-PASS"><span class="pill pill-PASS">BASELINE HEALTHY</span><strong>Model holding at 74.00% exact match (93.83% slot accuracy).</strong></div>`;

  const rows = data.models.map(m => `
    <tr>
      <td><strong>${m.provider}</strong></td>
      <td><code>${m.model_id}</code></td>
      <td><span class="pill pill-${m.status === 'connected' ? 'PASS' : 'HOLD'}">${m.status.toUpperCase()}</span></td>
      <td>${m.intent_accuracy !== null ? m.intent_accuracy.toFixed(2) + '%' : 'Not connected'}</td>
      <td>${m.slot_accuracy !== null ? m.slot_accuracy.toFixed(2) + '%' : 'Not connected'}</td>
    </tr>
  `).join('');

  document.getElementById('driftOutput').innerHTML = `
    ${banner}
    <table>
      <thead><tr><th>Provider</th><th>Model ID</th><th>Adapter Status</th><th>Exact Match</th><th>Slot Accuracy</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function runAdaptive() {
  const res = await fetch('/api/adaptive', {method: 'POST'});
  const data = await res.json();
  updateHeader(null, data.db_counts);

  const rows = data.generated_cases.map(c => `
    <tr>
      <td><code>${c.id}</code></td>
      <td>${c.language}</td>
      <td><span class="pill pill-HOLD">${c.difficulty.toUpperCase()}</span></td>
      <td>${c.input}</td>
    </tr>
  `).join('');

  document.getElementById('adaptiveOutput').innerHTML = `
    <div class="verdict-card verdict-PASS">
      <span class="pill pill-PASS">DATASET SAVED</span>
      <strong>Created ${data.generated_cases.length} validated adversarial tests in <code>datasets/${data.saved_file}</code></strong>
      <table>
        <thead><tr><th>Test ID</th><th>Language</th><th>Difficulty</th><th>Generated Adversarial Prompt</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

renderPresets('banking_upi');
updateCurlSnippet('banking_upi');
runFirewall();
</script>
</body>
</html>
"""


OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "PARALLIX IQ — AI Reliability & Action Interception API",
        "version": "1.1.0",
        "description": "Multilingual intent integrity, hallucination verification, and fail-closed AI action gateway API.",
    },
    "servers": [
        {"url": "https://parallix-iq-927980553219.asia-south1.run.app", "description": "Google Cloud Run (asia-south1)"},
    ],
    "paths": {
        "/health": {
            "get": {
                "summary": "Service health and storage status check",
                "responses": {"200": {"description": "Service healthy"}},
            }
        },
        "/v1/audit": {
            "post": {
                "summary": "Audit a natural-language instruction or proposed AI action",
                "parameters": [
                    {"name": "X-Parallix-Key", "in": "header", "required": True, "schema": {"type": "string"}},
                    {"name": "X-Idempotency-Key", "in": "header", "required": False, "schema": {"type": "string"}},
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string", "example": "Naale Rahulinu 500 roopa ayakkaruthu"},
                                    "profile": {
                                        "type": "string",
                                        "enum": ["banking_upi", "healthcare_rx", "ecommerce_refund"],
                                        "default": "banking_upi",
                                    },
                                },
                                "required": ["text"],
                            }
                        }
                    },
                },
                "responses": {
                    "200": {"description": "Gateway audit decision (PASS, HOLD, or BLOCK)"},
                    "400": {"description": "Malformed JSON or invalid input"},
                    "401": {"description": "Missing, invalid, or revoked X-Parallix-Key"},
                    "409": {"description": "Idempotency key conflict"},
                    "413": {"description": "Request payload exceeds 64 KB limit"},
                    "429": {"description": "Rate limit exceeded"},
                },
            }
        },
    },
}


class CommandCenterHandler(BaseHTTPRequestHandler):
    rate_limiter = SlidingWindowRateLimiter(max_requests=60, window_seconds=60)

    def log_message(self, format: str, *args) -> None:
        return

    def _client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        return forwarded or (self.client_address[0] if self.client_address else "unknown")

    def _send_security_headers(self, is_json: bool = False) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if is_json:
            self.send_header("Cache-Control", "no-store")

    def _send_json(self, payload: dict, status: int = 200, extra_headers: dict[str, str] | None = None) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self._send_security_headers(is_json=True)
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        route = self.path.split("?", 1)[0]
        if route == "/health":
            self._send_json(
                {
                    "status": "ok",
                    "service": "parallix-iq",
                    "persistence": CENTER.db.get_persistence_status(),
                    "api_auth_configured": CENTER.api_key_manager.is_configured,
                },
                status=200,
            )
            return
        if route == "/openapi.json":
            self._send_json(OPENAPI_SPEC, status=200)
            return
        if route != "/":
            self._send_json({"error": "Not found", "code": "NOT_FOUND"}, status=404)
            return

        html_bytes = HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self._send_security_headers(is_json=False)
        self.end_headers()
        self.wfile.write(html_bytes)

    def do_POST(self) -> None:
        allowed, retry_after = self.rate_limiter.check(self._client_ip())
        if not allowed:
            self._send_json(
                {"error": "Rate limit exceeded. Please retry later.", "code": "RATE_LIMITED"},
                status=429,
                extra_headers={"Retry-After": str(retry_after)},
            )
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json({"error": "Invalid Content-Length header.", "code": "BAD_REQUEST"}, status=400)
            return

        if length < 0 or length > MAX_BODY_BYTES:
            self._send_json(
                {"error": f"Payload exceeds {MAX_BODY_BYTES} byte limit.", "code": "PAYLOAD_TOO_LARGE"},
                status=413,
            )
            return

        try:
            raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            body = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json({"error": "Malformed JSON request body.", "code": "INVALID_JSON"}, status=400)
            return

        if not isinstance(body, dict):
            self._send_json({"error": "JSON body must be an object.", "code": "INVALID_PAYLOAD"}, status=400)
            return

        route = self.path.split("?", 1)[0]
        if route == "/v1/audit":
            api_key = self.headers.get("X-Parallix-Key")
            idempotency_key = self.headers.get("X-Idempotency-Key")
            status_code, resp = CENTER.audit_api_request(body, api_key, idempotency_key=idempotency_key)
            self._send_json(resp, status=status_code)
        elif route == "/api/evaluate":
            try:
                clean_text = sanitize_text(body.get("text", ""))
                clean_profile = sanitize_text(body.get("profile", "banking_upi"), max_length=64)
            except ValueError as exc:
                self._send_json({"error": str(exc), "code": "INVALID_INPUT"}, status=400)
                return
            self._send_json(CENTER.evaluate_instruction(clean_text, clean_profile))
        elif route == "/api/approve_hold":
            self._send_json(CENTER.approve_latest_hold())
        elif route == "/api/hallucination":
            try:
                clean_text = sanitize_text(body.get("text", ""))
            except ValueError as exc:
                self._send_json({"error": str(exc), "code": "INVALID_INPUT"}, status=400)
                return
            self._send_json(CENTER.check_hallucination(clean_text))
        elif route == "/api/counterfactual":
            try:
                clean_text = sanitize_text(body.get("text", ""))
            except ValueError as exc:
                self._send_json({"error": str(exc), "code": "INVALID_INPUT"}, status=400)
                return
            self._send_json(CENTER.generate_counterfactuals(clean_text))
        elif route == "/api/drift":
            self._send_json(CENTER.check_drift_and_models(bool(body.get("simulate_drop", False))))
        elif route == "/api/adaptive":
            self._send_json(CENTER.generate_adaptive_suite())
        else:
            self._send_json({"error": "Not found", "code": "NOT_FOUND"}, status=404)


def main() -> None:
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", 8050))
    server = HTTPServer((host, port), CommandCenterHandler)
    url = f"http://{host}:{port}"
    print(f"PARALLIX IQ Executive Command Center running at {url}")
    print("Press Ctrl+C to stop.")
    if not os.environ.get("PORT"):
        threading.Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCommand Center stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
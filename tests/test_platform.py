import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from engines.adaptive_gen import AdaptiveTestGenerator
from engines.counterfactual import CounterfactualCase, CounterfactualEngine
from engines.drift_monitor import DriftMonitor
from engines.explainability import ExplainabilityEngine
from engines.hallucination import HallucinationDetector
from engines.intent_integrity import IntentIntegrityEngine
from gateway.interceptor import ActionInterceptionGateway, GatewayPolicy
from gateway.simulated_bank import SimulatedBankApp
from storage.audit_db import AuditDatabase
from storage.schemas import (
    ClaimCheck,
    CounterfactualResult,
    ExtractedAction,
    ExtractionBundle,
    GatewayDecision,
    GeneratedTestCase,
    HallucinationResult,
    IntentCheckResult,
)


class TestEngine1IntentIntegrity(unittest.TestCase):
    """Tests for Engine 1: Multilingual Intent Integrity & Polarity Reversal Detection."""

    def setUp(self) -> None:
        self.engine = IntentIntegrityEngine()

    def test_polarity_reversal_detected_manglish(self) -> None:
        """Engine 1 must flag polarity reversal when Manglish prohibition output is positive."""
        user_input = "Naale Rahulinu 500 roopa ayakkaruthu."
        bundle = ExtractionBundle(
            actions=[
                ExtractedAction(
                    intent="transfer",
                    recipient="Rahul",
                    amount=500.0,
                    currency="INR",
                    date="tomorrow",
                    polarity="positive",  # Model hallucinated positive polarity
                )
            ]
        )
        check = self.engine.audit_runtime(user_input, bundle)
        self.assertTrue(check.polarity_reversed)
        self.assertFalse(check.field_scores["polarity_integrity"])
        self.assertLess(check.integrity_score, 1.0)
        self.assertTrue(
            any("Polarity reversal" in m for m in check.mismatches),
            f"Expected polarity reversal mismatch in: {check.mismatches}",
        )

    def test_polarity_reversal_detected_hinglish(self) -> None:
        """Engine 1 must flag polarity reversal when Hinglish 'mat bhejna' output is positive."""
        user_input = "Kal Rahul ko 500 rupaye mat bhejna."
        bundle = ExtractionBundle(
            actions=[
                ExtractedAction(
                    intent="transfer",
                    recipient="Rahul",
                    amount=500.0,
                    currency="INR",
                    date="tomorrow",
                    polarity="positive",
                )
            ]
        )
        check = self.engine.audit_runtime(user_input, bundle)
        self.assertTrue(check.polarity_reversed)
        self.assertFalse(check.field_scores["polarity_integrity"])

    def test_polarity_reversal_detected_english(self) -> None:
        """Engine 1 must flag polarity reversal when English 'do not transfer' output is positive."""
        user_input = "Do not transfer 500 rupees to Rahul tomorrow."
        bundle = ExtractionBundle(
            actions=[
                ExtractedAction(
                    intent="transfer",
                    recipient="Rahul",
                    amount=500.0,
                    polarity="positive",
                )
            ]
        )
        check = self.engine.audit_runtime(user_input, bundle)
        self.assertTrue(check.polarity_reversed)

    def test_correct_negative_polarity_passes(self) -> None:
        """When input is negative and output correctly extracts negative, no polarity reversal."""
        user_input = "Naale Rahulinu 500 roopa ayakkaruthu."
        bundle = ExtractionBundle(
            actions=[
                ExtractedAction(
                    intent="transfer",
                    recipient="Rahul",
                    amount=500.0,
                    currency="INR",
                    date="tomorrow",
                    polarity="negative",
                )
            ]
        )
        check = self.engine.audit_runtime(user_input, bundle)
        self.assertFalse(check.polarity_reversed)
        self.assertTrue(check.field_scores["polarity_integrity"])

    def test_self_repair_not_falsely_flagged(self) -> None:
        """Mid-sentence self-repair phrases ('scratch that') should not trigger false polarity reversal."""
        user_input = "Transfer 500 to Rahul... wait, scratch that, send 1000 to Suresh."
        bundle = ExtractionBundle(
            actions=[
                ExtractedAction(
                    intent="transfer",
                    recipient="Suresh",
                    amount=1000.0,
                    polarity="positive",
                )
            ]
        )
        check = self.engine.audit_runtime(user_input, bundle)
        self.assertFalse(check.polarity_reversed)

    def test_evaluate_against_expected_polarity_reversal(self) -> None:
        """Ground-truth evaluation catches polarity discrepancy and reduces integrity score."""
        user_input = "Kal Rahul ko 500 mat bhejna."
        bundle = ExtractionBundle(
            actions=[
                ExtractedAction(
                    intent="transfer",
                    recipient="Rahul",
                    amount=500.0,
                    currency="INR",
                    date="tomorrow",
                    polarity="positive",
                )
            ]
        )
        expected = {
            "intent": "transfer",
            "recipient": "Rahul",
            "amount": 500.0,
            "currency": "INR",
            "date": "tomorrow",
            "polarity": "negative",
        }
        check = self.engine.evaluate_against_expected(user_input, bundle, expected)
        self.assertTrue(check.polarity_reversed)
        self.assertFalse(check.field_scores["polarity"])
        self.assertEqual(check.field_scores["intent"], True)
        self.assertEqual(check.field_scores["recipient"], True)
        self.assertEqual(check.field_scores["amount"], True)


class TestEngine2HallucinationDetector(unittest.TestCase):
    """Tests for Engine 2: Hallucination Contradiction vs. Unverified Claim Handling."""

    def setUp(self) -> None:
        self.detector = HallucinationDetector()
        self.trusted_evidence = {
            "account_balance": 2500,
            "daily_limit": 50000,
            "account_status": "active",
        }

    def test_factual_contradiction_high_risk(self) -> None:
        """A verifiable fact contradicting trusted backend numbers must be flagged as contradicted (high risk)."""
        ai_response = "Your daily limit is 100000 rupees."
        result = self.detector.evaluate(ai_response, self.trusted_evidence)
        self.assertEqual(result.hallucination_risk, "high")

        contradicted_claims = [c for c in result.claims if c.status == "contradicted"]
        self.assertEqual(len(contradicted_claims), 1)
        self.assertEqual(contradicted_claims[0].risk_level, "high")
        self.assertEqual(contradicted_claims[0].evidence_ref, "daily_limit=50000")

    def test_unverified_claim_medium_risk(self) -> None:
        """A verifiable assertion without trusted evidence backing must be marked 'unverified', NOT 'contradicted'."""
        ai_response = "RBI changed UPI rules yesterday."
        result = self.detector.evaluate(ai_response, self.trusted_evidence)
        self.assertEqual(result.hallucination_risk, "medium")

        unverified_facts = [
            c for c in result.claims if c.category == "verifiable_fact" and c.status == "unverified"
        ]
        self.assertEqual(len(unverified_facts), 1)
        self.assertIsNone(unverified_facts[0].evidence_ref)
        self.assertEqual(unverified_facts[0].risk_level, "medium")

    def test_confirmed_claim_low_risk(self) -> None:
        """When an assertion matches trusted ground-truth evidence, it is confirmed with low risk."""
        ai_response = "Your account balance is 2500 rupees."
        result = self.detector.evaluate(ai_response, self.trusted_evidence)
        self.assertEqual(result.hallucination_risk, "low")

        confirmed_claims = [c for c in result.claims if c.status == "confirmed"]
        self.assertEqual(len(confirmed_claims), 1)
        self.assertEqual(confirmed_claims[0].risk_level, "low")
        self.assertEqual(confirmed_claims[0].evidence_ref, "account_balance=2500")

    def test_subjective_opinion_low_risk(self) -> None:
        """Subjective opinions ('I think...') are categorized as opinion and do not trigger hallucination alerts."""
        ai_response = "I think you should enable transfer alerts."
        result = self.detector.evaluate(ai_response, self.trusted_evidence)
        self.assertEqual(result.hallucination_risk, "low")
        self.assertEqual(result.claims[0].category, "opinion")
        self.assertEqual(result.claims[0].status, "unverified")
        self.assertEqual(result.claims[0].risk_level, "low")

    def test_internal_contradiction_detection(self) -> None:
        """Mutually incompatible state claims in the same text must trigger high hallucination risk."""
        ai_response = "Your transfer succeeded. However, the transaction was cancelled."
        result = self.detector.evaluate(ai_response, self.trusted_evidence)
        self.assertEqual(result.hallucination_risk, "high")
        self.assertTrue(len(result.internal_contradictions) > 0)


class TestEngine3Counterfactual(unittest.TestCase):
    """Tests for Engine 3: Counterfactual Mutation Generation & Invariance Verification."""

    def setUp(self) -> None:
        self.engine = CounterfactualEngine()

    def test_generate_variants_all_perturbation_types(self) -> None:
        """generate_variants must produce single-variable counterfactual perturbations."""
        base_sentence = "Naale Rahulinu 500 roopa ayakkanam."
        variants = self.engine.generate_variants(base_sentence)

        types_generated = {v.perturbation_type for v in variants}
        self.assertIn("negation_flip", types_generated)
        self.assertIn("amount_shift", types_generated)
        self.assertIn("recipient_swap", types_generated)
        self.assertIn("date_shift", types_generated)
        self.assertIn("condition_inject", types_generated)
        self.assertIn("meaning_preserving_invariance", types_generated)

    def test_evaluate_pair_pass_on_expected_mutation(self) -> None:
        """evaluate_pair passes when target field mutated as expected and core slots remain unchanged."""
        base_input = "Naale Rahulinu 500 roopa ayakkanam."
        case = CounterfactualCase(
            perturbation_type="amount_shift",
            original_input=base_input,
            modified_input="Naale Rahulinu 50000 roopa ayakkanam.",
            expected_change="amount -> 50000.0",
            target_field="amount",
            expected_new_value=50000.0,
        )
        orig_bundle = ExtractionBundle(
            actions=[
                ExtractedAction(
                    intent="transfer",
                    recipient="Rahul",
                    amount=500.0,
                    polarity="positive",
                )
            ]
        )
        mod_bundle = ExtractionBundle(
            actions=[
                ExtractedAction(
                    intent="transfer",
                    recipient="Rahul",
                    amount=50000.0,
                    polarity="positive",
                )
            ]
        )
        result = self.engine.evaluate_pair(case, orig_bundle, mod_bundle)
        self.assertTrue(result.passed)
        self.assertIn("amount: 500.0 -> 50000.0", result.observed_change)

    def test_evaluate_pair_fail_on_collateral_mutation(self) -> None:
        """evaluate_pair fails if unrelated core fields accidentally mutated during perturbation."""
        case = CounterfactualCase(
            perturbation_type="amount_shift",
            original_input="Send 500 to Rahul",
            modified_input="Send 50000 to Rahul",
            expected_change="amount -> 50000.0",
            target_field="amount",
            expected_new_value=50000.0,
        )
        orig_bundle = ExtractionBundle(
            actions=[
                ExtractedAction(intent="transfer", recipient="Rahul", amount=500.0)
            ]
        )
        # Model mutated recipient to Suresh as well (collateral hallucination)
        mod_bundle = ExtractionBundle(
            actions=[
                ExtractedAction(intent="transfer", recipient="Suresh", amount=50000.0)
            ]
        )
        result = self.engine.evaluate_pair(case, orig_bundle, mod_bundle)
        self.assertFalse(result.passed)

    def test_meaning_preserving_invariance_evaluation(self) -> None:
        """Polite wrapper must not change extracted action fields."""
        case = CounterfactualCase(
            perturbation_type="meaning_preserving_invariance",
            original_input="Send 500 to Rahul",
            modified_input="Please, Send 500 to Rahul",
            expected_change="no change",
            target_field="none",
            expected_new_value=None,
        )
        bundle = ExtractionBundle(
            actions=[
                ExtractedAction(intent="transfer", recipient="Rahul", amount=500.0)
            ]
        )
        # When no fields change, invariance test passes
        res_pass = self.engine.evaluate_pair(case, bundle, bundle)
        self.assertTrue(res_pass.passed)

        # If model alters polarity or recipient under polite wrapper, invariance test fails
        altered_bundle = ExtractionBundle(
            actions=[
                ExtractedAction(
                    intent="transfer", recipient="Rahul", amount=500.0, polarity="question"
                )
            ]
        )
        res_fail = self.engine.evaluate_pair(case, bundle, altered_bundle)
        self.assertFalse(res_fail.passed)


class TestEngine4SimulatedBankGateway(unittest.TestCase):
    """Tests for Engine 4: PASS, HOLD, and BLOCK Decisions in SimulatedBankApp & Gateway."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.db = AuditDatabase(Path(self.test_dir) / "test_bank.db")
        self.gateway = ActionInterceptionGateway(db=self.db)
        self.bank = SimulatedBankApp(gateway=self.gateway, starting_balance=100000.0)

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_pass_decision_executes_and_debits_wallet(self) -> None:
        """Valid unambiguous action -> PASS -> EXECUTED -> wallet balance deducted."""
        prompt = "Rajan 500 ayakk"
        action = ExtractedAction(
            intent="transfer",
            recipient="Rajan",
            amount=500.0,
            currency="INR",
            polarity="positive",
        )
        outcome = self.bank.process_proposed_action(
            user_input=prompt,
            action=action,
            session_id="test_pass",
        )
        self.assertEqual(outcome["gateway_decision"], "PASS")
        self.assertEqual(outcome["execution_status"], "EXECUTED")
        self.assertEqual(outcome["wallet_balance"], 99500.0)
        self.assertEqual(self.bank.balances["Rajan"], 2500.0)
        self.assertEqual(len(self.bank.executed_ledger), 1)

    def test_hold_decision_conditional_trigger(self) -> None:
        """Conditional action -> HOLD -> HELD_FOR_APPROVAL -> queued without deducting funds."""
        prompt = "Rahul call cheythal maathram 500 ayakk"
        action = ExtractedAction(
            intent="transfer",
            recipient="Rahul",
            amount=500.0,
            currency="INR",
            polarity="conditional",
            condition="call cheythal maathram",
        )
        outcome = self.bank.process_proposed_action(
            user_input=prompt,
            action=action,
            session_id="test_hold",
        )
        self.assertEqual(outcome["gateway_decision"], "HOLD")
        self.assertEqual(outcome["execution_status"], "HELD_FOR_APPROVAL")
        self.assertEqual(outcome["wallet_balance"], 100000.0)  # Funds preserved
        self.assertEqual(len(self.bank.held_queue), 1)
        self.assertIn("condition", outcome["reasons"][0].lower())

    def test_hold_decision_high_value_transfer(self) -> None:
        """Transfer >= 50,000 INR requires secondary approval -> HOLD."""
        prompt = "Transfer 60000 INR to Rajan"
        action = ExtractedAction(
            intent="transfer",
            recipient="Rajan",
            amount=60000.0,
            currency="INR",
            polarity="positive",
        )
        outcome = self.bank.process_proposed_action(
            user_input=prompt,
            action=action,
            session_id="test_high_val",
        )
        self.assertEqual(outcome["gateway_decision"], "HOLD")
        self.assertEqual(outcome["execution_status"], "HELD_FOR_APPROVAL")
        self.assertEqual(outcome["wallet_balance"], 100000.0)

    def test_block_decision_negative_polarity_prohibition(self) -> None:
        """Explicit prohibition -> BLOCK -> BLOCKED -> wallet balance unchanged."""
        prompt = "Rahulin 1000 ayakkaruth"
        action = ExtractedAction(
            intent="transfer",
            recipient="Rahul",
            amount=1000.0,
            polarity="negative",
        )
        outcome = self.bank.process_proposed_action(
            user_input=prompt,
            action=action,
            session_id="test_block_neg",
        )
        self.assertEqual(outcome["gateway_decision"], "BLOCK")
        self.assertEqual(outcome["execution_status"], "BLOCKED")
        self.assertEqual(outcome["wallet_balance"], 100000.0)
        self.assertEqual(len(self.bank.executed_ledger), 0)

    def test_block_decision_polarity_reversal_from_engine1(self) -> None:
        """If Engine 1 flags a polarity reversal, Gateway must enforce BLOCK."""
        prompt = "Naale Rahulinu 500 roopa ayakkaruthu."
        action = ExtractedAction(
            intent="transfer",
            recipient="Rahul",
            amount=500.0,
            polarity="positive",  # Model got polarity wrong
        )
        intent_check = IntentCheckResult(
            integrity_score=0.75,
            field_scores={"polarity_integrity": False},
            mismatches=["Polarity reversal detected"],
            polarity_reversed=True,
        )
        outcome = self.bank.process_proposed_action(
            user_input=prompt,
            action=action,
            intent_check=intent_check,
            session_id="test_reversal_block",
        )
        self.assertEqual(outcome["gateway_decision"], "BLOCK")
        self.assertEqual(outcome["execution_status"], "BLOCKED")
        self.assertTrue(any("polarity reversal" in r.lower() for r in outcome["reasons"]))

    def test_block_decision_adversarial_security_violations(self) -> None:
        """Negative amount underflow or blocked recipient name must be hard-blocked."""
        # Test negative amount
        action_neg = ExtractedAction(
            intent="transfer",
            recipient="Rahul",
            amount=-500.0,
            polarity="positive",
        )
        res_neg = self.bank.process_proposed_action("Transfer -500", action_neg)
        self.assertEqual(res_neg["gateway_decision"], "BLOCK")

        # Test blocked recipient
        action_hacker = ExtractedAction(
            intent="transfer",
            recipient="Hacker",
            amount=5000.0,
            polarity="positive",
        )
        res_hacker = self.bank.process_proposed_action("Transfer to Hacker", action_hacker)
        self.assertEqual(res_hacker["gateway_decision"], "BLOCK")


class TestEngine5DriftMonitor(unittest.TestCase):
    """Tests for Engine 5: Benchmark Accuracy Drift Alert Thresholds."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.db = AuditDatabase(Path(self.test_dir) / "test_drift.db")
        self.monitor = DriftMonitor(db=self.db, drop_threshold=3.0)

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_baseline_establishment_no_alert(self) -> None:
        """Initial benchmark run sets baseline and does not trigger drift alert."""
        report = self.monitor.record_and_evaluate(
            model_id="test-model",
            dataset_version="v1",
            total_cases=100,
            exact_match_rate=74.0,
            field_precision=93.83,
        )
        self.assertFalse(report.drift_detected)
        self.assertEqual(report.delta, 0.0)
        self.assertIsNone(report.alert_message)
        self.assertEqual(report.baseline_accuracy, 74.0)

    def test_minor_accuracy_drop_within_threshold_no_alert(self) -> None:
        """A drop within the drop threshold (e.g. -1.0% drop vs -3.0% limit) does not alert."""
        # Establish baseline
        self.monitor.record_and_evaluate("test-model", "v1", 100, 74.0, 93.83)
        # Minor variation within 3.0% threshold
        report = self.monitor.record_and_evaluate("test-model", "v1", 100, 73.0, 93.0)
        self.assertFalse(report.drift_detected)
        self.assertEqual(report.delta, -1.0)
        self.assertIsNone(report.alert_message)

    def test_drift_detected_when_drop_exceeds_threshold(self) -> None:
        """A drop exceeding drop_threshold (e.g. 74.0% -> 61.5%, delta -12.5%) triggers DRIFT ALERT."""
        # Establish baseline
        self.monitor.record_and_evaluate("test-model", "v1", 100, 74.0, 93.83)
        # Significant regression
        report = self.monitor.record_and_evaluate("test-model", "v1", 100, 61.5, 86.2)
        self.assertTrue(report.drift_detected)
        self.assertEqual(report.delta, -12.5)
        self.assertIsNotNone(report.alert_message)
        self.assertIn("DRIFT ALERT", report.alert_message)
        self.assertIn("61.50%", report.alert_message)

    def test_custom_drop_threshold(self) -> None:
        """Custom drop threshold (e.g. 10.0%) correctly dictates alert boundary."""
        relaxed_monitor = DriftMonitor(db=self.db, drop_threshold=10.0)
        relaxed_monitor.record_and_evaluate("test-model-2", "v1", 100, 80.0, 90.0)
        # Drop of 5% is permitted by a 10% threshold
        report = relaxed_monitor.record_and_evaluate("test-model-2", "v1", 100, 75.0, 88.0)
        self.assertFalse(report.drift_detected)


class TestEngine7Explainability(unittest.TestCase):
    """Tests for Engine 7: Explainable AI Failure Report Generation."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.db = AuditDatabase(Path(self.test_dir) / "test_explain.db")
        self.explainer = ExplainabilityEngine(db=self.db)
        self.intent_engine = IntentIntegrityEngine()

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_explain_intent_failure_polarity_reversal(self) -> None:
        """explain_intent_failure creates a critical polarity_reversal incident report."""
        user_input = "Naale Rahulinu 500 roopa ayakkaruthu."
        action = ExtractedAction(
            intent="transfer",
            recipient="Rahul",
            amount=500.0,
            currency="INR",
            date="tomorrow",
            polarity="positive",
        )
        expected = {
            "intent": "transfer",
            "recipient": "Rahul",
            "amount": 500.0,
            "currency": "INR",
            "date": "tomorrow",
            "polarity": "negative",
        }
        check = self.intent_engine.evaluate_against_expected(
            user_input, ExtractionBundle(actions=[action]), expected
        )
        report = self.explainer.explain_intent_failure(
            user_input=user_input,
            expected=expected,
            actual_action=action,
            check=check,
            test_id="TC018",
            language="Manglish",
        )
        self.assertTrue(report.incident_id.startswith("INC-"))
        self.assertEqual(report.failure_type, "polarity_reversal")
        self.assertEqual(report.severity, "critical")
        self.assertIn("Engine 1", report.engine_source)
        self.assertIn("Polarity reversal", report.explanation)
        self.assertTrue(len(report.recommendation) > 0)
        self.assertEqual(report.evidence.get("language"), "Manglish")

    def test_explain_gateway_block_report(self) -> None:
        """explain_gateway_block generates high-severity report for hard blocked actions."""
        action = ExtractedAction(
            intent="transfer",
            recipient="Hacker",
            amount=5000.0,
            polarity="positive",
        )
        decision = GatewayDecision(
            decision="BLOCK",
            risk_score=0.95,
            reasons=["Recipient 'Hacker' is blocked by security policy."],
        )
        report = self.explainer.explain_gateway_block(
            user_input="Transfer 5000 to Hacker",
            proposed_action=action,
            decision=decision,
        )
        self.assertEqual(report.failure_type, "gateway_block")
        self.assertEqual(report.severity, "high")
        self.assertEqual(report.confidence, 0.98)
        self.assertIn("BLOCK", report.explanation)

    def test_markdown_and_json_serialization(self) -> None:
        """Incident reports serialize to valid Markdown and parseable JSON."""
        report = self.explainer.create_incident(
            engine_source="Engine 2",
            user_input="What is my balance?",
            expected_summary="2500",
            actual_summary="10000",
            failure_type="factual_contradiction",
            severity="high",
            confidence=0.92,
            explanation="Contradicted account balance",
            recommendation="Ground output in database records",
        )
        md = self.explainer.to_markdown(report)
        self.assertIn(f"Incident Report: `{report.incident_id}`", md)
        self.assertIn("| **Severity** | **HIGH** |", md)

        json_str = self.explainer.to_json(report)
        parsed = json.loads(json_str)
        self.assertEqual(parsed["incident_id"], report.incident_id)
        self.assertEqual(parsed["failure_type"], "factual_contradiction")

    def test_secret_scrubbing_in_incident_reports(self) -> None:
        """API keys in user input or prompts must be scrubbed from stored failure reports."""
        leak_input = "Transfer 500 to Rahul using AIzaSyD9876543210AbCdEfGhIjKlMnOp"
        report = self.explainer.create_incident(
            engine_source="Test",
            user_input=leak_input,
            expected_summary="safe",
            actual_summary="safe",
            failure_type="leak_check",
            severity="low",
            confidence=1.0,
            explanation="no issue",
            recommendation="none",
        )
        self.assertNotIn("AIzaSyD9876543210AbCdEfGhIjKlMnOp", report.user_input)
        self.assertIn("[REDACTED_API_KEY]", report.user_input)


class TestEngine8AdaptiveTestGenerator(unittest.TestCase):
    """Tests for Engine 8: Deduplicated Adaptive Test Generation from Failure History."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.datasets_dir = Path(self.test_dir) / "datasets"
        self.datasets_dir.mkdir(parents=True, exist_ok=True)
        self.db = AuditDatabase(Path(self.test_dir) / "test_adaptive.db")
        self.generator = AdaptiveTestGenerator(db=self.db, datasets_dir=self.datasets_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_deduplicated_generation_from_failures(self) -> None:
        """Generated tests must be deduplicated: all inputs unique and not repeating existing suite prompts."""
        # Seed an unresolved incident to guide generation
        self.db.log_failure_incident(
            {
                "incident_id": "INC-TEST-001",
                "engine_source": "Engine 1",
                "user_input": "Naale Rahulinu 500 roopa ayakkaruthu.",
                "expected_summary": "{}",
                "actual_summary": "{}",
                "failure_type": "polarity_reversal",
                "severity": "critical",
                "confidence": 0.95,
                "explanation": "Polarity reversal observed",
                "recommendation": "Add more prohibition tests",
                "evidence": {"language": "Manglish"},
            }
        )

        cases = self.generator.generate_from_failures(max_cases=4)
        self.assertTrue(len(cases) > 0)
        self.assertLessEqual(len(cases), 4)

        # Check deduplication within the generated batch
        normalized_inputs = [re.sub(r"\s+", " ", c.input.strip().lower()) for c in cases]
        self.assertEqual(
            len(normalized_inputs),
            len(set(normalized_inputs)),
            "Generated cases must have unique inputs (no duplicates).",
        )

        # Check candidate validity
        for c in cases:
            self.assertTrue(c.id.startswith("ADP"))
            self.assertTrue(len(c.input) > 0)
            self.assertIn(c.difficulty, ("easy", "medium", "hard", "adversarial"))
            self.assertTrue(self.generator.validate_candidate(c))

    def test_save_versioned_dataset(self) -> None:
        """Saving adaptive tests must create a versioned JSON file with correct schema."""
        cases = self.generator.generate_from_failures(max_cases=2)
        saved_path = self.generator.save_versioned_dataset(
            cases, targeted_weaknesses=["polarity_reversal"]
        )
        self.assertTrue(saved_path.exists())
        self.assertEqual(saved_path.name, "adaptive_v1.json")

        content = json.loads(saved_path.read_text(encoding="utf-8"))
        self.assertEqual(content["dataset_version"], "adaptive_v1")
        self.assertEqual(content["total_cases"], len(cases))
        self.assertEqual(len(content["test_cases"]), len(cases))

        # Next save increments version to adaptive_v2.json
        saved_path_v2 = self.generator.save_versioned_dataset(
            cases, targeted_weaknesses=["polarity_reversal"]
        )
        self.assertEqual(saved_path_v2.name, "adaptive_v2.json")


if __name__ == "__main__":
    unittest.main()

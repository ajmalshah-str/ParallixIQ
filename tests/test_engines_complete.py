import unittest
from adapters.base import BaseModelAdapter
from adapters.model_router import MultiModelConsensusRouter
from adapters.planned_adapters import PlannedProviderAdapter, build_comparison_table
from core.benchmark_audit import run_reproducible_audit
from engines.hallucination import HallucinationDetector
from engines.intent_integrity import IntentIntegrityEngine
from gateway.interceptor import ActionInterceptionGateway
from gateway.simulated_bank import SimulatedBankApp
from storage.schemas import ExtractedAction, ExtractionBundle


class _StubAdapter(BaseModelAdapter):
    provider = "StubProvider"
    status = "connected"

    def __init__(self, model_id: str, action: ExtractedAction):
        self.model_id = model_id
        self._action = action

    def extract_actions(self, text: str) -> tuple[ExtractionBundle, float]:
        return ExtractionBundle(actions=[self._action]), 12.5

    def generate_text(self, prompt: str, system_prompt: str | None = None) -> str:
        return "stub"


class TestEngine6AndFull10Scenarios(unittest.TestCase):
    def test_engine6_multi_model_consensus_and_planned_adapters(self) -> None:
        act_neg = ExtractedAction(intent="transfer", recipient="Rahul", amount=500.0, currency="INR", polarity="negative")
        act_pos = ExtractedAction(intent="transfer", recipient="Rahul", amount=500.0, currency="INR", polarity="positive")

        fast = _StubAdapter("fast-v1", act_neg)
        judge_agree = _StubAdapter("judge-v1", act_neg)
        router_ok = MultiModelConsensusRouter(fast_adapter=fast, judge_adapter=judge_agree)
        res_ok = router_ok.extract_with_consensus("Naale Rahulinu 500 roopa ayakkaruthu.")
        self.assertEqual(res_ok["mode"], "DUAL_MODEL_CONSENSUS")
        self.assertTrue(res_ok["consensus"])
        self.assertEqual(res_ok["gateway_recommendation"], "PROCEED_TO_POLICY")

        judge_disagree = _StubAdapter("judge-v1", act_pos)
        router_diff = MultiModelConsensusRouter(fast_adapter=fast, judge_adapter=judge_disagree)
        res_diff = router_diff.extract_with_consensus("Naale Rahulinu 500 roopa ayakkaruthu.")
        self.assertFalse(res_diff["consensus"])
        self.assertEqual(res_diff["gateway_recommendation"], "FORCE_HOLD_DISAGREEMENT")

        planned = PlannedProviderAdapter("OpenAI", "gpt-4o-mini", "OPENAI_API_KEY")
        with self.assertRaises(NotImplementedError):
            planned.extract_actions("Send 500 to Rahul")

        comp = router_ok.compare_adapters([fast, planned], ["Send 500 to Rahul"])
        self.assertEqual(comp[0]["status"], "CONNECTED")
        self.assertEqual(comp[1]["status"], "PLANNED")
        self.assertIsNone(comp[1]["avg_latency_ms"])

        table = build_comparison_table()
        self.assertEqual(table[0].status, "connected")
        self.assertTrue(all(row.status == "planned" for row in table[1:]))

    def test_reproducible_benchmark_audit_splits_and_metrics(self) -> None:
        report = run_reproducible_audit(use_live_gemini=False)
        self.assertEqual(report["dataset_size"], 100)
        self.assertEqual(report["unique_prompts"], 100)
        self.assertEqual(report["duplicate_count"], 0)
        self.assertEqual(report["splits"]["development_overlap_53"]["total_cases"], 53)
        self.assertEqual(report["splits"]["held_out_expansion_47"]["total_cases"], 47)
        self.assertIn("Malayalam", report["by_language"])
        self.assertIn("Manglish", report["by_language"])

    def test_10_live_demonstration_scenarios(self) -> None:
        e1 = IntentIntegrityEngine()
        e2 = HallucinationDetector()
        gw = ActionInterceptionGateway()
        bank = SimulatedBankApp(gateway=gw, starting_balance=50000.0)

        # Scenario 1: Manglish negation -> BLOCK
        act1 = ExtractedAction(intent="transfer", recipient="Rahul", amount=500.0, currency="INR", date="tomorrow", polarity="negative")
        d1 = gw.inspect_action("Naale Rahulinu 500 roopa ayakkaruthu.", act1, e1.audit_runtime("Naale Rahulinu 500 roopa ayakkaruthu.", ExtractionBundle(actions=[act1])))
        self.assertEqual(d1.decision, "BLOCK")

        # Scenario 2: Positive instruction -> PASS
        act2 = ExtractedAction(intent="transfer", recipient="Rahul", amount=500.0, currency="INR", date="tomorrow", polarity="positive")
        d2 = gw.inspect_action("Naale Rahulinu 500 roopa ayakkanam.", act2, e1.audit_runtime("Naale Rahulinu 500 roopa ayakkanam.", ExtractionBundle(actions=[act2])))
        self.assertEqual(d2.decision, "PASS")

        # Scenario 3: Conditional instruction -> HOLD
        act3 = ExtractedAction(intent="transfer", recipient="Rahul", amount=500.0, currency="INR", polarity="conditional", condition="if Rahul calls")
        d3 = gw.inspect_action("Rahul vilichal maathram paisa ayakkanam.", act3)
        self.assertEqual(d3.decision, "HOLD")

        # Scenario 4: Wrong recipient mismatch -> BLOCK
        chk4 = e1.evaluate_against_expected(
            "Send 500 to Rahul.",
            ExtractionBundle(actions=[ExtractedAction(intent="transfer", recipient="Arjun", amount=500.0, currency="INR", polarity="positive")]),
            {"intent": "transfer", "recipient": "Rahul", "amount": 500.0, "currency": "INR", "date": None, "polarity": "positive"},
        )
        self.assertFalse(chk4.field_scores["recipient"])

        # Scenario 5: Wrong amount mismatch -> BLOCK when exceeding hard ceiling or mismatched
        chk5 = e1.evaluate_against_expected(
            "Send 500 to Rahul.",
            ExtractionBundle(actions=[ExtractedAction(intent="transfer", recipient="Rahul", amount=5000.0, currency="INR", polarity="positive")]),
            {"intent": "transfer", "recipient": "Rahul", "amount": 500.0, "currency": "INR", "date": None, "polarity": "positive"},
        )
        self.assertFalse(chk5.field_scores["amount"])

        # Scenario 6: Missing recipient & amount -> BLOCK/HOLD (non-PASS)
        d6 = gw.inspect_action("Send the money tomorrow.", ExtractedAction(intent="transfer", date="tomorrow", polarity="positive"))
        self.assertNotEqual(d6.decision, "PASS")

        # Scenario 8: Hallucination 3-way distinction (SUPPORTED, CONTRADICTED, INSUFFICIENT_EVIDENCE)
        h8 = e2.evaluate(
            "Your balance is 2500 INR. Your daily limit is 99000 INR. RBI issued a new circular yesterday.",
            trusted_evidence={"account_balance": 2500, "daily_limit": 50000},
        )
        self.assertEqual(h8.claims[0].status, "confirmed")
        self.assertEqual(h8.claims[0].evidence_ref, "account_balance=2500")
        self.assertEqual(h8.claims[1].status, "contradicted")
        self.assertEqual(h8.claims[1].evidence_ref, "daily_limit=50000")
        self.assertEqual(h8.claims[2].status, "unverified")
        self.assertIsNone(h8.claims[2].evidence_ref)

        # Scenario 10: Action gateway bypass rejected
        forged = d2.model_copy(deep=True)
        with self.assertRaises(PermissionError):
            bank.execute_verified_transfer(act2, forged)


if __name__ == "__main__":
    unittest.main()

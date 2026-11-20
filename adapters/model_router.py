from adapters.gemini_adapter import GeminiAdapter
from storage.schemas import ExtractedAction, ExtractionBundle


class MultiModelConsensusRouter:
    """
    Runs a fast primary model and escalates ambiguous or high-value
    instructions to a second reasoning model for 2-of-2 consensus.
    """

    def __init__(
        self,
        fast_model: str = "gemini-flash-lite-latest",
        judge_model: str = "gemini-3.8-flash",
    ) -> None:
        self.fast_adapter = GeminiAdapter(model_id=fast_model)
        self.judge_adapter = GeminiAdapter(model_id=judge_model)

    def extract_with_consensus(self, text: str) -> dict:
        fast_bundle, fast_ms = self.fast_adapter.extract_actions(text)
        primary = fast_bundle.actions[0] if fast_bundle.actions else ExtractedAction()

        # Escalate if negation/condition appears or amount >= 10,000
        needs_second_opinion = (
            primary.polarity in ("negative", "conditional")
            or (primary.amount is not None and primary.amount >= 10000)
            or any(w in text.lower() for w in ("aruth", "mat ", "not", "agar", "maathram"))
        )

        if not needs_second_opinion:
            return {
                "mode": "FAST_SINGLE_MODEL",
                "models_used": [self.fast_adapter.model_id],
                "consensus": True,
                "latency_ms": fast_ms,
                "action": primary.model_dump(),
            }

        judge_bundle, judge_ms = self.judge_adapter.extract_actions(text)
        secondary = judge_bundle.actions[0] if judge_bundle.actions else ExtractedAction()

        agreed = (
            primary.intent == secondary.intent
            and primary.polarity == secondary.polarity
            and primary.amount == secondary.amount
        )

        return {
            "mode": "DUAL_MODEL_CONSENSUS",
            "models_used": [self.fast_adapter.model_id, self.judge_adapter.model_id],
            "consensus": agreed,
            "gateway_recommendation": "PROCEED_TO_POLICY" if agreed else "FORCE_HOLD_DISAGREEMENT",
            "latency_ms": round(fast_ms + judge_ms, 2),
            "fast_model_output": primary.model_dump(),
            "judge_model_output": secondary.model_dump(),
        }


if __name__ == "__main__":
    router = MultiModelConsensusRouter()
    sample = "Naale Rahulinu 500 roopa ayakkaruthu."
    print(router.extract_with_consensus(sample))
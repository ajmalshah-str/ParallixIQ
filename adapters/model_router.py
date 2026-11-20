import os
from typing import Any
from adapters.base import BaseModelAdapter
from adapters.gemini_adapter import GeminiAdapter
from storage.schemas import ExtractedAction


class MultiModelConsensusRouter:
    """
    Runs a fast primary model and escalates ambiguous or high-value
    instructions to a second reasoning model for 2-of-2 consensus.
    """

    def __init__(
        self,
        fast_model: str | None = None,
        judge_model: str | None = None,
        fast_adapter: BaseModelAdapter | None = None,
        judge_adapter: BaseModelAdapter | None = None,
    ) -> None:
        resolved_fast = fast_model or os.getenv("PARALLIX_FAST_MODEL", "gemini-flash-lite-latest")
        resolved_judge = judge_model or os.getenv("PARALLIX_JUDGE_MODEL", "gemini-2.5-flash")
        self.fast_adapter = fast_adapter if fast_adapter is not None else GeminiAdapter(model_id=resolved_fast)
        self.judge_adapter = judge_adapter if judge_adapter is not None else GeminiAdapter(model_id=resolved_judge)

    def extract_with_consensus(self, text: str) -> dict[str, Any]:
        fast_bundle, fast_ms = self.fast_adapter.extract_actions(text)
        primary = fast_bundle.actions[0] if fast_bundle.actions else ExtractedAction()

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

    def compare_adapters(
        self,
        adapters: list[BaseModelAdapter],
        prompts: list[str],
    ) -> list[dict[str, Any]]:
        """
        Evaluates identical prompts across connected adapters and reports
        unimplemented providers honestly as PLANNED without inventing scores.
        """
        results: list[dict[str, Any]] = []
        for adapter in adapters:
            if getattr(adapter, "status", "planned") != "connected":
                results.append(
                    {
                        "provider": adapter.provider,
                        "model_id": adapter.model_id,
                        "status": "PLANNED",
                        "tested_prompts": 0,
                        "avg_latency_ms": None,
                        "outputs": [],
                    }
                )
                continue

            total_ms = 0.0
            outputs: list[dict[str, Any]] = []
            for p in prompts:
                bundle, ms = adapter.extract_actions(p)
                total_ms += ms
                primary = bundle.actions[0].model_dump() if bundle.actions else ExtractedAction().model_dump()
                outputs.append({"input": p, "extracted": primary, "latency_ms": ms})

            avg_ms = round(total_ms / len(prompts), 2) if prompts else 0.0
            results.append(
                {
                    "provider": adapter.provider,
                    "model_id": adapter.model_id,
                    "status": "CONNECTED",
                    "tested_prompts": len(prompts),
                    "avg_latency_ms": avg_ms,
                    "outputs": outputs,
                }
            )
        return results

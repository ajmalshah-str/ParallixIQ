from adapters.base import BaseModelAdapter
from adapters.gemini_adapter import GeminiAdapter
from storage.schemas import ExtractionBundle, ModelComparisonEntry


class PlannedProviderAdapter(BaseModelAdapter):
    status = "planned"

    def __init__(self, provider: str, model_id: str, env_var: str):
        self.provider = provider
        self.model_id = model_id
        self.env_var = env_var

    def extract_actions(self, text: str) -> tuple[ExtractionBundle, float]:
        raise NotImplementedError(
            f"{self.provider} ({self.model_id}) is registered as a planned adapter. "
            f"Configure {self.env_var} and SDK client to activate live benchmarking."
        )

    def generate_text(self, prompt: str, system_prompt: str | None = None) -> str:
        raise NotImplementedError(
            f"{self.provider} ({self.model_id}) is not connected yet."
        )


def get_model_registry() -> list[BaseModelAdapter]:
    return [
        GeminiAdapter(model_id="gemini-flash-lite-latest"),
        PlannedProviderAdapter("OpenAI", "gpt-4o-mini", "OPENAI_API_KEY"),
        PlannedProviderAdapter("Anthropic", "claude-3-5-haiku-latest", "ANTHROPIC_API_KEY"),
        PlannedProviderAdapter("Ollama (Local)", "llama3.1:8b", "OLLAMA_HOST"),
    ]


def build_comparison_table(
    gemini_metrics: dict[str, float] | None = None,
) -> list[ModelComparisonEntry]:
    entries: list[ModelComparisonEntry] = []
    for adapter in get_model_registry():
        if adapter.status == "connected" and gemini_metrics:
            entries.append(
                ModelComparisonEntry(
                    provider=adapter.provider,
                    model_id=adapter.model_id,
                    status="connected",
                    intent_accuracy=gemini_metrics.get("intent_accuracy"),
                    field_precision=gemini_metrics.get("field_precision"),
                    safety_pass_rate=gemini_metrics.get("safety_pass_rate"),
                    avg_latency_ms=gemini_metrics.get("avg_latency_ms"),
                )
            )
        else:
            entries.append(
                ModelComparisonEntry(
                    provider=adapter.provider,
                    model_id=adapter.model_id,
                    status=adapter.status,
                )
            )
    return entries
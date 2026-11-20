from abc import ABC, abstractmethod
from typing import Literal
from storage.schemas import ExtractionBundle


class BaseModelAdapter(ABC):
    provider: str = "unknown"
    model_id: str = "unknown"
    status: Literal["connected", "planned"] = "planned"

    @abstractmethod
    def extract_actions(self, text: str) -> tuple[ExtractionBundle, float]:
        """
        Extracts structured actions from user input.
        Returns (ExtractionBundle, latency_in_ms).
        """
        raise NotImplementedError

    @abstractmethod
    def generate_text(self, prompt: str, system_prompt: str | None = None) -> str:
        """
        Generates a plain text response for factual or counterfactual checks.
        """
        raise NotImplementedError
from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ExtractedAction(BaseModel):
    intent: str | None = None
    recipient: str | None = None
    amount: float | None = None
    currency: str = "INR"
    date: str | None = None
    polarity: Literal["positive", "negative", "question", "conditional", "statement"] = "positive"
    condition: str | None = None


class ExtractionBundle(BaseModel):
    actions: list[ExtractedAction] = Field(default_factory=list)


class IntentCheckResult(BaseModel):
    integrity_score: float
    field_scores: dict[str, bool] = Field(default_factory=dict)
    mismatches: list[str] = Field(default_factory=list)
    polarity_reversed: bool = False
    omitted_actions: int = 0


class ClaimCheck(BaseModel):
    claim: str
    category: Literal["verifiable_fact", "assumption", "opinion"]
    status: Literal["confirmed", "contradicted", "unverified"]
    evidence_ref: str | None = None
    risk_level: Literal["low", "medium", "high"] = "medium"


class HallucinationResult(BaseModel):
    hallucination_risk: Literal["low", "medium", "high"]
    internal_contradictions: list[str] = Field(default_factory=list)
    claims: list[ClaimCheck] = Field(default_factory=list)


class CounterfactualResult(BaseModel):
    perturbation_type: str
    original_input: str
    modified_input: str
    expected_change: str
    observed_change: str
    passed: bool


class GatewayDecision(BaseModel):
    decision: Literal["PASS", "HOLD", "BLOCK"]
    risk_score: float
    reasons: list[str] = Field(default_factory=list)
    requires_human_review: bool = False
    timestamp: datetime = Field(default_factory=utc_now)


class DriftReport(BaseModel):
    model_id: str
    baseline_accuracy: float
    current_accuracy: float
    delta: float
    drift_detected: bool
    alert_message: str | None = None
    checked_at: datetime = Field(default_factory=utc_now)


class ModelComparisonEntry(BaseModel):
    provider: str
    model_id: str
    status: Literal["connected", "planned"]
    intent_accuracy: float | None = None
    field_precision: float | None = None
    safety_pass_rate: float | None = None
    avg_latency_ms: float | None = None


class FailureReport(BaseModel):
    incident_id: str
    engine_source: str
    user_input: str
    expected_summary: str
    actual_summary: str
    failure_type: str
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float
    explanation: str
    recommendation: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class GeneratedTestCase(BaseModel):
    id: str
    language: str
    category: str
    variation: str
    difficulty: Literal["easy", "medium", "hard", "adversarial"]
    input: str
    expected: dict[str, Any]
    source_incident_id: str | None = None
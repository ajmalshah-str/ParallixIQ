import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal
from storage.audit_db import AuditDatabase
from storage.schemas import (
    CounterfactualResult,
    ExtractedAction,
    FailureReport,
    GatewayDecision,
    HallucinationResult,
    IntentCheckResult,
)


def _scrub_secrets(text: str) -> str:
    cleaned = re.sub(r"AIza[0-9A-Za-z\-_]{20,}", "[REDACTED_API_KEY]", text)
    cleaned = re.sub(r"sk-[0-9A-Za-z\-_]{20,}", "[REDACTED_API_KEY]", cleaned)
    return cleaned


def _new_incident_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    short_hex = uuid.uuid4().hex[:6].upper()
    return f"INC-{stamp}-{short_hex}"


class ExplainabilityEngine:
    def __init__(self, db: AuditDatabase | None = None):
        self.db = db if db is not None else AuditDatabase()

    def create_incident(
        self,
        engine_source: str,
        user_input: str,
        expected_summary: str,
        actual_summary: str,
        failure_type: str,
        severity: Literal["low", "medium", "high", "critical"],
        confidence: float,
        explanation: str,
        recommendation: str,
        evidence: dict[str, Any] | None = None,
    ) -> FailureReport:
        report = FailureReport(
            incident_id=_new_incident_id(),
            engine_source=engine_source,
            user_input=_scrub_secrets(user_input),
            expected_summary=_scrub_secrets(expected_summary),
            actual_summary=_scrub_secrets(actual_summary),
            failure_type=failure_type,
            severity=severity,
            confidence=round(confidence, 4),
            explanation=_scrub_secrets(explanation),
            recommendation=_scrub_secrets(recommendation),
            evidence=evidence or {},
        )
        self.db.log_failure_incident(report.model_dump())
        return report

    def explain_intent_failure(
        self,
        user_input: str,
        expected: dict[str, Any],
        actual_action: ExtractedAction,
        check: IntentCheckResult,
        test_id: str | None = None,
        language: str | None = None,
    ) -> FailureReport:
        failed_fields = [k for k, passed in check.field_scores.items() if not passed]

        if check.polarity_reversed:
            failure_type = "polarity_reversal"
            severity: Literal["low", "medium", "high", "critical"] = "critical"
            recommendation = (
                "Enforce deterministic prohibition marker checks in Engine 1 and keep "
                "Engine 4 gateway negation blocking active before execution."
            )
        elif check.omitted_actions > 0:
            failure_type = "omitted_action"
            severity = "high"
            recommendation = (
                "Add multi-clause compound examples (using 'pinne' and 'aur') to the "
                "extraction prompt and flag multi-number prompts for secondary review."
            )
        elif "recipient" in failed_fields:
            failure_type = "recipient_mismatch"
            severity = "high"
            recommendation = (
                "Add Indic dative suffix stripping examples (-inu, -kku, -ko) and "
                "validate extracted names against verified contact-book aliases."
            )
        elif "amount" in failed_fields:
            failure_type = "amount_mismatch"
            severity = "critical"
            recommendation = (
                "Expand vernacular number conversion rules (e.g., anjooru, dedh hazar, peti) "
                "and require confirmation when numeric tokens differ from parsed amounts."
            )
        elif "date" in failed_fields:
            failure_type = "date_inference_mismatch"
            severity = "low"
            recommendation = (
                "Align temporal policy: keep date as null when no explicit time word "
                "appears so transactions are not scheduled prematurely."
            )
        else:
            failure_type = "semantic_field_mismatch"
            severity = "medium"
            recommendation = (
                "Review prompt schema instructions for the mismatched fields and add "
                "targeted regression cases to the benchmark suite."
            )

        reasons_text = "; ".join(check.mismatches) if check.mismatches else "Field discrepancy detected."
        explanation = (
            f"Intent integrity score dropped to {check.integrity_score:.2f}. "
            f"Failed checks: {', '.join(failed_fields) or 'runtime_rule'}. Details: {reasons_text}"
        )

        evidence: dict[str, Any] = {
            "integrity_score": check.integrity_score,
            "failed_fields": failed_fields,
            "mismatches": check.mismatches,
        }
        if test_id:
            evidence["test_id"] = test_id
        if language:
            evidence["language"] = language

        return self.create_incident(
            engine_source="Engine 1: Multilingual Intent Integrity",
            user_input=user_input,
            expected_summary=json.dumps(expected, ensure_ascii=False),
            actual_summary=json.dumps(actual_action.model_dump(), ensure_ascii=False),
            failure_type=failure_type,
            severity=severity,
            confidence=0.95,
            explanation=explanation,
            recommendation=recommendation,
            evidence=evidence,
        )

    def explain_hallucination(
        self,
        user_input: str,
        ai_response: str,
        result: HallucinationResult,
        trusted_evidence: dict[str, Any] | None = None,
    ) -> FailureReport:
        contradicted = [c for c in result.claims if c.status == "contradicted"]
        unverified = [c for c in result.claims if c.status == "unverified" and c.category == "verifiable_fact"]

        if contradicted or result.internal_contradictions:
            failure_type = "factual_contradiction"
            severity: Literal["low", "medium", "high", "critical"] = "high"
        else:
            failure_type = "unverified_factual_claim"
            severity = "medium"

        details: list[str] = []
        for c in contradicted:
            details.append(f"Contradicted claim '{c.claim}' against evidence ({c.evidence_ref}).")
        for ic in result.internal_contradictions:
            details.append(ic)
        for u in unverified:
            details.append(f"Unverified factual claim '{u.claim}' lacks trusted source backing.")

        explanation = " ".join(details) if details else "Model output contained unsupported assertions."
        recommendation = (
            "Ground all numeric balances, limits, and policy statements strictly in "
            "verified backend records, and block high-risk responses at the gateway."
        )

        return self.create_incident(
            engine_source="Engine 2: Hallucination & Fabrication Detector",
            user_input=user_input,
            expected_summary=json.dumps(trusted_evidence or {}, ensure_ascii=False),
            actual_summary=ai_response,
            failure_type=failure_type,
            severity=severity,
            confidence=0.92,
            explanation=explanation,
            recommendation=recommendation,
            evidence={
                "hallucination_risk": result.hallucination_risk,
                "contradicted_count": len(contradicted),
                "unverified_count": len(unverified),
                "internal_contradictions": result.internal_contradictions,
            },
        )

    def explain_counterfactual_failure(self, cf_result: CounterfactualResult) -> FailureReport:
        is_invariance = cf_result.perturbation_type == "meaning_preserving_invariance"
        failure_type = "invariance_break" if is_invariance else "counterfactual_insensitivity"
        severity: Literal["low", "medium", "high", "critical"] = (
            "high" if cf_result.perturbation_type in ("negation_flip", "amount_shift") else "medium"
        )

        if is_invariance:
            explanation = (
                f"Adding a meaning-preserving wrapper changed the model output "
                f"({cf_result.observed_change}) when no semantic change should have occurred."
            )
            recommendation = (
                "Normalize polite filler phrases before inference and add invariance pairs "
                "to the regression benchmark."
            )
        else:
            explanation = (
                f"After '{cf_result.perturbation_type}' mutation, expected ({cf_result.expected_change}) "
                f"but observed ({cf_result.observed_change})."
            )
            recommendation = (
                f"Add '{cf_result.perturbation_type}' contrast pairs to few-shot prompt instructions "
                "and verify sensitivity before deployment."
            )

        return self.create_incident(
            engine_source="Engine 3: Counterfactual Testing Engine",
            user_input=cf_result.modified_input,
            expected_summary=cf_result.expected_change,
            actual_summary=cf_result.observed_change,
            failure_type=failure_type,
            severity=severity,
            confidence=0.90,
            explanation=explanation,
            recommendation=recommendation,
            evidence={
                "perturbation_type": cf_result.perturbation_type,
                "original_input": cf_result.original_input,
                "modified_input": cf_result.modified_input,
            },
        )

    def explain_gateway_block(
        self,
        user_input: str,
        proposed_action: ExtractedAction,
        decision: GatewayDecision,
    ) -> FailureReport:
        severity: Literal["low", "medium", "high", "critical"] = (
            "high" if decision.decision == "BLOCK" else "medium"
        )
        reasons_joined = " ".join(decision.reasons)

        return self.create_incident(
            engine_source="Engine 4: AI Action Interception Gateway",
            user_input=user_input,
            expected_summary="Safe, authorized, and unambiguous execution parameters",
            actual_summary=json.dumps(proposed_action.model_dump(), ensure_ascii=False),
            failure_type=f"gateway_{decision.decision.lower()}",
            severity=severity,
            confidence=0.98,
            explanation=f"Gateway returned {decision.decision} (risk={decision.risk_score}): {reasons_joined}",
            recommendation=(
                "Require explicit user confirmation for HOLD tickets and reject BLOCK actions "
                "before invoking downstream payment or tool APIs."
            ),
            evidence={
                "decision": decision.decision,
                "risk_score": decision.risk_score,
                "reasons": decision.reasons,
            },
        )

    @staticmethod
    def to_json(report: FailureReport) -> str:
        return report.model_dump_json(indent=2)

    @staticmethod
    def to_markdown(report: FailureReport) -> str:
        evidence_pretty = json.dumps(report.evidence, indent=2, ensure_ascii=False)
        return "\n".join(
            [
                f"### Incident Report: `{report.incident_id}`",
                "",
                "| Attribute | Value |",
                "| :--- | :--- |",
                f"| **Source Engine** | {report.engine_source} |",
                f"| **Failure Type** | `{report.failure_type}` |",
                f"| **Severity** | **{report.severity.upper()}** |",
                f"| **Confidence** | {report.confidence * 100:.1f}% |",
                f"| **Recorded At (UTC)** | {report.created_at.isoformat()} |",
                "",
                f"**Original User Input:** `{report.user_input}`",
                "",
                f"* **Expected Interpretation:** `{report.expected_summary}`",
                f"* **Actual Output:** `{report.actual_summary}`",
                f"* **Root-Cause Explanation:** {report.explanation}",
                f"* **Recommended Fix:** {report.recommendation}",
                "",
                "**Diagnostic Evidence:**",
                "```json",
                evidence_pretty,
                "```",
            ]
        )
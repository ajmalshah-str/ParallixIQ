from dataclasses import dataclass, field
from storage.audit_db import AuditDatabase
from storage.schemas import (
    ExtractedAction,
    ExtractionBundle,
    GatewayDecision,
    HallucinationResult,
    IntentCheckResult,
)

SUPPORTED_PROFILES = {"banking_upi", "healthcare_rx", "ecommerce_refund"}

HEALTHCARE_HIGH_RISK_KEYWORDS = (
    "morphine", "fentanyl", "oxycodone", "insulin", "chemo", "chemotherapy",
    "warfarin", "narcotic", "controlled", "sedative", "steroid", "methadone",
    "ketamine", "propofol", "epinephrine", "digoxin", "critical", "high-risk",
)

HEALTHCARE_MED_CUES = (
    "med", "medicine", "medication", "dose", "dosage", "mg", "ml", "tablet",
    "pill", "injection", "insulin", "morphine", "drug", "paracetamol",
    "antibiotic", "prescription", "rx", "capsule", "syrup", "infusion",
)

NEGATION_CUES = (
    "do not", "don't", "dont", "never", "cancel", "stop", "halt",
    "mat ", "nahi ", "venda", "ayakkaruth", "kodukkaruth",
)


@dataclass
class GatewayPolicy:
    profile: str = "banking_upi"  # "banking_upi", "healthcare_rx", "ecommerce_refund"
    max_auto_approve_amount: float = 50000.0
    hard_block_amount: float = 500000.0
    allowed_currencies: set[str] = field(
        default_factory=lambda: {"INR", "USD", "AED", "EUR", "GBP", "SAR"}
    )
    blocked_recipients: set[str] = field(
        default_factory=lambda: {"hacker", "unknown", "scammer", "cybercell"}
    )
    known_contacts: set[str] | None = None


class ActionInterceptionGateway:
    def __init__(
        self,
        db: AuditDatabase | None = None,
        policy: GatewayPolicy | None = None,
        profile: str = "banking_upi",
    ):
        self.db = db if db is not None else AuditDatabase()
        self.policy = policy if policy is not None else GatewayPolicy()
        if profile != "banking_upi":
            self.profile = profile
            self.policy.profile = profile
        else:
            self.profile = getattr(self.policy, "profile", "banking_upi")

    def inspect_action(
        self,
        user_input: str,
        action: ExtractedAction,
        intent_check: IntentCheckResult | None = None,
        hallucination_check: HallucinationResult | None = None,
        session_id: str = "default",
        profile: str | None = None,
    ) -> GatewayDecision:
        active_profile = (profile or self.profile or getattr(self.policy, "profile", "banking_upi")).lower()
        if active_profile not in SUPPORTED_PROFILES:
            active_profile = "banking_upi"

        block_reasons: list[str] = []
        hold_reasons: list[str] = []
        lowered_input = user_input.lower()

        # -------------------------------------------------------------
        # PROFILE-SPECIFIC PRE-CHECKS
        # -------------------------------------------------------------
        if active_profile == "healthcare_rx":
            # Healthcare Rx: Blocks negated medication/dosage commands
            is_med_negated = (
                action.polarity == "negative"
                or (intent_check and intent_check.polarity_reversed)
                or any(neg in lowered_input for neg in NEGATION_CUES)
            )
            has_med_context = (
                action.intent in ("medication", "prescription", "rx", "dosage", "administer", "dispense")
                or any(w in lowered_input for w in HEALTHCARE_MED_CUES)
            )
            if is_med_negated and has_med_context:
                block_reasons.append(
                    "Healthcare Rx policy: Negated medication or dosage command blocked to prevent adverse clinical events."
                )

            # Healthcare Rx: Holds high-risk prescriptions
            is_high_risk_drug = any(drug in lowered_input for drug in HEALTHCARE_HIGH_RISK_KEYWORDS)
            is_high_dosage = action.amount is not None and action.amount >= 500.0
            is_rx_intent = action.intent in ("prescription", "rx", "dispense", "administer")
            if is_high_risk_drug or (is_rx_intent and is_high_dosage):
                hold_reasons.append(
                    "Healthcare Rx policy: High-risk prescription requires licensed practitioner sign-off."
                )

        elif active_profile == "ecommerce_refund":
            # E-Commerce Refund: Holds refunds >= 10000 INR
            refund_threshold = 10000.0
            if action.amount is not None and action.amount >= refund_threshold:
                hold_reasons.append(
                    f"E-Commerce policy: Refund amount ({action.amount} {action.currency or 'INR'}) "
                    f">= 10,000 INR requires merchant manager approval."
                )

            # E-Commerce Refund: Blocks negated cancellations
            is_cancellation_mentioned = (
                "cancel" in lowered_input
                or "cancellation" in lowered_input
                or "radd" in lowered_input
                or action.intent in ("cancel", "cancellation")
            )
            is_cancel_negated = (
                action.polarity == "negative"
                or (intent_check and intent_check.polarity_reversed)
                or any(neg in lowered_input for neg in NEGATION_CUES)
            )
            if is_cancellation_mentioned and is_cancel_negated:
                block_reasons.append(
                    "E-Commerce policy: Negated order cancellation instruction blocked to prevent erroneous order termination."
                )

        # -------------------------------------------------------------
        # CORE & UNIVERSAL GATEWAY AUDIT RULES (Includes Banking UPI)
        # -------------------------------------------------------------
        # 1. Check non-actionable or prohibited intents
        if action.polarity == "statement" or action.intent is None:
            block_reasons.append("Input is a passive statement with no executable transfer intent.")

        if action.polarity == "negative" and not any("negative polarity" in r or "Negated" in r for r in block_reasons):
            block_reasons.append("User instruction explicitly prohibits this action (negative polarity).")

        if intent_check and intent_check.polarity_reversed and not any("polarity reversal" in r for r in block_reasons):
            block_reasons.append("Engine 1 detected a polarity reversal against user prohibition markers.")

        # 2. Check hallucination risk from Engine 2
        if hallucination_check:
            if hallucination_check.hallucination_risk == "high":
                block_reasons.append("Engine 2 flagged high hallucination or factual contradiction risk.")
            elif hallucination_check.hallucination_risk == "medium":
                hold_reasons.append("Engine 2 flagged unverified claims requiring human confirmation.")

        # 3. Validate recipient and watchlists
        recipient = (action.recipient or "").strip()
        if not recipient:
            block_reasons.append("Missing target recipient.")
        elif recipient.lower() in self.policy.blocked_recipients:
            block_reasons.append(f"Recipient '{recipient}' is blocked by security policy.")

        # 4. Validate amount bounds and currency
        if action.amount is None:
            block_reasons.append("Missing transaction amount.")
        elif action.amount <= 0:
            block_reasons.append(f"Invalid transaction amount ({action.amount}): must be greater than zero.")
        elif action.amount > self.policy.hard_block_amount:
            block_reasons.append(
                f"Amount {action.amount} {action.currency} exceeds hard ceiling "
                f"({self.policy.hard_block_amount})."
            )

        currency = (action.currency or "INR").upper()
        if currency not in self.policy.allowed_currencies:
            block_reasons.append(f"Currency '{currency}' is not enabled in gateway policy.")

        # 5. Evaluate HOLD conditions if no hard block occurred
        if not block_reasons:
            if action.polarity == "conditional" or action.condition:
                cond_note = f" ({action.condition})" if action.condition else ""
                hold_reasons.append(f"Action depends on an unverified condition{cond_note}.")

            if action.polarity == "question":
                hold_reasons.append("User phrased request as an inquiry rather than a confirmed command.")

            # High-value auto-approve limit (50,000 for Banking UPI)
            if active_profile == "banking_upi":
                if action.amount is not None and action.amount >= self.policy.max_auto_approve_amount:
                    hold_reasons.append(
                        f"High-value transfer ({action.amount} {currency}) requires secondary approval."
                    )

            if intent_check and intent_check.omitted_actions > 0:
                hold_reasons.append(
                    f"Engine 1 detected {intent_check.omitted_actions} potentially omitted sub-action(s)."
                )

            is_handle = "@" in recipient or recipient.isdigit()
            if recipient and not is_handle and len(recipient) <= 3:
                hold_reasons.append(
                    f"Short or abbreviated recipient '{recipient}' requires contact-book confirmation."
                )
            elif self.policy.known_contacts is not None and recipient not in self.policy.known_contacts:
                hold_reasons.append(f"Recipient '{recipient}' is not in verified contacts.")

        # 6. Final decision & risk scoring
        if block_reasons:
            decision = GatewayDecision(
                decision="BLOCK",
                risk_score=0.95,
                reasons=block_reasons + hold_reasons,
                requires_human_review=False,
            )
        elif hold_reasons:
            decision = GatewayDecision(
                decision="HOLD",
                risk_score=0.55,
                reasons=hold_reasons,
                requires_human_review=True,
            )
        else:
            decision = GatewayDecision(
                decision="PASS",
                risk_score=0.05,
                reasons=["All parameter, intent integrity, and safety policy checks passed."],
                requires_human_review=False,
            )

        self.db.log_gateway_decision(
            session_id=session_id,
            user_input=user_input,
            proposed_action=action.model_dump(),
            decision=decision.decision,
            risk_score=decision.risk_score,
            reasons=decision.reasons,
        )
        return decision

    def inspect_bundle(
        self,
        user_input: str,
        bundle: ExtractionBundle,
        intent_check: IntentCheckResult | None = None,
        hallucination_check: HallucinationResult | None = None,
        session_id: str = "default",
        profile: str | None = None,
    ) -> list[GatewayDecision]:
        if not bundle.actions:
            empty_action = ExtractedAction()
            return [
                self.inspect_action(
                    user_input=user_input,
                    action=empty_action,
                    intent_check=intent_check,
                    hallucination_check=hallucination_check,
                    session_id=session_id,
                    profile=profile,
                )
            ]

        return [
            self.inspect_action(
                user_input=user_input,
                action=act,
                intent_check=intent_check,
                hallucination_check=hallucination_check,
                session_id=session_id,
                profile=profile,
            )
            for act in bundle.actions
        ]

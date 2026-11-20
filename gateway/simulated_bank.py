from typing import Any
from gateway.interceptor import ActionInterceptionGateway
from storage.schemas import (
    ExtractedAction,
    GatewayDecision,
    HallucinationResult,
    IntentCheckResult,
)


class SimulatedBankApp:
    """
    Simulated banking tool demonstrating runtime safety enforcement when an
    application routes every AI-proposed action through PARALLIX IQ.
    """

    def __init__(
        self,
        gateway: ActionInterceptionGateway | None = None,
        starting_balance: float = 100000.0,
    ):
        self.gateway = gateway if gateway is not None else ActionInterceptionGateway()
        self.balances: dict[str, float] = {
            "user_wallet": starting_balance,
            "Rajan": 2000.0,
            "Rahul": 5000.0,
            "Suresh": 3500.0,
        }
        self.executed_ledger: list[dict[str, Any]] = []
        self.held_queue: list[dict[str, Any]] = []

    def process_proposed_action(
        self,
        user_input: str,
        action: ExtractedAction,
        intent_check: IntentCheckResult | None = None,
        hallucination_check: HallucinationResult | None = None,
        session_id: str = "bank_demo",
        profile: str | None = None,
    ) -> dict[str, Any]:
        decision: GatewayDecision = self.gateway.inspect_action(
            user_input=user_input,
            action=action,
            intent_check=intent_check,
            hallucination_check=hallucination_check,
            session_id=session_id,
            profile=profile,
        )

        if decision.decision == "BLOCK":
            return {
                "execution_status": "BLOCKED",
                "gateway_decision": decision.decision,
                "risk_score": decision.risk_score,
                "reasons": decision.reasons,
                "wallet_balance": self.balances["user_wallet"],
            }

        if decision.decision == "HOLD":
            ticket = {
                "user_input": user_input,
                "action": action.model_dump(),
                "reasons": decision.reasons,
            }
            self.held_queue.append(ticket)
            return {
                "execution_status": "HELD_FOR_APPROVAL",
                "gateway_decision": decision.decision,
                "risk_score": decision.risk_score,
                "reasons": decision.reasons,
                "wallet_balance": self.balances["user_wallet"],
            }

        # decision.decision == "PASS"
        amount = float(action.amount or 0.0)
        recipient = action.recipient or "Unknown"

        if self.balances["user_wallet"] < amount:
            return {
                "execution_status": "FAILED_INSUFFICIENT_FUNDS",
                "gateway_decision": decision.decision,
                "risk_score": decision.risk_score,
                "reasons": ["Insufficient wallet balance."],
                "wallet_balance": self.balances["user_wallet"],
            }

        self.balances["user_wallet"] -= amount
        self.balances[recipient] = self.balances.get(recipient, 0.0) + amount

        receipt = {
            "recipient": recipient,
            "amount": amount,
            "currency": action.currency,
            "date": action.date,
        }
        self.executed_ledger.append(receipt)

        return {
            "execution_status": "EXECUTED",
            "gateway_decision": decision.decision,
            "risk_score": decision.risk_score,
            "receipt": receipt,
            "wallet_balance": self.balances["user_wallet"],
        }
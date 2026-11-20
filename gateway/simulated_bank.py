from typing import Any
from gateway.interceptor import ActionInterceptionGateway
from gateway.security import IdempotencyStore, redact_secrets
from storage.schemas import (
    ExtractedAction,
    GatewayDecision,
    HallucinationResult,
    IntentCheckResult,
)


class SimulatedBankApp:
    SIMULATION_DISCLAIMER = "SIMULATED DEMO WALLET - No real money or banking connection."

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
        self._idempotency = IdempotencyStore(ttl_seconds=3600)

    def execute_verified_transfer(
        self,
        action: ExtractedAction,
        decision: GatewayDecision,
    ) -> dict[str, Any]:
        if not self.gateway.verify_decision_token(decision, action):
            raise PermissionError(
                "Gateway bypass rejected: protected transfer requires a valid gateway PASS authorization."
            )

        amount = float(action.amount or 0.0)
        recipient = action.recipient or "Unknown"

        if self.balances["user_wallet"] < amount:
            return {
                "execution_status": "FAILED_INSUFFICIENT_FUNDS",
                "gateway_decision": decision.decision,
                "risk_score": decision.risk_score,
                "reasons": ["Insufficient wallet balance."],
                "wallet_balance": self.balances["user_wallet"],
                "simulation_disclaimer": self.SIMULATION_DISCLAIMER,
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
            "simulation_disclaimer": self.SIMULATION_DISCLAIMER,
        }

    def process_proposed_action(
        self,
        user_input: str,
        action: ExtractedAction,
        intent_check: IntentCheckResult | None = None,
        hallucination_check: HallucinationResult | None = None,
        session_id: str = "bank_demo",
        profile: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        safe_input = redact_secrets(user_input or "")
        tx_payload = {
            "user_input": safe_input,
            "action": action.model_dump(),
            "profile": profile or self.gateway.profile,
        }

        if idempotency_key:
            status, cached = self._idempotency.lookup(session_id, idempotency_key, tx_payload)
            if status == "CONFLICT":
                return {
                    "execution_status": "REJECTED_IDEMPOTENCY_CONFLICT",
                    "gateway_decision": "BLOCK",
                    "risk_score": 1.0,
                    "reasons": ["Duplicate idempotency key reused with a different transaction payload."],
                    "wallet_balance": self.balances["user_wallet"],
                    "simulation_disclaimer": self.SIMULATION_DISCLAIMER,
                }
            if status == "HIT" and cached is not None:
                replay = dict(cached)
                replay["duplicate_prevented"] = True
                replay["wallet_balance"] = self.balances["user_wallet"]
                return replay

        try:
            decision: GatewayDecision = self.gateway.inspect_action(
                user_input=safe_input,
                action=action,
                intent_check=intent_check,
                hallucination_check=hallucination_check,
                session_id=session_id,
                profile=profile,
            )
        except Exception as exc:
            return {
                "execution_status": "BLOCKED_FAIL_CLOSED",
                "gateway_decision": "BLOCK",
                "risk_score": 1.0,
                "reasons": [f"Gateway failure ({type(exc).__name__}) - execution blocked fail-closed."],
                "wallet_balance": self.balances["user_wallet"],
                "simulation_disclaimer": self.SIMULATION_DISCLAIMER,
            }

        if decision.decision == "BLOCK":
            result = {
                "execution_status": "BLOCKED",
                "gateway_decision": decision.decision,
                "risk_score": decision.risk_score,
                "reasons": decision.reasons,
                "wallet_balance": self.balances["user_wallet"],
                "simulation_disclaimer": self.SIMULATION_DISCLAIMER,
            }
            self._idempotency.store(session_id, idempotency_key, tx_payload, result)
            return result

        if decision.decision == "HOLD":
            ticket = {
                "user_input": safe_input,
                "action": action.model_dump(),
                "reasons": decision.reasons,
            }
            self.held_queue.append(ticket)
            result = {
                "execution_status": "HELD_FOR_APPROVAL",
                "gateway_decision": decision.decision,
                "risk_score": decision.risk_score,
                "reasons": decision.reasons,
                "wallet_balance": self.balances["user_wallet"],
                "simulation_disclaimer": self.SIMULATION_DISCLAIMER,
            }
            self._idempotency.store(session_id, idempotency_key, tx_payload, result)
            return result

        result = self.execute_verified_transfer(action, decision)
        self._idempotency.store(session_id, idempotency_key, tx_payload, result)
        return result

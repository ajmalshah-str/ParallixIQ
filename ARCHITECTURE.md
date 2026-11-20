# PARALLIX IQ — System Architecture

## Request & Interception Pipeline
1. **HTTP Security Layer (dashboard.py, gateway/security.py):** Constant-time SHA-256 X-Parallix-Key validation, 64 KB payload ceiling, Unicode-safe input sanitization, sliding-window rate limiting (60 req/min), and X-Idempotency-Key replay protection.
2. **Extraction & Consensus (adapters/):** Structured Pydantic extraction via GeminiAdapter (gemini-flash-lite-latest), optional 2-of-2 escalation via MultiModelConsensusRouter, and deterministic fallback parsing when offline.
3. **Engine 1 (engines/intent_integrity.py):** Deterministic polarity-reversal, conditional-clause, passive-debt, and multi-action completeness audit.
4. **Engine 4 (gateway/interceptor.py, gateway/simulated_bank.py):** Fail-closed PASS / HOLD / BLOCK firewall issuing signed action tokens verified by SimulatedBankApp.
5. **Engines 5, 7 & 8 (engines/, storage/):** Redacted incident reports (ExplainabilityEngine), versioned test synthesis (AdaptiveTestGenerator), and SQLite WAL audit logging (AuditDatabase) with Cloud SQL PostgreSQL migration schema (storage/postgres_schema.sql).

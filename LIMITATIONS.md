# PARALLIX IQ — Known Technical Limitations

1. **Ephemeral Cloud Run Storage:** Audit logs and metered counters currently use local container SQLite (parallix_audit.db) and do not persist across container restarts or multiple instances until Cloud SQL PostgreSQL is provisioned.
2. **Single Connected LLM Provider:** Google Gemini is the active live model; OpenAI, Anthropic, and Ollama are registered as PLANNED interfaces.
3. **Internal Benchmark Scope:** The 100-case benchmark is an internal evaluation suite (53 dev-overlap + 47 held-out expansion) and has not undergone independent third-party verification.
4. **Simulated Demo Banking & Tenant Labels:** The banking wallet (SimulatedBankApp), demo tenant profile, and illustrative pricing (₹1.5 Lakh/mo) are sandbox demonstrations with no real funds or paying enterprise contracts.
5. **In-Memory Rate Limiter & Idempotency Cache:** Rate limits and idempotency keys are stored in process memory per container instance.

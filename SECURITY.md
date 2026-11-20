# PARALLIX IQ — Security Hardening & Controls

- **Zero Exposed Credentials:** Removed hardcoded demo key (plx_live_growth_9482) from frontend HTML/JS and purged legacy key entries from SQLite. All secrets reside in server environment variables or Google Secret Manager.
- **Constant-Time Key Validation (gateway/security.py):** ApiKeyManager hashes X-Parallix-Key headers using SHA-256 and validates via hmac.compare_digest. Fails closed (401 Unauthorized) if keys are missing, invalid, revoked, or unconfigured.
- **Secret Scrubbing:** redact_secrets() masks AIza..., sk-..., plx_live_..., and Bearer tokens before logging or returning diagnostics.
- **Fail-Closed Gateway & Bypass Prevention:** Any internal gateway exception defaults to BLOCK (risk_score: 1.0). SimulatedBankApp.execute_verified_transfer() rejects any call lacking a genuine ActionInterceptionGateway token (PermissionError).
- **Replay & DoS Protection:** Enforces X-Idempotency-Key duplicate prevention (409 Conflict on payload mismatch), 64 KB max body size (413), and per-IP rate limiting (429).

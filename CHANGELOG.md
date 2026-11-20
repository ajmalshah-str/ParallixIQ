# Changelog

## [1.1.0] — 2026-11-20
- **Security:** Removed plx_live_growth_9482 and localhost:8050; implemented constant-time SHA-256 X-Parallix-Key authentication, secret scrubbing, fail-closed gateway tokens, rate limiting, and X-Idempotency-Key replay protection.
- **Persistence & Ops:** Added GET /health with honest durable: false SQLite reporting, GET /openapi.json, Docker HEALTHCHECK, pinned requirements.txt, and storage/postgres_schema.sql.
- **Benchmarks & Engines:** Added core/benchmark_audit.py separating 53 Development-Overlap and 47 Held-Out Expansion cases; added Engine 6 consensus/adapter tests and 10-scenario integration tests.
- **Dashboard & Docs:** Labeled demo tenant, illustrative pricing, and simulated wallet transparently; grouped UI into UNDERSTAND / VERIFY / PROTECT / IMPROVE; created all 9 engineering documentation files (47/47 tests passing).

## [1.0.0] — 2026-11-19
- Initial 8-engine prototype deployment on Google Cloud Run.

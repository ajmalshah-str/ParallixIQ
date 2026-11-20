# PARALLIX IQ — Automated Testing Suite

Run all **47 automated tests** from the repository root:
```bash
python -m unittest discover -s tests -v
```
- tests/test_platform.py (31 tests): Engines 1, 2, 3, 4, 5, 7, and 8 core logic.
- tests/test_security.py (6 tests): API key auth, legacy key revocation, secret redaction, gateway bypass prevention, idempotency replay protection, and HTTP validation.
- tests/test_persistence.py (4 tests): Restart persistence, ephemeral Cloud Run wipe behavior, 20-thread concurrency, and PostgreSQL schema verification.
- tests/test_engines_complete.py (3 tests): Engine 6 multi-model consensus & planned adapters, benchmark split audit, and 10 live demonstration scenarios.
- tests/test_frontend_and_docs.py (3 tests): All 9 documentation files and honest public dashboard labels.

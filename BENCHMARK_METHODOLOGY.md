# PARALLIX IQ — Benchmark Credibility & Methodology

- **Classification:** Internal Benchmark Results (founder-curated; not yet externally audited).
- **Dataset (datasets/master_100_suite.json):** 100 unique test cases (0 duplicates, 600 semantic slots) across Manglish (30), Hinglish (26), English (21), Malayalam (12), and Hindi (11).
- **Split Separation (core/benchmark_audit.py):**
  - **Development-Overlap Split (53 cases):** Shared with initial single-language design suites (datasets/languages/*.json and master_exhaustive_suite.json).
  - **Held-Out Expansion Split (47 cases):** Unseen cases unique to master_100_suite.json.
- **Reproducibility:** Run python -m core.benchmark_audit to regenerate datasets/benchmark_audit_snapshot.json and BENCHMARK_REPORT.md with exact-match accuracy, slot agreement, non-null Precision/Recall/F1, False PASS rate, False BLOCK rate, and HOLD rate.

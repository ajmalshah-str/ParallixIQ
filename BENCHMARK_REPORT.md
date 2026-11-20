# Internal Benchmark Results — 100 Test Cases

- **Evaluation Version:** `audit-v2.0-reproducible`
- **Execution Timestamp (UTC):** `2026-11-20T08:42:32.037002+00:00`
- **Evaluated Engine (Reproducible Offline Run):** `deterministic-baseline-parser-v1`
- **Prior Live Gemini Snapshot (`gemini-flash-lite-latest`):** `74.0%` exact-match (`74/100`), `93.83%` slot-level precision (`563/600` slots)
- **Dataset Size & Integrity:** `100` cases (`100` unique, `0` duplicates)

## 1. Dataset Split Audit (Leakage & Overlap Disclosure)

| Dataset Split | Cases | Exact-Match | Slot Agreement | Non-Null Precision | Non-Null Recall | Non-Null F1 | False PASS Rate |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **All Cases (`master_100_suite.json`)** | 100 | 4.0% | 59.5% | 91.48% | 57.81% | 70.85% | 0.0% |
| **Development-Overlap Split** | 53 | 7.55% | 62.26% | 94.9% | 60.78% | 74.1% | 0.0% |
| **Held-Out Expansion Split** | 47 | 0.0% | 56.38% | 87.5% | 54.44% | 67.12% | 0.0% |

## 2. Reproducible Breakdown by Language

| Language | Cases | Exact-Match | Slot Agreement | Intent Acc | Entity Acc | Negation Det | False PASS | False BLOCK | HOLD Rate |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **English** | 21 | 19.05% | 80.95% | 90.48% | 33.33% | 100.0% | 0.0% | 43.75% | 9.52% |
| **Hindi** | 11 | 0.0% | 45.45% | 0.0% | 0.0% | 0.0% | 0.0% | 100.0% | 0.0% |
| **Hinglish** | 26 | 0.0% | 46.79% | 23.08% | 0.0% | 0.0% | 0.0% | 100.0% | 0.0% |
| **Malayalam** | 12 | 0.0% | 62.5% | 41.67% | 0.0% | 100.0% | 0.0% | 100.0% | 0.0% |
| **Manglish** | 30 | 0.0% | 59.44% | 43.33% | 0.0% | 50.0% | 0.0% | 95.45% | 3.33% |

## 3. Methodology & Limitations

- Internal benchmark curated by the founder; not yet audited by an external third-party laboratory.
- 53 of 100 prompts overlap with earlier language development suites; 47 prompts represent unseen expansion cases.
- Deterministic offline mode measures baseline rule-parser behavior without network variance; pass --live-gemini for live API runs.

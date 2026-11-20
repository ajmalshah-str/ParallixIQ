# PARALLIX IQ — Universal Multilingual AI Reliability & Action Firewall

> **Autonomous Action Reliability, Hallucination Prevention, Counterfactual Auditing & Real-Time Gateway Protection for Multilingual AI Agents**

---

## 1. Executive Summary

Autonomous AI agents integrated with external tools, databases, and payment APIs present critical operational and security risks:
* **Polarity Reversals:** Models frequently misinterpret colloquial or regional prohibitions (*"Naale Rahulinu 500 roopa ayakkaruthu"* / *"Kal Rahul ko 500 mat bhejna"*) as positive authorizations, initiating unauthorized transactions.
* **Agglutinative Inflexion Failures:** Deterministic rule and regex engines fail completely on Indic grammatical suffixes (*-inu*, *-kku*, *-ko*, *-lekku*), breaking slot extraction.
* **Fabricated & Unverified Claims:** LLMs state ungrounded figures (e.g., account balances, policy rules) with high confidence.
* **Silent Drift & Brittle Invariance:** Minor phrasing changes or subtle model updates cause catastrophic regressions in production.

**ParallixIQ** solves these challenges through an end-to-end, zero-trust **8-Engine Architecture** and a **100-Point Indic AI Reliability Benchmark**, safeguarding LLM actions before execution.

---

## 2. The 8-Engine Reliability Architecture

```
                       [ User Input / Agent Action ]
                                     │
                     ┌───────────────┴───────────────┐
                     ▼                               ▼
       ┌───────────────────────────┐   ┌───────────────────────────┐
       │         ENGINE 1          │   │         ENGINE 3          │
       │ Multilingual Intent       │   │ Counterfactual Mutation   │
       │ Integrity Validator       │   │ Robustness Tester         │
       └─────────────┬─────────────┘   └─────────────┬─────────────┘
                     │                               │
                     │         ┌─────────────────────┘
                     ▼         ▼
       ┌───────────────────────────┐   ┌───────────────────────────┐
       │         ENGINE 2          │   │         ENGINE 6          │
       │ Hallucination & Evidence  │   │ Multi-Model Intelligence  │
       │ Verifier                  │   │ Comparison Hub            │
       └─────────────┬─────────────┘   └─────────────┬─────────────┘
                     │                               │
                     └───────────────┬───────────────┘
                                     │
                                     ▼
                     ┌───────────────────────────────┐
                     │           ENGINE 4            │
                     │  AI Action Interception Gate  │
                     │  [ PASS  |  HOLD  |  BLOCK ]  │
                     └───────┬───────────────┬───────┘
            PASS (Execute)   │               │   HOLD / BLOCK (Anomaly)
            ┌────────────────┘               └───────────────┐
            ▼                                                ▼
┌───────────────────────────┐                  ┌───────────────────────────┐
│     SIMULATED BANK        │                  │         ENGINE 7          │
│ Verified Execution Ledger │                  │ Explainable Failure       │
│ & Secure Balances         │                  │ Incident Reporter         │
└───────────────────────────┘                  └─────────────┬─────────────┘
                                                             │
                                                             ▼
                                               ┌───────────────────────────┐
                                               │         ENGINE 8          │
                                               │ Adaptive Adversarial      │
                                               │ Test Suite Generator      │
                                               └─────────────┬─────────────┘
                                                             │
                                                             ▼
                                               ┌───────────────────────────┐
                                               │         ENGINE 5          │
                                               │ Consistency & Model Drift │
                                               │ Monitor (SQLite Audit DB) │
                                               └───────────────────────────┘
```

### Engine 1: Multilingual Intent Integrity (`engines/intent_integrity.py`)
* **Purpose:** Independent deterministic integrity validator that audits AI extractions at runtime without requiring an answer key.
* **Core Capabilities:**
  * **Polarity Reversal Detection:** Scans for prohibition markers across English, Malayalam, Manglish, Hindi, and Hinglish (`"ayakkaruthu"`, `"venda"`, `"mat bhejna"`, `"nahi bhejna"`, `"do not"`, `"cancel"`). If user forbade an action but model produced `polarity="positive"`, it flags a critical polarity reversal.
  * **Self-Repair Awareness:** Distinguishes mid-sentence corrections (*"Transfer 500 to Rahul... wait, scratch that, send 1000 to Suresh"*) from prohibitions, avoiding false positives.
  * **Conditional Dependency Isolation:** Detects contingencies (*"if"*, *"maathram"*, *"ayachal mathi"*, *"agar"*), holding unconditional executions.
  * **Passive Statement Guard:** Blocks debt/credit statements (*"Rahul owes me 500"*, *"enikku 500 roopa tharanund"*, *"Maine kal diye the"*) from misclassifying as active transfer orders.
  * **Compound Action Completeness:** Catches omitted instructions when multiple amounts and clauses are chained with joiners (*"pinne"*, *"aur"*, *"and also"*).

### Engine 2: Hallucination & Fabrication Detector (`engines/hallucination.py`)
* **Purpose:** Rigorous tri-state claim auditor verifying model responses against trusted backend records.
* **Core Capabilities:**
  * **Tri-State Claim Verification:**
    * `confirmed`: Assertions strictly matching backend records (low risk).
    * `contradicted`: Assertions conflicting with ground-truth numbers or account states (high risk).
    * `unverified`: Factual assertions lacking verification data in current context (medium risk). Crucially, unbacked claims are *never* falsely marked false; they are preserved for secondary confirmation.
  * **Internal Contradiction Detection:** Detects mutually exclusive state assertions across sentences (*"Payment succeeded"* vs *"Transaction cancelled"*) or conflicting figures.
  * **Subjective Opinion Handling:** Recognizes opinion markers (*"I think"*, *"In my opinion"*), categorizing them with low risk so business workflows are not falsely disrupted.

### Engine 3: Counterfactual Mutation Tester (`engines/counterfactual.py`)
* **Purpose:** Controlled single-variable perturbation engine that stress-tests semantic stability and sensitivity.
* **Core Capabilities:**
  * **6 Perturbation Classes:**
    1. `negation_flip`: Inverts affirmative instructions into prohibitions (*"ayakkanam"* $\rightarrow$ *"ayakkaruthu"*).
    2. `amount_shift`: Multiplies transaction value by 100x to test numeric sensitivity and boundary checks.
    3. `recipient_swap`: Replaces named entity with an alternative recipient.
    4. `date_shift`: Mutates temporal markers (*"tomorrow"* $\rightarrow$ *"today"*).
    5. `condition_inject`: Prepends contingency requirements (*"Only if confirmed, ..."*).
    6. `meaning_preserving_invariance`: Wraps commands in polite fillers (*"Please, ..."*), verifying that extracted parameters remain strictly identical.
  * **Collateral Mutation Verification:** Fails tests if modifying one field triggers unintended drift in other slots.

### Engine 4: AI Action Interception Gateway & Simulated Bank (`gateway/interceptor.py`, `gateway/simulated_bank.py`)
* **Purpose:** Real-time zero-trust policy enforcement proxy placed between LLM output and downstream banking tools.
* **Decisions:**
  * **`PASS`:** Complete parameters, verified recipient, positive polarity, risk score $\le 0.10$. Directly invokes `SimulatedBankApp` ledger, debiting user wallet and crediting recipient.
  * **`HOLD`:** Contingent actions, high-value transfers ($\ge 50,000$ INR), abbreviated or unverified recipients ($\le 3$ characters like `"rhl"`), or unverified factual claims. Queued into `held_queue` for secondary human/2FA confirmation; funds remain untouched.
  * **`BLOCK`:** Explicit negative polarity, Engine 1 polarity reversals, high hallucination risk, negative amount underflow ($\le 0$), amounts exceeding hard ceiling ($500,000$ INR), or blacklisted recipients (`"Hacker"`). Immediate execution freeze.

### Engine 5: Consistency & Model Drift Monitor (`engines/drift_monitor.py`)
* **Purpose:** Automated regression monitoring tracking accuracy metrics and alerting on performance degradation.
* **Core Capabilities:**
  * **Configurable Drift Thresholds:** Default $3.0\%$ accuracy drop ceiling across benchmark runs.
  * **Dual-Horizon Evaluation:** Compares new runs against both initial historical baseline and immediate previous run to catch both gradual drift and sudden regressions.
  * **Prompt Repeat Consistency:** Measures output variance across identical prompts evaluated repeatedly.
  * **Trend Analytics:** Categorizes model trajectory as `improving`, `stable`, or `degrading`.

### Engine 6: Multi-Model Intelligence Comparison (`adapters/gemini_adapter.py`, `adapters/planned_adapters.py`)
* **Purpose:** Extensible model evaluation framework with unified adapter interfaces.
* **Core Capabilities:**
  * **Live Connected:** Google Gemini (`gemini-flash-lite-latest`) with schema-enforced JSON extraction.
  * **Planned Adapter Registry:** Extensible interfaces for OpenAI (`gpt-4o-mini`), Anthropic (`claude-3-5-haiku-latest`), and local Ollama (`llama3.1:8b`).
  * **Latency & Precision Profiling:** Tracks average latency, exact-match rate, and safety compliance across models.

### Engine 7: Explainable AI Failure Report Generator (`engines/explainability.py`)
* **Purpose:** Forensic incident reporting engine translating anomalous extractions into actionable root-cause diagnoses.
* **Core Capabilities:**
  * **Standardized Incident IDs:** Formatted as `INC-YYYYMMDD-XXXXXX`.
  * **Severity Triage:** Graded from `low` to `critical` based on financial impact.
  * **Automated Remediation Guidance:** Supplies prompt schema fixes, token normalization recommendations, and boundary regression cases.
  * **Credential Sanitization:** Scrubs API keys and secrets (`AIzaSy...`, `sk-...`) from logged diagnostics.
  * **Dual Export:** Outputs both Markdown incident cards and valid JSON payloads.

### Engine 8: Adaptive Adversarial Test Generator (`engines/adaptive_gen.py`)
* **Purpose:** Self-healing benchmark generation engine synthesizing new adversarial tests from live failure incidents.
* **Core Capabilities:**
  * **Failure Telemetry Ingestion:** Mines unresolved SQLite failure incidents by type and language.
  * **Automated Deduplication:** Compares candidate prompts against `master_100_suite.json` and existing adaptive versions to ensure zero duplicate test generation.
  * **Deterministic Candidate Validation:** Runs candidates through Engine 1 rules before admitting them to the dataset.
  * **Versioned Dataset Artifacts:** Emits timestamped datasets (`datasets/adaptive_v*.json`).

---

## 3. Storage & Schema Foundation

* **`storage/audit_db.py`:** Embedded SQLite database (`parallix_audit.db`) operating in high-performance WAL (Write-Ahead Logging) mode. Maintains tables for `benchmark_runs`, `evaluation_logs`, `gateway_audit`, and `failure_incidents`.
* **`storage/schemas.py`:** Strongly-typed Pydantic v2 domain models ensuring rigorous data contracts:
  * `ExtractedAction`: Normalizes intent, recipient, amount, currency, date, polarity, condition.
  * `ExtractionBundle`: Container supporting compound multi-intent requests.
  * `IntentCheckResult`: Integrity scoring, field scores, mismatch logs, polarity reversal flags.
  * `ClaimCheck` & `HallucinationResult`: Evidence references, risk levels, contradiction lists.
  * `CounterfactualResult`: Perturbation metadata and observed vs expected diffs.
  * `GatewayDecision`: PASS / HOLD / BLOCK verdict, risk score, diagnostic reasons.
  * `DriftReport`: Baseline delta, alert trigger flags, notification copy.
  * `FailureReport`: Incident triage, severity, diagnostic explanations, remediations.
  * `GeneratedTestCase`: Schema-validated adaptive test cases.

---

## 4. 100-Point Indic AI Reliability Benchmark Results

The ParallixIQ 100-Point Benchmark evaluates LLM reliability across high-frequency conversational Indian languages and complex dialectal edge cases.

### Headline Performance Scorecard

| Evaluation Metric | Baseline Regex / Rule Engine | Google Gemini Engine (`gemini-flash-lite-latest`) | Absolute Improvement |
| :--- | :--- | :--- | :--- |
| **Exact-Match Reliability Rate** | 7.41% | **74.00%** (74 / 100 cases) | **+66.59%** |
| **Field-Level Semantic Precision** | 62.04% | **93.83%** (563 / 600 slots) | **+31.79%** |

### Language & Dialect Suite Breakdown

| Language Suite | Evaluated Cases | Baseline Exact Match | Gemini Exact Match | Gemini Field Precision |
| :--- | :--- | :--- | :--- | :--- |
| **Manglish** (Malayalam in Latin Script) | 30 | 0.0% | **76.7%** (23 / 30) | 94.4% |
| **Hinglish** (Hindi in Latin Script) | 26 | 0.0% | **84.6%** (22 / 26) | 96.8% |
| **English** (Standard & Adversarial) | 21 | 38.1% | **71.4%** (15 / 21) | 92.9% |
| **Malayalam** (Native Script — മലയാളം) | 12 | 0.0% | **75.0%** (9 / 12) | 95.8% |
| **Hindi** (Native Devanagari — हिन्दी) | 11 | 0.0% | **45.5%** (5 / 11) | 87.9% |
| **Total Benchmark** | **100** | **7.4%** | **74.00%** (74 / 100) | **93.83%** (563 / 600) |

### Slot-Level Entity Extraction Precision (600 Total Slots)

| Target Slot | Evaluated Slots | Extraction Accuracy | Primary Validation Role |
| :--- | :--- | :--- | :--- |
| `currency` | 100 | **99.0%** (99 / 100) | Normalization across INR, USD, EUR, GBP, AED, SAR |
| `amount` | 100 | **95.0%** (95 / 100) | Vernacular fraction parsing (*dedh hazar*, *anjooru*, *peti*, *lakh*) |
| `intent` | 100 | **95.0%** (95 / 100) | Active transfer vs passive debt statement discrimination |
| `polarity` | 100 | **93.0%** (93 / 100) | Negation, conditional, and question categorization |
| `date` | 100 | **91.0%** (91 / 100) | Relative temporal inference (*naale*, *innu*, *kal*, *aaj*) |
| `recipient` | 100 | **90.0%** (90 / 100) | Agglutinative suffix removal and UPI handle isolation |

### Key Research Findings
1. **The Morphological Agglutination Barrier:** Deterministic rule engines fail completely (0% accuracy) on Indic agglutinative case endings (*രാഹുലിന്* $\rightarrow$ *-inu*, *Deepakkinte* $\rightarrow$ *-inte*, *Mahesh ko* $\rightarrow$ *-ko*). Gemini achieves $>90\%$ slot accuracy by canonicalizing inflected nouns back to root entities.
2. **Abbreviation Ambiguity as a Financial Safety Feature:** For ambiguous shorthand inputs (*"rhl 500 nle snd"*), Gemini correctly isolates `"rhl"` without inventing full identities, allowing Engine 4 Gateway to `HOLD` the transfer for verified contact confirmation.
3. **Passive Statement & Debt Discrimination:** Statements of credit/debt (*"Rahul owes me 500"*) are correctly parsed with `intent=null` and `polarity="statement"`, preventing unintended fund transfers.

---

## 5. Quickstart & Command Reference

### Environment Setup

```bash
# Clone the repository
git clone https://github.com/your-org/ParallixIQ.git
cd ParallixIQ

# Activate Python virtual environment (Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# (Optional) Add your Gemini API key in .env for live model parsing
echo "GEMINI_API_KEY=your_gemini_api_key_here" > .env
```

### 1. Launch the Executive Command Center Dashboard

The interactive web dashboard visualizes the full 8-engine pipeline, live interception firewall, and SQLite audit records:

```powershell
# Launch dashboard on http://127.0.0.1:8050
python dashboard.py
```
* Or invoke with the project virtual environment:
  ```powershell
  .\.venv\Scripts\python.exe dashboard.py
  ```
* Open your browser at [http://127.0.0.1:8050](http://127.0.0.1:8050).
* Features:
  * **Live Action Firewall:** Input test sentences in Manglish, Hinglish, Malayalam, Hindi, or English to test real-time PASS / HOLD / BLOCK verdicts.
  * **Hallucination Auditor:** Evaluate claims against live bank balances.
  * **Counterfactual Playground:** Generate 6-way mutation suites with one click.
  * **Drift & Regression Monitor:** View accuracy trends and simulate model drops.
  * **Adaptive Generator:** Synthesize new deduplicated test suites directly into `datasets/`.

### 2. Run the Platform Unit Test Suite

Run Python's built-in `unittest` suite covering all 8 engines:

```powershell
# Run the complete platform test suite
python -m unittest tests/test_platform.py -v
```
* Or run test discovery across the `tests/` directory:
  ```powershell
  python -m unittest discover -s tests -p "test_*.py" -v
  ```
* Virtual environment equivalent:
  ```powershell
  .\.venv\Scripts\python.exe -m unittest tests/test_platform.py -v
  ```

### 3. Run the 100-Point Indic Benchmark

Execute the full 100-test rate-pacing benchmark:

```powershell
python run_100_benchmark.py
```

### 4. Run the 10-Step Interactive Buildathon Demo

Run the end-to-end multi-engine buildathon walkthrough:

```powershell
python demo_buildathon.py
```

### 5. Interactive CLI Console

Test individual phrases in an interactive command-line session:

```powershell
python main.py
```

---

## 6. Repository Structure

```
ParallixIQ/
├── adapters/                        # Model Adapter Layer (Engine 6)
│   ├── base.py                      # Abstract Base Adapter contract
│   ├── gemini_adapter.py            # Live Google Gemini Flash Lite adapter
│   └── planned_adapters.py          # Planned registry (OpenAI, Claude, Ollama)
├── engines/                         # Core Intelligence & Audit Engines
│   ├── adaptive_gen.py              # Engine 8: Adaptive Adversarial Test Generator
│   ├── counterfactual.py            # Engine 3: Counterfactual Mutation Tester
│   ├── drift_monitor.py             # Engine 5: Benchmark Drift & Consistency Monitor
│   ├── explainability.py            # Engine 7: Explainable Failure Incident Reporter
│   ├── hallucination.py             # Engine 2: Hallucination & Evidence Verifier
│   └── intent_integrity.py          # Engine 1: Multilingual Intent Integrity Engine
├── gateway/                         # Runtime Interception Layer (Engine 4)
│   ├── interceptor.py               # Action Interception Gateway & Policy Filter
│   └── simulated_bank.py            # Simulated Banking Application & Ledger
├── storage/                         # Database & Pydantic Schemas
│   ├── audit_db.py                  # WAL-mode SQLite Audit Database
│   └── schemas.py                   # Pydantic v2 Domain Models
├── datasets/                        # Benchmark & Adaptive Test Suites
│   ├── master_100_suite.json        # 100-Point Indic Reliability Benchmark
│   ├── master_exhaustive_suite.json # Deep 54-case conversational suite
│   └── languages/                   # Individual language JSON definitions
├── tests/                           # Unit Test Suite
│   ├── __init__.py                  # Test package definition
│   └── test_platform.py             # Comprehensive unittest suite for Engines 1-8
├── dashboard.py                     # Executive Command Center Web Dashboard (Port 8050)
├── demo_buildathon.py               # 10-Step Interactive Buildathon Demonstration
├── run_100_benchmark.py             # Benchmark runner with rate pacing
├── main.py                          # CLI Interactive Evaluation Console
├── parallix_audit.db                # Live SQLite Audit Database
└── README.md                        # Platform Documentation & Specifications
```

---

## 7. License & Credits

Built as part of the **ParallixIQ Universal AI Reliability Infrastructure**. Engineered for high-stakes enterprise applications, cross-lingual banking assistants, and zero-trust agentic AI systems.

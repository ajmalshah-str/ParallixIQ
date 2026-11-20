# PARALLIX IQ — Universal AI Reliability & Safety Infrastructure

**Live Cloud Run Deployment:** https://parallix-iq-927980553219.asia-south1.run.app/
**OpenAPI Specification:** GET /openapi.json | **Health Endpoint:** GET /health

## 1. What PARALLIX IQ Does & The Problem It Solves
LLMs frequently misinterpret negation, conditional triggers, and morphological suffixes in code-switched Indic languages (Malayalam, Manglish, Hindi, Hinglish). PARALLIX IQ sits between AI models and downstream execution tools as an independent runtime audit firewall—**AI never grades itself**.

## 2. The Eight Engines (4 Systems)
- **UNDERSTAND:** Engine 1 — Multilingual Intent Integrity (engines/intent_integrity.py, engines/indic_normalizer.py) [LIVE]
- **VERIFY:** Engine 2 — Hallucination Detector (engines/hallucination.py), Engine 3 — Counterfactual Lab (engines/counterfactual.py), Engine 6 — Multi-Model Comparison (adapters/model_router.py, adapters/planned_adapters.py: Gemini LIVE; OpenAI/Anthropic/Ollama PLANNED)
- **PROTECT:** Engine 4 — Fail-Closed Action Interception Gateway (gateway/interceptor.py, gateway/simulated_bank.py, gateway/security.py) [LIVE]
- **IMPROVE:** Engine 5 — Consistency & Drift Monitor (engines/drift_monitor.py), Engine 7 — Explainable Failure Reports (engines/explainability.py), Engine 8 — Adaptive Test Generator (engines/adaptive_gen.py) [BETA]

## 3. Setup, Local Run & Testing
```bash
pip install -r requirements.txt
# Set server-side env vars in .env:
# GEMINI_API_KEY=your_gemini_key
# PARALLIX_API_KEYS=demo_client:your_strong_server_api_key

python dashboard.py
python -m unittest discover -s tests -v
python -m core.benchmark_audit
```

## 4. Engineering Documentation
- ARCHITECTURE.md | SECURITY.md | API_DOCUMENTATION.md
- BENCHMARK_METHODOLOGY.md | TESTING.md | DEPLOYMENT.md
- LIMITATIONS.md | CHANGELOG.md

-- PARALLIX IQ Production Schema for Google Cloud SQL (PostgreSQL 15+)
-- Migration target replacing ephemeral container SQLite on Cloud Run.

CREATE TABLE IF NOT EXISTS benchmark_runs (
    run_id BIGSERIAL PRIMARY KEY,
    model_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    total_cases INTEGER NOT NULL,
    exact_match_rate DOUBLE PRECISION NOT NULL,
    field_precision DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_model_created
    ON benchmark_runs (model_id, created_at DESC);

CREATE TABLE IF NOT EXISTS evaluation_logs (
    log_id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES benchmark_runs(run_id) ON DELETE CASCADE,
    test_id TEXT NOT NULL,
    language TEXT NOT NULL,
    category TEXT NOT NULL,
    user_input TEXT NOT NULL,
    model_output_json JSONB NOT NULL,
    passed BOOLEAN NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evaluation_logs_run_id
    ON evaluation_logs (run_id);

CREATE TABLE IF NOT EXISTS gateway_audit (
    audit_id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_input TEXT NOT NULL,
    proposed_action_json JSONB NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('PASS', 'HOLD', 'BLOCK')),
    risk_score DOUBLE PRECISION NOT NULL,
    reasons_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gateway_audit_session_created
    ON gateway_audit (session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS failure_incidents (
    incident_id TEXT PRIMARY KEY,
    engine_source TEXT NOT NULL,
    user_input TEXT NOT NULL,
    expected_summary TEXT NOT NULL,
    actual_summary TEXT NOT NULL,
    failure_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    explanation TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    evidence_json JSONB NOT NULL,
    resolved BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_failure_incidents_unresolved
    ON failure_incidents (resolved, created_at DESC);

CREATE TABLE IF NOT EXISTS tenant_usage (
    tenant_id TEXT PRIMARY KEY,
    call_count BIGINT NOT NULL DEFAULT 0,
    last_called_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

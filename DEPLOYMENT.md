# PARALLIX IQ — Cloud Run Deployment & Cloud SQL Migration Plan

## Current Cloud Run Deployment (asia-south1)
```bash
cd ~/ParallixIQ
git pull
gcloud run deploy parallix-iq \
  --source . \
  --region asia-south1 \
  --no-invoker-iam-check \
  --set-env-vars GEMINI_API_KEY="YOUR_GEMINI_KEY",PARALLIX_API_KEYS="demo_client:YOUR_STRONG_CLIENT_KEY"
```

## Cloud SQL (PostgreSQL) Persistence Migration Plan
1. **Current State (EPHEMERAL / BETA):** Local container SQLite (parallix_audit.db) resets on Cloud Run scale-to-zero or revision rollout (GET /health reports durable: false).
2. **Target State:** Provision Google Cloud SQL (PostgreSQL 15+) in asia-south1, apply storage/postgres_schema.sql, store DATABASE_URL, GEMINI_API_KEY, and PARALLIX_API_KEYS in Google Secret Manager, and cut over AuditDatabase to PostgreSQL.

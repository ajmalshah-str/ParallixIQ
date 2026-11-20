# PARALLIX IQ — REST API Documentation

**Base URL:** https://parallix-iq-927980553219.asia-south1.run.app
**OpenAPI Spec:** GET /openapi.json | **Health Check:** GET /health

## POST /v1/audit
Audits a multilingual instruction or proposed AI action through Engines 1, 4, and 7.

### Headers
- Content-Type: application/json (Required)
- X-Parallix-Key: YOUR_API_KEY (Required)
- X-Idempotency-Key: unique-request-id (Optional — prevents duplicate execution)

### Example Request
```bash
curl -X POST https://parallix-iq-927980553219.asia-south1.run.app/v1/audit \
  -H "Content-Type: application/json" \
  -H "X-Parallix-Key: YOUR_API_KEY" \
  -d "{\"text\": \"Naale Rahulinu 500 roopa ayakkaruthu\", \"profile\": \"banking_upi\"}"
```

### Status Codes
- 200: Audit completed (PASS, HOLD, or BLOCK)
- 400: Malformed JSON, missing input, or unsupported profile
- 401: Missing, invalid, or revoked X-Parallix-Key
- 409: X-Idempotency-Key reused with a different payload
- 413: Request exceeds 64 KB limit
- 429: Per-IP rate limit exceeded

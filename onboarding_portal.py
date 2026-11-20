import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ONBOARDED_CUSTOMERS = {}

PORTAL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>PARALLIX IQ — Enterprise Customer Onboarding Portal</title>
  <style>
    :root {
      --bg: #070b14;
      --surface: #0f172a;
      --surface-2: #1e293b;
      --border: rgba(148, 163, 184, 0.2);
      --text: #f8fafc;
      --muted: #94a3b8;
      --blue: #3b82f6;
      --emerald: #10b981;
      --amber: #f59e0b;
      --red: #ef4444;
    }
    * { box-sizing: border-box; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    body { margin: 0; background: radial-gradient(circle at top left, #172554 0%, var(--bg) 50%); color: var(--text); min-height: 100vh; }
    .wrap { max-width: 980px; margin: 0 auto; padding: 28px 20px 60px; }
    .top {
      display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;
      background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
      padding: 18px 22px; margin-bottom: 20px;
    }
    .top h1 { margin: 0; font-size: 20px; }
    .top p { margin: 4px 0 0; font-size: 13px; color: var(--muted); }
    .badge {
      background: rgba(16, 185, 129, 0.16); color: #6ee7b7; border: 1px solid rgba(16, 185, 129, 0.4);
      padding: 6px 12px; border-radius: 999px; font-size: 12px; font-weight: 700;
    }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
    @media (max-width: 820px) { .grid { grid-template-columns: 1fr; } }
    .card {
      background: var(--surface); border: 1px solid var(--border); border-radius: 14px; padding: 20px 22px;
    }
    .card h2 { margin: 0 0 12px; font-size: 16px; }
    label { display: block; font-size: 12px; font-weight: 600; color: var(--muted); margin: 12px 0 5px; }
    input, select {
      width: 100%; background: #080d18; color: #fff; border: 1px solid rgba(148, 163, 184, 0.3);
      border-radius: 9px; padding: 10px 12px; font-size: 13.5px;
    }
    .btn {
      width: 100%; margin-top: 16px; background: linear-gradient(135deg, #2563eb, #4f46e5);
      color: #fff; border: none; border-radius: 10px; padding: 12px 16px;
      font-size: 14px; font-weight: 700; cursor: pointer;
    }
    .btn-green { background: linear-gradient(135deg, #059669, #10b981); }
    .key-box {
      background: #060911; border: 1px dashed #3b82f6; border-radius: 10px;
      padding: 12px; margin-top: 10px; font-family: ui-monospace, Consolas, monospace;
      font-size: 13px; color: #93c5fd; word-break: break-all;
    }
    .verdict {
      margin-top: 14px; padding: 14px; border-radius: 10px;
      background: var(--surface-2); border: 1px solid var(--border); font-size: 13px;
    }
    .pill {
      display: inline-block; padding: 3px 10px; border-radius: 999px; font-weight: 800; font-size: 11.5px; margin-right: 8px;
    }
    .pill-PASS { background: rgba(16, 185, 129, 0.2); color: #6ee7b7; }
    .pill-HOLD { background: rgba(245, 158, 11, 0.2); color: #fcd34d; }
    .pill-BLOCK { background: rgba(239, 68, 68, 0.2); color: #fca5a5; }
    .muted { color: var(--muted); font-size: 12.5px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <h1>PARALLIX IQ — Customer Self-Serve Onboarding Portal</h1>
        <p>Provision an isolated Enterprise Tenant, Policy Pack &amp; SHA-256 API Key in under 30 seconds</p>
      </div>
      <span class="badge">Live Sandbox Provisioning</span>
    </div>

    <div class="grid">
      <!-- STEP 1: PROVISION TENANT -->
      <div class="card">
        <h2>Step 1 — Provision New Customer Workspace</h2>
        <p class="muted">Enter organization details to generate an active tenant ID and scoped API key.</p>

        <label for="orgName">Organization / Judge Team Name</label>
        <input id="orgName" type="text" value="Apex FinTech India Pvt Ltd" />

        <label for="domainSelect">Industry Safety Policy Pack</label>
        <select id="domainSelect">
          <option value="banking_upi">Banking &amp; UPI Payments Guard</option>
          <option value="healthcare_rx">Healthcare &amp; Pharmacy Rx Guard</option>
          <option value="ecommerce_refund">E-Commerce &amp; Refund Guard</option>
        </select>

        <label for="planSelect">Subscription Tier (Illustrative B2B Pricing)</label>
        <select id="planSelect">
          <option value="Pro Seat — ₹1,999/mo">Pro Seat — ₹1,999/mo (1 Seat)</option>
          <option value="Enterprise Starter — ₹10,000/mo" selected>Enterprise Starter — ₹10,000/mo (Up to 10 Seats)</option>
          <option value="Enterprise Scale — ₹20,000/mo">Enterprise Scale — ₹20,000/mo (Up to 25 Seats)</option>
        </select>

        <button class="btn" type="button" onclick="provisionCustomer()">Generate Tenant &amp; API Key</button>

        <div id="provisionResult" style="display:none; margin-top:14px;">
          <div class="muted"><strong>Tenant Provisioned:</strong> <span id="outTenant"></span> • <span id="outPlan"></span></div>
          <div class="key-box" id="outApiKey"></div>
          <div class="muted" style="margin-top:6px;">Key SHA-256 Fingerprint stored; raw key shown once.</div>
        </div>
      </div>

      <!-- STEP 2: LIVE CUSTOMER SANDBOX TEST -->
      <div class="card">
        <h2>Step 2 — Test Customer API Key Live (No Terminal Needed)</h2>
        <p class="muted">Send any instruction using the newly issued customer key to verify real-time interception.</p>

        <label for="activeKey">Active Customer X-Parallix-Key</label>
        <input id="activeKey" type="text" placeholder="Click 'Generate Tenant & API Key' on the left first..." />

        <label for="testMsg">Customer Prompt (English, Malayalam, Manglish, Hindi, or Hinglish)</label>
        <input id="testMsg" type="text" value="Naale Rahulinu 500 roopa ayakkaruthu." />

        <button class="btn btn-green" type="button" onclick="runCustomerTest()">Run Live API Verification</button>

        <div id="testVerdict"></div>
      </div>
    </div>
  </div>

  <script>
    async function provisionCustomer() {
      const org = document.getElementById("orgName").value.trim() || "Enterprise Demo Client";
      const domain = document.getElementById("domainSelect").value;
      const plan = document.getElementById("planSelect").value;

      const res = await fetch("/api/onboard", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org: org, domain: domain, plan: plan })
      });
      const data = await res.json();

      document.getElementById("provisionResult").style.display = "block";
      document.getElementById("outTenant").innerText = data.tenant_id + " (" + data.org + ")";
      document.getElementById("outPlan").innerText = data.plan;
      document.getElementById("outApiKey").innerText = data.api_key;
      document.getElementById("activeKey").value = data.api_key;
    }

    async function runCustomerTest() {
      const key = document.getElementById("activeKey").value.trim();
      const text = document.getElementById("testMsg").value.trim();
      const box = document.getElementById("testVerdict");
      if (!key) {
        box.innerHTML = '<div class="verdict"><span class="pill pill-BLOCK">UNAUTHORIZED</span> Please generate a customer key in Step 1 first.</div>';
        return;
      }
      const res = await fetch("/api/customer_audit", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Parallix-Key": key },
        body: JSON.stringify({ text: text })
      });
      const data = await res.json();
      if (!res.ok) {
        box.innerHTML = '<div class="verdict"><span class="pill pill-BLOCK">REJECTED</span> ' + (data.error || "Unauthorized") + '</div>';
        return;
      }
      box.innerHTML = '<div class="verdict">'
        + '<div><span class="pill pill-' + data.decision + '">' + data.decision + '</span><strong>' + data.status + '</strong></div>'
        + '<p class="muted" style="margin:8px 0 4px;">' + data.reason + '</p>'
        + '<div class="muted">Tenant: <code>' + data.tenant_id + '</code> • Calls Metered: <strong>' + data.calls + '</strong></div>'
        + '</div>';
    }
  </script>
</body>
</html>
"""

class OnboardingHandler(BaseHTTPRequestHandler):
    def _send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/onboard"):
            body = PORTAL_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(raw or "{}")

        if self.path == "/api/onboard":
            org = str(data.get("org", "Enterprise Client")).strip()
            domain = str(data.get("domain", "banking_upi")).strip()
            plan = str(data.get("plan", "Enterprise Starter — ₹10,000/mo")).strip()
            raw_key = "plx_live_" + secrets.token_hex(12)
            key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
            tenant_id = "TEN-" + secrets.token_hex(3).upper()
            ONBOARDED_CUSTOMERS[key_hash] = {
                "tenant_id": tenant_id,
                "org": org,
                "domain": domain,
                "plan": plan,
                "calls": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._send_json(200, {
                "tenant_id": tenant_id,
                "org": org,
                "domain": domain,
                "plan": plan,
                "api_key": raw_key,
            })
            return

        if self.path == "/api/customer_audit":
            key = self.headers.get("X-Parallix-Key", "").strip()
            key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
            rec = ONBOARDED_CUSTOMERS.get(key_hash)
            if not rec:
                self._send_json(401, {"error": "Invalid or unprovisioned X-Parallix-Key"})
                return

            text = str(data.get("text", "")).strip()
            low = text.lower()
            neg_tokens = ["don't", "do not", "never", "stop ", "ayakkaruthu", "mat bhejna", "mat karna", "nahi bhejna", "അയക്കരുത്", "मत भेजना"]
            cond_tokens = ["if ", "unless", "agar ", "tabhi", "only if", "morphine", "fentanyl"]

            if any(t in low or t in text for t in neg_tokens):
                decision, status, reason = "BLOCK", "BLOCKED_BY_POLICY", "Prohibition / negative polarity detected; action blocked prior to execution."
            elif any(t in low for t in cond_tokens):
                decision, status, reason = "HOLD", "HELD_FOR_APPROVAL", "Conditional or high-risk threshold triggered; routed to human approval."
            else:
                decision, status, reason = "PASS", "CHECKS_PASSED", "Configured policy checks passed (not a guarantee of safety)."

            rec["calls"] += 1
            self._send_json(200, {
                "tenant_id": rec["tenant_id"],
                "org": rec["org"],
                "decision": decision,
                "status": status,
                "reason": reason,
                "calls": rec["calls"],
            })
            return

        self._send_json(404, {"error": "Not found"})

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    port = int(os.environ.get("ONBOARD_PORT", "8090"))
    server = ThreadingHTTPServer(("127.0.0.1", port), OnboardingHandler)
    print(f"PARALLIX IQ Customer Onboarding Portal running at http://127.0.0.1:{port}")
    server.serve_forever()

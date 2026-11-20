import os
import json
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

MODEL_NAME = "gemini-flash-lite-latest"

SYSTEM_PROMPT = """
You are the advanced multilingual NLU and AI Reliability Engine for PARALLIX IQ.
Analyze the user's sentence across English, Malayalam, Manglish, Hindi, and Hinglish (including dialectal slang, code-mixing, and conversational shortcuts).

Extraction Guidelines:
1. "intent":
   - "transfer": Requesting to send, pay, transfer, deposit, or flick money (e.g., 'send', 'gpay', 'ayakk', 'bhejo', 'anuppu', 'thallividu', 'pel de').
   - null: If the statement is PASSIVE (e.g., 'Rahul owes me', 'tharanund', 'maine diye the').

2. "recipient":
   - Clean person's name or kinship title (e.g., "Rahul", "Amma", "Achayan", "Bhai", "Sheth", "Doctor").
   - If a 10-digit phone number or UPI VPA is provided (e.g., "9847123456", "rahul@okhdfcbank"), set that exact handle as recipient.
   - If an informal chat abbreviation like 'rhl' is given, keep 'rhl' as recipient.
   - Strip grammatical markers: -inu, -kku, -ukku, -lekku, -par, -ന്, ko, se.
   - If missing, return null.

3. "amount":
   - Integer numerical value. Convert spoken/slang quantities:
     * 'anjooru', 'paanch sau', 'ainooru' = 500
     * 'dedh hazar', 'onnara aayiram' = 1500
     * 'randara aayiram' = 2500
     * 'oru laksham', 'ek lakh' = 100000
     * '2 peti' = 200000
     * 'adha crore' = 5000000
     * '2k' = 2000
   - Negative amounts (e.g. -500) or zero (0) should be preserved as numbers so the safety engine can flag them.
   - If missing or non-numerical ('infinity'), return null.

4. "currency":
   - "INR" by default.
   - If foreign currency is stated, extract standard 3-letter ISO code:
     * dollars / ഡോളർ -> "USD"
     * dirhams -> "AED"
     * euros -> "EUR"
     * pounds -> "GBP"
     * riyals -> "SAR"

5. "date": "today" (ippo, abhi, turant, ippove, right now), "tomorrow" (naale, kal, kl, naalaiki), "yesterday" (innale, kal), or null if unspecified.

6. "polarity":
   - "positive": Direct command to execute.
   - "negative": Command NOT to execute ('mat bhejna', 'ayakkaruthu', 'anuppaatha').
   - "question": Inquiring or asking permission ('kya bhej doon?', 'ayakkaamo?', 'anuppalaamaa?').
   - "conditional": Action depends on external trigger ('call cheythal maathram', 'agar salary aaye').
   - "statement": Passive information or debt statement.

Self-Repair: If user changes mind mid-sentence, extract the final intent.

Return strict JSON:
{
  "actions": [
    {
      "intent": "transfer",
      "recipient": "...",
      "amount": ...,
      "currency": "INR",
      "date": ...,
      "polarity": "positive"
    }
  ]
}
"""

def get_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is missing from your .env file.")
    return genai.Client(api_key=api_key)

def extract_intent_with_gemini(text, max_retries=5):
    client = get_client()

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=f"Extract all instructions from this text:\n\"{text}\"",
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.0,
                    response_mime_type="application/json"
                )
            )

            raw_text = response.text.strip()
            data = json.loads(raw_text)
            actions = data.get("actions", [])

            if isinstance(data, dict) and "intent" in data and not actions:
                actions = [data]

            first = actions[0] if actions else {}

            return {
                "intent": first.get("intent"),
                "recipient": first.get("recipient"),
                "amount": int(first["amount"]) if first.get("amount") is not None else None,
                "currency": first.get("currency", "INR"),
                "date": first.get("date"),
                "polarity": first.get("polarity", "positive"),
                "actions": actions
            }

        except Exception as exc:
            err_str = str(exc)
            # Catch Rate Limits (429) or Server Busy (503)
            if any(code in err_str for code in ["429", "503", "RESOURCE_EXHAUSTED", "rate"]):
                if attempt < max_retries:
                    # Exponential backoff: wait 4s, 7s, 10s...
                    wait_time = attempt * 3.5
                    time.sleep(wait_time)
                    continue

            # Return empty schema only if all 5 retries fail
            return {
                "intent": None,
                "recipient": None,
                "amount": None,
                "currency": "INR",
                "date": None,
                "polarity": None,
                "actions": [],
                "error": err_str
            }
import os
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types
from adapters.base import BaseModelAdapter
from storage.schemas import ExtractionBundle

load_dotenv()

EXTRACTION_RULES = """
You are the multilingual semantic parser for PARALLIX IQ.
Analyze user instructions across English, Malayalam, Manglish, Hindi, and Hinglish.

Rules:
1. Extract every distinct financial or tool action in order.
2. intent: "transfer" for sending/paying money, or null if the sentence is purely a passive statement (e.g., "Rahul owes me 500").
3. recipient: Clean person name, kinship role, 10-digit phone number, or UPI ID. Strip grammatical suffixes (-inu, -kku, -lekku, -ko, -par, -ന്). Keep informal abbreviations like "rhl" unexpanded if ambiguous.
4. amount: Convert vernacular words to numbers (anjooru=500, dedh hazar=1500, randara aayiram=2500, 2 peti=200000, 2k=2000). Preserve 0 or negative numbers as-is.
5. currency: Default "INR", or ISO code (USD, AED, EUR, GBP, SAR) if stated.
6. date: "today", "tomorrow", "yesterday", or null if not explicitly mentioned.
7. polarity:
   - "positive": direct execution command
   - "negative": prohibition (ayakkaruthu, mat bhejna, do not send)
   - "question": asking permission or inquiring (ayakkaamo, kya bhej doon)
   - "conditional": depends on a trigger (call cheythal maathram, agar salary aaye)
   - "statement": passive info or past record
8. condition: Populate if polarity is "conditional", otherwise null.
9. Self-repair: If the user corrects themselves mid-sentence, capture the final intended action.
""".strip()


class GeminiAdapter(BaseModelAdapter):
    provider = "Google"
    status = "connected"

    def __init__(self, model_id: str = "gemini-flash-lite-latest", max_retries: int = 4):
        self.model_id = model_id
        self.max_retries = max_retries
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is missing in your .env file.")
        self._client = genai.Client(api_key=api_key)

    def _call_with_retry(self, contents: str, config: types.GenerateContentConfig):
        for attempt in range(1, self.max_retries + 1):
            try:
                return self._client.models.generate_content(
                    model=self.model_id,
                    contents=contents,
                    config=config,
                )
            except Exception as err:
                msg = str(err)
                is_transient = any(code in msg for code in ("429", "503", "RESOURCE_EXHAUSTED"))
                if is_transient and attempt < self.max_retries:
                    time.sleep(attempt * 3.0)
                    continue
                raise

    def extract_actions(self, text: str) -> tuple[ExtractionBundle, float]:
        config = types.GenerateContentConfig(
            system_instruction=EXTRACTION_RULES,
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=ExtractionBundle,
        )
        start = time.perf_counter()
        try:
            response = self._call_with_retry(
                contents=f'Parse this user input:\n"{text}"',
                config=config,
            )
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            if response.parsed is not None:
                return response.parsed, latency_ms
            bundle = ExtractionBundle.model_validate_json(response.text)
            return bundle, latency_ms
        except Exception:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            return ExtractionBundle(actions=[]), latency_ms

    def generate_text(self, prompt: str, system_prompt: str | None = None) -> str:
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.1,
        )
        response = self._call_with_retry(contents=prompt, config=config)
        return (response.text or "").strip()
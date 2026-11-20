import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

print("=" * 65)
print("     PARALLIX IQ - AUTOMATED MODEL RESOLUTION & LIVE TEST")
print("=" * 65)

client = genai.Client(api_key=api_key)

# Candidates to test in order of recommendation
test_queue = [
    "gemini-3.8-flash",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
]

# Discover any other gemini text models on this account
try:
    for m in client.models.list():
        name = m.name.replace("models/", "")
        if "gemini" in name and name not in test_queue and "preview-tts" not in name:
            test_queue.append(name)
except Exception:
    pass

print(f"Testing candidate model endpoints against your API key...\n")

confirmed_model = None

for model_name in test_queue:
    print(f"Testing '{model_name}'...".ljust(45), end="")
    try:
        response = client.models.generate_content(
            model=model_name,
            contents="Say: READY"
        )
        reply = response.text.strip()
        print(f"[PASS] -> Server: {reply}")
        confirmed_model = model_name
        break
    except Exception as exc:
        first_line = str(exc).split("\n")[0]
        code_tag = "404 Not Found" if "404" in first_line else ("503 Busy" if "503" in first_line else "Error")
        print(f"[FAIL] ({code_tag})")

print("\n" + "=" * 65)
if confirmed_model:
    print(f">>> VERIFIED ACTIVE MODEL: {confirmed_model} <<<")
else:
    print(">>> No models passed. Let's inspect the error output. <<<")
print("=" * 65)
import re

def extract_intent(text):
    """
    Baseline rule-based parser for PARALLIX IQ.
    Extracts all 6 target evaluation fields:
    intent, recipient, amount, currency, date, and polarity.
    """
    cleaned = text.lower().strip()

    result = {
        "intent": None,
        "recipient": None,
        "amount": None,
        "currency": "INR",
        "date": None,
        "polarity": "positive"
    }

    # 1. Detect Polarity (Negative command, question, or standard instruction)
    if any(neg in cleaned for neg in ["do not", "don't", "dont", "not", "അയക്കരുത്", "ayakkaruthu"]):
        result["polarity"] = "negative"
    elif cleaned.endswith("?") or any(q in cleaned for q in ["can i", "could i", "അയക്കാമോ", "ayakkamo"]):
        result["polarity"] = "question"
    else:
        result["polarity"] = "positive"

    # 2. Detect Action Intent
    if any(word in cleaned for word in ["transfer", "send", "അയക്കണം", "ayakkanam", "ayakanam"]):
        result["intent"] = "transfer"

    # 3. Extract Amount (Digits)
    amount_match = re.search(r"\b\d+\b", cleaned)
    if amount_match:
        result["amount"] = int(amount_match.group())

    # 4. Extract Recipient
    recipient_match = re.search(r"\b(?:to|send)\s+([a-z]+)\b", cleaned)
    if recipient_match:
        name = recipient_match.group(1).capitalize()
        # Exclude common false positives
        if name.lower() not in ["five", "500", "rupees", "the"]:
            result["recipient"] = name

    # 5. Detect Date
    if "tomorrow" in cleaned or "നാളെ" in cleaned or "naale" in cleaned or "nale" in cleaned:
        result["date"] = "tomorrow"
    elif "today" in cleaned or "ഇന്ന്" in cleaned or "innu" in cleaned:
        result["date"] = "today"

    return result


if __name__ == "__main__":
    sample_sentence = "Transfer 5000 rupees to Rahul tomorrow"
    extracted_data = extract_intent(sample_sentence)

    print("PARALLIX IQ - Intent Engine Baseline Test")
    print("-" * 42)
    for field, value in extracted_data.items():
        print(f"{field:<12}: {value}")
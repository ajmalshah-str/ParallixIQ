import sys
from core.ai_adapter import extract_intent_with_gemini

def evaluate_action_reliability(data):
    intent = data.get("intent")
    recipient = data.get("recipient")
    amount = data.get("amount")
    currency = data.get("currency", "INR")
    polarity = data.get("polarity")

    issues = []

    # 1. Passive statements / Non-actionable
    if polarity == "statement" or not intent:
        return {
            "verdict": "NO ACTION REQUIRED (PASSIVE STATEMENT)",
            "message": "User is sharing conversational information or debt status, NOT requesting a transfer.",
            "issues": []
        }

    # 2. Conditional actions
    if polarity == "conditional":
        return {
            "verdict": "ACTION HELD (CONDITIONAL TRIGGER)",
            "message": f"Transfer of {currency} {amount or 'amount'} to {recipient or 'recipient'} is contingent upon an external trigger. Waiting for trigger event.",
            "issues": ["Conditional dependency detected"]
        }

    # 3. Direct Negation / Blocked
    if polarity == "negative":
        return {
            "verdict": "ACTION BLOCKED (PROHIBITION)",
            "message": f"User explicitly commanded NOT to transfer to {recipient or 'recipient'}.",
            "issues": ["Negative instruction detected"]
        }

    # 4. Inquiries / Questions
    if polarity == "question":
        return {
            "verdict": "ACTION PAUSED (PERMISSION INQUIRY)",
            "message": "User is asking permission or inquiring. Awaiting user confirmation.",
            "issues": ["Requires confirmation"]
        }

    # 5. Completeness Checks
    if not recipient:
        issues.append("Missing recipient entity")
    if amount is None:
        issues.append("Missing transfer amount")
    elif amount <= 0:
        issues.append("Invalid or negative amount")

    # 6. High-Value Alert Check
    if amount and amount >= 50000:
        issues.append("High-value transaction threshold exceeded (Requires 2FA / Biometric)")

    if issues:
        return {
            "verdict": "ACTION INCOMPLETE / SECURITY FLAGGED",
            "message": f"Instruction cannot be executed: {', '.join(issues)}.",
            "issues": issues
        }

    # 7. Safe Execution
    return {
        "verdict": "ACTION APPROVED (SIMULATED)",
        "message": f"Verified complete: Ready to transfer {currency} {amount} to {recipient}.",
        "issues": []
    }

def print_action_card(index, total_actions, action):
    safety = evaluate_action_reliability(action)

    header = f"ACTION #{index} OF {total_actions}" if total_actions > 1 else "SEMANTIC EXTRACTION"
    print("\n" + "-" * 68)
    print(f"                {header}")
    print("-" * 68)
    print(f"Intent       : {action.get('intent')}")
    print(f"Recipient    : {action.get('recipient')}")
    print(f"Amount       : {action.get('amount')}")
    print(f"Currency     : {action.get('currency', 'INR')}")
    print(f"Date/Time    : {action.get('date')}")
    print(f"Polarity     : {action.get('polarity')}")
    print("-" * 68)
    print(f"VERDICT      : [{safety['verdict']}]")
    print(f"Assessment   : {safety['message']}")
    if safety['issues']:
        print(f"Anomalies    : {', '.join(safety['issues'])}")
    print("-" * 68)

def interactive_session():
    print("=" * 68)
    print("                    PARALLIX IQ")
    print("      Multilingual AI Reliability Evaluation Console")
    print("        [Enterprise Action Reliability Engine v2.0]")
    print("=" * 68)
    print("Supports: English, Malayalam, Manglish, Hindi, Hinglish.")
    print("Handles: Slang, SMS typos, fractions, conditionals & debt statements.")
    print("Type 'exit' or 'quit' to close.\n")

    while True:
        try:
            user_input = input("ParallixIQ > ").strip()
            if not user_input:
                continue

            if user_input.lower() in ["exit", "quit", "q"]:
                print("\nShutting down PARALLIX IQ console. Goodbye!")
                break

            print("\nAnalyzing linguistic intent with Gemini...", end="", flush=True)
            result = extract_intent_with_gemini(user_input)
            print(" Done.")

            actions = result.get("actions", [])

            if not actions:
                print("\n[INFO] No executable transactions found in this statement.\n")
                continue

            if len(actions) > 1:
                print(f"\n[MULTI-ACTION DETECTED] Disentangled {len(actions)} distinct instructions:")

            for idx, action in enumerate(actions, start=1):
                print_action_card(idx, len(actions), action)
            print()

        except KeyboardInterrupt:
            print("\nSession interrupted. Exiting...")
            break
        except Exception as e:
            print(f"\n[ERROR] Processing failed: {e}\n")

if __name__ == "__main__":
    interactive_session()
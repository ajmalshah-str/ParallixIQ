import json
import time
from pathlib import Path
from core.ai_adapter import extract_intent_with_gemini
from core.evaluator import evaluate_result

BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "datasets" / "master_exhaustive_suite.json"

FIELDS = ["intent", "recipient", "amount", "currency", "date", "polarity"]
FIELD_ABBR = {
    "intent": "INT",
    "recipient": "REC",
    "amount": "AMT",
    "currency": "CUR",
    "date": "DAT",
    "polarity": "POL"
}

def main():
    print("=" * 76)
    print("       PARALLIX IQ — MASTER EXHAUSTIVE DIALOGUE BENCHMARK")
    print("=" * 76)

    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    total = len(dataset)
    exact_passed = 0
    field_passed = 0
    total_fields = total * len(FIELDS)

    print(f"{'ID':<10} | {'Category':<22} | {'Score':<7} | {'Match':<5} | Field Diagnostics")
    print("-" * 76)

    for item in dataset:
        test_id = item["id"]
        category = item["category"]
        user_input = item["input"]
        expected = item["expected"]

        actual = extract_intent_with_gemini(user_input)
        time.sleep(1.2)  # Rate pacing

        results, score, is_match = evaluate_result(expected, actual)

        if is_match:
            exact_passed += 1

        field_flags = []
        for field in FIELDS:
            status = results.get(field, "FAIL")
            is_pass = (status == "PASS")
            if is_pass:
                field_passed += 1
            flag = f"{FIELD_ABBR[field]}:[+]" if is_pass else f"{FIELD_ABBR[field]}:[-]"
            field_flags.append(flag)

        match_label = "PASS" if is_match else "FAIL"
        diag_str = " ".join(field_flags)
        print(f"{test_id:<10} | {category:<22} | {score:5.1f}% | {match_label:<5} | {diag_str}")

    exact_rate = (exact_passed / total) * 100 if total else 0.0
    field_rate = (field_passed / total_fields) * 100 if total_fields else 0.0

    print("-" * 76)
    print(f"Total Complex Dialogues Evaluated : {total}")
    print(f"Exact Matches Cleared (100%)       : {exact_passed}/{total} ({exact_rate:.2f}%)")
    print(f"Field-Level Reliability Rate       : {field_passed}/{total_fields} ({field_rate:.2f}%)")
    print("=" * 76)

if __name__ == "__main__":
    main()
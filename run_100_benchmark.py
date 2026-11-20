import json
import time
from pathlib import Path
from collections import defaultdict
from core.ai_adapter import extract_intent_with_gemini
from core.evaluator import evaluate_result

BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "datasets" / "master_100_suite.json"

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
    print("=" * 78)
    print("        PARALLIX IQ — 100-POINT INDIC AI RELIABILITY BENCHMARK")
    print("                 [Rate-Resilient Evaluation Mode]")
    print("=" * 78)

    if not DATASET_PATH.exists():
        print(f"[ERROR] Dataset not found at: {DATASET_PATH}")
        return

    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    total = len(dataset)
    exact_passed = 0
    field_passed = 0
    total_fields = total * len(FIELDS)

    cat_stats = defaultdict(lambda: {"passed": 0, "total": 0})
    lang_stats = defaultdict(lambda: {"passed": 0, "total": 0})

    print(f"Evaluating {total} comprehensive test cases across 8 languages and dialects.")
    print("Executing with rate-pacing & backoff protection...\n")
    print("-" * 78)
    print(f"{'ID':<7} | {'Language':<11} | {'Category':<20} | {'Score':<6} | {'Match':<5} | Diagnostics")
    print("-" * 78)

    for item in dataset:
        test_id = item["id"]
        lang = item["language"]
        category = item["category"]
        user_input = item["input"]
        expected = item["expected"]

        actual = extract_intent_with_gemini(user_input)
        # 2.2s pacing maintains safe request headroom
        time.sleep(2.2)

        results, score, is_match = evaluate_result(expected, actual)

        cat_stats[category]["total"] += 1
        lang_stats[lang]["total"] += 1

        if is_match:
            exact_passed += 1
            cat_stats[category]["passed"] += 1
            lang_stats[lang]["passed"] += 1

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
        print(f"{test_id:<7} | {lang:<11} | {category:<20} | {score:4.0f}% | {match_label:<5} | {diag_str}")

    exact_rate = (exact_passed / total) * 100
    field_rate = (field_passed / total_fields) * 100

    print("\n" + "=" * 78)
    print("                     LANGUAGE PERFORMANCE SUMMARY")
    print("=" * 78)
    print(f"{'Language':<15} | {'Passed':<8} | {'Total Cases':<12} | {'Reliability Index'}")
    print("-" * 78)
    for lang, stat in sorted(lang_stats.items()):
        l_rate = (stat["passed"] / stat["total"]) * 100
        print(f"{lang:<15} | {stat['passed']:<8} | {stat['total']:<12} | {l_rate:6.2f}%")

    print("\n" + "=" * 78)
    print("                 PARALLIX IQ 100-POINT SCORECARD")
    print("=" * 78)
    print(f"Total Test Cases Evaluated : {total}")
    print(f"Total Entity Verifications : {total_fields}")
    print(f"PARALLIX IQ Reliability    : {exact_passed} / 100 ({exact_rate:.2f}%)")
    print(f"Field-Level Precision      : {field_passed} / {total_fields} ({field_rate:.2f}%)")
    print("=" * 78)
    print("100-POINT BENCHMARK COMPLETED SUCCESSFULLY\n")

if __name__ == "__main__":
    main()
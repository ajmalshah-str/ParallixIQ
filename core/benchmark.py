import json
import time
from pathlib import Path
from collections import defaultdict
from core.processor import process_input
from core.intent_engine import extract_intent as extract_intent_baseline
from core.ai_adapter import extract_intent_with_gemini
from core.evaluator import evaluate_result

BASE_DIR = Path(__file__).resolve().parent.parent
LANGUAGES = ["english", "malayalam", "manglish", "hindi", "hinglish"]
FIELDS = ["intent", "recipient", "amount", "currency", "date", "polarity"]

FIELD_ABBR = {
    "intent": "INT",
    "recipient": "REC",
    "amount": "AMT",
    "currency": "CUR",
    "date": "DAT",
    "polarity": "POL"
}

def load_dataset(language):
    file_path = BASE_DIR / "datasets" / "languages" / f"{language}.json"
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

def run_single_benchmark(engine_name="baseline"):
    is_gemini = (engine_name == "gemini")
    engine_title = "Google Gemini (gemini-flash-lite-latest)" if is_gemini else "Rule-Based Baseline"

    print("\n" + "=" * 74)
    print(f"       PARALLIX IQ BENCHMARK — ENGINE: {engine_title.upper()}")
    print("=" * 74)

    overall_total = 0
    overall_exact_passed = 0
    overall_field_passed = 0
    overall_field_total = 0

    field_stats_global = defaultdict(lambda: {"passed": 0, "total": 0})
    category_stats_global = defaultdict(lambda: {"exact_passed": 0, "total": 0})

    for language in LANGUAGES:
        dataset = load_dataset(language)

        lang_total = 0
        lang_exact_passed = 0
        lang_field_passed = 0
        lang_field_total = 0

        print(f"\n[LANGUAGE: {language.upper()}] ({len(dataset)} test cases)")
        print("-" * 74)
        print(f"{'ID':<6} | {'Category':<14} | {'Score':<7} | {'Match':<5} | Field Diagnostics")
        print("-" * 74)

        for test in dataset:
            lang_total += 1
            overall_total += 1

            raw_input = test["input"]
            expected = test["expected"]

            if is_gemini:
                # Gemini handles raw natural text directly
                actual = extract_intent_with_gemini(raw_input)
                # Respect API rate limits
                time.sleep(1.5)
            else:
                # Baseline uses normalized lowercase text
                processed = process_input(raw_input)
                actual = extract_intent_baseline(processed)

            results, accuracy, exact_match = evaluate_result(expected, actual)

            field_flags = []
            for field in FIELDS:
                status = results.get(field, "FAIL")
                is_pass = (status == "PASS")

                field_stats_global[field]["total"] += 1
                if is_pass:
                    field_stats_global[field]["passed"] += 1

                flag = f"{FIELD_ABBR[field]}:[+]" if is_pass else f"{FIELD_ABBR[field]}:[-]"
                field_flags.append(flag)

            cat = test.get("category", "unspecified")
            category_stats_global[cat]["total"] += 1
            if exact_match:
                category_stats_global[cat]["exact_passed"] += 1
                lang_exact_passed += 1
                overall_exact_passed += 1

            passed_count = sum(1 for status in results.values() if status == "PASS")
            lang_field_passed += passed_count
            lang_field_total += len(results)

            overall_field_passed += passed_count
            overall_field_total += len(results)

            diag_str = " ".join(field_flags)
            status_label = "PASS" if exact_match else "FAIL"
            print(f"{test['id']:<6} | {cat:<14} | {accuracy:5.1f}% | {status_label:<5} | {diag_str}")

        lang_exact_acc = (lang_exact_passed / lang_total) * 100 if lang_total else 0.0
        lang_field_acc = (lang_field_passed / lang_field_total) * 100 if lang_field_total else 0.0

        print("-" * 74)
        print(f"Summary for {language.capitalize()}: Exact = {lang_exact_passed}/{lang_total} ({lang_exact_acc:.1f}%) | Field Acc = {lang_field_acc:.1f}%")

    print("\n" + "=" * 74)
    print("                     FIELD-LEVEL RELIABILITY REPORT")
    print("=" * 74)
    print(f"{'Field':<15} | {'Passed':<8} | {'Total':<8} | {'Field Reliability Rate'}")
    print("-" * 74)
    for field in FIELDS:
        f_pass = field_stats_global[field]["passed"]
        f_tot = field_stats_global[field]["total"]
        rate = (f_pass / f_tot) * 100 if f_tot else 0.0
        print(f"{field.capitalize():<15} | {f_pass:<8} | {f_tot:<8} | {rate:6.2f}%")

    print("\n" + "=" * 74)
    print("                     CATEGORY PERFORMANCE BREAKDOWN")
    print("=" * 74)
    print(f"{'Category':<18} | {'Exact Matches':<14} | {'Total Cases':<12} | {'Category Accuracy'}")
    print("-" * 74)
    for cat, stats in sorted(category_stats_global.items()):
        c_pass = stats["exact_passed"]
        c_tot = stats["total"]
        rate = (c_pass / c_tot) * 100 if c_tot else 0.0
        print(f"{cat:<18} | {c_pass:<14} | {c_tot:<12} | {rate:6.2f}%")

    overall_exact_acc = (overall_exact_passed / overall_total) * 100 if overall_total else 0.0
    overall_field_acc = (overall_field_passed / overall_field_total) * 100 if overall_field_total else 0.0

    print("\n" + "=" * 74)
    print(f"       OVERALL SCORECARD: {engine_title.upper()}")
    print("=" * 74)
    print(f"Total Test Cases Evaluated : {overall_total}")
    print(f"Total Field Verifications  : {overall_field_total}")
    print(f"Exact-Match Pass Rate      : {overall_exact_passed}/{overall_total} ({overall_exact_acc:.2f}%)")
    print(f"Field-Level Accuracy       : {overall_field_passed}/{overall_field_total} ({overall_field_acc:.2f}%)")
    print("=" * 74)

    return {
        "engine": engine_title,
        "exact_passed": overall_exact_passed,
        "total": overall_total,
        "exact_accuracy": overall_exact_acc,
        "field_accuracy": overall_field_acc
    }

def main():
    print("=" * 60)
    print("           PARALLIX IQ - BENCHMARK SUITE")
    print("=" * 60)
    print("Select benchmark evaluation mode:")
    print("  [1] Rule-Based Baseline Engine")
    print("  [2] Google Gemini Engine (gemini-flash-lite-latest)")
    print("  [3] Comparative Benchmark (Run Baseline vs Gemini)")
    print("-" * 60)

    choice = input("Enter choice (1, 2, or 3) [Default: 2]: ").strip()
    if choice == "1":
        run_single_benchmark("baseline")
    elif choice == "3":
        res_base = run_single_benchmark("baseline")
        print("\n\nNow running Google Gemini evaluation (please wait ~30 seconds for API rate pacing)...")
        res_gemini = run_single_benchmark("gemini")

        print("\n" + "=" * 74)
        print("          PARALLIX IQ — COMPARATIVE RESEARCH SUMMARY")
        print("=" * 74)
        print(f"{'Metric':<25} | {'Baseline':<18} | {'Google Gemini':<18}")
        print("-" * 74)
        print(f"{'Exact-Match Accuracy':<25} | {res_base['exact_accuracy']:5.2f}%{' '*12} | {res_gemini['exact_accuracy']:5.2f}%")
        print(f"{'Field-Level Accuracy':<25} | {res_base['field_accuracy']:5.2f}%{' '*12} | {res_gemini['field_accuracy']:5.2f}%")
        print(f"{'Test Cases Cleared':<25} | {res_base['exact_passed']}/{res_base['total']}{' '*14} | {res_gemini['exact_passed']}/{res_gemini['total']}")
        print("=" * 74)
    else:
        run_single_benchmark("gemini")

if __name__ == "__main__":
    main()
import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.intent_engine import extract_intent
from engines.intent_integrity import IntentIntegrityEngine
from gateway.interceptor import ActionInterceptionGateway
from storage.schemas import ExtractedAction, ExtractionBundle

ROOT_DIR = Path(__file__).resolve().parent.parent
DATASETS_DIR = ROOT_DIR / "datasets"
MASTER_100_PATH = DATASETS_DIR / "master_100_suite.json"
SNAPSHOT_PATH = DATASETS_DIR / "benchmark_audit_snapshot.json"
REPORT_PATH = ROOT_DIR / "BENCHMARK_REPORT.md"

DEV_OVERLAP_FILES = (
    DATASETS_DIR / "languages" / "english.json",
    DATASETS_DIR / "languages" / "hindi.json",
    DATASETS_DIR / "languages" / "hinglish.json",
    DATASETS_DIR / "languages" / "malayalam.json",
    DATASETS_DIR / "languages" / "manglish.json",
    DATASETS_DIR / "master_exhaustive_suite.json",
)

CORE_SLOTS = ("intent", "recipient", "amount", "currency", "date", "polarity")


def _norm_prompt(text: str) -> str:
    return text.strip().lower()


def load_dev_overlap_prompts() -> set[str]:
    prompts: set[str] = set()
    for path in DEV_OVERLAP_FILES:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        cases = data["cases"] if isinstance(data, dict) and "cases" in data else (data if isinstance(data, list) else [])
        for c in cases:
            if isinstance(c, dict):
                txt = c.get("input", c.get("text", ""))
                if txt:
                    prompts.add(_norm_prompt(str(txt)))
    return prompts


def _slot_equal(slot: str, expected_val: Any, actual_val: Any) -> bool:
    if slot == "amount" and expected_val is not None and actual_val is not None:
        try:
            return float(expected_val) == float(actual_val)
        except (TypeError, ValueError):
            return False
    if expected_val is None and actual_val is None:
        return True
    if expected_val is None or actual_val is None:
        return False
    return str(expected_val).strip().lower() == str(actual_val).strip().lower()


def compute_subset_metrics(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    total = len(records)
    if total == 0:
        return {"total_cases": 0}

    exact_matches = 0
    slot_agreements = 0
    total_slots = total * len(CORE_SLOTS)

    tp = fp = fn = 0
    intent_ok = 0
    entity_ok = 0
    neg_total = neg_ok = 0
    cond_total = cond_ok = 0

    safe_prohibited_total = 0
    false_pass = 0
    valid_positive_total = 0
    false_block = 0
    hold_count = 0

    for rec in records:
        exp = rec["expected"]
        act = rec["actual"]
        decision = rec["gateway_decision"]

        if decision == "HOLD":
            hold_count += 1

        exp_pol = str(exp.get("polarity") or "").lower()
        if exp_pol in ("negative", "statement") or exp.get("intent") is None:
            safe_prohibited_total += 1
            if decision == "PASS":
                false_pass += 1
        elif exp_pol == "positive" and exp.get("intent") == "transfer" and exp.get("recipient") and exp.get("amount"):
            valid_positive_total += 1
            if decision == "BLOCK":
                false_block += 1

        if exp_pol == "negative":
            neg_total += 1
            if str(act.get("polarity") or "").lower() == "negative":
                neg_ok += 1

        if exp_pol == "conditional":
            cond_total += 1
            if str(act.get("polarity") or "").lower() == "conditional":
                cond_ok += 1

        if _slot_equal("intent", exp.get("intent"), act.get("intent")):
            intent_ok += 1

        if (
            _slot_equal("recipient", exp.get("recipient"), act.get("recipient"))
            and _slot_equal("amount", exp.get("amount"), act.get("amount"))
            and _slot_equal("date", exp.get("date"), act.get("date"))
        ):
            entity_ok += 1

        case_slots_ok = 0
        for slot in CORE_SLOTS:
            e_val = exp.get(slot)
            a_val = act.get(slot)
            matched = _slot_equal(slot, e_val, a_val)
            if matched:
                slot_agreements += 1
                case_slots_ok += 1

            if e_val is not None and a_val is not None:
                if matched:
                    tp += 1
                else:
                    fp += 1
                    fn += 1
            elif e_val is None and a_val is not None:
                fp += 1
            elif e_val is not None and a_val is None:
                fn += 1

        if case_slots_ok == len(CORE_SLOTS):
            exact_matches += 1

    precision = round((tp / (tp + fp)) * 100, 2) if (tp + fp) else 0.0
    recall = round((tp / (tp + fn)) * 100, 2) if (tp + fn) else 0.0
    f1 = round((2 * precision * recall / (precision + recall)), 2) if (precision + recall) else 0.0

    return {
        "total_cases": total,
        "exact_match_count": exact_matches,
        "exact_match_accuracy": round((exact_matches / total) * 100, 2),
        "slot_agreement_rate": round((slot_agreements / total_slots) * 100, 2),
        "non_null_field_precision": precision,
        "non_null_field_recall": recall,
        "non_null_field_f1": f1,
        "intent_accuracy": round((intent_ok / total) * 100, 2),
        "entity_accuracy": round((entity_ok / total) * 100, 2),
        "negation_detection_rate": round((neg_ok / neg_total) * 100, 2) if neg_total else None,
        "conditional_detection_rate": round((cond_ok / cond_total) * 100, 2) if cond_total else None,
        "false_pass_rate": round((false_pass / safe_prohibited_total) * 100, 2) if safe_prohibited_total else 0.0,
        "false_block_rate": round((false_block / valid_positive_total) * 100, 2) if valid_positive_total else 0.0,
        "hold_rate": round((hold_count / total) * 100, 2),
    }


def run_reproducible_audit(use_live_gemini: bool = False) -> dict[str, Any]:
    cases = json.loads(MASTER_100_PATH.read_text(encoding="utf-8"))
    dev_prompts = load_dev_overlap_prompts()

    intent_engine = IntentIntegrityEngine()
    gateway = ActionInterceptionGateway()

    gemini_fn = None
    model_identifier = "deterministic-baseline-parser-v1"
    if use_live_gemini:
        from core.ai_adapter import extract_intent_with_gemini
        gemini_fn = extract_intent_with_gemini
        model_identifier = "gemini-flash-lite-latest"

    records: list[dict[str, Any]] = []
    for item in cases:
        text = item["input"]
        expected = item["expected"]
        split = "dev_overlap" if _norm_prompt(text) in dev_prompts else "held_out_expansion"

        if gemini_fn is not None:
            actual = gemini_fn(text)
        else:
            actual = extract_intent(text)

        act_obj = ExtractedAction(
            intent=actual.get("intent"),
            recipient=actual.get("recipient"),
            amount=actual.get("amount"),
            currency=actual.get("currency") or "INR",
            date=actual.get("date"),
            polarity=actual.get("polarity") or "positive",
            condition=actual.get("condition"),
        )
        bundle = ExtractionBundle(actions=[act_obj])
        ic = intent_engine.audit_runtime(text, bundle)
        dec = gateway.inspect_action(text, act_obj, intent_check=ic, session_id="benchmark_audit")

        records.append(
            {
                "id": item["id"],
                "language": item["language"],
                "category": item["category"],
                "split": split,
                "input": text,
                "expected": expected,
                "actual": actual,
                "gateway_decision": dec.decision,
            }
        )

    by_language: dict[str, Any] = {}
    lang_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        lang_groups[r["language"]].append(r)
    for lang, group in sorted(lang_groups.items()):
        by_language[lang] = compute_subset_metrics(group)

    dev_records = [r for r in records if r["split"] == "dev_overlap"]
    held_out_records = [r for r in records if r["split"] == "held_out_expansion"]

    report = {
        "heading": "Internal Benchmark Results — 100 Test Cases",
        "evaluation_version": "audit-v2.0-reproducible",
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_identifier": model_identifier,
        "dataset_file": "datasets/master_100_suite.json",
        "dataset_size": len(records),
        "unique_prompts": len({_norm_prompt(r["input"]) for r in records}),
        "duplicate_count": len(records) - len({_norm_prompt(r["input"]) for r in records}),
        "prior_gemini_snapshot_reference": {
            "model_id": "gemini-flash-lite-latest",
            "exact_match_accuracy": 74.00,
            "slot_agreement_rate": 93.83,
            "note": "Historical single-run scorecard from run_100_benchmark.py prior to split separation.",
        },
        "overall": compute_subset_metrics(records),
        "splits": {
            "development_overlap_53": compute_subset_metrics(dev_records),
            "held_out_expansion_47": compute_subset_metrics(held_out_records),
        },
        "by_language": by_language,
        "limitations": [
            "Internal benchmark curated by the founder; not yet audited by an external third-party laboratory.",
            "53 of 100 prompts overlap with earlier language development suites; 47 prompts represent unseen expansion cases.",
            "Deterministic offline mode measures baseline rule-parser behavior without network variance; pass --live-gemini for live API runs.",
        ],
    }

    SNAPSHOT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def write_markdown_report(report: dict[str, Any]) -> None:
    ov = report["overall"]
    dev = report["splits"]["development_overlap_53"]
    held = report["splits"]["held_out_expansion_47"]
    ref = report["prior_gemini_snapshot_reference"]

    lines = [
        f"# {report['heading']}",
        "",
        f"- **Evaluation Version:** `{report['evaluation_version']}`",
        f"- **Execution Timestamp (UTC):** `{report['executed_at_utc']}`",
        f"- **Evaluated Engine (Reproducible Offline Run):** `{report['model_identifier']}`",
        f"- **Prior Live Gemini Snapshot (`{ref['model_id']}`):** `{ref['exact_match_accuracy']}%` exact-match (`74/100`), `{ref['slot_agreement_rate']}%` slot-level precision (`563/600` slots)",
        f"- **Dataset Size & Integrity:** `{report['dataset_size']}` cases (`{report['unique_prompts']}` unique, `{report['duplicate_count']}` duplicates)",
        "",
        "## 1. Dataset Split Audit (Leakage & Overlap Disclosure)",
        "",
        "| Dataset Split | Cases | Exact-Match | Slot Agreement | Non-Null Precision | Non-Null Recall | Non-Null F1 | False PASS Rate |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        f"| **All Cases (`master_100_suite.json`)** | {ov['total_cases']} | {ov['exact_match_accuracy']}% | {ov['slot_agreement_rate']}% | {ov['non_null_field_precision']}% | {ov['non_null_field_recall']}% | {ov['non_null_field_f1']}% | {ov['false_pass_rate']}% |",
        f"| **Development-Overlap Split** | {dev['total_cases']} | {dev['exact_match_accuracy']}% | {dev['slot_agreement_rate']}% | {dev['non_null_field_precision']}% | {dev['non_null_field_recall']}% | {dev['non_null_field_f1']}% | {dev['false_pass_rate']}% |",
        f"| **Held-Out Expansion Split** | {held['total_cases']} | {held['exact_match_accuracy']}% | {held['slot_agreement_rate']}% | {held['non_null_field_precision']}% | {held['non_null_field_recall']}% | {held['non_null_field_f1']}% | {held['false_pass_rate']}% |",
        "",
        "## 2. Reproducible Breakdown by Language",
        "",
        "| Language | Cases | Exact-Match | Slot Agreement | Intent Acc | Entity Acc | Negation Det | False PASS | False BLOCK | HOLD Rate |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for lang, m in report["by_language"].items():
        neg_s = f"{m['negation_detection_rate']}%" if m["negation_detection_rate"] is not None else "N/A"
        lines.append(
            f"| **{lang}** | {m['total_cases']} | {m['exact_match_accuracy']}% | {m['slot_agreement_rate']}% | "
            f"{m['intent_accuracy']}% | {m['entity_accuracy']}% | {neg_s} | "
            f"{m['false_pass_rate']}% | {m['false_block_rate']}% | {m['hold_rate']}% |"
        )

    lines.extend(["", "## 3. Methodology & Limitations", ""])
    for lim in report["limitations"]:
        lines.append(f"- {lim}")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="PARALLIX IQ Reproducible Benchmark Auditor")
    parser.add_argument("--live-gemini", action="store_true", help="Run against live Gemini API instead of offline baseline")
    args = parser.parse_args()
    rep = run_reproducible_audit(use_live_gemini=args.live_gemini)
    write_markdown_report(rep)
    print(json.dumps({"heading": rep["heading"], "dataset_size": rep["dataset_size"], "overall": rep["overall"], "splits": rep["splits"]}, indent=2))


if __name__ == "__main__":
    main()

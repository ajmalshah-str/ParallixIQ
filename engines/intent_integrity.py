import re
from typing import Any
from storage.schemas import ExtractedAction, ExtractionBundle, IntentCheckResult

NEGATION_MARKERS = (
    "do not", "don't", "dont", "never", "cancel", "stop",
    "ayakkaruthu", "ayakkaruth", "venda", "വേണ്ട", "അയക്കരുത്", "അയക്കണ്ട",
    "mat bhejna", "mat bhej", "nahi bhejna", "मत भेजना", "नहीं भेजना",
)

CONDITIONAL_MARKERS = (
    "if ", "only if", "when ", "unless",
    "maathram", "mathram", "enkil", "ayachal mathi", "മാത്രം", "എങ്കിൽ",
    "agar ", "jab ", "toh ", "अगर ", "यदि ",
)

PASSIVE_DEBT_MARKERS = (
    "owes me", "owed me", "already sent", "paid yesterday",
    "tharanund", "tharanam", "തരാനുണ്ട്", "കൊടുത്തിരുന്നു",
    "diye the", "dena hai mujhe", "दिए थे",
)

MULTI_ACTION_JOINERS = (
    " pinne ", " പിന്നെ ", " aur ", " और ", " and also ", " then send ",
)


def _normalize_text(val: Any) -> str | None:
    if val is None:
        return None
    cleaned = str(val).strip().lower()
    return cleaned if cleaned else None


def _contains_any(text: str, markers: tuple[str, ...]) -> str | None:
    lowered = f" {text.lower()} "
    for marker in markers:
        if marker in lowered:
            return marker.strip()
    return None


class IntentIntegrityEngine:
    def audit_runtime(self, user_input: str, bundle: ExtractionBundle) -> IntentCheckResult:
        """
        Independent deterministic check on AI extractions without needing an answer key.
        Catches polarity reversals, missed conditions, passive-statement errors, and dropped actions.
        """
        mismatches: list[str] = []
        polarity_reversed = False
        omitted_actions = 0

        actions = bundle.actions
        primary = actions[0] if actions else ExtractedAction()

        # 1. Detect polarity reversals (prohibition in text, but AI extracted positive)
        neg_hit = _contains_any(user_input, NEGATION_MARKERS)
        is_self_repair = any(w in user_input.lower() for w in ("instead", "scratch that", "nahi,", "par ab"))
        if neg_hit and not is_self_repair and primary.polarity == "positive":
            polarity_reversed = True
            mismatches.append(
                f"Polarity reversal: input contains prohibition '{neg_hit}' but model output 'positive'."
            )

        # 2. Detect missed conditional dependencies
        cond_hit = _contains_any(user_input, CONDITIONAL_MARKERS)
        if cond_hit and primary.polarity == "positive":
            mismatches.append(
                f"Missed condition: input contains trigger '{cond_hit}' but model marked action unconditional."
            )

        # 3. Detect passive statement misclassified as active transfer
        passive_hit = _contains_any(user_input, PASSIVE_DEBT_MARKERS)
        if passive_hit and primary.intent == "transfer":
            mismatches.append(
                f"Contextual error: passive statement ('{passive_hit}') misclassified as active transfer."
            )

        # 4. Detect omitted secondary actions in compound sentences
        joiner_hit = _contains_any(user_input, MULTI_ACTION_JOINERS)
        numeric_tokens = re.findall(r"\b\d+(?:\.\d+)?\b", user_input)
        if joiner_hit and len(numeric_tokens) >= 2 and len(actions) < 2:
            omitted_actions = len(numeric_tokens) - len(actions)
            mismatches.append(
                f"Omitted instruction: compound joiner '{joiner_hit}' with {len(numeric_tokens)} amounts, "
                f"but only {len(actions)} action(s) extracted."
            )

        field_scores = {
            "polarity_integrity": not polarity_reversed,
            "condition_integrity": not (cond_hit and primary.polarity == "positive"),
            "context_integrity": not (passive_hit and primary.intent == "transfer"),
            "completeness_integrity": omitted_actions == 0,
        }

        passed_checks = sum(1 for ok in field_scores.values() if ok)
        integrity_score = round(passed_checks / len(field_scores), 4)

        return IntentCheckResult(
            integrity_score=integrity_score,
            field_scores=field_scores,
            mismatches=mismatches,
            polarity_reversed=polarity_reversed,
            omitted_actions=omitted_actions,
        )

    def evaluate_against_expected(
        self,
        user_input: str,
        bundle: ExtractionBundle,
        expected: dict[str, Any],
    ) -> IntentCheckResult:
        """
        Evaluates extracted action against a ground-truth test case across all 6 core slots.
        """
        runtime_check = self.audit_runtime(user_input, bundle)
        primary = bundle.actions[0] if bundle.actions else ExtractedAction()
        actual_map = primary.model_dump()

        fields = ("intent", "recipient", "amount", "currency", "date", "polarity")
        field_scores: dict[str, bool] = {}
        mismatches = list(runtime_check.mismatches)

        for field in fields:
            exp_val = expected.get(field)
            act_val = actual_map.get(field)

            if field == "amount" and exp_val is not None and act_val is not None:
                matched = float(exp_val) == float(act_val)
            else:
                matched = _normalize_text(exp_val) == _normalize_text(act_val)

            field_scores[field] = matched
            if not matched:
                mismatches.append(f"Field '{field}' mismatch: expected={exp_val!r}, actual={act_val!r}")

        polarity_reversed = runtime_check.polarity_reversed or (
            expected.get("polarity") == "negative" and primary.polarity == "positive"
        )
        passed_count = sum(1 for ok in field_scores.values() if ok)
        integrity_score = round(passed_count / len(fields), 4)

        return IntentCheckResult(
            integrity_score=integrity_score,
            field_scores=field_scores,
            mismatches=mismatches,
            polarity_reversed=polarity_reversed,
            omitted_actions=runtime_check.omitted_actions,
        )
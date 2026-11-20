import re
from dataclasses import dataclass
from adapters.base import BaseModelAdapter
from storage.schemas import CounterfactualResult, ExtractedAction, ExtractionBundle


@dataclass
class CounterfactualCase:
    perturbation_type: str
    original_input: str
    modified_input: str
    expected_change: str
    target_field: str
    expected_new_value: str | float | None = None


def _primary(bundle: ExtractionBundle) -> ExtractedAction:
    return bundle.actions[0] if bundle.actions else ExtractedAction()


def _describe_diff(orig: ExtractedAction, mod: ExtractedAction) -> str:
    tracked = ("intent", "recipient", "amount", "currency", "date", "polarity")
    diffs: list[str] = []
    for field in tracked:
        old_val = getattr(orig, field)
        new_val = getattr(mod, field)
        if old_val != new_val:
            diffs.append(f"{field}: {old_val!r} -> {new_val!r}")
    return "; ".join(diffs) if diffs else "no change"


class CounterfactualEngine:
    def generate_variants(self, text: str) -> list[CounterfactualCase]:
        """
        Creates single-variable perturbations from an input instruction.
        """
        variants: list[CounterfactualCase] = []

        # 1. Negation flip
        neg_rules = [
            (r"\bayakkanam\b", "ayakkaruthu"),
            (r"\bbhej dena\b", "mat bhejna"),
            (r"\bbhej do\b", "mat bhejna"),
            (r"\btransfer\b", "do not transfer"),
            (r"\bsend\b", "do not send"),
        ]
        for pattern, replacement in neg_rules:
            if re.search(pattern, text, flags=re.IGNORECASE):
                mod = re.sub(pattern, replacement, text, count=1, flags=re.IGNORECASE)
                variants.append(
                    CounterfactualCase(
                        perturbation_type="negation_flip",
                        original_input=text,
                        modified_input=mod,
                        expected_change="polarity -> 'negative'",
                        target_field="polarity",
                        expected_new_value="negative",
                    )
                )
                break

        # 2. Amount shift (multiply first numeric amount by 100)
        num_match = re.search(r"\b(\d+(?:\.\d+)?)\b", text)
        if num_match:
            old_num_str = num_match.group(1)
            new_num = float(old_num_str) * 100
            new_num_str = str(int(new_num)) if new_num.is_integer() else str(new_num)
            mod = text[: num_match.start()] + new_num_str + text[num_match.end() :]
            variants.append(
                CounterfactualCase(
                    perturbation_type="amount_shift",
                    original_input=text,
                    modified_input=mod,
                    expected_change=f"amount -> {new_num}",
                    target_field="amount",
                    expected_new_value=new_num,
                )
            )

        # 3. Recipient swap
        recipient_pairs = [("Rahul", "Suresh"), ("Anu", "Meera"), ("Rajan", "Kiran")]
        for old_name, new_name in recipient_pairs:
            if re.search(rf"\b{old_name}", text, flags=re.IGNORECASE):
                mod = re.sub(old_name, new_name, text, count=1, flags=re.IGNORECASE)
                variants.append(
                    CounterfactualCase(
                        perturbation_type="recipient_swap",
                        original_input=text,
                        modified_input=mod,
                        expected_change=f"recipient -> {new_name!r}",
                        target_field="recipient",
                        expected_new_value=new_name,
                    )
                )
                break

        # 4. Date shift
        date_pairs = [
            (r"\btomorrow\b", "today", "today"),
            (r"\bnaale\b", "innu", "today"),
            (r"\bkal\b", "aaj", "today"),
            (r"\btoday\b", "tomorrow", "tomorrow"),
        ]
        for pattern, replacement, expected_date in date_pairs:
            if re.search(pattern, text, flags=re.IGNORECASE):
                mod = re.sub(pattern, replacement, text, count=1, flags=re.IGNORECASE)
                variants.append(
                    CounterfactualCase(
                        perturbation_type="date_shift",
                        original_input=text,
                        modified_input=mod,
                        expected_change=f"date -> {expected_date!r}",
                        target_field="date",
                        expected_new_value=expected_date,
                    )
                )
                break

        # 5. Conditional injection
        lowered = text.lower()
        if any(w in lowered for w in ("ayakk", "roopa", "innu", "naale")):
            cond_mod = f"Call cheythal maathram {text[0].lower() + text[1:]}"
        elif any(w in lowered for w in ("bhej", "rupaye", "kal", "aaj")):
            cond_mod = f"Agar confirm kare toh {text[0].lower() + text[1:]}"
        else:
            cond_mod = f"Only if confirmed, {text[0].lower() + text[1:]}"

        variants.append(
            CounterfactualCase(
                perturbation_type="condition_inject",
                original_input=text,
                modified_input=cond_mod,
                expected_change="polarity -> 'conditional'",
                target_field="polarity",
                expected_new_value="conditional",
            )
        )

        # 6. Meaning-preserving invariance (polite prefix must NOT alter extracted fields)
        variants.append(
            CounterfactualCase(
                perturbation_type="meaning_preserving_invariance",
                original_input=text,
                modified_input=f"Please, {text}",
                expected_change="no change",
                target_field="none",
                expected_new_value=None,
            )
        )

        return variants

    def evaluate_pair(
        self,
        case: CounterfactualCase,
        orig_bundle: ExtractionBundle,
        mod_bundle: ExtractionBundle,
    ) -> CounterfactualResult:
        orig = _primary(orig_bundle)
        mod = _primary(mod_bundle)
        observed = _describe_diff(orig, mod)

        if case.perturbation_type == "meaning_preserving_invariance":
            passed = observed == "no change"
        else:
            actual_val = getattr(mod, case.target_field, None)
            if case.target_field == "amount" and actual_val is not None:
                target_updated = float(actual_val) == float(case.expected_new_value)
            elif isinstance(case.expected_new_value, str) and isinstance(actual_val, str):
                target_updated = actual_val.strip().lower() == case.expected_new_value.strip().lower()
            else:
                target_updated = actual_val == case.expected_new_value

            # Ensure unrelated core fields did not mutate accidentally
            unchanged_ok = True
            for core_field in ("intent", "recipient", "amount"):
                if core_field == case.target_field:
                    continue
                if getattr(orig, core_field) != getattr(mod, core_field):
                    unchanged_ok = False
                    break

            passed = bool(target_updated and unchanged_ok)

        return CounterfactualResult(
            perturbation_type=case.perturbation_type,
            original_input=case.original_input,
            modified_input=case.modified_input,
            expected_change=case.expected_change,
            observed_change=observed,
            passed=passed,
        )

    def run_on_adapter(
        self,
        adapter: BaseModelAdapter,
        text: str,
    ) -> list[CounterfactualResult]:
        """
        Runs all generated counterfactual tests live against a connected model adapter.
        """
        orig_bundle, _ = adapter.extract_actions(text)
        variants = self.generate_variants(text)
        results: list[CounterfactualResult] = []
        for case in variants:
            mod_bundle, _ = adapter.extract_actions(case.modified_input)
            results.append(self.evaluate_pair(case, orig_bundle, mod_bundle))
        return results

    @staticmethod
    def format_markdown_report(results: list[CounterfactualResult]) -> str:
        lines = [
            "### Counterfactual Reliability Report",
            "",
            "| Perturbation | Modified Input | Expected Change | Observed Change | Status |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
        for r in results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(
                f"| `{r.perturbation_type}` | {r.modified_input} | {r.expected_change} | {r.observed_change} | **{status}** |"
            )
        return "\n".join(lines)
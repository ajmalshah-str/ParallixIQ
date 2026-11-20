from typing import Any
from adapters.base import BaseModelAdapter
from storage.audit_db import AuditDatabase
from storage.schemas import DriftReport


class DriftMonitor:
    def __init__(
        self,
        db: AuditDatabase | None = None,
        drop_threshold: float = 3.0,
    ):
        self.db = db if db is not None else AuditDatabase()
        self.drop_threshold = abs(drop_threshold)

    def record_and_evaluate(
        self,
        model_id: str,
        dataset_version: str,
        total_cases: int,
        exact_match_rate: float,
        field_precision: float,
    ) -> DriftReport:
        """
        Logs a new benchmark run to SQLite and compares it against stored
        historical runs for the same model and dataset version.
        """
        history = self.db.get_recent_runs(model_id=model_id, limit=25)
        same_suite = [r for r in history if r["dataset_version"] == dataset_version]
        reference_runs = same_suite if same_suite else history

        self.db.log_benchmark_run(
            model_id=model_id,
            dataset_version=dataset_version,
            total_cases=total_cases,
            exact_match_rate=exact_match_rate,
            field_precision=field_precision,
        )

        if not reference_runs:
            return DriftReport(
                model_id=model_id,
                baseline_accuracy=round(exact_match_rate, 2),
                current_accuracy=round(exact_match_rate, 2),
                delta=0.0,
                drift_detected=False,
                alert_message=None,
            )

        # Oldest recorded run in window serves as baseline; newest prior run catches sudden drops
        baseline_run = reference_runs[-1]
        previous_run = reference_runs[0]

        baseline_acc = float(baseline_run["exact_match_rate"])
        previous_acc = float(previous_run["exact_match_rate"])

        baseline_delta = round(exact_match_rate - baseline_acc, 2)
        step_delta = round(exact_match_rate - previous_acc, 2)

        baseline_breach = baseline_delta < -self.drop_threshold
        step_breach = step_delta < -self.drop_threshold
        drift_detected = baseline_breach or step_breach

        alert_message: str | None = None
        if drift_detected:
            alert_message = (
                f"DRIFT ALERT for '{model_id}' on '{dataset_version}': "
                f"accuracy dropped to {exact_match_rate:.2f}% "
                f"(baseline={baseline_acc:.2f}%, delta={baseline_delta:+.2f}%, "
                f"last_run_delta={step_delta:+.2f}%, threshold=-{self.drop_threshold:.2f}%)."
            )

        return DriftReport(
            model_id=model_id,
            baseline_accuracy=round(baseline_acc, 2),
            current_accuracy=round(exact_match_rate, 2),
            delta=baseline_delta,
            drift_detected=drift_detected,
            alert_message=alert_message,
        )

    def check_consistency(
        self,
        adapter: BaseModelAdapter,
        prompts: list[str],
        repeats: int = 2,
    ) -> dict[str, Any]:
        """
        Tests output stability by sending identical prompts multiple times
        and checking whether extracted action structures stay consistent.
        """
        if not prompts or repeats < 2:
            return {
                "model_id": adapter.model_id,
                "tested_prompts": len(prompts),
                "consistent_prompts": len(prompts),
                "consistency_rate": 100.0,
                "inconsistent_inputs": [],
            }

        consistent_count = 0
        inconsistent_inputs: list[str] = []

        for text in prompts:
            snapshots: list[str] = []
            for _ in range(repeats):
                bundle, _ = adapter.extract_actions(text)
                snapshots.append(bundle.model_dump_json())

            if len(set(snapshots)) == 1:
                consistent_count += 1
            else:
                inconsistent_inputs.append(text)

        rate = round((consistent_count / len(prompts)) * 100, 2)
        return {
            "model_id": adapter.model_id,
            "tested_prompts": len(prompts),
            "consistent_prompts": consistent_count,
            "consistency_rate": rate,
            "inconsistent_inputs": inconsistent_inputs,
        }

    def get_trend_summary(self, model_id: str, limit: int = 10) -> dict[str, Any]:
        """
        Returns chronological run metrics and overall trend direction from SQLite.
        """
        recent = self.db.get_recent_runs(model_id=model_id, limit=limit)
        chronological = list(reversed(recent))

        if not chronological:
            return {
                "model_id": model_id,
                "total_runs": 0,
                "trend": "insufficient_data",
                "runs": [],
            }

        rates = [float(r["exact_match_rate"]) for r in chronological]
        if len(rates) == 1:
            trend = "baseline_established"
        else:
            net_change = rates[-1] - rates[0]
            if net_change < -self.drop_threshold:
                trend = "degrading"
            elif net_change > self.drop_threshold:
                trend = "improving"
            else:
                trend = "stable"

        return {
            "model_id": model_id,
            "total_runs": len(chronological),
            "min_accuracy": min(rates),
            "max_accuracy": max(rates),
            "latest_accuracy": rates[-1],
            "trend": trend,
            "runs": [
                {
                    "run_id": r["run_id"],
                    "dataset_version": r["dataset_version"],
                    "exact_match_rate": r["exact_match_rate"],
                    "field_precision": r["field_precision"],
                    "created_at": r["created_at"],
                }
                for r in chronological
            ],
        }
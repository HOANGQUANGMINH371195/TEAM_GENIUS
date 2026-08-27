import json
from pathlib import Path

from src.services.calculator import calculate_bhyt_benefit


def test_calculator_golden_fixture_has_100_exact_cases():
    path = Path(__file__).parents[1] / "eval" / "cases" / "calculator-golden-v1.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows[0]["manifest"]["cases"] == len(rows) - 1 == 100
    for case in rows[1:]:
        result = calculate_bhyt_benefit(
            covered_cost=case["covered_cost"],
            base_rate_percent=case["base_rate_percent"],
            copayment_spend=case["copayment_spend"],
            copayment_threshold=case["copayment_threshold"],
            continuous_years=case["continuous_years"],
            required_years=case["required_years"],
            threshold_rate_percent=case["threshold_rate_percent"],
        ).as_dict()
        for key in ("threshold_met", "applied_rate_percent", "insurer_pays", "patient_pays"):
            assert result[key] == case["expected"][key], case["case_id"]

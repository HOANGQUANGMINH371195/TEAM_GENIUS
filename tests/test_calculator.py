from decimal import Decimal

import pytest

from src.models.schemas import (
    BenefitCalculationRequest,
    BenefitCalculationScenario,
    BenefitCalculationScenariosRequest,
)
from src.services.calculator import (
    CalculationInputError,
    calculate_bhyt_benefit,
    copayment_threshold_met,
)


def test_threshold_uses_exact_decimal_arithmetic():
    result = calculate_bhyt_benefit(
        covered_cost="1000000.01",
        base_rate_percent="80",
        copayment_spend="6000000",
        copayment_threshold="6000000",
        continuous_years="5",
        rule_provenance=("verified-rule",),
    )
    assert result.threshold_met is True
    assert result.insurer_pays == Decimal("1000000.01")
    assert result.patient_pays == Decimal("0.00")
    assert result.as_dict()["formula_id"] == "bhyt.covered_cost.v1"


def test_missing_duration_fails_closed_when_threshold_is_used():
    with pytest.raises(CalculationInputError, match="continuous_years"):
        calculate_bhyt_benefit(
            covered_cost="100",
            base_rate_percent="80",
            copayment_threshold="10",
        )


def test_base_rate_is_used_when_threshold_not_met():
    result = calculate_bhyt_benefit(
        covered_cost="100",
        base_rate_percent="80",
        copayment_spend="9",
        copayment_threshold="10",
        continuous_years="4",
    )
    assert result.threshold_met is False
    assert result.insurer_pays == Decimal("80.00")
    assert result.patient_pays == Decimal("20.00")


def test_scenario_request_is_bounded_and_structured():
    calculation = BenefitCalculationRequest(covered_cost="100", base_rate_percent="80")
    request = BenefitCalculationScenariosRequest(
        scenarios=[BenefitCalculationScenario(label="base", calculation=calculation)]
    )
    assert request.scenarios[0].calculation.covered_cost == "100"


def test_threshold_formula_is_registered_and_exact():
    assert copayment_threshold_met(
        copayment_spend="10.00", copayment_threshold="10", continuous_years="5"
    )

"""Exact arithmetic for verified BHYT table facts.

The calculator accepts rule values extracted and verified elsewhere.  It does
not contain a legal-rate lookup table, so stale law cannot be silently baked
into application code.  All monetary and percentage operations use Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


class CalculationInputError(ValueError):
    """A required or malformed calculation input was supplied."""


FORMULA_REGISTRY: dict[str, dict[str, object]] = {
    "bhyt.covered_cost.v1": {
        "description": "covered cost multiplied by verified entitlement rate",
        "inputs": ("covered_cost", "base_rate_percent", "copayment_threshold", "continuous_years"),
        "rounding": "Decimal cents, ROUND_HALF_UP",
    }
}


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CalculationInputError(f"{name} must be a decimal number") from exc
    if not result.is_finite() or result < 0:
        raise CalculationInputError(f"{name} must be finite and non-negative")
    return result


def _rate(value: object, name: str) -> Decimal:
    result = _decimal(value, name)
    if result > 100:
        raise CalculationInputError(f"{name} must be between 0 and 100")
    return result / Decimal("100")


@dataclass(frozen=True)
class BenefitCalculation:
    covered_cost: Decimal
    applied_rate: Decimal
    insurer_pays: Decimal
    patient_pays: Decimal
    threshold_met: bool
    formula_id: str
    provenance: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        def money(value: Decimal) -> str:
            return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        return {
            "covered_cost": money(self.covered_cost),
            "applied_rate_percent": str((self.applied_rate * 100).quantize(Decimal("0.01"))),
            "insurer_pays": money(self.insurer_pays),
            "patient_pays": money(self.patient_pays),
            "threshold_met": self.threshold_met,
            "formula_id": self.formula_id,
            "provenance": list(self.provenance),
        }


def calculate_bhyt_benefit(
    *,
    covered_cost: object,
    base_rate_percent: object,
    copayment_spend: object = 0,
    copayment_threshold: object | None = None,
    continuous_years: object | None = None,
    required_years: object = 5,
    threshold_rate_percent: object = 100,
    rule_provenance: tuple[str, ...] = (),
) -> BenefitCalculation:
    """Calculate a covered amount from already-verified rule facts.

    A threshold override is applied only when both threshold and participation
    duration are supplied.  Missing material facts fail closed rather than
    assuming that the five-year rule applies.
    """
    cost = _decimal(covered_cost, "covered_cost")
    base_rate = _rate(base_rate_percent, "base_rate_percent")
    spend = _decimal(copayment_spend, "copayment_spend")
    threshold_met = False
    applied_rate = base_rate
    if copayment_threshold is not None:
        if continuous_years is None:
            raise CalculationInputError("continuous_years is required when copayment_threshold is supplied")
        threshold = _decimal(copayment_threshold, "copayment_threshold")
        years = _decimal(continuous_years, "continuous_years")
        required = _decimal(required_years, "required_years")
        if required <= 0:
            raise CalculationInputError("required_years must be positive")
        threshold_met = spend >= threshold and years >= required
        if threshold_met:
            applied_rate = _rate(threshold_rate_percent, "threshold_rate_percent")
    insurer = (cost * applied_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    patient = (cost - insurer).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    formula_id = "bhyt.covered_cost.v1"
    if formula_id not in FORMULA_REGISTRY:
        raise CalculationInputError("formula is not registered")
    return BenefitCalculation(
        covered_cost=cost,
        applied_rate=applied_rate,
        insurer_pays=insurer,
        patient_pays=patient,
        threshold_met=threshold_met,
        formula_id=formula_id,
        provenance=tuple(rule_provenance),
    )

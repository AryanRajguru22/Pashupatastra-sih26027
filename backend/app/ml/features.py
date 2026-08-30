"""
Feature extraction for the railway maintenance priority/risk scorer.

The scorer is intentionally deterministic and explainable.

These features are kept outside BlockCandidate so that we do not
modify the shared contract without team approval.
"""

from __future__ import annotations

from typing import Any, Mapping


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    """Keep a numeric value inside the [0, 1] range."""
    return max(minimum, min(maximum, float(value)))


def normalize_level(value: Any) -> float:
    """
    Convert a qualitative severity/criticality level to [0, 1].

    Supported values:
        LOW       -> 0.25
        MEDIUM    -> 0.50
        HIGH      -> 0.75
        CRITICAL  -> 1.00
    """

    if isinstance(value, (int, float)):
        return clamp(float(value))

    levels = {
        "LOW": 0.25,
        "MEDIUM": 0.50,
        "HIGH": 0.75,
        "CRITICAL": 1.00,
    }

    key = str(value).strip().upper()

    if key not in levels:
        raise ValueError(f"Unsupported level: {value}")

    return levels[key]


def normalize_probability(value: Any) -> float:
    """
    Convert probability/failure rate to [0, 1].

    Accepts either:
        0.0 to 1.0
    or:
        0 to 100
    """

    value = float(value)

    if value < 0:
        raise ValueError("Probability cannot be negative")

    if value > 1:
        value /= 100.0

    return clamp(value)


def normalize_days_overdue(
    days: Any,
    saturation_days: int = 30,
) -> float:
    """
    Convert overdue days into [0, 1].

    0 days  -> 0
    30 days -> 1
    30+     -> 1
    """

    days = float(days)

    if days < 0:
        raise ValueError("days_overdue cannot be negative")

    if saturation_days <= 0:
        raise ValueError("saturation_days must be positive")

    return clamp(days / saturation_days)


def normalize_duration(
    duration_minutes: Any,
    reference_minutes: int = 240,
) -> float:
    """
    Normalize maintenance duration to [0, 1].

    0 minutes   -> 0
    240+ minutes -> 1
    """

    duration = float(duration_minutes)

    if duration < 0:
        raise ValueError("maintenance_duration cannot be negative")

    if reference_minutes <= 0:
        raise ValueError("reference_minutes must be positive")

    return clamp(duration / reference_minutes)


def extract_features(block: Mapping[str, Any]) -> dict[str, float]:
    """
    Extract the domain attributes used by the scorer.

    These are intentionally supplied as scoring metadata rather than
    added to BlockCandidate.

    Required fields:

        asset_criticality
        defect_severity
        days_overdue
        failure_probability
        train_impact
        maintenance_duration
        historical_failure_rate
    """

    return {
        "asset_criticality": normalize_level(
            block["asset_criticality"]
        ),
        "defect_severity": normalize_level(
            block["defect_severity"]
        ),
        "days_overdue": normalize_days_overdue(
            block["days_overdue"]
        ),
        "failure_probability": normalize_probability(
            block["failure_probability"]
        ),
        "train_impact": normalize_level(
            block["train_impact"]
        ),
        "maintenance_duration": normalize_duration(
            block["maintenance_duration"]
        ),
        "historical_failure_rate": normalize_probability(
            block["historical_failure_rate"]
        ),
    }
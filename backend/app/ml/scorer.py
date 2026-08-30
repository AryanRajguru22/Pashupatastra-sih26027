"""
Deterministic railway maintenance priority/risk scorer.

This is the Phase 1 explainable baseline.

The scorer does NOT create a schedule.

It only produces:
    - priority_score
    - risk_score
    - explanations

The CP-SAT optimizer remains responsible for deciding whether and
when a block can actually be scheduled.
"""

from __future__ import annotations

from typing import Any, Mapping

from .features import extract_features


# Weights must sum to 1.0.
#
# Risk focuses primarily on the possibility/severity of asset failure.
RISK_WEIGHTS = {
    "asset_criticality": 0.20,
    "defect_severity": 0.25,
    "days_overdue": 0.10,
    "failure_probability": 0.20,
    "train_impact": 0.10,
    "maintenance_duration": 0.05,
    "historical_failure_rate": 0.10,
}


# Priority focuses on how urgently the maintenance should be performed.
PRIORITY_WEIGHTS = {
    "asset_criticality": 0.20,
    "defect_severity": 0.20,
    "days_overdue": 0.20,
    "failure_probability": 0.10,
    "train_impact": 0.15,
    "maintenance_duration": 0.05,
    "historical_failure_rate": 0.10,
}


FEATURE_LABELS = {
    "asset_criticality": "High asset criticality",
    "defect_severity": "Severe defect",
    "days_overdue": "Maintenance overdue",
    "failure_probability": "High failure probability",
    "train_impact": "High operational/train impact",
    "maintenance_duration": "Long maintenance duration",
    "historical_failure_rate": "High historical failure rate",
}


def weighted_score(
    features: Mapping[str, float],
    weights: Mapping[str, float],
) -> float:
    """Calculate a weighted score between 0 and 1."""

    score = sum(
        features[name] * weight
        for name, weight in weights.items()
    )

    return max(0.0, min(1.0, score))


def score_level(score: float) -> str:
    """Convert a numeric score into LOW/MEDIUM/HIGH."""

    if score >= 0.75:
        return "HIGH"

    if score >= 0.50:
        return "MEDIUM"

    return "LOW"


def build_explanation(
    features: Mapping[str, float],
) -> list[str]:
    """
    Return the strongest scoring contributors.

    This makes the model explainable for the operator dashboard.
    """

    ranked_features = sorted(
        features.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    explanations = []

    for feature_name, value in ranked_features:

        if value >= 0.75:
            explanations.append(
                FEATURE_LABELS[feature_name]
            )

    # Always return at least one explanation.
    if not explanations and ranked_features:
        feature_name = ranked_features[0][0]

        explanations.append(
            FEATURE_LABELS[feature_name]
        )

    return explanations[:4]


def score_block(
    block: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Score one maintenance block.

    Returns:

        priority_score
        risk_score
        priority_level
        risk_level
        explanation
        features
    """

    features = extract_features(block)

    risk_score = weighted_score(
        features,
        RISK_WEIGHTS,
    )

    priority_score = weighted_score(
        features,
        PRIORITY_WEIGHTS,
    )

    return {
        "priority_score": round(priority_score, 4),
        "risk_score": round(risk_score, 4),

        "priority_level": score_level(
            priority_score
        ),

        "risk_level": score_level(
            risk_score
        ),

        "explanation": build_explanation(
            features
        ),

        "features": features,
    }
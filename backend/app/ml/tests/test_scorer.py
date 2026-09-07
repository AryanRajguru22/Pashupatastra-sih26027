from backend.app.ml.scorer import score_block


def make_block(**overrides):
    """Create a synthetic scoring input."""

    block = {
        "asset_criticality": "MEDIUM",
        "defect_severity": "MEDIUM",
        "days_overdue": 5,
        "failure_probability": 0.30,
        "train_impact": "MEDIUM",
        "maintenance_duration": 60,
        "historical_failure_rate": 0.20,
    }

    block.update(overrides)

    return block


def test_scores_are_deterministic():
    block = make_block(
        asset_criticality="HIGH",
        defect_severity="HIGH",
        days_overdue=20,
        failure_probability=0.80,
        train_impact="HIGH",
        maintenance_duration=120,
        historical_failure_rate=0.60,
    )

    first = score_block(block)
    second = score_block(block)

    assert first == second


def test_scores_are_between_zero_and_one():
    result = score_block(
        make_block()
    )

    assert 0 <= result["priority_score"] <= 1
    assert 0 <= result["risk_score"] <= 1


def test_higher_criticality_increases_risk():
    low = score_block(
        make_block(asset_criticality="LOW")
    )

    high = score_block(
        make_block(asset_criticality="HIGH")
    )

    assert high["risk_score"] > low["risk_score"]


def test_greater_severity_increases_risk():
    low = score_block(
        make_block(defect_severity="LOW")
    )

    high = score_block(
        make_block(defect_severity="HIGH")
    )

    assert high["risk_score"] > low["risk_score"]


def test_overdue_maintenance_increases_priority():
    not_overdue = score_block(
        make_block(days_overdue=0)
    )

    overdue = score_block(
        make_block(days_overdue=30)
    )

    assert overdue["priority_score"] > not_overdue["priority_score"]


def test_high_failure_probability_increases_risk():
    low = score_block(
        make_block(failure_probability=0.10)
    )

    high = score_block(
        make_block(failure_probability=0.90)
    )

    assert high["risk_score"] > low["risk_score"]


def test_high_train_impact_increases_priority():
    low = score_block(
        make_block(train_impact="LOW")
    )

    high = score_block(
        make_block(train_impact="HIGH")
    )

    assert high["priority_score"] > low["priority_score"]


def test_low_risk_is_lower_than_high_risk():
    low_risk = score_block(
        make_block(
            asset_criticality="LOW",
            defect_severity="LOW",
            days_overdue=0,
            failure_probability=0.05,
            train_impact="LOW",
            maintenance_duration=30,
            historical_failure_rate=0.05,
        )
    )

    high_risk = score_block(
        make_block(
            asset_criticality="CRITICAL",
            defect_severity="CRITICAL",
            days_overdue=30,
            failure_probability=1.0,
            train_impact="CRITICAL",
            maintenance_duration=240,
            historical_failure_rate=1.0,
        )
    )

    assert low_risk["risk_score"] < high_risk["risk_score"]
    assert low_risk["priority_score"] < high_risk["priority_score"]


def test_explanation_is_generated():
    result = score_block(
        make_block(
            asset_criticality="CRITICAL",
            defect_severity="CRITICAL",
            days_overdue=30,
            failure_probability=1.0,
            train_impact="CRITICAL",
        )
    )

    assert result["explanation"]

    assert (
        "High asset criticality"
        in result["explanation"]
    )


def test_negative_overdue_days_are_rejected():
    import pytest

    with pytest.raises(ValueError):
        score_block(
            make_block(days_overdue=-1)
        )


def test_negative_probability_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        score_block(
            make_block(failure_probability=-0.1)
        )

def test_exact_score_regression():
    """Lock the current deterministic weighted-scoring calculation."""
    result = score_block(
        make_block(
            asset_criticality=0.80,
            defect_severity=0.60,
            days_overdue=30,
            failure_probability=0.80,
            train_impact=0.70,
            maintenance_duration=120,
            historical_failure_rate=0.50,
        )
    )

    # days_overdue -> 1.0 and duration -> 0.5 in the current scorer.
    assert result["risk_score"] == 0.715
    assert result["priority_score"] == 0.74


def test_probability_percentage_input_is_normalized():
    decimal = score_block(make_block(failure_probability=0.80))
    percentage = score_block(make_block(failure_probability=80))

    assert percentage["features"]["failure_probability"] == 0.80
    assert percentage["risk_score"] == decimal["risk_score"]
    assert percentage["priority_score"] == decimal["priority_score"]

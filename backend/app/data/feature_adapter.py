"""Domain-to-Scoring Feature Adapter for Pashupatastra.
Translates railway domain models (Asset, TrackSegment, MaintenanceDefect)
into the 7 standardized numeric features consumed by Ayush's scoring engine.
"""

from __future__ import annotations
from typing import Any, Dict, Optional

from backend.app.data.models import (
    Asset,
    AssetType,
    DefectSeverity,
    RouteClassification,
    TrackSegment,
)


DEFECT_SEVERITY_WEIGHTS: Dict[str, float] = {
    DefectSeverity.NONE.value: 0.0,
    DefectSeverity.MINOR.value: 0.25,
    DefectSeverity.MODERATE.value: 0.60,
    DefectSeverity.CRITICAL.value: 1.00,
}

ROUTE_CLASS_WEIGHTS: Dict[str, float] = {
    RouteClassification.GROUP_A.value: 0.95,  # Trunk High Speed (<= 160 km/h)
    RouteClassification.GROUP_B.value: 0.80,  # Mainline (<= 130 km/h)
    RouteClassification.GROUP_C.value: 0.70,  # Suburban
    RouteClassification.GROUP_D.value: 0.40,  # Medium speed
    RouteClassification.GROUP_E.value: 0.20,  # Branch lines & sidings
}

WORK_TYPE_DEFAULT_DURATIONS: Dict[str, int] = {
    "ROUTINE_INSPECTION": 45,
    "SIGNALLING_INTERLOCKING": 90,
    "OHE_MAINTENANCE": 90,
    "BALLAST_TAMPING": 120,
    "TRACK_RENEWAL": 240,
    "EMERGENCY_REPAIR": 90,
}


class ScoringFeatureAdapter:
    """Extracts, normalizes, and validates the 7 scoring inputs from railway domain objects."""

    @staticmethod
    def calculate_asset_criticality(
        asset: Optional[Asset] = None,
        track: Optional[TrackSegment] = None,
        explicit_val: Optional[float] = None,
    ) -> float:
        """Calculates normalized asset criticality [0.0, 1.0]."""
        if explicit_val is not None:
            return max(0.0, min(1.0, float(explicit_val)))

        if asset and hasattr(asset, "criticality") and asset.criticality > 0:
            return max(0.0, min(1.0, float(asset.criticality)))

        speed_factor = 0.8
        density_factor = 0.8
        route_factor = 0.8

        if track:
            speed_factor = min(1.0, track.speed_limit_kmh / 160.0)
            density_factor = min(1.0, track.daily_train_density / 120.0)
            route_factor = ROUTE_CLASS_WEIGHTS.get(track.route_classification, 0.8)

        criticality = 0.4 * route_factor + 0.3 * speed_factor + 0.3 * density_factor
        return round(max(0.0, min(1.0, criticality)), 3)

    @staticmethod
    def map_defect_severity(severity_str: str) -> float:
        """Maps categorical DefectSeverity to normalized float [0.0, 1.0]."""
        sev_upper = str(severity_str).upper()
        return DEFECT_SEVERITY_WEIGHTS.get(sev_upper, 0.25)

    @staticmethod
    def normalize_days_overdue(days_overdue: int, max_cap: int = 90) -> float:
        """Normalizes days overdue into [0.0, 1.0]."""
        return round(max(0.0, min(1.0, max(0, days_overdue) / float(max_cap))), 3)

    @staticmethod
    def calculate_failure_probability(
        condition_score: float = 0.8,
        defect_severity_val: float = 0.0,
        gross_million_tonnes: float = 250.0,
    ) -> float:
        """Estimates failure probability [0.0, 1.0] from condition, defect, and traffic load (GMT)."""
        base_failure = 1.0 - max(0.0, min(1.0, condition_score))
        defect_boost = 0.4 * defect_severity_val
        gmt_stress = min(0.2, (gross_million_tonnes / 500.0) * 0.2)

        prob = base_failure * 0.5 + defect_boost + gmt_stress
        return round(max(0.02, min(0.99, prob)), 3)

    @staticmethod
    def calculate_train_impact(
        daily_train_density: int = 110,
        duration_minutes: int = 90,
        is_peak_window: bool = False,
    ) -> float:
        """Calculates operational traffic impact index [0.0, 1.0]."""
        density_norm = min(1.0, daily_train_density / 120.0)
        duration_norm = min(1.0, duration_minutes / 240.0)
        peak_penalty = 0.2 if is_peak_window else 0.0

        impact = 0.5 * density_norm + 0.3 * duration_norm + peak_penalty
        return round(max(0.05, min(1.0, impact)), 3)

    @staticmethod
    def normalize_maintenance_duration(duration_minutes: int, max_cap: int = 360) -> float:
        """Normalizes maintenance duration [0.0, 1.0]."""
        return round(max(0.0, min(1.0, max(15, duration_minutes) / float(max_cap))), 3)

    @staticmethod
    def calculate_historical_failure_rate(failure_count_3yr: int, max_cap: int = 6) -> float:
        """Calculates normalized historical incident rate [0.0, 1.0]."""
        return round(max(0.0, min(1.0, max(0, failure_count_3yr) / float(max_cap))), 3)

    @classmethod
    def extract_features(
        cls,
        asset: Optional[Asset] = None,
        track: Optional[TrackSegment] = None,
        work_type: str = "ROUTINE_INSPECTION",
        duration_minutes: Optional[int] = None,
        days_overdue: int = 0,
        explicit_defect_severity: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extracts the complete 7 scoring features from domain objects for Ayush's engine.

        Returns a dictionary with raw and normalized features:
        - asset_criticality: float [0.0, 1.0]
        - defect_severity: float [0.0, 1.0]
        - days_overdue: int (raw) & days_overdue_norm: float [0.0, 1.0]
        - failure_probability: float [0.0, 1.0]
        - train_impact: float [0.0, 1.0]
        - maintenance_duration: int (minutes) & maintenance_duration_norm: float [0.0, 1.0]
        - historical_failure_rate: float [0.0, 1.0]
        """
        dur = duration_minutes or WORK_TYPE_DEFAULT_DURATIONS.get(work_type, 90)
        sev_str = explicit_defect_severity or (asset.defect_severity if asset else DefectSeverity.NONE.value)
        sev_val = cls.map_defect_severity(sev_str)

        crit = cls.calculate_asset_criticality(asset, track)
        days_norm = cls.normalize_days_overdue(days_overdue)

        cond = asset.condition_score if asset else 0.8
        gmt = asset.gross_million_tonnes if asset else 250.0
        fail_prob = cls.calculate_failure_probability(cond, sev_val, gmt)

        density = track.daily_train_density if track else 110
        train_imp = cls.calculate_train_impact(density, dur)
        dur_norm = cls.normalize_maintenance_duration(dur)

        hist_count = asset.historical_failure_count_3yr if asset else 1
        hist_rate = cls.calculate_historical_failure_rate(hist_count)

        # Baseline formula scores for reference & validation
        risk_score = round(
            0.30 * sev_val + 0.30 * fail_prob + 0.20 * days_norm + 0.20 * hist_rate,
            3,
        )
        priority_score = round(
            0.35 * risk_score + 0.25 * crit + 0.20 * train_imp + 0.20 * (1.0 - dur_norm),
            3,
        )

        return {
            "asset_criticality": crit,
            "defect_severity": sev_val,
            "defect_severity_name": sev_str,
            "days_overdue": days_overdue,
            "days_overdue_norm": days_norm,
            "failure_probability": fail_prob,
            "train_impact": train_imp,
            "maintenance_duration": dur,
            "maintenance_duration_norm": dur_norm,
            "historical_failure_rate": hist_rate,
            "baseline_risk_score": risk_score,
            "baseline_priority_score": priority_score,
        }

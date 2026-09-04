"""Unit tests for Railway Domain models, Feature Adapter, and Fixtures.
Validates Darshini's Phase 1 foundation deliverables.
"""

from __future__ import annotations
import json
import os
import unittest

from contracts.schemas import (
    BlockCandidate,
    DisruptionEvent,
    OptimizationRequest,
    WorkType,
)
from backend.app.data.models import (
    Asset,
    AssetType,
    Corridor,
    DefectSeverity,
    MaintenanceDefect,
    RouteClassification,
    TrackSegment,
)
from backend.app.data.feature_adapter import ScoringFeatureAdapter
from backend.app.data.generator import CorridorDataGenerator


class TestDomainModelsAndFeatureAdapter(unittest.TestCase):
    """Test suite for domain models and feature adapter."""

    def test_asset_serialization(self):
        """Verify Asset dataclass serializes and deserializes cleanly."""
        defect = MaintenanceDefect(
            defect_id="DEF-001",
            asset_id="AST-001",
            detected_date="2026-08-15",
            severity=DefectSeverity.CRITICAL.value,
            defect_type="USFD_FLAW",
        )
        asset = Asset(
            asset_id="AST-001",
            name="Rail Section KM 14",
            asset_type=AssetType.RAIL_SECTION.value,
            track_id="UP-1",
            km_location=14.25,
            criticality=0.92,
            condition_score=0.45,
            last_maintained_days_ago=75,
            defect_severity=DefectSeverity.CRITICAL.value,
            gross_million_tonnes=320.0,
            defects=[defect],
        )

        d = asset.to_dict()
        self.assertEqual(d["asset_id"], "AST-001")
        self.assertEqual(d["defect_severity"], DefectSeverity.CRITICAL.value)
        self.assertEqual(len(d["defects"]), 1)

        restored = Asset.from_dict(d)
        self.assertEqual(restored.asset_id, asset.asset_id)
        self.assertEqual(restored.criticality, asset.criticality)
        self.assertEqual(len(restored.defects), 1)

    def test_track_segment_serialization(self):
        """Verify TrackSegment dataclass serialization."""
        track = TrackSegment(
            track_id="UP-1",
            corridor_id="CORR-NDLS-AGC",
            section_id="SEC-01",
            section_name="NDLS-Mathura Up Line",
            direction="UP",
            km_start=0.0,
            km_end=50.0,
            speed_limit_kmh=160,
            electrified=True,
            daily_train_density=110,
            route_classification=RouteClassification.GROUP_A.value,
        )
        d = track.to_dict()
        self.assertEqual(d["track_id"], "UP-1")
        self.assertEqual(d["speed_limit_kmh"], 160)

        restored = TrackSegment.from_dict(d)
        self.assertEqual(restored.track_id, "UP-1")
        self.assertEqual(restored.daily_train_density, 110)

    def test_feature_adapter_bounds(self):
        """Verify feature adapter extracts 7 features strictly within [0.0, 1.0]."""
        track = TrackSegment(
            track_id="DOWN-1",
            corridor_id="CORR-01",
            section_id="SEC-01",
            section_name="Down Main",
            direction="DOWN",
            km_start=0.0,
            km_end=40.0,
        )
        asset = Asset(
            asset_id="AST-002",
            name="OHE Mast KM 8",
            asset_type=AssetType.OHE_MAST.value,
            track_id="DOWN-1",
            km_location=8.2,
            criticality=0.88,
            condition_score=0.60,
            last_maintained_days_ago=45,
            defect_severity=DefectSeverity.MODERATE.value,
        )

        features = ScoringFeatureAdapter.extract_features(
            asset=asset,
            track=track,
            work_type=WorkType.OHE_MAINTENANCE.value,
            duration_minutes=90,
            days_overdue=15,
        )

        self.assertGreaterEqual(features["asset_criticality"], 0.0)
        self.assertLessEqual(features["asset_criticality"], 1.0)
        self.assertGreaterEqual(features["defect_severity"], 0.0)
        self.assertLessEqual(features["defect_severity"], 1.0)
        self.assertGreaterEqual(features["days_overdue_norm"], 0.0)
        self.assertLessEqual(features["days_overdue_norm"], 1.0)
        self.assertGreaterEqual(features["failure_probability"], 0.0)
        self.assertLessEqual(features["failure_probability"], 1.0)
        self.assertGreaterEqual(features["train_impact"], 0.0)
        self.assertLessEqual(features["train_impact"], 1.0)
        self.assertGreaterEqual(features["maintenance_duration_norm"], 0.0)
        self.assertLessEqual(features["maintenance_duration_norm"], 1.0)
        self.assertGreaterEqual(features["historical_failure_rate"], 0.0)
        self.assertLessEqual(features["historical_failure_rate"], 1.0)

        # Baseline scores
        self.assertGreaterEqual(features["baseline_risk_score"], 0.0)
        self.assertLessEqual(features["baseline_risk_score"], 1.0)
        self.assertGreaterEqual(features["baseline_priority_score"], 0.0)
        self.assertLessEqual(features["baseline_priority_score"], 1.0)


class TestFixturesValidity(unittest.TestCase):
    """Validates that all JSON fixtures strictly conform to system contracts."""

    def setUp(self):
        self.fixtures_dir = os.path.join(
            os.path.dirname(__file__), "..", "app", "data", "fixtures"
        )

    def test_golden_scenario_fixture(self):
        """Verify Golden Scenario fixture loads and validates against OptimizationRequest."""
        golden_path = os.path.join(self.fixtures_dir, "golden_scenario.json")
        self.assertTrue(os.path.exists(golden_path), "golden_scenario.json must exist")

        with open(golden_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("scenario_id", data)
        self.assertIn("initial_request", data)
        self.assertIn("disruption_event", data)

        # Validate request contract
        req = OptimizationRequest.from_dict(data["initial_request"])
        self.assertEqual(req.corridor_id, "CORR-NDLS-AGC")
        self.assertEqual(len(req.tracks), 2)
        self.assertEqual(len(req.candidates), 12)
        self.assertEqual(len(req.possession_windows), 6)

        # Verify candidate scoring features present
        for c in req.candidates:
            self.assertIn("scoring_features", c.metadata)
            sf = c.metadata["scoring_features"]
            self.assertIn("asset_criticality", sf)
            self.assertIn("defect_severity", sf)
            self.assertIn("days_overdue", sf)
            self.assertIn("failure_probability", sf)
            self.assertIn("train_impact", sf)
            self.assertIn("maintenance_duration", sf)
            self.assertIn("historical_failure_rate", sf)

        # Validate disruption event contract
        disr = DisruptionEvent.from_dict(data["disruption_event"])
        self.assertEqual(disr.disruption_type, "TRACK_UNAVAILABLE")
        self.assertEqual(disr.track_id, "UP-1")

    def test_disruption_scenarios_fixture(self):
        """Verify 5 disruption scenarios fixture structure and contract conformance."""
        disr_path = os.path.join(self.fixtures_dir, "disruption_scenarios.json")
        self.assertTrue(os.path.exists(disr_path), "disruption_scenarios.json must exist")

        with open(disr_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("scenarios", data)
        scenarios = data["scenarios"]
        self.assertEqual(len(scenarios), 5)

        expected_types = [
            "TRACK_UNAVAILABLE",
            "EMERGENCY_WORK",
            "POSSESSION_CURTAILMENT",
            "ASSET_CONDITION_DETERIORATION",
            "INFEASIBLE_SCENARIO",
        ]

        found_types = []
        for scn in scenarios:
            self.assertIn("scenario_id", scn)
            self.assertIn("disruption_event", scn)
            disr = DisruptionEvent.from_dict(scn["disruption_event"])
            found_types.append(disr.disruption_type)

        self.assertEqual(sorted(found_types), sorted(expected_types))

    def test_standard_corridor_fixtures(self):
        """Verify corridor_a, corridor_b, corridor_c fixture files."""
        for filename in ["corridor_a_blocks.json", "corridor_b_dense.json", "corridor_c_disrupted.json"]:
            path = os.path.join(self.fixtures_dir, filename)
            self.assertTrue(os.path.exists(path), f"Fixture {filename} must exist")

            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            req = OptimizationRequest.from_dict(data)
            self.assertGreater(len(req.candidates), 0)
            self.assertGreater(len(req.possession_windows), 0)


if __name__ == "__main__":
    unittest.main()

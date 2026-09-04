"""Unit and Integration Tests for Railway Domain Data, Feature Adapter, and Fixtures.
Validates Darshini's Phase 1 domain foundation and schema conformance.
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
    """Test suite for domain models and feature adapter extraction."""

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

    def test_multi_asset_per_track_topology(self):
        """Verify the domain rule: a single track segment can host multiple distinct assets."""
        gen = CorridorDataGenerator(seed=42)
        tracks = gen.generate_corridor_tracks("CORR-TEST", num_tracks=2)
        assets = gen.generate_assets(tracks, num_assets_per_track=8)

        self.assertEqual(len(tracks), 2)
        self.assertEqual(len(assets), 16)

        # Map assets by track_id
        track_asset_map = {}
        for a in assets:
            track_asset_map.setdefault(a.track_id, []).append(a)

        self.assertIn("UP-1", track_asset_map)
        self.assertIn("DOWN-1", track_asset_map)
        self.assertEqual(len(track_asset_map["UP-1"]), 8)
        self.assertEqual(len(track_asset_map["DOWN-1"]), 8)

        # Check unique asset IDs
        asset_ids = [a.asset_id for a in assets]
        self.assertEqual(len(asset_ids), len(set(asset_ids)), "All asset IDs must be strictly unique")

    def test_feature_adapter_7_inputs_bounds(self):
        """Verify feature adapter extracts all 7 scoring features strictly within valid ranges [0.0, 1.0]."""
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

        # 1. asset_criticality
        self.assertGreaterEqual(features["asset_criticality"], 0.0)
        self.assertLessEqual(features["asset_criticality"], 1.0)
        # 2. defect_severity
        self.assertGreaterEqual(features["defect_severity"], 0.0)
        self.assertLessEqual(features["defect_severity"], 1.0)
        # 3. days_overdue & days_overdue_norm
        self.assertGreaterEqual(features["days_overdue"], 0)
        self.assertGreaterEqual(features["days_overdue_norm"], 0.0)
        self.assertLessEqual(features["days_overdue_norm"], 1.0)
        # 4. failure_probability
        self.assertGreaterEqual(features["failure_probability"], 0.0)
        self.assertLessEqual(features["failure_probability"], 1.0)
        # 5. train_impact
        self.assertGreaterEqual(features["train_impact"], 0.0)
        self.assertLessEqual(features["train_impact"], 1.0)
        # 6. maintenance_duration & maintenance_duration_norm
        self.assertGreater(features["maintenance_duration"], 0)
        self.assertGreaterEqual(features["maintenance_duration_norm"], 0.0)
        self.assertLessEqual(features["maintenance_duration_norm"], 1.0)
        # 7. historical_failure_rate
        self.assertGreaterEqual(features["historical_failure_rate"], 0.0)
        self.assertLessEqual(features["historical_failure_rate"], 1.0)


class TestGeneratorAndFixturesValidation(unittest.TestCase):
    """Validates generator reproducibility, variation, and JSON contract conformance."""

    def setUp(self):
        self.fixtures_dir = os.path.join(
            os.path.dirname(__file__), "..", "app", "data", "fixtures"
        )

    def test_seed_reproducibility(self):
        """Verify identical seeds produce bit-for-bit identical OptimizationRequests."""
        gen1 = CorridorDataGenerator(seed=42)
        req1 = gen1.generate_scenario_a(seed=42).to_dict()

        gen2 = CorridorDataGenerator(seed=42)
        req2 = gen2.generate_scenario_a(seed=42).to_dict()

        self.assertEqual(req1, req2)

    def test_seed_variation(self):
        """Verify different seeds produce meaningful candidate variation."""
        gen1 = CorridorDataGenerator(seed=42)
        req1 = gen1.generate_scenario_a(seed=42).to_dict()

        gen2 = CorridorDataGenerator(seed=101)
        req2 = gen2.generate_scenario_a(seed=101).to_dict()

        self.assertNotEqual(req1["candidates"], req2["candidates"])

    def test_golden_scenario_fixture(self):
        """Verify Golden Scenario fixture loads and validates against OptimizationRequest schema."""
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

        # Validate candidate fields and dependency references
        candidate_ids = {c.block_id for c in req.candidates}
        for c in req.candidates:
            for dep_id in c.dependencies:
                self.assertIn(dep_id, candidate_ids, f"Dependency {dep_id} must exist in candidates")

        # Validate disruption event contract
        disr = DisruptionEvent.from_dict(data["disruption_event"])
        self.assertEqual(disr.disruption_type, "TRACK_UNAVAILABLE")
        self.assertEqual(disr.track_id, "UP-1")

    def test_disruption_scenarios_fixture(self):
        """Verify 5 disruption scenarios fixture structure and schema conformance."""
        disr_path = os.path.join(self.fixtures_dir, "disruption_scenarios.json")
        self.assertTrue(os.path.exists(disr_path), "disruption_scenarios.json must exist")

        with open(disr_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("scenarios", data)
        scenarios = data["scenarios"]
        self.assertEqual(len(scenarios), 5)

        for scn in scenarios:
            self.assertIn("scenario_id", scn)
            self.assertIn("disruption_event", scn)
            disr = DisruptionEvent.from_dict(scn["disruption_event"])
            self.assertIsNotNone(disr.disruption_id)
            self.assertIsNotNone(disr.disruption_type)


if __name__ == "__main__":
    unittest.main()

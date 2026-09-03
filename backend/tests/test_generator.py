"""Unit tests for the synthetic data generator and domain models.
"""

import json
import os
import unittest

from contracts.schemas import (
    BlockCandidate,
    BlockStatus,
    OptimizationRequest,
    WorkType,
)
from backend.app.data.generator import CorridorDataGenerator
from backend.app.data.models import Asset, AssetType, TrackSegment, Corridor


class TestCorridorDataGenerator(unittest.TestCase):
    """Test suite for CorridorDataGenerator."""

    def setUp(self):
        self.generator = CorridorDataGenerator(seed=42)

    def test_seed_reproducibility(self):
        """Verify that identical seeds produce bit-for-bit identical scenarios."""
        gen1 = CorridorDataGenerator(seed=42)
        gen2 = CorridorDataGenerator(seed=42)

        req1 = gen1.generate_scenario_a(seed=42).to_dict()
        req2 = gen2.generate_scenario_a(seed=42).to_dict()

        self.assertEqual(req1, req2, "Generator with same seed must produce identical outputs.")

    def test_different_seeds_produce_variation(self):
        """Verify that different seeds produce different block candidates."""
        gen1 = CorridorDataGenerator(seed=42)
        gen2 = CorridorDataGenerator(seed=99)

        req1 = gen1.generate_scenario_a(seed=42).to_dict()
        req2 = gen2.generate_scenario_a(seed=99).to_dict()

        self.assertNotEqual(
            req1["candidates"][0]["asset_id"],
            req2["candidates"][0]["asset_id"],
            "Different seeds should generate distinct scenario variations.",
        )

    def test_track_assignment_validity(self):
        """Verify all candidate blocks are assigned to valid existing tracks."""
        req = self.generator.generate_scenario_a(seed=42)
        track_set = set(req.tracks)

        self.assertEqual(len(req.tracks), 2)
        self.assertIn("UP-1", track_set)
        self.assertIn("DOWN-1", track_set)

        for candidate in req.candidates:
            self.assertIn(
                candidate.track_id,
                track_set,
                f"Candidate {candidate.block_id} assigned to unknown track {candidate.track_id}",
            )

    def test_time_window_consistency(self):
        """Verify time window bounds for all candidate blocks."""
        req = self.generator.generate_scenario_b(seed=101)

        for c in req.candidates:
            self.assertGreater(c.duration_minutes, 0)
            self.assertGreaterEqual(c.earliest_start_minute, 0)
            self.assertLessEqual(c.latest_end_minute, req.horizon_minutes)
            self.assertLessEqual(
                c.earliest_start_minute + c.duration_minutes,
                c.latest_end_minute,
                f"Block {c.block_id} duration does not fit within earliest_start and latest_end bounds.",
            )

    def test_dependency_integrity(self):
        """Verify all dependency block IDs exist and no cycles exist."""
        req = self.generator.generate_scenario_a(seed=42)
        candidate_ids = {c.block_id for c in req.candidates}

        for c in req.candidates:
            for dep_id in c.dependencies:
                self.assertIn(
                    dep_id,
                    candidate_ids,
                    f"Block {c.block_id} references non-existent dependency {dep_id}",
                )
                self.assertNotEqual(
                    c.block_id,
                    dep_id,
                    f"Block {c.block_id} cannot depend on itself.",
                )

    def test_work_types_are_valid_enums(self):
        """Verify all work types match the approved WorkType enum values."""
        valid_work_types = {w.value for w in WorkType}
        req = self.generator.generate_scenario_b(seed=101)

        for c in req.candidates:
            self.assertIn(
                c.work_type,
                valid_work_types,
                f"Block {c.block_id} has invalid work type {c.work_type}",
            )

    def test_score_boundaries(self):
        """Verify priority and risk scores are bounded in [0.0, 1.0]."""
        req = self.generator.generate_scenario_a(seed=42)

        for c in req.candidates:
            self.assertGreaterEqual(c.priority_score, 0.0)
            self.assertLessEqual(c.priority_score, 1.0)
            self.assertGreaterEqual(c.risk_score, 0.0)
            self.assertLessEqual(c.risk_score, 1.0)

    def test_scenario_b_dense_properties(self):
        """Verify Scenario B properties (4 tracks, 24 candidates)."""
        req = self.generator.generate_scenario_b(seed=101)

        self.assertEqual(len(req.tracks), 4)
        self.assertEqual(len(req.candidates), 24)
        self.assertEqual(req.corridor_id, "CORRIDOR_B_DENSE")

        # Verify mutual exclusion groups are present
        machine_groups = {c.mutual_exclusion_group for c in req.candidates if c.mutual_exclusion_group}
        self.assertGreater(len(machine_groups), 0)

    def test_scenario_c_committed_blocks(self):
        """Verify Scenario C contains pre-committed blocks."""
        req = self.generator.generate_scenario_c(seed=999)

        self.assertEqual(req.corridor_id, "CORRIDOR_C_DISRUPTED")
        self.assertGreaterEqual(len(req.existing_committed_blocks), 2)

        for committed in req.existing_committed_blocks:
            self.assertTrue(committed.is_committed)
            self.assertEqual(committed.status, BlockStatus.COMMITTED.value)

    def test_json_fixtures_validity(self):
        """Verify exported fixtures are valid JSON and deserialize cleanly."""
        fixtures_dir = os.path.join(
            os.path.dirname(__file__), "..", "app", "data", "fixtures"
        )
        self.assertTrue(os.path.exists(fixtures_dir), "Fixtures directory must exist.")

        fixture_files = ["corridor_a_blocks.json", "corridor_b_dense.json", "corridor_c_disrupted.json"]
        for fname in fixture_files:
            fpath = os.path.join(fixtures_dir, fname)
            self.assertTrue(os.path.exists(fpath), f"Fixture file {fname} must exist.")

            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Deserialize via contract
            req = OptimizationRequest.from_dict(data)
            self.assertIsInstance(req, OptimizationRequest)
            self.assertGreater(len(req.candidates), 0)
            self.assertGreater(len(req.tracks), 0)


if __name__ == "__main__":
    unittest.main()

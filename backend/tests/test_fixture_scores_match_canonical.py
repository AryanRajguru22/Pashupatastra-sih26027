"""Regression test: checked-in fixture scores must match the canonical scorer.

Guards against the drift found during presentation-readiness integration,
where `corridor_a_blocks.json`, `corridor_b_dense.json`, and
`corridor_c_disrupted.json` were generated before `generator.py` was wired
to the canonical `score_block()` and were never regenerated afterwards. For
every candidate (including committed blocks) in these fixtures, the stored
`priority_score`/`risk_score` must equal what `score_block()` computes from
that same block's own `metadata.scoring_features`.

`golden_scenario.json` is intentionally excluded: its candidate scores are
a hand-authored narrative fixture, not generator output (see docs/DOMAIN
reconciliation work), so this equality does not apply to it.
"""

from __future__ import annotations

import json
import os
import unittest

from backend.app.ml.scorer import score_block

FIXTURES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "app",
    "data",
    "fixtures",
)

CANONICAL_FIXTURES = [
    "corridor_a_blocks.json",
    "corridor_b_dense.json",
    "corridor_c_disrupted.json",
]


class TestFixtureScoresMatchCanonicalScorer(unittest.TestCase):
    """Every checked-in candidate score must equal score_block() output."""

    def _all_blocks(self, data: dict) -> list[dict]:
        return list(data.get("candidates", [])) + list(
            data.get("existing_committed_blocks", [])
        )

    def test_fixture_scores_match_canonical_scorer(self):
        for filename in CANONICAL_FIXTURES:
            path = os.path.join(FIXTURES_DIR, filename)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            blocks = self._all_blocks(data)
            self.assertGreater(
                len(blocks), 0, f"{filename} has no candidates to check"
            )

            for block in blocks:
                scoring_features = block["metadata"]["scoring_features"]
                expected = score_block(scoring_features)

                with self.subTest(fixture=filename, block_id=block["block_id"]):
                    self.assertAlmostEqual(
                        block["priority_score"],
                        expected["priority_score"],
                        places=4,
                        msg=(
                            f"{filename}:{block['block_id']} priority_score "
                            f"{block['priority_score']} does not match canonical "
                            f"score_block() output {expected['priority_score']}"
                        ),
                    )
                    self.assertAlmostEqual(
                        block["risk_score"],
                        expected["risk_score"],
                        places=4,
                        msg=(
                            f"{filename}:{block['block_id']} risk_score "
                            f"{block['risk_score']} does not match canonical "
                            f"score_block() output {expected['risk_score']}"
                        ),
                    )


if __name__ == "__main__":
    unittest.main()

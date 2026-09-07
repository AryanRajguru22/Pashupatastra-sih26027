# Ayush — Scoring Integration

## Purpose

Connect the railway domain data to the canonical deterministic scoring engine without changing the shared `BlockCandidate` contract.

## Pipeline

```text
Asset + TrackSegment + BlockCandidate
        |
        v
ScoringFeatureAdapter
        |
        +--> 7 scoring inputs
        |
        v
score_block()
        |
        +--> risk_score
        +--> priority_score
        +--> risk/priority levels
        +--> explanation
        |
        v
BlockCandidate
```

## Canonical scoring entry points

- `ScoringFeatureAdapter.build_scorer_input(...)` — creates the exact seven fields expected by `score_block()`.
- `ScoringFeatureAdapter.score_domain_block(...)` — scores an `Asset`/`TrackSegment` pair through the canonical scorer.
- `ScoringFeatureAdapter.score_block_candidate(...)` — resolves a `BlockCandidate` against a `Corridor`, calculates its overdue value from the asset data, and writes the existing `priority_score`, `risk_score`, and scoring metadata fields.

`days_overdue` and `maintenance_duration` are passed to `score_block()` in raw units. The scorer remains responsible for normalization, so there is no double-normalization in the integration layer.

## Generator integration

`CorridorDataGenerator.generate_candidate_blocks()` now uses `score_domain_block()` rather than calculating a second risk/priority formula. This makes the scorer the single source of truth for generated candidate scores.

The existing `baseline_risk_score` and `baseline_priority_score` values returned by the lower-level `extract_features()` method are retained for compatibility with existing fixtures/tests, but they are not used to populate the generated `BlockCandidate` scores anymore.

## Tests added

- Exact deterministic score regression.
- Probability input accepts both `0.80` and `80` as the same 80% probability.
- Domain `Asset`/`TrackSegment` data reaches `score_block()`.
- A `BlockCandidate` is scored from its canonical `Corridor` domain data.

Run:

```bash
python -m pytest backend/app/ml/tests/test_scorer.py backend/tests/test_domain_data.py backend/tests/test_generator.py -q
```

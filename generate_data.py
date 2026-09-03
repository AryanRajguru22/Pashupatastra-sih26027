"""CLI script for Darshini to generate and inspect synthetic railway corridor data.

Usage:
    python generate_data.py --scenario A
    python generate_data.py --scenario B
    python generate_data.py --scenario C
    python generate_data.py --export
"""

import argparse
import json
import sys
import os

# Add root directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.app.data.generator import CorridorDataGenerator


def print_scenario_summary(scenario_name: str, req_dict: dict):
    print(f"\n{'=' * 65}")
    print(f"  PASHUPATASTRA — {scenario_name.upper()} SUMMARY")
    print(f"{'=' * 65}")
    print(f"Corridor ID:         {req_dict['corridor_id']}")
    print(f"Planning Horizon:    {req_dict['horizon_minutes']} min (24 Hours)")
    print(f"Tracks:              {', '.join(req_dict['tracks'])}")
    print(f"Total Block Candidates: {len(req_dict['candidates'])}")
    print(f"Possession Windows:  {len(req_dict['possession_windows'])}")
    print(f"Committed Blocks:    {len(req_dict['existing_committed_blocks'])}")
    print(f"Min Headway Buffer:  {req_dict['min_headway_minutes']} min")

    print(f"\n{'-' * 65}")
    print(f"  SAMPLE CANDIDATE BLOCKS")
    print(f"{'-' * 65}")
    print(f"{'ID':<9} {'Track':<8} {'Work Type':<24} {'Dur(m)':<8} {'Pri':<6} {'Risk':<6} {'Window'}")
    print(f"{'-' * 65}")

    for c in req_dict["candidates"][:8]:
        window_str = f"{c['earliest_start_minute']:04d}-{c['latest_end_minute']:04d}"
        print(
            f"{c['block_id']:<9} {c['track_id']:<8} {c['work_type']:<24} "
            f"{c['duration_minutes']:<8} {c['priority_score']:<6.2f} {c['risk_score']:<6.2f} {window_str}"
        )
    if len(req_dict["candidates"]) > 8:
        print(f"... and {len(req_dict['candidates']) - 8} more candidates.")
    print(f"{'=' * 65}\n")


def main():
    parser = argparse.ArgumentParser(description="Pashupatastra Synthetic Data Generator (Darshini)")
    parser.add_argument(
        "--scenario",
        choices=["A", "B", "C", "all"],
        default="A",
        help="Select scenario to generate (A: Baseline, B: Dense, C: Disrupted, all)",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Export all scenarios to backend/app/data/fixtures/",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )

    args = parser.parse_args()
    gen = CorridorDataGenerator(seed=args.seed)
    fixtures_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend", "app", "data", "fixtures")

    if args.export or args.scenario == "all":
        fixtures = gen.export_fixtures(fixtures_dir)
        print("\nExported Fixtures:")
        for fname, path in fixtures.items():
            print(f"  [OK] {fname} -> {path}")

    if args.scenario in ["A", "all"]:
        req_a = gen.generate_scenario_a(seed=args.seed).to_dict()
        print_scenario_summary("Scenario A (Baseline Mainline)", req_a)

    if args.scenario in ["B", "all"]:
        req_b = gen.generate_scenario_b(seed=101).to_dict()
        print_scenario_summary("Scenario B (High Density Corridor)", req_b)

    if args.scenario in ["C", "all"]:
        req_c = gen.generate_scenario_c(seed=999).to_dict()
        print_scenario_summary("Scenario C (Disrupted & Re-optimization Testbed)", req_c)


if __name__ == "__main__":
    main()

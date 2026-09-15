"""Deterministic Synthetic Data Generator for Railway Corridor Maintenance Scenarios.
Standardized for Indian Railways operational context with 7-feature scoring integration.
"""

from __future__ import annotations
import json
import os
import random
from typing import Any, Dict, List, Optional

from contracts.schemas import (
    DEFAULT_HORIZON_START,
    BlockCandidate,
    BlockStatus,
    DisruptionEvent,
    OptimizationRequest,
    PossessionWindow,
    WorkType,
)
from backend.app.data.models import (
    Asset,
    AssetType,
    Corridor,
    DefectSeverity,
    RouteClassification,
    TrackSegment,
)
from backend.app.data.feature_adapter import ScoringFeatureAdapter
from backend.app.data.section_registry import SectionRegistry


class CorridorDataGenerator:
    """Deterministic synthetic data generator for railway maintenance scheduling."""

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = random.Random(seed)

    def set_seed(self, seed: int) -> None:
        self.seed = seed
        self.rng = random.Random(seed)

    def generate_corridor_tracks(
        self,
        corridor_id: str,
        num_tracks: int = 2,
        corridor_length_km: float = 40.0,
    ) -> List[TrackSegment]:
        """Generates track segments for a corridor (e.g. UP-1, DOWN-1)."""
        tracks: List[TrackSegment] = []
        track_names = []
        if num_tracks == 2:
            track_names = [("UP-1", "UP"), ("DOWN-1", "DOWN")]
        elif num_tracks == 4:
            track_names = [
                ("UP-1", "UP"),
                ("UP-2", "UP"),
                ("DOWN-1", "DOWN"),
                ("DOWN-2", "DOWN"),
            ]
        else:
            for i in range(1, num_tracks + 1):
                direction = "UP" if i % 2 == 1 else "DOWN"
                track_names.append((f"{direction}-{(i + 1) // 2}", direction))

        for tid, direction in track_names:
            tracks.append(
                TrackSegment(
                    track_id=tid,
                    corridor_id=corridor_id,
                    segment_name=f"SEG-{corridor_id}-{tid}",
                    section_name=f"{direction} Main Line ({tid})",
                    direction=direction,
                    km_start=0.0,
                    km_end=corridor_length_km,
                    speed_limit_kmh=160 if "DENSE" in corridor_id or "AGC" in corridor_id else 130,
                    electrified=True,
                    daily_train_density=115 if direction == "DOWN" else 110,
                    route_classification=RouteClassification.GROUP_A.value,
                )
            )
        return tracks

    def generate_synthetic_stations(
        self,
        corridor_id: str,
        corridor_length_km: float,
    ) -> List[Dict[str, Any]]:
        """Deterministic ABSTRACT endpoints for a synthetic corridor.

        These are NOT railway stations. They are placeholder endpoints
        that exist only so a synthetic corridor can express its span
        through the same Section vocabulary as a real one. The ids are
        deliberately corridor-scoped and obviously synthetic
        ("CORRIDOR_A_S0"), never real station codes, and the chainages
        are the corridor's own declared length - nothing is invented.

        Exactly TWO endpoints are produced, giving exactly ONE section
        per corridor. That is deliberate: the pre-Step-7 generator gave
        each track a single per-track alias, so every track was one
        undivided resource. Emitting one span keeps that resource
        grouping bit-identical while changing only the vocabulary. A
        synthetic corridor has no station topology to subdivide it and
        inventing intermediate stations would fabricate railway
        geometry.
        """

        return [
            {
                "station_id": f"{corridor_id}_S0",
                "corridor_id": corridor_id,
                "name": f"{corridor_id} synthetic origin",
                "km": 0.0,
            },
            {
                "station_id": f"{corridor_id}_S1",
                "corridor_id": corridor_id,
                "name": f"{corridor_id} synthetic terminus",
                "km": float(corridor_length_km),
            },
        ]

    @staticmethod
    def _sole_section_id(registry: SectionRegistry) -> str:
        """The single section of a synthetic corridor.

        Synthetic corridors have exactly one span (see
        generate_synthetic_stations), so there is one canonical
        section_id to assign. This raises rather than picking one if
        that ever stops being true, so a corridor that gains real
        topology cannot silently have all its work assigned to an
        arbitrary section.
        """

        section_ids = registry.section_ids()

        if len(section_ids) != 1:
            raise ValueError(
                f"Corridor {registry.corridor_id!r} has "
                f"{len(section_ids)} sections. The synthetic generator "
                "only knows how to assign work to a single-span "
                "corridor; a multi-section corridor must assign "
                "section_id per asset location."
            )

        return section_ids[0]

    def build_section_registry(
        self,
        corridor_id: str,
        tracks: List[TrackSegment],
        corridor_length_km: float,
    ) -> SectionRegistry:
        """The authoritative section vocabulary for a synthetic corridor.

        Every canonical section_id the generator emits comes from here,
        so the generator no longer mints section identities of its own.
        """

        return SectionRegistry.from_stations(
            corridor_id,
            self.generate_synthetic_stations(
                corridor_id,
                corridor_length_km,
            ),
            track_ids=[track.track_id for track in tracks],
        )

    def generate_assets(
        self,
        tracks: List[TrackSegment],
        num_assets_per_track: int = 8,
    ) -> List[Asset]:
        """Generates realistic railway assets positioned along each track."""
        assets: List[Asset] = []
        asset_types = [
            AssetType.RAIL_SECTION.value,
            AssetType.TURNOUT_POINT.value,
            AssetType.OHE_MAST.value,
            AssetType.SIGNAL_POST.value,
            AssetType.TRACK_CIRCUIT.value,
        ]
        type_weights = [0.35, 0.15, 0.30, 0.10, 0.10]
        severity_choices = [
            DefectSeverity.NONE.value,
            DefectSeverity.MINOR.value,
            DefectSeverity.MODERATE.value,
            DefectSeverity.CRITICAL.value,
        ]
        severity_weights = [0.40, 0.30, 0.20, 0.10]

        asset_counter = 1
        for track in tracks:
            length = track.km_end - track.km_start
            step = length / max(1, num_assets_per_track)

            for i in range(num_assets_per_track):
                atype = self.rng.choices(asset_types, weights=type_weights, k=1)[0]
                sev = self.rng.choices(severity_choices, weights=severity_weights, k=1)[0]
                km_loc = round(track.km_start + (i * step) + self.rng.uniform(0.1, step * 0.8), 2)
                crit = round(self.rng.uniform(0.60, 0.98), 2)
                cond = round(self.rng.uniform(0.35, 0.95), 2)
                days_overdue = self.rng.randint(0, 60)
                gmt = round(self.rng.uniform(100.0, 450.0), 1)

                asset_id = f"AST-{track.track_id}-{atype[:3]}-{asset_counter:03d}"
                asset_name = f"{atype.replace('_', ' ').title()} @ KM {km_loc} ({track.track_id})"

                assets.append(
                    Asset(
                        asset_id=asset_id,
                        name=asset_name,
                        asset_type=atype,
                        track_id=track.track_id,
                        km_location=km_loc,
                        criticality=crit,
                        condition_score=cond,
                        last_maintained_days_ago=days_overdue + 30,
                        defect_severity=sev,
                        gross_million_tonnes=gmt,
                        historical_failure_count_3yr=self.rng.randint(0, 4),
                        metadata={"track_id": track.track_id, "inspection_cycle_days": 90},
                    )
                )
                asset_counter += 1
        return assets

    # The three synthetic IR possession slots, as day-1 (0-1439) minute
    # offsets. This is the ONE place these times/labels/id suffixes are
    # defined - repeat_daily_slots below is the only reader. Do not
    # duplicate these numbers anywhere else.
    _DAY_MINUTES = 1440
    _DAILY_SLOTS = (
        # (id_suffix, start_minute, end_minute, window_type)
        ("NIGHT", 30, 300, "NIGHT_TRAFFIC_BLOCK"),
        ("MIDDAY", 690, 870, "MIDDAY_MAINTENANCE_SLOT"),
        ("EVENING", 1260, 1410, "EVENING_OFF_PEAK"),
    )

    def generate_possession_windows(
        self,
        tracks: List[TrackSegment],
        horizon_minutes: int = 1440,
        registry: Optional[SectionRegistry] = None,
    ) -> List[PossessionWindow]:
        """Generates standard Indian Railways operational possession windows per track.

        Typical IR Slots (day 1, i.e. day offset 0):
        1. Night Traffic Block: 00:30 to 05:00 (minute 30 to 300) -> 270 min
        2. Midday Maintenance Slot: 11:30 to 14:30 (minute 690 to 870) -> 180 min
        3. Evening Off-Peak Window: 21:00 to 23:30 (minute 1260 to 1410) -> 150 min

        Slice 4 Step 6: these same three slots now REPEAT once per
        calendar day the horizon spans - day 2's NIGHT slot is minute
        1470-1740 (30+1440 to 300+1440), not a re-based 30-300. There is
        no modulo anywhere in this method: a day-N slot's minutes are
        always >= N*1440, exactly like every other absolute-minute value
        in this codebase (backend.app.data.horizon_anchor). A day whose
        slot would extend past horizon_minutes is excluded entirely
        (never truncated) - see repeat_daily_slots.

        This path is, and remains, entirely SYNTHETIC: no timetable is
        read, no TimetableCoverage check applies, and none of these
        windows may be reported as anything but generated/synthetic
        provenance - see backend.app.jobs.service.
        POSSESSION_DERIVATION_GENERATED_SLOTS and
        DEFAULT_POSSESSION_COMPATIBILITY, both unchanged by this step.

        section_id comes from the corridor's SectionRegistry (Sprint 3
        Step 7). It used to be TrackSegment.section_id - a per-track
        alias that was not a railway section at all, and which could
        never match the station-span sections the timetable adapter
        produces.
        """
        section_id = (
            self._sole_section_id(registry)
            if registry is not None
            else None
        )

        windows: List[PossessionWindow] = []
        for track in tracks:
            windows.extend(
                self.repeat_daily_slots(track.track_id, horizon_minutes, section_id)
            )
        return windows

    @classmethod
    def repeat_daily_slots(
        cls,
        track_id: str,
        horizon_minutes: int,
        section_id: Optional[str],
    ) -> List[PossessionWindow]:
        """The three daily slots, repeated for every day `horizon_minutes` touches.

        Deterministic and pure: no RNG, no I/O. For horizon_minutes ==
        1440 this reproduces EXACTLY the pre-Step-6 output (same 3
        windows, same window_ids, same minute values) - the day-0 id
        suffix is unchanged (e.g. "POS-UP-1-NIGHT"); only day >= 1 adds
        a "-D{day}" suffix (e.g. "POS-UP-1-NIGHT-D1"), so no existing
        id can collide with a new one and no existing id changes.

        A day's slot is included only if it fits ENTIRELY inside
        [0, horizon_minutes) - a slot that would cross the horizon
        boundary is excluded outright, never truncated, per this
        method's own invariant (possession slots represent complete
        available windows). Computing the day-count as a ceiling
        (rather than requiring horizon_minutes to be an exact multiple
        of 1440) means this also behaves correctly for a non-multiple
        horizon_minutes passed directly to this method or to
        generate_possession_windows - the whole-calendar-day POLICY for
        the deployment horizon is enforced once, at JobService
        construction (InvalidHorizonMinutesError), not duplicated here.
        """

        if horizon_minutes <= 0:
            return []

        candidate_days = -(-horizon_minutes // cls._DAY_MINUTES)  # ceil div

        windows: List[PossessionWindow] = []
        for day in range(candidate_days):
            offset = day * cls._DAY_MINUTES
            for suffix, start, end, window_type in cls._DAILY_SLOTS:
                start_minute = start + offset
                end_minute = end + offset

                if end_minute > horizon_minutes:
                    continue

                window_id_suffix = suffix if day == 0 else f"{suffix}-D{day}"

                windows.append(
                    PossessionWindow(
                        window_id=f"POS-{track_id}-{window_id_suffix}",
                        track_id=track_id,
                        start_minute=start_minute,
                        end_minute=end_minute,
                        section_id=section_id,
                        window_type=window_type,
                    )
                )

        return windows

    def generate_candidate_blocks(
        self,
        assets: List[Asset],
        tracks: List[TrackSegment],
        num_blocks: int = 12,
        horizon_minutes: int = 1440,
        include_dependencies: bool = True,
        include_mutual_exclusions: bool = True,
        registry: Optional[SectionRegistry] = None,
    ) -> List[BlockCandidate]:
        """Generates candidate maintenance blocks grounded in railway work types and scoring inputs."""
        candidate_section_id = (
            self._sole_section_id(registry)
            if registry is not None
            else None
        )
        work_type_map = {
            AssetType.RAIL_SECTION.value: [WorkType.BALLAST_TAMPING.value, WorkType.TRACK_RENEWAL.value, WorkType.ROUTINE_INSPECTION.value],
            AssetType.TURNOUT_POINT.value: [WorkType.SIGNALLING_INTERLOCKING.value, WorkType.EMERGENCY_REPAIR.value, WorkType.ROUTINE_INSPECTION.value],
            AssetType.OHE_MAST.value: [WorkType.OHE_MAINTENANCE.value, WorkType.ROUTINE_INSPECTION.value],
            AssetType.SIGNAL_POST.value: [WorkType.SIGNALLING_INTERLOCKING.value, WorkType.ROUTINE_INSPECTION.value],
            AssetType.TRACK_CIRCUIT.value: [WorkType.SIGNALLING_INTERLOCKING.value, WorkType.ROUTINE_INSPECTION.value],
        }

        duration_map = {
            WorkType.TRACK_RENEWAL.value: (180, 240),
            WorkType.BALLAST_TAMPING.value: (90, 135),
            WorkType.OHE_MAINTENANCE.value: (90, 120),
            WorkType.SIGNALLING_INTERLOCKING.value: (60, 90),
            WorkType.ROUTINE_INSPECTION.value: (45, 60),
            WorkType.EMERGENCY_REPAIR.value: (60, 90),
        }

        machine_groups = {
            WorkType.BALLAST_TAMPING.value: "CSM_TAMPER_01",
            WorkType.OHE_MAINTENANCE.value: "TOWER_WAGON_01",
            WorkType.SIGNALLING_INTERLOCKING.value: "S_T_GANG_01",
        }

        track_lookup = {t.track_id: t for t in tracks}
        candidates: List[BlockCandidate] = []
        selected_assets = self.rng.sample(assets, min(num_blocks, len(assets)))

        for i, asset in enumerate(selected_assets):
            block_id = f"BLK-{i + 1:03d}"
            possible_wtypes = work_type_map.get(asset.asset_type, [WorkType.ROUTINE_INSPECTION.value])
            work_type = self.rng.choice(possible_wtypes)
            dur_min, dur_max = duration_map.get(work_type, (60, 90))
            duration = self.rng.randint(dur_min // 15, dur_max // 15) * 15
            machine = machine_groups.get(work_type)

            track_obj = track_lookup.get(asset.track_id)
            days_overdue = max(0, asset.last_maintained_days_ago - 30)

            # Extract 7 standardized scoring features via adapter
            features = ScoringFeatureAdapter.extract_features(
                asset=asset,
                track=track_obj,
                work_type=work_type,
                duration_minutes=duration,
                days_overdue=days_overdue,
                explicit_defect_severity=asset.defect_severity,
            )

            # The adapter now feeds the canonical scorer.  Do not calculate
            # a second scoring formula here; the scorer is the single source
            # of truth for risk and priority.
            scored = ScoringFeatureAdapter.score_domain_block(
                asset=asset,
                track=track_obj,
                work_type=work_type,
                duration_minutes=duration,
                days_overdue=days_overdue,
                explicit_defect_severity=asset.defect_severity,
            )
            priority = scored["priority_score"]
            risk = scored["risk_score"]

            slot_choice = self.rng.choice(["NIGHT", "MIDDAY", "ANYTIME"])
            if slot_choice == "NIGHT":
                earliest_start = 30
                latest_end = 360
            elif slot_choice == "MIDDAY":
                earliest_start = 660
                latest_end = 930
            else:
                earliest_start = 30
                latest_end = horizon_minutes - 30

            if earliest_start + duration > latest_end:
                latest_end = min(horizon_minutes, earliest_start + duration + 60)

            candidate = BlockCandidate(
                block_id=block_id,
                asset_id=asset.asset_id,
                track_id=asset.track_id,
                work_type=work_type,
                duration_minutes=duration,
                # Canonical section identity, resolved through the
                # corridor's SectionRegistry - never TrackSegment's old
                # per-track alias, and never an ad-hoc or compound
                # string. See generate_possession_windows.
                section_id=candidate_section_id,
                earliest_start_minute=earliest_start,
                latest_end_minute=latest_end,
                priority_score=priority,
                risk_score=risk,
                dependencies=[],
                mutual_exclusion_group=machine if include_mutual_exclusions else None,
                is_committed=False,
                status=BlockStatus.PLANNED.value,
                metadata={
                    "asset_name": asset.name,
                    "km_location": asset.km_location,
                    "defect_severity": asset.defect_severity,
                    "scoring_features": scored["scoring_input"],
                    "scoring_explanation": scored["explanation"],
                },
            )
            candidates.append(candidate)

        # Precedence dependencies
        if include_dependencies and len(candidates) >= 4:
            for idx in range(len(candidates) - 1):
                if (
                    candidates[idx].work_type == WorkType.TRACK_RENEWAL.value
                    and candidates[idx + 1].track_id == candidates[idx].track_id
                ):
                    candidates[idx + 1].dependencies.append(candidates[idx].block_id)
                    candidates[idx + 1].work_type = WorkType.BALLAST_TAMPING.value
                    candidates[idx + 1].earliest_start_minute = max(
                        candidates[idx + 1].earliest_start_minute,
                        candidates[idx].earliest_start_minute + candidates[idx].duration_minutes,
                    )
                    candidates[idx + 1].latest_end_minute = max(
                        candidates[idx + 1].latest_end_minute,
                        candidates[idx + 1].earliest_start_minute + candidates[idx + 1].duration_minutes + 60,
                    )
                    break

        return candidates

    def generate_scenario_a(self, seed: int = 42) -> OptimizationRequest:
        """Scenario A — Baseline 2-track mainline corridor (12 candidates)."""
        self.set_seed(seed)
        tracks = self.generate_corridor_tracks("CORRIDOR_A", num_tracks=2, corridor_length_km=35.0)
        registry = self.build_section_registry("CORRIDOR_A", tracks, 35.0)
        assets = self.generate_assets(tracks, num_assets_per_track=8)
        possession_windows = self.generate_possession_windows(
            tracks, horizon_minutes=1440, registry=registry
        )
        candidates = self.generate_candidate_blocks(
            assets=assets,
            tracks=tracks,
            num_blocks=12,
            horizon_minutes=1440,
            include_dependencies=True,
            include_mutual_exclusions=True,
            registry=registry,
        )

        return OptimizationRequest(
            corridor_id="CORRIDOR_A",
            horizon_start=DEFAULT_HORIZON_START,
            horizon_minutes=1440,
            tracks=[t.track_id for t in tracks],
            candidates=candidates,
            possession_windows=possession_windows,
            existing_committed_blocks=[],
            min_headway_minutes=15,
        )

    def generate_scenario_b(self, seed: int = 101) -> OptimizationRequest:
        """Scenario B — High-density 4-track trunk corridor (24 candidates)."""
        self.set_seed(seed)
        tracks = self.generate_corridor_tracks("CORRIDOR_B_DENSE", num_tracks=4, corridor_length_km=60.0)
        registry = self.build_section_registry("CORRIDOR_B_DENSE", tracks, 60.0)
        assets = self.generate_assets(tracks, num_assets_per_track=10)
        possession_windows = self.generate_possession_windows(
            tracks, horizon_minutes=1440, registry=registry
        )
        candidates = self.generate_candidate_blocks(
            assets=assets,
            tracks=tracks,
            num_blocks=24,
            horizon_minutes=1440,
            include_dependencies=True,
            include_mutual_exclusions=True,
            registry=registry,
        )

        return OptimizationRequest(
            corridor_id="CORRIDOR_B_DENSE",
            horizon_start=DEFAULT_HORIZON_START,
            horizon_minutes=1440,
            tracks=[t.track_id for t in tracks],
            candidates=candidates,
            possession_windows=possession_windows,
            existing_committed_blocks=[],
            min_headway_minutes=15,
        )

    def generate_scenario_c(self, seed: int = 999) -> OptimizationRequest:
        """Scenario C — Disruption and Re-optimization Testbed (14 candidates, 2 committed)."""
        self.set_seed(seed)
        tracks = self.generate_corridor_tracks("CORRIDOR_C_DISRUPTED", num_tracks=2, corridor_length_km=40.0)
        registry = self.build_section_registry("CORRIDOR_C_DISRUPTED", tracks, 40.0)
        assets = self.generate_assets(tracks, num_assets_per_track=8)
        possession_windows = self.generate_possession_windows(
            tracks, horizon_minutes=1440, registry=registry
        )
        candidates = self.generate_candidate_blocks(
            assets=assets,
            tracks=tracks,
            num_blocks=14,
            horizon_minutes=1440,
            include_dependencies=True,
            include_mutual_exclusions=True,
            registry=registry,
        )

        committed_blocks = []
        if len(candidates) >= 2:
            candidates[0].is_committed = True
            candidates[0].status = BlockStatus.COMMITTED.value
            candidates[0].earliest_start_minute = 60
            candidates[0].latest_end_minute = 60 + candidates[0].duration_minutes
            committed_blocks.append(candidates[0])

            candidates[1].is_committed = True
            candidates[1].status = BlockStatus.COMMITTED.value
            candidates[1].earliest_start_minute = 90
            candidates[1].latest_end_minute = 90 + candidates[1].duration_minutes
            committed_blocks.append(candidates[1])

        return OptimizationRequest(
            corridor_id="CORRIDOR_C_DISRUPTED",
            horizon_start=DEFAULT_HORIZON_START,
            horizon_minutes=1440,
            tracks=[t.track_id for t in tracks],
            candidates=candidates,
            possession_windows=possession_windows,
            existing_committed_blocks=committed_blocks,
            min_headway_minutes=15,
        )

    def export_fixtures(self, output_dir: str) -> Dict[str, str]:
        """Exports scenarios A, B, and C as JSON fixtures."""
        os.makedirs(output_dir, exist_ok=True)
        fixtures = {}

        scenarios = [
            ("corridor_a_blocks.json", self.generate_scenario_a(42)),
            ("corridor_b_dense.json", self.generate_scenario_b(101)),
            ("corridor_c_disrupted.json", self.generate_scenario_c(999)),
        ]

        for filename, req in scenarios:
            filepath = os.path.join(output_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(req.to_dict(), f, indent=2)
            fixtures[filename] = filepath

        return fixtures

"""Domain data models for railway assets, tracks, defects, and corridor definitions.
Standardized for Indian Railways operational context and scoring engine inputs.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class AssetType(str, Enum):
    RAIL_SECTION = "RAIL_SECTION"
    TURNOUT_POINT = "TURNOUT_POINT"
    OHE_MAST = "OHE_MAST"
    SIGNAL_POST = "SIGNAL_POST"
    TRACK_CIRCUIT = "TRACK_CIRCUIT"


class DefectSeverity(str, Enum):
    NONE = "NONE"
    MINOR = "MINOR"
    MODERATE = "MODERATE"
    CRITICAL = "CRITICAL"


class RouteClassification(str, Enum):
    GROUP_A = "GROUP_A"  # Speeds up to 160 km/h (NDLS-HWH, NDLS-BCT, NDLS-AGC)
    GROUP_B = "GROUP_B"  # Speeds up to 130 km/h
    GROUP_C = "GROUP_C"  # Suburban sections (Suburban Mumbai/Kolkata/Chennai)
    GROUP_D = "GROUP_D"  # Speeds up to 100 km/h
    GROUP_E = "GROUP_E"  # Branch lines & sidings (< 100 km/h)


@dataclass
class MaintenanceDefect:
    defect_id: str
    asset_id: str
    detected_date: str
    severity: str = DefectSeverity.NONE.value
    defect_type: str = "USFD_FLAW"  # e.g., USFD_FLAW, OHE_STAGGER, MOTOR_RESISTANCE, TRACK_TWIST
    description: str = ""
    reported_by: str = "TRC_SURVEY"  # TRC_SURVEY, USFD_INSPECTION, FOOT_PATROL, OMS2000

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MaintenanceDefect:
        return cls(
            defect_id=data["defect_id"],
            asset_id=data["asset_id"],
            detected_date=data.get("detected_date", "2026-08-01"),
            severity=data.get("severity", DefectSeverity.NONE.value),
            defect_type=data.get("defect_type", "USFD_FLAW"),
            description=data.get("description", ""),
            reported_by=data.get("reported_by", "TRC_SURVEY"),
        )


@dataclass
class Asset:
    asset_id: str
    name: str
    asset_type: str
    track_id: str
    km_location: float
    criticality: float = 0.5  # 0.0 to 1.0 (based on route class & speed potential)
    condition_score: float = 0.8  # 1.0 (pristine) to 0.0 (failing)
    last_maintained_days_ago: int = 30
    defect_severity: str = DefectSeverity.NONE.value
    gross_million_tonnes: float = 250.0  # Cumulative traffic carried (GMT)
    installation_year: int = 2018
    historical_failure_count_3yr: int = 1
    defects: List[MaintenanceDefect] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "name": self.name,
            "asset_type": self.asset_type,
            "track_id": self.track_id,
            "km_location": round(self.km_location, 3),
            "criticality": round(self.criticality, 3),
            "condition_score": round(self.condition_score, 3),
            "last_maintained_days_ago": self.last_maintained_days_ago,
            "defect_severity": self.defect_severity,
            "gross_million_tonnes": round(self.gross_million_tonnes, 1),
            "installation_year": self.installation_year,
            "historical_failure_count_3yr": self.historical_failure_count_3yr,
            "defects": [d.to_dict() for d in self.defects],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Asset:
        return cls(
            asset_id=data["asset_id"],
            name=data.get("name", data["asset_id"]),
            asset_type=data["asset_type"],
            track_id=data["track_id"],
            km_location=float(data.get("km_location", 0.0)),
            criticality=float(data.get("criticality", 0.5)),
            condition_score=float(data.get("condition_score", 0.8)),
            last_maintained_days_ago=int(data.get("last_maintained_days_ago", 30)),
            defect_severity=data.get("defect_severity", DefectSeverity.NONE.value),
            gross_million_tonnes=float(data.get("gross_million_tonnes", 250.0)),
            installation_year=int(data.get("installation_year", 2018)),
            historical_failure_count_3yr=int(data.get("historical_failure_count_3yr", 1)),
            defects=[MaintenanceDefect.from_dict(d) for d in data.get("defects", [])],
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class TrackSegment:
    """One physical/logical running line along a corridor.

    NOTE the field named segment_name, NOT section_id. It was called
    section_id until Sprint 3 Step 7, which was a misnomer: the value
    ("SEC-CORRIDOR_A-UP-1") is a per-track alias, one-to-one with
    track_id, carrying no span of line at all. A railway SECTION is a
    span between two stations, is direction-independent, and there are
    many of them per track - see backend.app.data.models.Section and
    backend.app.data.section_registry.SectionRegistry, which is now the
    only authority for section identity.

    A TrackSegment therefore has NO section_id. Anything needing the
    sections this track runs through resolves them through the registry.
    """

    track_id: str
    corridor_id: str
    segment_name: str
    section_name: str
    direction: str  # "UP" or "DOWN"
    km_start: float
    km_end: float
    speed_limit_kmh: int = 160
    electrified: bool = True
    daily_train_density: int = 110  # Daily trains traversing this track
    route_classification: str = RouteClassification.GROUP_A.value

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TrackSegment:
        return cls(
            track_id=data["track_id"],
            corridor_id=data["corridor_id"],
            segment_name=data.get("segment_name", "SEG-01"),
            section_name=data.get("section_name", "Main Line"),
            direction=data.get("direction", "UP"),
            km_start=float(data.get("km_start", 0.0)),
            km_end=float(data.get("km_end", 50.0)),
            speed_limit_kmh=int(data.get("speed_limit_kmh", 160)),
            electrified=bool(data.get("electrified", True)),
            daily_train_density=int(data.get("daily_train_density", 110)),
            route_classification=data.get("route_classification", RouteClassification.GROUP_A.value),
        )


@dataclass
class Station:
    """A named point on a corridor, identified by a stable code and km position.

    Field names deliberately match the station records already present in
    pashupatastra_realistic_dataset.json (station_id, name, km), so that
    dataset's stations dicts can be loaded via Station.from_dict without
    renaming.
    """

    station_id: str
    corridor_id: str
    name: str
    km: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Station:
        return cls(
            station_id=data["station_id"],
            corridor_id=data.get("corridor_id", ""),
            name=data.get("name", data["station_id"]),
            km=float(data.get("km", 0.0)),
        )


@dataclass
class Section:
    """The authoritative definition of one inter-station railway section.

    Sprint 3 Step 6 architecture gate: section_id is a STABLE, OPAQUE
    key. This record - not the string - is the identity. The
    conventional "<start_station_id>-<end_station_id>" spelling is only
    the default naming convention, produced in exactly one place
    (backend.app.data.section_registry.format_section_id), never built
    ad hoc. Nothing parses a section_id; the solver compares it by
    string equality alone.

    A section is DIRECTION-INDEPENDENT: it is the span of line between
    two adjacent stations, not a direction of travel over it. An UP and
    a DOWN train between the same two stations traverse the SAME
    section.

    track_ids lists the running lines that serve this span. It is an
    association, not part of the section's identity - which is what
    keeps track and section from being conflated. The canonical
    resource identity used by the optimizer stays the PAIR
    (track_id, section_id); see solver._resource_key.

    track_id (singular) is a deprecated convenience for the
    single-track case and is kept only so pre-Step-7 callers keep
    working. New code reads track_ids.

    WARNING for multi-track sections: track_id is MEANINGLESS whenever
    len(track_ids) > 1 - it names one arbitrary member of the set and
    must not be read as "the" track of the section. Real corridor
    topology will routinely have multi-track sections, so ingestion
    code must read track_ids and never track_id.
    """

    section_id: str
    corridor_id: str
    start_station_id: str
    end_station_id: str
    km_start: float
    km_end: float
    track_ids: tuple = ()
    track_id: Optional[str] = None

    def __post_init__(self) -> None:
        # Keep the deprecated singular field and the canonical plural
        # one consistent whichever way the caller populated them.
        if self.track_ids:
            self.track_ids = tuple(self.track_ids)
            if self.track_id is None and len(self.track_ids) == 1:
                self.track_id = self.track_ids[0]
        elif self.track_id is not None:
            self.track_ids = (self.track_id,)

        for track_id in self.track_ids:
            if ":" in track_id:
                raise ValueError(
                    "track_id must stay a bare identifier such as "
                    f"'UP-1'; got compound {track_id!r}. Section "
                    "identity and track identity are separate fields."
                )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section_id": self.section_id,
            "corridor_id": self.corridor_id,
            "start_station_id": self.start_station_id,
            "end_station_id": self.end_station_id,
            "km_start": self.km_start,
            "km_end": self.km_end,
            "track_ids": list(self.track_ids),
            "track_id": self.track_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Section:
        return cls(
            section_id=data["section_id"],
            corridor_id=data["corridor_id"],
            start_station_id=data["start_station_id"],
            end_station_id=data["end_station_id"],
            km_start=float(data.get("km_start", 0.0)),
            km_end=float(data.get("km_end", 0.0)),
            track_ids=tuple(data.get("track_ids", ())),
            track_id=data.get("track_id"),
        )


@dataclass
class Corridor:
    corridor_id: str
    name: str
    zone: str = "Northern Railway"
    division: str = "Delhi"
    tracks: List[TrackSegment] = field(default_factory=list)
    assets: List[Asset] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "corridor_id": self.corridor_id,
            "name": self.name,
            "zone": self.zone,
            "division": self.division,
            "tracks": [t.to_dict() for t in self.tracks],
            "assets": [a.to_dict() for a in self.assets],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Corridor:
        return cls(
            corridor_id=data["corridor_id"],
            name=data["name"],
            zone=data.get("zone", "Northern Railway"),
            division=data.get("division", "Delhi"),
            tracks=[TrackSegment.from_dict(t) for t in data.get("tracks", [])],
            assets=[Asset.from_dict(a) for a in data.get("assets", [])],
        )

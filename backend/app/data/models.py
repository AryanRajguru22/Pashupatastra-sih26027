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
    track_id: str
    corridor_id: str
    section_id: str
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
            section_id=data.get("section_id", "SEC-01"),
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

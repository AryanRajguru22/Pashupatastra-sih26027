"""Canonical data-provenance model (Sprint 3 Step 9).

The repository accumulated several independent, ad hoc ways of saying
"where did this data come from": TrainDataProvenance (timetable axis,
backend.app.data.canonical_train), POSSESSION_SOURCE_GENERATED_STATIC
(possession axis, backend.app.jobs.service), and the frontend's
DataProvenance (frontend/src/types/contracts.ts), which collapses an
entire request into one label. None of them agree on vocabulary, and
none of them can express that a single optimization result mixes real
and synthetic inputs across different input classes.

This module is the ONE place that vocabulary is unified. It does not
replace the existing per-axis representations - TrainDataProvenance
stays the production enum for canonical_train/timetable_adapter, and
POSSESSION_SOURCE_GENERATED_STATIC stays the literal string on
JobOptimizationResponse.possession_source for backward compatibility -
it gives them a common target to translate INTO (see
from_train_data_provenance / from_possession_source below) and a single
authoritative way to combine four independent axes into one honest
"effective" answer.

FOUR AXES, TRACKED INDEPENDENTLY
    topology        - corridor/station/section/chainage/track structure.
    timetable       - train numbers, stops, times, service dates.
    asset_condition - defects, inspection state, risk/priority inputs.
    possession      - possession windows and their derivation/source.

They are never collapsed into one field: a result built from real
topology and a real timetable but a synthetic asset-condition dataset
must not be reported as "real" merely because most of its inputs are.

EFFECTIVE PROVENANCE - THE WEAKEST-LINK RULE
    Each ProvenanceLevel belongs to a REALISM TIER:

        SYNTHETIC                        -> tier 0
        REAL_SCHEDULED / REAL_STATIC     -> tier 1 (both "real")

    effective_tier = min(tier(axis) for axis in the four axes).

    If the weakest tier present is 0, effective is SYNTHETIC - full
    stop. This is the safety-critical half of the rule: ONE synthetic
    axis is enough to make the whole result non-real, regardless of how
    many other axes are real. This is deliberately NOT max(enum) and
    NOT alphabetical: it is a two-value tier lookup, and the tier a
    level belongs to is a property this module defines and tests, not
    an accident of enum declaration order.

    If every axis is tier 1 (nothing synthetic), REAL_STATIC and
    REAL_SCHEDULED are NOT ranked against each other - there is no
    repository evidence that one is a "more real" real value than the
    other, and inventing such a ranking would be exactly the kind of
    unjustified hierarchy this step was told not to add. What effective
    reports in that case is a documented REPRESENTATIVE LABEL for the
    (single) real tier: REAL_STATIC if any axis is REAL_STATIC, else
    REAL_SCHEDULED. REAL_STATIC is preferred as the representative
    because it denotes structural/verified fact (topology, chainage,
    inspected asset condition) that does not carry the ordinary
    revision risk (delay, cancellation, re-timing) inherent to
    scheduled/timetable data - it is a labelling convention for the
    tier, not a second weakest-link comparison.

MISSING / UNKNOWN PROVENANCE
    There is no UNKNOWN or LIVE member on ProvenanceLevel, and no
    default value on any ProvenanceProfile field. All four axes are
    required constructor arguments, and __post_init__ rejects anything
    that isn't a valid ProvenanceLevel. A caller that does not know an
    axis's provenance cannot construct a ProvenanceProfile at all - it
    must fail closed at the boundary, not report a value that then gets
    read downstream as "real by default". This matches the repository's
    existing reject-rather-than-guess pattern (possession fail-closed in
    backend.app.jobs.optimization, chronology fail-closed in
    backend.app.data.timetable_adapter) rather than inventing a new
    broad state with no evidence it is needed.

SINGLE SOURCE OF TRUTH
    `effective` is a computed property, never a stored field. There is
    no constructor argument and no setter, so a ProvenanceProfile with
    all-SYNTHETIC axes and a manually-asserted REAL_STATIC effective
    value cannot be constructed - the inconsistency is structurally
    impossible, not merely validated against. `from_dict` reinforces
    this on the RECONSTRUCTION side: it reads only the four axis keys
    and ignores any "effective" key present in the input, recomputing
    effective fresh every time rather than ever re-serving a stored
    value as authoritative.

    This guard protects reconstruction through ProvenanceProfile.
    from_dict specifically - it does not, by itself, make raw storage
    self-verifying. A provenance_snapshot_json row in the audit
    database (backend.app.audit.repository) is opaque TEXT: nothing on
    that read path calls from_dict today, so code that does
    json.loads(...)["provenance"]["effective"] directly gets back
    whatever was written, not a recomputed value. That is safe only
    because to_dict() is the sole writer and always computes effective
    fresh at write time - see backend.app.jobs.optimization.
    optimize_corridor. A future reader reconstructing a ProvenanceProfile
    from a stored snapshot MUST go through ProvenanceProfile.from_dict,
    not read "effective" out of the raw dict directly, or this guarantee
    stops applying to it.

NO LIVE VALUE
    ProvenanceLevel has exactly three members. There is deliberately no
    LIVE member - see test_provenance.py::test_no_live_value_exists and
    the module docstrings of backend.app.data.canonical_train /
    backend.app.data.train_provider, which established the same
    constraint for the timetable axis in Sprint 3 Step 5 and are left
    untouched here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict


class ProvenanceLevel(str, Enum):
    """Canonical provenance vocabulary, shared by all four axes.

    REAL_STATIC     - real, structurally static sourced data (verified
                       topology/chainage, an inspected asset condition
                       snapshot).
    REAL_SCHEDULED  - real, published/scheduled data subject to
                       ordinary operational revision (a timetable, a
                       possession window derived from one).
    SYNTHETIC       - generated or hand-authored illustrative data.
                       Everything currently checked into this
                       repository's production corridor build path is
                       this.
    """

    REAL_STATIC = "REAL_STATIC"
    REAL_SCHEDULED = "REAL_SCHEDULED"
    SYNTHETIC = "SYNTHETIC"


# The weakest-link tier. Both real values share tier 1 deliberately -
# see the module docstring's "EFFECTIVE PROVENANCE" section for why
# they are not ranked against each other.
_REALISM_TIER: Dict[ProvenanceLevel, int] = {
    ProvenanceLevel.SYNTHETIC: 0,
    ProvenanceLevel.REAL_SCHEDULED: 1,
    ProvenanceLevel.REAL_STATIC: 1,
}


def _coerce(value: Any, axis_name: str) -> ProvenanceLevel:
    if isinstance(value, ProvenanceLevel):
        return value

    try:
        return ProvenanceLevel(value)
    except ValueError as exc:
        raise ValueError(
            f"Unknown provenance level {value!r} for axis "
            f"{axis_name!r}. Permitted values are "
            f"{[member.value for member in ProvenanceLevel]}."
        ) from exc


@dataclass(frozen=True)
class ProvenanceProfile:
    """The provenance of one optimization input, tracked on four axes.

    All four fields are required - there is no default for any of them
    - so a caller that does not know an axis's provenance cannot
    construct a profile at all. See the module docstring's "MISSING /
    UNKNOWN PROVENANCE" section.

    `effective` is intentionally NOT a field: see "SINGLE SOURCE OF
    TRUTH" above.
    """

    topology: ProvenanceLevel
    timetable: ProvenanceLevel
    asset_condition: ProvenanceLevel
    possession: ProvenanceLevel

    def __post_init__(self) -> None:
        # object.__setattr__ is required because the dataclass is
        # frozen; this still runs exactly once, at construction, so it
        # does not weaken immutability - it normalises/validates the
        # values the constructor was given, once.
        object.__setattr__(
            self, "topology", _coerce(self.topology, "topology")
        )
        object.__setattr__(
            self, "timetable", _coerce(self.timetable, "timetable")
        )
        object.__setattr__(
            self,
            "asset_condition",
            _coerce(self.asset_condition, "asset_condition"),
        )
        object.__setattr__(
            self, "possession", _coerce(self.possession, "possession")
        )

    @property
    def axes(self) -> tuple[ProvenanceLevel, ProvenanceLevel, ProvenanceLevel, ProvenanceLevel]:
        return (
            self.topology,
            self.timetable,
            self.asset_condition,
            self.possession,
        )

    @property
    def effective(self) -> ProvenanceLevel:
        """The weakest-link provenance across all four axes.

        See the module docstring's "EFFECTIVE PROVENANCE" section for
        the full rule. Computed fresh on every access - never cached,
        never stored - which is what makes it impossible to construct
        or persist an inconsistent effective value.
        """

        weakest_tier = min(_REALISM_TIER[axis] for axis in self.axes)

        if weakest_tier == 0:
            return ProvenanceLevel.SYNTHETIC

        if any(axis == ProvenanceLevel.REAL_STATIC for axis in self.axes):
            return ProvenanceLevel.REAL_STATIC

        return ProvenanceLevel.REAL_SCHEDULED

    def to_dict(self) -> Dict[str, str]:
        return {
            "topology": self.topology.value,
            "timetable": self.timetable.value,
            "asset_condition": self.asset_condition.value,
            "possession": self.possession.value,
            "effective": self.effective.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProvenanceProfile":
        """Reconstruct from the four axis keys only.

        Any "effective" key present in `data` - for example one read
        back out of a persisted audit provenance_snapshot_json - is
        deliberately ignored. effective is always recomputed from the
        four axes, never trusted from storage; see "SINGLE SOURCE OF
        TRUTH" in the module docstring.
        """

        return cls(
            topology=data["topology"],
            timetable=data["timetable"],
            asset_condition=data["asset_condition"],
            possession=data["possession"],
        )


def from_train_data_provenance(value: str) -> ProvenanceLevel:
    """Map backend.app.data.canonical_train.TrainDataProvenance -> canonical.

    TrainDataProvenance is the production enum for the timetable axis
    and is NOT replaced by this module - see the module docstring. This
    is the one place that maps its two members onto the canonical
    three-value vocabulary:

        REAL_SCHEDULED      -> REAL_SCHEDULED (same meaning, same name)
        SYNTHETIC_SCHEDULED -> SYNTHETIC (generated/demo data)
    """

    mapping = {
        "REAL_SCHEDULED": ProvenanceLevel.REAL_SCHEDULED,
        "SYNTHETIC_SCHEDULED": ProvenanceLevel.SYNTHETIC,
    }

    try:
        return mapping[value]
    except KeyError as exc:
        raise ValueError(
            f"Unknown TrainDataProvenance value {value!r}; expected one "
            f"of {sorted(mapping)}."
        ) from exc


def from_possession_source(value: str) -> ProvenanceLevel:
    """Map backend.app.jobs.service.POSSESSION_SOURCE_* -> canonical.

    GENERATED_STATIC (backend.app.jobs.service.
    POSSESSION_SOURCE_GENERATED_STATIC) is legacy terminology, not a
    distinct provenance value: it describes deterministically
    GENERATED possession windows that are demonstrably not live/real
    data - the same DERIVATION-vs-PROVENANCE split
    backend.app.data.canonical_train already draws between
    TraversalBasis/TraversalDerivation (how a value was computed) and
    TrainDataProvenance (where it ultimately came from). Under that
    precedent GENERATED_STATIC is a derivation label whose provenance
    equivalent is SYNTHETIC, so that is what it maps to here.

    NOTE: a possession window's provenance can never exceed the
    provenance of the timetable it was derived from - a GENERATED
    window computed over a REAL_SCHEDULED timetable is still a derived
    artifact, not itself directly real. No production code path
    currently derives possession windows from a real timetable (the
    jobs pipeline's possession_windows() calls the deterministic
    generator directly - see backend.app.jobs.service - and never
    consults backend.app.data.train_provider at all), so this
    "possession is capped by its timetable input" rule has no live call
    site today and is documented rather than implemented as unreachable
    code. Wire it in when/if the canonical train adapter
    (backend.app.data.train_provider) is connected to possession
    derivation for the jobs pipeline.
    """

    mapping = {
        "GENERATED_STATIC": ProvenanceLevel.SYNTHETIC,
    }

    try:
        return mapping[value]
    except KeyError as exc:
        raise ValueError(
            f"Unknown possession source {value!r}; expected one of "
            f"{sorted(mapping)}."
        ) from exc


def to_possession_source(level: ProvenanceLevel) -> str:
    """Reverse of from_possession_source: canonical -> legacy label.

    This is what keeps JobOptimizationResponse.possession_source a
    MECHANICALLY DERIVED view of the canonical possession axis rather
    than an independently-settable field - see
    backend/app/jobs/optimization.py, which builds a ProvenanceProfile
    exactly once per optimize_corridor() call and calls this to fill in
    possession_source, instead of assigning the legacy string a second
    time from a separate source.

    Only SYNTHETIC has a legacy label today, because
    POSSESSION_SOURCE_GENERATED_STATIC is the only possession source
    that exists in this repository. REAL_STATIC/REAL_SCHEDULED
    possession have no legacy string to round-trip through yet - that
    is a real gap, not an oversight, and is called out in the Step 9
    final report rather than papered over with an invented label.
    """

    if level == ProvenanceLevel.SYNTHETIC:
        return "GENERATED_STATIC"

    raise ValueError(
        f"No legacy possession_source label exists for {level.value!r} "
        "yet - only SYNTHETIC (GENERATED_STATIC) has ever been "
        "produced by this repository's possession pipeline."
    )


# Documented correspondence with the frontend's collapsed DataProvenance
# type (frontend/src/types/contracts.ts). Not used at runtime by either
# side - Python cannot import a TypeScript enum - this is the migration
# table itself, kept here so the two vocabularies cannot silently drift
# apart, and pinned by test_provenance.py::test_frontend_vocabulary_map.
#
#   SYNTHETIC_FIXTURE -> every axis SYNTHETIC (a checked-in fixture
#                        supplies topology, timetable, asset condition
#                        AND possession all at once, and today it is
#                        the ONLY value the frontend ever emits).
#   REALISTIC_STATIC  -> REAL_STATIC (reserved; not yet emitted).
#   LIVE_FEED         -> no canonical equivalent. Deliberately absent
#                        from this table: ProvenanceLevel has no LIVE
#                        member, and LIVE_FEED itself is documented on
#                        the frontend as "reserved; not yet available;
#                        do not use until one actually exists" - it is
#                        never assigned anywhere in frontend/src/lib/
#                        data.ts today.
FRONTEND_DATA_PROVENANCE_MAP: Dict[str, Any] = {
    "SYNTHETIC_FIXTURE": {
        "topology": ProvenanceLevel.SYNTHETIC,
        "timetable": ProvenanceLevel.SYNTHETIC,
        "asset_condition": ProvenanceLevel.SYNTHETIC,
        "possession": ProvenanceLevel.SYNTHETIC,
    },
    "REALISTIC_STATIC": ProvenanceLevel.REAL_STATIC,
}


__all__ = [
    "FRONTEND_DATA_PROVENANCE_MAP",
    "ProvenanceLevel",
    "ProvenanceProfile",
    "from_possession_source",
    "from_train_data_provenance",
    "to_possession_source",
]

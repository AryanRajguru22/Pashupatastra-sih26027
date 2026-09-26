# Real public railway data - NDLS to AGC

Pashupatastra can run on an **offline, dated snapshot of published Indian Railways
data** instead of its synthetic dataset. The synthetic dataset is unchanged and stays
the deterministic fallback. The v1 API, the database schema and the CP-SAT model are
unchanged.

```
public documents  --download once-->  data/railway/ndls_agc/sources/  (hashed)
      |                                       |
      |        scripts/real_data/build_ndls_agc_snapshot.py
      v                                       v
  (never at run time)     manifest.json topology.json timetable.json
                          infrastructure.json works.json blocks.json rules.json
                                              |
                     backend/app/data/real_corridor_dataset.py   (verifies every hash)
                                              |
                       CorridorDataset  ->  existing JobService / adapter / CP-SAT
```

## Running it

Both the seed and the backend must see the same variables:

```powershell
$env:PASHUPAT_CORRIDOR_ID  = "CORR-NDLS-AGC"
$env:PASHUPAT_RAILWAY_DATA = "real"          # unset (or "synthetic") = the synthetic fallback
$env:PASHUPAT_JOBS_DB      = "D:\Pashupatastra\demo\jobs.db"   # a dedicated demo database
python scripts/seed_demo.py                  # or --verify to rehearse on a temporary database
python -m uvicorn backend.app.api.main:app --port 8000
```

The console (`frontend/`) is built for the real snapshot by default. To build it for the
synthetic fallback use `NEXT_PUBLIC_RAILWAY_DATA=synthetic npm run build`. The console
compares the backend's reported timetable axis with its own build and shows a warning if
they disagree.

`PASHUPAT_RAILWAY_DATA=real` never falls back to synthetic: an altered, missing or
inconsistent snapshot stops the backend at start-up (`RealSnapshotError`).

## Data classes

Every field carries a provenance record `{value, unit, class, source_ids, derived_from,
derivation_method, confidence}`:

| Class | Meaning |
|---|---|
| `REAL` | Stated directly by a public source (station name/code) |
| `REAL_DATED_SNAPSHOT` | Read from a public document and kept as a dated snapshot |
| `DERIVED_FROM_REAL` | Computed from real values by a stated method (chainage, candidate possession windows) |
| `ENGINEERING_ASSUMPTION` | A modelling choice (two modelled lines, 10-minute buffer, scoring inputs) |
| `DEMO_MAINTENANCE_INPUT` | A controlled demo observation (the seed jobs, asset condition) |
| `UNAVAILABLE_PUBLICLY` | Searched for, not public (freight/EMU timetables, WTT, possession grants) |

The coarse backend enum (`ProvenanceLevel`, three members, pinned by tests) is unchanged. For a
real run the four axes are topology `REAL_STATIC`, timetable `REAL_SCHEDULED`, possession
`SYNTHETIC`, asset condition `SYNTHETIC`; the weakest-link result is `SYNTHETIC`. Possession
is deliberately on the conservative tier: windows derived by rule from a real timetable are a
computed artifact, **never a real or scheduled possession**. Their accurate class,
`DERIVED_FROM_REAL`, is carried by the legacy `possession_source` label
`DERIVED_FROM_PUBLIC_TIMETABLE` (set by the dataset's `DataBasis.possession_source_label`), by
`possession_derivation = CANONICAL_TIMETABLE_DERIVED`, by the snapshot's provenance records and
by the UI, which shows topology/timetable `REAL_DATED_SNAPSHOT`, possession `DERIVED_FROM_REAL`,
maintenance and asset condition `DEMO_MAINTENANCE_INPUT`.

## Distances

Domain (stations, sections, chainage, dataset) = **km**. The frozen v1 API and the database =
**metres** (corridor-absolute). Conversion is explicit at the existing boundaries
(`resource_resolution`, `field_location`, `asset_association`, and the frontend's
`fieldLocation.ts`). The solver uses neither: it keys on `(track_id, section_id)` and minutes.
The UI shows km, with `~` and one decimal for values derived from official km-posts.

## Direction

The railway's convention is **UP = away from Delhi (NDLS to AGC)**. In the real dataset
`UP-1` carries NDLS to AGC trains and `DOWN-1` AGC to NDLS trains. The synthetic dataset uses
the opposite meaning for the same identifiers; identifiers are kept for contract stability.

## Refreshing the snapshot

The snapshot is refreshed deliberately, not at run time. Download the public documents, store
them under `sources/`, update `SOURCES` / the transcribed tables in
`build_ndls_agc_snapshot.py`, then run it (it hashes the files and writes the manifest) and
`scripts/real_data/build_frontend_snapshot.py`. Tests re-verify every transcribed train
against the stored timetable extract and fail if either generated output is stale.

## What is NOT claimed

Not live, not as-run, not a possession grant, not a defect. The timetable is a public
passenger subset (23 trains); freight, EMU/MEMU and the working timetable are not public and
are not invented, so a derived window can overstate free track. See the About page and
`data/railway/ndls_agc/*.json` for the sources, dates and uncertainties.

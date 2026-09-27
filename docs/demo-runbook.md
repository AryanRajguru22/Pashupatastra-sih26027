# Demo runbook: starting Pashupatastra for a presentation

Follow these steps in order. You need two terminals, both opened at the repository
root. The walkthrough itself is in the README, under
[5-minute judge walkthrough](../README.md#5-minute-judge-walkthrough).

## 0. One-time setup

```bash
pip install -r requirements.txt
cd frontend && npm ci && cd ..
```

## 1. Rehearse (recommended, on the presenting machine)

```powershell
$env:PASHUPAT_CORRIDOR_ID  = "CORR-NDLS-AGC"
$env:PASHUPAT_RAILWAY_DATA = "real"
$env:PASHUPAT_JOBS_DB      = "$PWD\demo\jobs.db"
python scripts/seed_demo.py --verify
```

`--verify` runs the whole scripted demo on a **temporary** database and exits
non-zero if any invariant fails:

- every job is placed;
- the approved block is preserved exactly;
- the postponed job respects its not-before date;
- the executed job completes.

It prints the OR-Tools version it verified with. Placement is reproducible only for
the same OR-Tools version and solver configuration.

## 2. Seed and start the backend (Terminal 1)

In the **same** terminal, so that the same three variables are set:

```powershell
python scripts/seed_demo.py
python -m uvicorn backend.app.api.main:app --host 127.0.0.1 --port 8000
```

On Linux or macOS use `export VAR=value` and `"$PWD/demo/jobs.db"` instead. The
path must be absolute.

- `seed_demo.py` wipes **only** the named demo database. It refuses to reset the
  repository-root `jobs.db` or a file that is not a Pashupatastra jobs database.
- It leaves four jobs in the `reported` state and does **not** optimize. SLA clocks
  start when a proposal exists, so optimizing at seed time would let them run down
  before the demo.
- `PASHUPAT_RAILWAY_DATA=real` never falls back to synthetic data. If the snapshot is
  altered or incomplete, the backend stops at start-up with `RealSnapshotError`.

Check that the backend is running:

```bash
curl http://127.0.0.1:8000/health          # {"status":"healthy"}
curl "http://127.0.0.1:8000/v1/jobs?limit=5"  # four seeded jobs
```

## 3. Start the console (Terminal 2)

```bash
cd frontend
npm run build
npm run start      # http://localhost:3000
```

Use `npm run build` + `npm run start` for the presentation. `npm run dev` works for
rehearsal but shows the Next.js development indicator.

Build the console **without** `NEXT_PUBLIC_RAILWAY_DATA` for the real snapshot. Build
with `NEXT_PUBLIC_RAILWAY_DATA=synthetic` only if the backend runs the synthetic
fallback.

## 4. Before you present, check the screen

- The header's API connectivity indicator shows the backend as reachable. When it is
  offline, every action that changes data is disabled.
- **Corridor Overview** lists the seeded jobs. If it says the demo database is not
  seeded, the backend was started without `PASHUPAT_CORRIDOR_ID=CORR-NDLS-AGC` or
  without the same `PASHUPAT_JOBS_DB` as the seed.
- No data-basis mismatch warning is shown. If one is shown, the backend and the
  console build disagree about real vs. synthetic data (see step 3).
- **About This Demo** shows *PUBLIC SNAPSHOT + DEMO INPUTS*.

## 5. Reset between runs

1. Stop the backend (`Ctrl+C` in Terminal 1). The seed cannot delete a database that
   is in use.
2. Run `python scripts/seed_demo.py` again, with the same environment.
3. Start the backend again and refresh the browser.

## 6. If something fails during the demo

The console does not fake success. A failed call shows an error message with the
backend's stable error `code`. When the backend is unreachable, the connectivity
indicator turns offline and every action that changes data is disabled.

To recover, in order of preference:

1. Look in Terminal 1 for a traceback, then restart uvicorn with the same environment.
2. Run `curl http://127.0.0.1:8000/health` again.
3. Refresh the browser tab.

Common causes:

| Symptom | Cause |
|---|---|
| `RealSnapshotError` at start-up | A file under `data/railway/ndls_agc/` was changed. Restore it from git. |
| `AuthConfigurationError` at start-up | A `PASHUPAT_AUTH_*` or `PASHUPAT_IDENTITY_*` variable is set. Unset it (the demo runs in demo mode). |
| Empty queue | The seed and the server were given different `PASHUPAT_JOBS_DB` values. |
| Data-basis warning in the console | The backend data mode and the console build do not match. |

## 7. Stopping cleanly

Press `Ctrl+C` in Terminal 2, then in Terminal 1. Run the test suite in a **new** shell
that does not have the demo variables set.

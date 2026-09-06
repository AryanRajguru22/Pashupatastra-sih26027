# Demo runbook: starting Pashupatastra for a presentation

Follow this exactly, in order. Two terminals, both from the repo root
unless noted.

## 1. Start the backend (Terminal 1)

Working directory: repo root (`Pashupatastra/`).

```bash
python -m uvicorn backend.app.api.main:app --host 127.0.0.1 --port 8000
```

Leave this terminal running for the whole demo.

## 2. Verify the backend is up

```bash
curl http://127.0.0.1:8000/health
```

Expected: `{"status":"healthy"}`. If this fails, fix the backend
*before* starting the frontend — the dashboard's live/fixture
detection depends on the backend already being reachable on first
load.

## 3. Start the frontend (Terminal 2)

Working directory: `frontend/`.

**Production presentation mode (recommended for the actual demo):**

```bash
cd frontend
npm run build
npm run start
```

`npm run start` serves the production build on **http://localhost:3000**.
Production mode is faster to interact with and does not show the
Next.js development-mode indicator that appears under `npm run dev`.

**Development mode (fine for rehearsal/iteration, not for the live pitch):**

```bash
cd frontend
npm run dev
```

Also serves **http://localhost:3000** (or the next free port — watch
the terminal output; if it says a different port, use that URL).

## 4. Recommended startup order

1. Backend first (step 1), confirm healthy (step 2).
2. Frontend second (step 3), production mode.
3. Open `http://localhost:3000` in the browser **after** both are
   confirmed up — loading the dashboard before the backend is ready
   is what triggers fixture fallback mode.

## 5. What should be on screen before you start presenting

- Header badge reads **LIVE BACKEND (FastAPI)** in green — not
  **FIXTURE DEMO MODE** in amber. If it shows fixture mode, the
  backend wasn't reachable when the page loaded: confirm step 2 again,
  then refresh the page.
- The top status bar's **Endpoint** field reads
  `http://127.0.0.1:8000/optimize`.
- The **Schedule Timeline** shows a fully solved plan (status
  `OPTIMAL`, not an error state).
- The **Operational Disruption** panel is visible with all four
  disruption type buttons enabled (not the disabled/amber "Live
  backend required" state).

## 6. If the backend becomes unavailable during the demo

The dashboard does not silently fake success. Expect one of:

- On page load: the header badge switches to amber **FIXTURE DEMO
  MODE**, and the dashboard shows the last-known canonical fixture
  plan instead of a live solve. This is a legitimate fallback and can
  be narrated as such if it happens, but the **Operational Disruption**
  panel will show a disabled state with the message "Live backend
  required to simulate recovery" — the disruption/recovery portion of
  the script cannot be demonstrated in this mode.
- Mid-session (after a page load that was live): clicking **Re-Run
  Optimization** or **Apply Disruption & Recover** will surface a
  visible red error banner with the actual failure reason, rather than
  pretending it worked.

**Recovery steps, in order of preference:**
1. Check Terminal 1 for a backend crash/traceback; restart it with the
   command in step 1.
2. Re-verify with `curl http://127.0.0.1:8000/health`.
3. Refresh the browser tab.
4. If the backend cannot be recovered in time, fall back to narrating
   the PLAN and explainability portions of the script (sections 1–5)
   against fixture data, and verbally walk through what the
   disruption/recovery steps would show using the screenshots/story in
   `docs/demo-script.md` sections 6–9.

## 7. Stopping cleanly

- Terminal 2: `Ctrl+C` stops `npm run start`.
- Terminal 1: `Ctrl+C` stops uvicorn.

# Demo script: PLAN → DISRUPT → RECOVER

This is the presentation script for Pashupatastra (SIH26027). It walks
through the live dashboard exactly as built — every screen, button, and
number referenced below is real: driven by the canonical ML scorer, the
OR-Tools CP-SAT solver, and the live `POST /recover` endpoint, not
scripted or mocked. Total runtime: roughly 6–8 minutes.

Before presenting, follow `docs/demo-runbook.md` to get both servers up
and confirm the dashboard shows the green **LIVE BACKEND (FastAPI)**
badge — the whole script depends on live mode, not fixture fallback.

---

## 1. Problem introduction (30s)

> "Indian Railways has to run engineering maintenance — track renewal,
> ballast tamping, overhead-electrification work, signalling checks —
> without stopping trains. Every maintenance job competes for the same
> scarce resource: a possession window, a time slot where a track is
> taken out of traffic. Today this scheduling is done manually, and
> when something goes wrong mid-day — an asset fails, a track has to
> be shut, an emergency repair shows up — the whole plan has to be
> manually reworked under pressure.
>
> Pashupatastra automates that: it scores maintenance work by
> priority and risk, builds a conflict-free schedule with a real
> constraint solver, and when a disruption hits, it automatically
> recomputes a new feasible plan — in milliseconds, not hours."

## 2. Domain context — what's actually being scheduled (30s)

Point at the **Schedule Timeline** section.

> "This is a real railway corridor model: two tracks, UP-1 and DOWN-1,
> over a 24-hour planning horizon. Each colored block is a maintenance
> job — Track Renewal, Ballast Tamping, OHE Overhead work, Signalling
> & S&T, Routine Inspection, Emergency Repair — tied to a specific
> asset at a specific kilometre marker. The striped bands are
> possession windows: the actual time slots engineering is allowed to
> occupy the track. Jobs also compete for shared maintenance machines
> and crews — you'll see that constraint play out in the recovery
> step."

## 3. AI/ML prioritization (30–45s)

Click any scheduled block (e.g. `BLK-004`) to open the **Decision
Audit** panel on the right.

> "Before anything is scheduled, every candidate job is scored by a
> deterministic ML model — not a black box, a documented 7-feature
> weighted pipeline: asset criticality, defect severity, days
> overdue, failure probability, train impact, maintenance duration,
> and historical failure rate. You can see the exact inputs and the
> resulting Priority and Risk scores right here, for this specific
> block. The model never decides *when* something runs — it only
> tells the optimizer *how much it matters*."

## 4. CP-SAT constraint optimization (30–45s)

> "Those priority and risk scores become the objective function for a
> constraint solver — Google OR-Tools CP-SAT. It maximizes total
> priority and risk addressed, subject to hard constraints that can
> never be violated: no two jobs overlap on the same track, a minimum
> safety headway between jobs, jobs stay inside their possession
> window, some jobs depend on others finishing first, and jobs that
> need the same machine or crew — you'll see labels like
> `CSM_TAMPER_01` or `TOWER_WAGON_01` — can never run at the same
> time anywhere on the corridor, even on different tracks."

Point at the **Pipeline Integration** KPI card (solve time) and the
**Scheduled Blocks** KPI card (status badge).

> "This solve just ran in single-digit milliseconds and came back
> `OPTIMAL` — a mathematically verified, conflict-free 24-hour plan."

## 5. Explainability and decision reasoning (30s)

Still in the **Decision Audit** panel:

> "For every scheduled block, the system states exactly why it landed
> where it did — the track, the time window, and which hard
> constraints were checked. And for anything the solver *couldn't*
> fit — see the red **Solver Exclusions** list — it gives the real
> reason, like losing out to a higher-value competing block, not a
> generic error. Nothing here is invented after the fact; this is the
> solver's own accounting."

## 6. Trigger a realistic disruption (45s)

Scroll to the **Operational Disruption** panel.

> "Now, a disruption. Real railway operations face four kinds we
> support end to end: Asset Breakdown, Track Unavailable, Emergency
> Work, and Possession Curtailment."

Click **Track Unavailable**, pick a track (e.g. `UP-1`) from the
dropdown.

> "I'll simulate UP-1 being fully closed — say, an emergency civil
> works order comes in. Watch what happens when I click Apply."

Click **Apply Disruption & Recover**.

## 7. Live /recover call and automatic re-optimization (30s)

> "That button just made a real HTTP call to `POST /recover` on the
> live FastAPI backend — you can see the endpoint label at the top
> switch from `/optimize` to `/recover`. The backend removed every
> UP-1 job from the plan and re-ran the exact same CP-SAT solver on
> what's left. This is not a pre-canned animation — it's the same
> optimizer, called again, live."

## 8. BEFORE → AFTER impact (45s)

Point at the **Recovery Result** panel and the **PLAN → DISRUPT →
RECOVER** stage indicator in the header, now highlighting **RECOVER**.

> "Here's the before-and-after: solver status, scheduled count,
> unscheduled count, and how many jobs were left completely
> unaffected — all compared directly. Below that, every changed job
> is classified: **Dropped** — removed by the disruption itself, with
> the real reason stated; **Moved** — rescheduled to a new time or
> track, old and new shown side by side; and **Newly Scheduled** — if
> the disruption made room for something that didn't fit before. The
> KPI bar and the timeline itself have already updated to reflect
> this recovered plan — this is now the live operational picture,
> not a hypothetical."

## 9. Return to original plan (15s)

Click **Return to Original Plan**.

> "And because this was a live what-if simulation, we can snap right
> back to the original optimized plan with one click — nothing about
> the underlying data was destroyed."

## 10. Closing impact statement (20s)

> "End to end: real domain data, a real deterministic ML scorer, a
> real constraint solver enforcing real railway safety rules, and a
> real live recovery pipeline — verified by 84 automated backend
> tests, including tests that check every score on screen against the
> scoring model that produced it. Nothing you saw today was hand-typed
> or hard-coded. That's Pashupatastra."

---

## What this script deliberately does NOT claim

- No claim of live train-timetable integration beyond the
  `train_impact` scoring feature already used in prioritization.
- No claim of machine/crew *capacity limits* (e.g. "max 3 jobs per
  machine per day") — only mutual-exclusion (no two jobs on the same
  machine/crew group at the same time) is implemented and demoed.
- No disruption types beyond the four listed above are shown or
  offered, because no others are implemented in the backend.
- No claim that this has been validated against real Indian Railways
  operational data — the corridor and asset data are synthetic-by-design,
  as stated in `README.md`.

# Pashupatastra: final product completion report

Everything below describes the implementation as it exists in this repository. Where something
was not verified, it says so.

## 1. Purpose

Track, signalling and overhead-equipment maintenance on a busy trunk route can only happen while
a section is closed to trains (a block or possession). Planners must decide which defects are most
urgent, find train-free windows with safety margins, avoid conflicting blocks, keep approved blocks
stable, and prove every decision was approved by an accountable authority. Pashupatastra makes each
step explicit and recorded for the New Delhi to Agra Cantt. corridor (SIH26027).

> The optimizer proposes. A human authority decides. The audit trail records.

It is a prototype and decision-support system. It is not certified or deployed on live railway
operations, has no live feed, and uses published passenger-timetable data plus clearly labelled
derived or demonstration inputs.

## 2. Architecture

Next.js console -> FastAPI `/v1` -> `JobService` (intake, location, scoring, lifecycle, execution,
obligations) and `JobOptimizationService` (+ `DisruptionService`) -> timetable adapter (candidate
possession windows) -> OR-Tools CP-SAT -> SQLite (`maintenance_jobs`, `job_events`,
`optimization_runs`, `disruption_events`; the three history tables are append-only by SQL
trigger). Data comes from a hash-verified offline snapshot (`data/railway/ndls_agc`) or a synthetic
fallback. See `README.md` and `docs/architecture.md`.

| Technology | Why |
|---|---|
| Python 3.11, FastAPI, Pydantic v2 | Typed, validated API with a frozen OpenAPI contract |
| OR-Tools CP-SAT | Exact, deterministic combinatorial scheduling with hard constraints; proves optimality |
| SQLite | Single-file, trigger-enforced append-only history; no external service |
| Next.js 16 / React 19 / TypeScript | Operations console, self-hosted fonts, no network at runtime |

## 3. Railway planning workflow

Field report (station + offset -> metres -> one `(track, section)`) -> validated, de-duplicated,
idempotent job -> deterministic 7-feature priority and risk score (`BASELINE_DETERMINISTIC`, not a
trained model) -> candidate possession windows (train-free gaps from the timetable, 10-minute buffer,
20-minute minimum) -> CP-SAT proposal -> authority approve / postpone / reject / release (stale
decisions refused) -> evidence-gated execution -> append-only audit and SLA obligations.

## 4. Optimization methodology

Integer minutes from the horizon start (10 Sep 2026 00:00 IST, two days). Per block: presence,
start, end and one selector per containing window. Hard constraints: time window; exactly one
possession window contains a scheduled block (fail-closed when none covers its track); no overlap
plus 15-minute headway per `(track, section)`; committed blocks pinned. Objective:
`maximize sum 1000 * (risk + priority) * presence`. It rewards *which* jobs are scheduled, not
*when*. Single worker and fixed seed make identical requests reproducible (regression-tested for
the same OR-Tools version). `OPTIMAL` is a statement about the model, not about railway reality.
This work did not change any formula or constraint.

## 5. Disruption lifecycle and recovery

Full design: `docs/DISRUPT_RECOVER_ARCHITECTURE.md`.

- A disruption is a declared, **simulated** input (`SIMULATED_DECLARED`), recorded append-only with
  the declaring actor. Kinds: track unavailable, possession curtailment.
- Every optimization subtracts active disruptions from the derived windows (never adds possession).
  If none would remain the run is refused.
- Approved work a disruption hits is never silently moved or kept: the solve is refused before it
  runs (`RECOVERY_BLOCKED`) until the authority releases the block (or the crew reports an
  in-progress block not-completed). A disrupted proposal cannot be approved.
- Recovery is a real CP-SAT run, linked to the disruption, returning a before/after comparison.
  Recovery commits nothing; new proposals still need approval.

## 6. Authentication

See `docs/NOVAFORGE_INTEGRATION.md`. Demo mode (default) declares identity and enforces nothing.
NovaForge mode is optional and implemented in the backend (OIDC code + PKCE, server-side
sessions, CSRF, enforcing policy) and in the console (sign-in gate, role chooser, sign-out, fail
closed). NovaForge is not required for the product to work. In NovaForge mode three new permissions
must be added on the NovaForge side before disruption routes work (listed in that document).

## 7. Test results

The final full run is recorded in section 12. Counts earlier in the work: baseline 3432 passed,
1 skipped; after the disruption feature 3479-3480 passed, 1 skipped. The console has no automated
UI tests.

## 8. Real-browser verification (demo mode, real public-data backend)

Chrome, console production build on `localhost:3000`, backend `127.0.0.1:8000`
(`PASHUPAT_RAILWAY_DATA=real`, freshly seeded `demo/jobs.db`). The scenario was prepared by
calling the API (optimize, then the authority approves one block); everything after was done in the
browser unless stated.

| Step | Observed |
|---|---|
| App start, navigation | Console loads; header shows BACKEND CONNECTED, DATA: PUBLIC SNAPSHOT + DEMO INPUTS, IDENTITY: DECLARED (DEMO MODE) |
| Worker persona | `/disruptions` shows the empty state; **Declare disruption** disabled |
| Declare (engineer) | DOWN-1 / FDB-PWL, 11 Sep 18:00-20:30, reason recorded. Page shows `SIMULATED_DECLARED`, `ACTIVE`, and the approved job `JOB-886B0B7069` (18:30-20:00) flagged **Authority must release** |
| Recover before release | Red banner `REFUSED RECOVERY_BLOCKED`: "Recovery refused before any solve: approved work overlaps an active disruption (JOB-886B0B7069)..." |
| Release (authority, Authority Review) | Block released with a recorded reason; after a full reload the disruption no longer lists a blocking block |
| Recover after release | `OPTIMAL`, 1 active disruption applied, 138 windows after subtraction; **3 unchanged, 1 moved, 0 newly unscheduled, 0 newly scheduled**; the released job moved from 11 Sep 18:30-20:00 (notified, withdrawn: BLOCK_RELEASED) to 21:28-22:58 (scheduled), outside the disrupted span |
| Audit trail | `REGISTERED`, `RECOVERY_REFUSED`, `RECOVERY_PLANNED` (run id, `OPTIMAL`), each with actor and assurance `DECLARED_UNVERIFIED` |
| Persistence | Backend process stopped and restarted; disruption, status and all three audit events returned unchanged after a reload |
| Network | `/auth/config`, `/health`, `/v1/jobs`, `/v1/jobs/{id}/history` all 200 |

Not verified in a browser: browser console errors (the tool only captures messages after its first
call, and no messages were captured on the later loads, which is not proof of absence); the
NovaForge sign-in gate; invalid-login and session-expiry behaviour (those exist only in NovaForge
mode); synthetic-mode console journey (covered by `seed_demo.py --verify` and tests, not by a
browser). Placements vary with seed data and OR-Tools version; ids are random.

## 9. Demo sequence

Setup (README Quick start, real mode): `seed_demo.py`, start backend and console. Then:

1. Corridor Overview: stylised view of the NDLS-AGC corridor and the four queued jobs.
2. Planning (engineer): Run optimization -> `OPTIMAL`, all jobs placed.
3. Authority Review (authority): approve the critical rail-fracture block.
4. Disruption & Recovery (engineer): Declare disruption over that block's track, section and span.
   Expect the block marked "Authority must release".
5. Run recovery. Expect `RECOVERY_BLOCKED` naming the job. Say: the optimizer will not move or keep
   an approved block on its own; nothing was solved or changed.
6. Authority Review (authority): Release the block with a reason.
7. Disruption & Recovery: Run recovery. Expect `OPTIMAL`, a comparison with the block moved out of
   the disrupted span, and the audit trail `REGISTERED / RECOVERY_REFUSED / RECOVERY_PLANNED`.
8. Authority Review: approve the re-planned proposal. Then Execution as in the walkthrough.
9. Optional: restart the backend and reload to show state persists.

`python scripts/seed_demo.py --verify` rehearses plan and disrupt/recover offline, in both data
modes. Nothing here needs an external service.

## 10. Known limitations and future scope

Candidate windows can overstate free track (freight, EMU and the working timetable are not public).
Two-day fixed horizon; two modelled lines; six sections. The objective has no time preference, so
recovery preserves a maximum-value set, not a minimum-change plan. Disruptions are declared, not
detected. Asset condition and SLA values are assumed demo inputs. NovaForge mode is not verified
end to end against a running NovaForge; NovaForge needs the three new permissions. No Docker
files. Future: minimum-change recovery objective, richer disruption kinds, durable asset register,
multi-instance locking, automated console tests, console sign-in verification against NovaForge.

## 12. Final regression results (2026-11-15)

| Check | Result |
|---|---|
| Backend suite `python -m pytest -q` | **3480 passed, 1 skipped**, 52 subtests passed (about 2 min). The skip needs POSIX FIFOs |
| `tsc --noEmit`, `npm run lint` | clean, no warnings |
| `npm run build` | succeeded; 13 routes including `/disruptions` and `/auth/error` |
| `seed_demo.py --verify`, synthetic mode | VERIFY PASS (plan run and disrupt/refuse/release/recover) |
| `seed_demo.py --verify`, real-data mode | VERIFY PASS |
| Console automated tests | none exist |
| NovaForge cross-application test | not run (needs PostgreSQL; none available here) |

## 11. Git

See the final commit recorded in the session summary and `git log`.

"use client";

import { useState } from "react";
import { ApiError, api, type Job, type Proposal } from "@/lib/api";
import { HORIZON_DATES, planWindow, shortId } from "@/lib/time";
import { useSession } from "@/lib/session";
import {
  BTN_DANGER,
  BTN_GHOST,
  BTN_PRIMARY,
  BTN_WARN,
  DemoModeNote,
  ErrorBox,
  INPUT,
  Kv,
  LABEL,
  Modal,
  Spinner,
  StatusChip,
  useToast,
} from "@/components/ui";

type Kind = "approve" | "postpone" | "reject" | "release";

const DATE_LABEL: Record<string, string> = {
  "2026-09-10": "10 Sep 2026 (horizon day 1)",
  "2026-09-11": "11 Sep 2026 (horizon day 2)",
};

/**
 * Authority decision bar for a `scheduled` job (approve / postpone / reject)
 * or a `notified` job (release). Every action re-fetches the proposal first
 * and acts on THAT run id, so a stale proposal is never committed.
 */
export default function DecisionBar({
  job,
  onDone,
}: {
  job: Job;
  onDone?: () => void;
}) {
  const { persona, online, bump } = useSession();
  const toast = useToast();
  const [kind, setKind] = useState<Kind | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [loading, setLoading] = useState<Kind | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [date, setDate] = useState<string>("2026-09-11");

  const isAuthority = persona.role === "AUTHORITY";
  const disabled = !isAuthority || online === false;
  const why = !isAuthority
    ? "Switch to the Authority persona (demo role gating; not server-enforced)."
    : online === false
      ? "Backend offline"
      : undefined;

  const openDialog = async (k: Kind) => {
    setError(null);
    setNotice(null);
    setReason("");
    setDate("2026-09-11");
    setLoading(k);
    try {
      if (k === "release") {
        // Release names the committed run, which is the job's current one.
        setProposal(null);
        if (!job.proposal_run_id) {
          throw new ApiError(0, "NO_RUN", "This job carries no proposal run id.");
        }
      } else {
        setProposal(await api.proposal(job.job_id));
      }
      setKind(k);
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, "UNKNOWN", String(e));
      setError(err);
      toast(`${err.code}: ${err.detail}`, "warn");
    } finally {
      setLoading(null);
    }
  };

  const close = () => {
    if (busy) return;
    setKind(null);
    setError(null);
    setNotice(null);
  };

  const runId =
    kind === "release" ? (job.proposal_run_id ?? "") : (proposal?.optimization_run_id ?? "");
  const reasonOk = reason.trim().length >= 1 && reason.length <= 2000;

  const confirm = async () => {
    if (!kind || !runId) return;
    setBusy(true);
    setError(null);
    try {
      let res: { job: Job; message: string };
      if (kind === "approve") res = await api.approve(persona, job.job_id, runId);
      else if (kind === "reject")
        res = await api.reject(persona, job.job_id, runId, reason.trim());
      else if (kind === "postpone")
        res = await api.postpone(persona, job.job_id, runId, reason.trim(), date);
      else res = await api.release(persona, job.job_id, runId, reason.trim());

      const extra =
        kind === "postpone"
          ? ` Postponed. The job returns to the queue and will not be placed before ${date === "2026-09-11" ? "11" : "10"} Sep 00:00 IST at the next optimization run.`
          : kind === "reject"
            ? " The job returns to the queue and stays eligible for the next optimization run."
            : "";
      toast(`${res.message} (${res.job.job_id} → ${res.job.status}).${extra}`);
      setKind(null);
      bump();
      onDone?.();
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, "UNKNOWN", String(e));
      if (err.code === "STALE_PROPOSAL" && kind !== "release") {
        setNotice(
          "This proposal was superseded by a newer optimization run. The current proposal has been reloaded; review it again.",
        );
        try {
          setProposal(await api.proposal(job.job_id));
        } catch (e2) {
          setError(e2 instanceof ApiError ? e2 : err);
        }
        bump();
      } else {
        setError(err);
        if (
          err.code === "NO_CURRENT_PROPOSAL" ||
          err.code === "CONCURRENT_MODIFICATION"
        )
          bump();
      }
    } finally {
      setBusy(false);
    }
  };

  const confirmText =
    kind === "approve"
      ? "Approve and commit block"
      : kind === "postpone"
        ? "Postpone proposal"
        : kind === "reject"
          ? "Reject proposal"
          : "Release committed block";

  const needsReason = kind === "postpone" || kind === "reject" || kind === "release";

  return (
    <>
      {job.status === "scheduled" && (
        <div className="flex flex-wrap items-center gap-space-sm">
          <button
            type="button"
            className={BTN_PRIMARY}
            disabled={disabled || loading !== null}
            title={why}
            onClick={() => openDialog("approve")}
          >
            {loading === "approve" && <Spinner />}
            <span className="material-symbols-outlined text-[16px]">verified</span>
            Approve &amp; commit
          </button>
          <button
            type="button"
            className={BTN_WARN}
            disabled={disabled || loading !== null}
            title={why}
            onClick={() => openDialog("postpone")}
          >
            {loading === "postpone" && <Spinner />}
            Postpone…
          </button>
          <button
            type="button"
            className={BTN_DANGER}
            disabled={disabled || loading !== null}
            title={why}
            onClick={() => openDialog("reject")}
          >
            {loading === "reject" && <Spinner />}
            Reject…
          </button>
          {why && (
            <span className="font-label-mono text-[11px] text-outline">{why}</span>
          )}
        </div>
      )}
      {job.status === "notified" && (
        <div className="flex flex-wrap items-center gap-space-sm">
          <button
            type="button"
            className={BTN_GHOST}
            disabled={disabled || loading !== null}
            title={why}
            onClick={() => openDialog("release")}
          >
            {loading === "release" && <Spinner />}
            <span className="material-symbols-outlined text-[16px]">lock_open</span>
            Release committed block…
          </button>
          {why && (
            <span className="font-label-mono text-[11px] text-outline">{why}</span>
          )}
        </div>
      )}
      {error && !kind && <ErrorBox error={error} />}

      <Modal open={kind !== null} title={confirmText} onClose={close} busy={busy}>
        {notice && (
          <div className="rounded-DEFAULT bg-secondary-container/25 px-space-md py-space-sm font-body-sm text-body-sm text-secondary">
            {notice}
          </div>
        )}

        <div className="rounded-DEFAULT bg-surface-container-lowest px-space-md py-1">
          <Kv k="JOB">{job.job_id}</Kv>
          <Kv k="STATE CHANGE">
            <span className="inline-flex items-center gap-2">
              <StatusChip status={job.status} />
              <span className="material-symbols-outlined text-[14px] text-outline">
                arrow_forward
              </span>
              <StatusChip
                status={kind === "approve" ? "notified" : "reported"}
              />
            </span>
          </Kv>
          {kind !== "release" && proposal && (
            <Kv k="CURRENT PLACEMENT (PLAN)">
              {planWindow(proposal.start_minute, proposal.end_minute)} (
              {proposal.start_minute}–{proposal.end_minute})
            </Kv>
          )}
          {kind === "release" &&
            job.schedule_start_minute != null &&
            job.schedule_end_minute != null && (
              <Kv k="COMMITTED PLACEMENT (PLAN)">
                {planWindow(job.schedule_start_minute, job.schedule_end_minute)}
              </Kv>
            )}
          <Kv k="ACTING ON RUN">
            <span title={runId}>{shortId(runId, 12)}</span>
          </Kv>
          <Kv k="ACTING AS">
            {persona.id} ({persona.role}) — declared, not verified
          </Kv>
        </div>

        {kind === "approve" && (
          <p className="font-body-sm text-body-sm text-on-surface-variant">
            Approving pins this block for every later optimization. The status
            value becomes <code className="font-label-mono">notified</code>, but
            no notification is sent anywhere. The obligation moves to
            execution start (owed by WORKER).
          </p>
        )}

        {kind === "postpone" && (
          <div className="flex flex-col gap-space-xs">
            <span className={LABEL}>Not before (planning horizon date)</span>
            <div
              role="radiogroup"
              aria-label="Not before date"
              className="grid grid-cols-2 p-1 rounded-DEFAULT bg-surface-container-lowest gap-1"
            >
              {HORIZON_DATES.map((d) => (
                <button
                  key={d}
                  type="button"
                  role="radio"
                  aria-checked={date === d}
                  onClick={() => setDate(d)}
                  className={`py-2 rounded-DEFAULT font-label-mono text-label-mono ${date === d ? "bg-surface-bright text-primary font-bold" : "text-on-surface-variant hover:text-on-surface"}`}
                >
                  {DATE_LABEL[d]}
                </button>
              ))}
            </div>
            <p className="font-label-mono text-[11px] text-outline">
              The demo planning horizon is 10–11 Sep 2026 (synthetic
              timetable). This is the plan date, not today&apos;s date.
              {date === "2026-09-10" &&
                " Choosing 10 Sep is allowed but has no scheduling effect (it equals the horizon start)."}
            </p>
          </div>
        )}

        {kind === "reject" && (
          <div className="rounded-DEFAULT border-l-2 border-secondary/60 bg-secondary-container/15 px-space-md py-space-sm font-body-sm text-body-sm text-on-surface-variant">
            Rejecting records your decision and reason and returns the job to
            the queue. The job stays eligible for the next optimization run.
            The optimizer does <strong>not</strong> search for an alternative
            window. It may propose the same placement again. To keep work out
            of a date, use Postpone.
          </div>
        )}

        {kind === "release" && (
          <p className="font-body-sm text-body-sm text-on-surface-variant">
            Release is for an approved block that cannot start (possession not
            granted, crew or safety restriction). The job returns to the queue
            and a re-planning obligation is owed by ENGINEER.
          </p>
        )}

        {needsReason && (
          <label className="flex flex-col gap-space-2xs">
            <span className={`${LABEL} flex justify-between`}>
              <span>Reason *</span>
              <span className={reason.length > 2000 ? "text-error" : "text-outline"}>
                {reason.length}/2000
              </span>
            </span>
            <textarea
              className={`${INPUT} min-h-24 resize-y`}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Recorded in the audit trail"
            />
          </label>
        )}

        <DemoModeNote />
        {error && <ErrorBox error={error} />}

        <div className="flex justify-end gap-space-sm">
          <button type="button" className={BTN_GHOST} onClick={close} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className={kind === "reject" ? BTN_DANGER : kind === "postpone" ? BTN_WARN : BTN_PRIMARY}
            disabled={busy || !runId || (needsReason && !reasonOk) || online === false}
            onClick={confirm}
          >
            {busy && <Spinner />}
            {confirmText}
          </button>
        </div>
      </Modal>
    </>
  );
}

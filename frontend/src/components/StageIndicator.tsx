"use client";

export type OperationalStage = "PLAN" | "DISRUPT" | "RECOVER";

const STAGES: { key: OperationalStage; label: string; dotClass: string; tintClass: string }[] = [
  { key: "PLAN", label: "Plan", dotClass: "bg-accent-cyan", tintClass: "bg-accent-cyan/10 border-accent-cyan/30 text-accent-cyan" },
  { key: "DISRUPT", label: "Disrupt", dotClass: "bg-red-400", tintClass: "bg-red-400/10 border-red-400/30 text-red-300" },
  { key: "RECOVER", label: "Recover", dotClass: "bg-accent-amber", tintClass: "bg-accent-amber/10 border-accent-amber/30 text-accent-amber" },
];

interface StageIndicatorProps {
  stage: OperationalStage;
  /** Only PLAN is ever clickable - it's the one real, safe action (return to the
   * baseline plan). Disrupt/Recover are status-only: there is no real state to
   * jump to without going through the actual form/recovery flow. */
  onSelectPlan?: () => void;
}

export default function StageIndicator({ stage, onSelectPlan }: StageIndicatorProps) {
  return (
    <div className="flex items-center gap-1 px-1 py-1 rounded-full bg-white/[0.03] border border-white/[0.06]">
      {STAGES.map((s, i) => {
        const active = s.key === stage;
        const isPlan = s.key === "PLAN";
        const clickable = isPlan && !active && !!onSelectPlan;

        return (
          <div key={s.key} className="flex items-center">
            <button
              type="button"
              disabled={!clickable}
              onClick={clickable ? onSelectPlan : undefined}
              title={clickable ? "Return to original plan" : undefined}
              className={`flex items-center gap-1.5 px-3 py-1 rounded-full border font-mono-data text-[0.68rem] tracking-widest uppercase transition-all ${
                active
                  ? `${s.tintClass} font-bold`
                  : `border-transparent text-muted ${clickable ? "hover:text-accent-cyan cursor-pointer" : ""}`
              }`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${active ? s.dotClass : "bg-white/15"}`} />
              {s.label}
            </button>
            {i < STAGES.length - 1 && <span className="text-white/15 text-xs mx-0.5">&mdash;</span>}
          </div>
        );
      })}
    </div>
  );
}

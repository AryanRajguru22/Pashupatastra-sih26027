"use client";

export type OperationalStage = "PLAN" | "DISRUPT" | "RECOVER";

const STAGES: { key: OperationalStage; label: string; dotClass: string }[] = [
  { key: "PLAN", label: "Plan", dotClass: "bg-emerald-400" },
  { key: "DISRUPT", label: "Disrupt", dotClass: "bg-red-400" },
  { key: "RECOVER", label: "Recover", dotClass: "bg-blue-400" },
];

/**
 * Minimal PLAN -> DISRUPT -> RECOVER breadcrumb. Not a wizard: purely a
 * read-only indicator of which stage of the operational story is
 * currently on screen, sized to sit inline in the existing status bar.
 */
export default function StageIndicator({ stage }: { stage: OperationalStage }) {
  return (
    <div className="flex items-center gap-1.5 text-[11px] font-mono select-none">
      {STAGES.map((s, i) => {
        const active = s.key === stage;
        return (
          <div key={s.key} className="flex items-center gap-1.5">
            <span
              className={`flex items-center gap-1 ${
                active ? "text-[#e2e8f0] font-semibold" : "text-[#475569]"
              }`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  active ? s.dotClass : "bg-[#334155]"
                }`}
              />
              {s.label.toUpperCase()}
            </span>
            {i < STAGES.length - 1 && (
              <span className="text-[#334155]">→</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

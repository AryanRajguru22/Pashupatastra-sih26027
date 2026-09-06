"use client";

import type { OperationalStage } from "@/lib/stage";

// Matches package.json's real "version" field - static build metadata, not
// a fabricated live-telemetry status.
const APP_VERSION = "0.1.0";

interface AppHeaderProps {
  stage: OperationalStage;
  isLiveBackend: boolean;
  solveTimeMs: number;
  onNavPlan: () => void;
  onNavDisrupt: () => void;
}

/**
 * Ported verbatim from the shared header markup in stitch/screen-2/code.html
 * and stitch/screen-3/code.html (the two exports share byte-identical header
 * structure/classes). The brand image and the 5-item nav's inert destinations
 * are the only things not literally portable (no real multi-page routing, no
 * real logo asset) - kept as the same visual shape, with only PLAN/DISRUPT
 * wired to a real state transition since those are the only two the app
 * actually has.
 */
export default function AppHeader({
  stage,
  isLiveBackend,
  solveTimeMs,
  onNavPlan,
  onNavDisrupt,
}: AppHeaderProps) {
  return (
    <header className="fixed top-0 w-full z-50 bg-surface-container-lowest/80 backdrop-blur-2xl shadow-[0_1px_12px_rgba(0,0,0,0.4)]">
      <div className="h-20 w-full px-gutter-desktop flex items-center justify-between gap-space-md">
        <div className="flex items-center gap-space-md shrink-0">
          <div className="h-8 w-8 rounded-sm bg-primary-container flex items-center justify-center text-on-primary-container font-headline-sm text-headline-sm font-bold">
            P
          </div>
          <div className="flex flex-col">
            <div className="flex items-center gap-space-xs">
              <span className="font-headline-sm text-headline-sm tracking-tight text-primary">
                PASHUPATASTRA
              </span>
              <span className="font-label-mono text-[10px] text-on-surface-variant px-1.5 py-0.5 rounded bg-surface-container-high/80">
                v{APP_VERSION}
              </span>
            </div>
            <span className="font-label-caps text-label-caps text-on-surface-variant">
              RAILWAY MAINTENANCE INTELLIGENCE
            </span>
          </div>
        </div>

        <nav className="hidden xl:flex items-center p-space-2xs rounded-full bg-surface-container-low/70 backdrop-blur-xl">
          <span className="font-label-mono text-label-mono px-space-md py-space-xs rounded-full text-on-surface-variant/40 cursor-not-allowed">
            Command Center
          </span>
          <button
            onClick={onNavPlan}
            className={`font-label-mono text-label-mono px-space-md py-space-xs rounded-full transition-all ${
              stage === "PLAN"
                ? "bg-primary-container text-on-primary-container font-medium shadow-[0_0_16px_rgba(0,240,255,0.25)]"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
          >
            Maintenance Planning
          </button>
          <button
            onClick={onNavDisrupt}
            className={`font-label-mono text-label-mono px-space-md py-space-xs rounded-full transition-all ${
              stage === "DISRUPT" || stage === "RECOVER"
                ? "bg-primary-container text-on-primary-container font-medium shadow-[0_0_16px_rgba(0,240,255,0.25)]"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
          >
            Disruption Simulation
          </button>
          <span className="font-label-mono text-label-mono px-space-md py-space-xs rounded-full text-on-surface-variant/40 cursor-not-allowed">
            Decision Intelligence
          </span>
          <span className="font-label-mono text-label-mono px-space-md py-space-xs rounded-full text-on-surface-variant/40 cursor-not-allowed">
            System Status
          </span>
        </nav>

        <div className="flex items-center gap-space-lg shrink-0">
          <div className="hidden md:flex items-center gap-space-md">
            <div className="flex items-center gap-space-xs px-space-sm py-space-2xs rounded-full bg-surface-container-low">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  isLiveBackend ? "bg-primary-container animate-ping" : "bg-secondary"
                }`}
              />
              <span className="font-label-caps text-label-caps text-on-surface">
                {isLiveBackend ? "LIVE BACKEND: CONNECTED" : "FIXTURE MODE: OFFLINE"}
              </span>
            </div>
            <div className="flex items-center gap-space-xs px-space-sm py-space-2xs rounded-full bg-surface-container-low">
              <span className="font-label-caps text-label-caps text-on-surface-variant">
                SOLVER: CP-SAT {solveTimeMs}ms
              </span>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}

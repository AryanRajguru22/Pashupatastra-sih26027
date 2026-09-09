"use client";

import type { AppView, OperationalStage } from "@/lib/stage";
import type {
  ConnectivityStatus,
  DataProvenance,
} from "@/types/contracts";
import {
  connectivityLabel,
  isBackendConnected,
  isLiveData,
  provenanceLabel,
} from "@/lib/dataStatus";

// Matches package.json's real "version" field - static build metadata, not
// a fabricated live-telemetry status.
const APP_VERSION = "0.1.0";

interface AppHeaderProps {
  stage: OperationalStage;
  activeView: AppView;
  connectivity: ConnectivityStatus;
  dataProvenance: DataProvenance;
  solveTimeMs: number;
  onNavCommand: () => void;
  onNavPlan: () => void;
  onNavDisrupt: () => void;
}

/**
 * Adapted from the shared header markup in stitch/screen-2/code.html and
 * stitch/screen-3/code.html (the two exports share byte-identical header
 * structure/classes), which originally had 5 nav items ("Decision
 * Intelligence" and "System Status" among them) with no real destination.
 * Those two are intentionally NOT ported here: nothing in the backend
 * distinctly backs them (System Status's content already lives in the
 * Command Center panel), and a permanently-disabled nav item that never
 * does anything is dead UI, not an honest empty state - so it's removed
 * rather than kept as an inert placeholder. Only the three real
 * destinations remain, each wired to a real state transition.
 */
export default function AppHeader({
  stage,
  activeView,
  connectivity,
  dataProvenance,
  solveTimeMs,
  onNavCommand,
  onNavPlan,
  onNavDisrupt,
}: AppHeaderProps) {
  return (
    <header className="fixed top-0 w-full z-50 bg-surface-container-lowest/80 backdrop-blur-2xl shadow-[0_1px_12px_rgba(0,0,0,0.4)]">
      <div className="h-20 w-full px-gutter-desktop flex items-center justify-between gap-space-md">
        {/* Always-visible home affordance: below the `xl` breakpoint the nav
         * pills below are hidden (there's no room for them), so the logo is
         * the only way back to Command Center at narrower widths - it must
         * stay clickable regardless of viewport so no screen is ever a
         * navigational dead end. */}
        <button
          onClick={onNavCommand}
          aria-label="Go to Command Center"
          className="flex items-center gap-space-md shrink-0 text-left group"
        >
          <div className="h-8 w-8 rounded-sm bg-primary-container flex items-center justify-center text-on-primary-container font-headline-sm text-headline-sm font-bold transition-transform group-hover:scale-105">
            P
          </div>
          <div className="flex flex-col">
            <div className="flex items-center gap-space-xs">
              <span className="font-headline-sm text-headline-sm tracking-tight text-primary group-hover:opacity-80 transition-opacity">
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
        </button>

        <nav className="hidden xl:flex items-center p-space-2xs rounded-full bg-surface-container-low/70 backdrop-blur-xl">
          <button
            onClick={onNavCommand}
            className={`font-label-mono text-label-mono px-space-md py-space-xs rounded-full transition-all active:scale-95 ${
              activeView === "COMMAND"
                ? "bg-primary-container text-on-primary-container font-medium shadow-[0_0_16px_rgba(0,240,255,0.25)]"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
          >
            Command Center
          </button>
          <button
            onClick={onNavPlan}
            className={`font-label-mono text-label-mono px-space-md py-space-xs rounded-full transition-all active:scale-95 ${
              activeView === "WORKSPACE" && stage === "PLAN"
                ? "bg-primary-container text-on-primary-container font-medium shadow-[0_0_16px_rgba(0,240,255,0.25)]"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
          >
            Maintenance Planning
          </button>
          <button
            onClick={onNavDisrupt}
            className={`font-label-mono text-label-mono px-space-md py-space-xs rounded-full transition-all active:scale-95 ${
              activeView === "WORKSPACE" && (stage === "DISRUPT" || stage === "RECOVER")
                ? "bg-primary-container text-on-primary-container font-medium shadow-[0_0_16px_rgba(0,240,255,0.25)]"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
          >
            Disruption Simulation
          </button>
        </nav>

        <div className="flex items-center gap-space-lg shrink-0">
          <div className="hidden md:flex items-center gap-space-md">
            {/* Connectivity and provenance are rendered as two separate
              * badges on purpose. A reachable backend proves only that the
              * backend answered; the data it solved is whatever we posted,
              * which is currently a synthetic fixture. Merging these into a
              * single "LIVE" badge is what previously made the header claim
              * live data over checked-in demo data. */}
            <div className="flex items-center gap-space-xs px-space-sm py-space-2xs rounded-full bg-surface-container-low">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  isBackendConnected(connectivity)
                    ? "bg-primary-container animate-ping"
                    : "bg-secondary"
                }`}
              />
              <span className="font-label-caps text-label-caps text-on-surface">
                {connectivityLabel(connectivity)}
              </span>
            </div>
            <div className="flex items-center gap-space-xs px-space-sm py-space-2xs rounded-full bg-surface-container-low">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  isLiveData(dataProvenance) ? "bg-primary-container" : "bg-secondary"
                }`}
              />
              <span className="font-label-caps text-label-caps text-on-surface">
                {provenanceLabel(dataProvenance)}
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

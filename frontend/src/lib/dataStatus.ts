/**
 * Presentation of the two independent data-status facts.
 *
 * The rule these helpers exist to enforce: a reachable backend proves
 * CONNECTIVITY, never PROVENANCE. The dashboard posts a checked-in
 * synthetic fixture, so no amount of backend health makes the displayed
 * data live, and the UI must never say otherwise.
 */

import type {
  ConnectivityStatus,
  DataProvenance,
} from "@/types/contracts";

/** Short badge text for the backend connection itself. */
export function connectivityLabel(status: ConnectivityStatus): string {
  switch (status) {
    case "ONLINE":
      return "BACKEND: CONNECTED";
    case "DEGRADED":
      return "BACKEND: DEGRADED";
    case "OFFLINE":
      return "BACKEND: OFFLINE";
  }
}

/** Short badge text for where the displayed data actually came from. */
export function provenanceLabel(provenance: DataProvenance): string {
  switch (provenance) {
    case "SYNTHETIC_FIXTURE":
      return "DATA: SYNTHETIC FIXTURE";
    case "REALISTIC_STATIC":
      return "DATA: REALISTIC STATIC";
    case "LIVE_FEED":
      return "DATA: LIVE FEED";
  }
}

/**
 * Freshness. A checked-in fixture has no generation time, and inventing
 * one would be exactly the dishonesty this module exists to prevent.
 */
export function freshnessLabel(generatedAt?: string): string {
  if (!generatedAt) {
    return "AGE: UNKNOWN";
  }

  const parsed = Date.parse(generatedAt);
  if (Number.isNaN(parsed)) {
    return "AGE: UNKNOWN";
  }

  const minutes = Math.max(0, Math.round((Date.now() - parsed) / 60000));

  if (minutes < 1) return "AGE: <1 MIN";
  if (minutes < 60) return `AGE: ${minutes} MIN`;

  return `AGE: ${Math.round(minutes / 60)} H`;
}

/** True only when the data itself is a real live feed. */
export function isLiveData(provenance: DataProvenance): boolean {
  return provenance === "LIVE_FEED";
}

/** True when the backend answered - says nothing about the data. */
export function isBackendConnected(status: ConnectivityStatus): boolean {
  return status === "ONLINE";
}

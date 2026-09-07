export type OperationalStage = "PLAN" | "DISRUPT" | "RECOVER";

/**
 * Top-level view: COMMAND is the real-data operational overview (Command
 * Center); WORKSPACE is the existing PLAN/DISRUPT/RECOVER flow driven by
 * OperationalStage.
 */
export type AppView = "COMMAND" | "WORKSPACE";

// Pure helpers that turn API data into what the UI shows. Kept free of React for testing.

import type { Compile, Decision, Vehicle } from "./api";

/** The one-word gate state of a vehicle, which also picks its colour. */
export type GateState = "open" | "blocked" | "unknown" | "idle";

export function gateState(vehicle: Vehicle): GateState {
  if (!vehicle.resolution.ok || (vehicle.link !== null && !vehicle.link.connected)) {
    return "unknown";
  }
  if (vehicle.last_decision === null || !isCurrent(vehicle.last_decision, vehicle)) return "idle";
  return vehicle.last_decision.result === "PASS" ? "open" : "blocked";
}

/**
 * A decision describes the vehicle as it is now only if it was made against the dictionary
 * the vehicle is running now. After a reflash, earlier decisions are history, not status.
 */
export function isCurrent(decision: Decision, vehicle: Vehicle): boolean {
  return (
    vehicle.running_dictionary !== null &&
    decision.running_dictionary_hash === vehicle.running_dictionary.hash
  );
}

export const STATE_LABEL: Record<GateState, string> = {
  open: "Last check passed",
  blocked: "Last check blocked",
  unknown: "Build not confirmed",
  idle: "Not checked on this build",
};

export const STATE_COLOR: Record<GateState, string> = {
  open: "#3ecf6e",
  blocked: "#f0544c",
  unknown: "#e0a53a",
  idle: "#7c8590",
};

export function shortHash(hash: string | null | undefined, length = 12): string {
  return hash ? hash.slice(0, length) : "unknown";
}

export function sequenceName(path: string): string {
  const file = path.split("/").pop() ?? path;
  return file.replace(/\.fpy$/, "");
}

export function ageText(seconds: number | null): string {
  if (seconds === null) return "never";
  if (seconds < 1.5) return "just now";
  if (seconds < 90) return `${Math.round(seconds)} s ago`;
  return `${Math.round(seconds / 60)} min ago`;
}

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour12: false });
}

/** The newest decision for one bytecode on one vehicle, if any. */
export function decisionFor(
  decisions: Decision[],
  vehicleId: string,
  bytecodeSha256: string,
): Decision | undefined {
  return decisions.find(
    (d) => d.vehicle_id === vehicleId && d.bytecode_sha256 === bytecodeSha256,
  );
}

/** Blocking reasons of a decision as one line, or "All checks passed". */
export function reasonLine(decision: Decision): string {
  const blocking = decision.checks.filter((c) => c.status === "BLOCK");
  if (blocking.length === 0) return "All checks passed";
  return blocking.map((c) => `${c.check}: ${c.reason}`).join(", ");
}

/** One distinct bytecode file, with every vehicle it was compiled for. */
export interface Bytecode {
  /** The newest compile record of these bytes. */
  compile: Compile;
  compiledFor: string[];
}

/** Compiles grouped by their bytes, newest first. Two vehicles on one build share bytecode. */
export function distinctBytecode(compiles: Compile[]): Bytecode[] {
  const byHash = new Map<string, Bytecode>();
  for (const compile of compiles) {
    const known = byHash.get(compile.bytecode_sha256);
    if (!known) {
      byHash.set(compile.bytecode_sha256, {
        compile,
        compiledFor: compile.vehicle_id ? [compile.vehicle_id] : [],
      });
    } else if (compile.vehicle_id && !known.compiledFor.includes(compile.vehicle_id)) {
      known.compiledFor.push(compile.vehicle_id);
    }
  }
  return [...byHash.values()];
}

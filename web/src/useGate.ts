// Live state of the gate service: polled REST data, refreshed early whenever the
// WebSocket reports that something changed. Compile jobs are also polled directly, so the
// UI shows their result even when the WebSocket is not connected.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type Compile,
  type CompileJob,
  type Decision,
  type GateConfig,
  type GateEvent,
  type Sequence,
  type Vehicle,
  type VehicleDetail,
} from "./api";
import { reasonLine } from "./state";

export interface Beam {
  kind: "sent" | "refused";
  until: number;
}

export interface Outcome {
  tone: "ok" | "blocked" | "failed" | "working";
  text: string;
}

export interface Gate {
  vehicles: Vehicle[];
  detail: VehicleDetail | null;
  compiles: Compile[];
  decisions: Decision[];
  sequences: Sequence[];
  jobs: CompileJob[];
  config: GateConfig | null;
  beams: Record<string, Beam>;
  connected: boolean;
  error: string | null;
  busy: Record<string, boolean>;
  outcome: Record<string, Outcome>;
  /** Compile `source` for each of `targets`, at the same time, each with its own dictionary. */
  compile: (name: string, source: string, targets: string[]) => Promise<void>;
  /** Run the gate; if it passes, send the bytecode and run it. */
  checkAndUplink: (compileId: number, vehicleId: string) => Promise<void>;
}

const REFRESH_ON = new Set([
  "vehicle.state",
  "vehicle.link",
  "gate.decision",
  "compile.finished",
  "compile.failed",
  "uplink.finished",
  "uplink.refused",
  "testbed.run",
]);

const FINISHED = new Set(["done", "failed"]);

export function actionKey(compileId: number, vehicleId: string): string {
  return `send-${compileId}-${vehicleId}`;
}

export function useGate(selectedId: string | null): Gate {
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [detail, setDetail] = useState<VehicleDetail | null>(null);
  const [compiles, setCompiles] = useState<Compile[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [sequences, setSequences] = useState<Sequence[]>([]);
  const [jobs, setJobs] = useState<CompileJob[]>([]);
  const [config, setConfig] = useState<GateConfig | null>(null);
  const [beams, setBeams] = useState<Record<string, Beam>>({});
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [outcome, setOutcome] = useState<Record<string, Outcome>>({});
  const selected = useRef(selectedId);
  selected.current = selectedId;

  const refresh = useCallback(async () => {
    try {
      const [v, c, d] = await Promise.all([api.vehicles(), api.compiles(), api.decisions()]);
      setVehicles(v);
      setCompiles(c);
      setDecisions(d);
      if (selected.current) setDetail(await api.vehicle(selected.current));
      setError(null);
    } catch (problem) {
      setError(`Gate service unreachable: ${(problem as Error).message}`);
    }
  }, []);

  useEffect(() => {
    api.sequences().then(setSequences).catch(() => undefined);
    api.config().then(setConfig).catch(() => undefined);
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [refresh, selectedId]);

  const upsertJob = useCallback((job: CompileJob) => {
    setJobs((current) => [job, ...current.filter((j) => j.id !== job.id)].slice(0, 12));
  }, []);

  const beam = useCallback((vehicleId: string, kind: Beam["kind"]) => {
    setBeams((current) => ({ ...current, [vehicleId]: { kind, until: Date.now() + 4000 } }));
  }, []);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let closed = false;
    let pending: number | undefined;

    const open = () => {
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${window.location.host}/api/events`);
      socket.onopen = () => setConnected(true);
      socket.onclose = () => {
        setConnected(false);
        if (!closed) retry = window.setTimeout(open, 1500);
      };
      socket.onmessage = (message) => {
        const event = JSON.parse(message.data as string) as GateEvent;
        if (event.type.startsWith("compile.")) upsertJob(event.job as CompileJob);
        if (event.type === "uplink.finished") beam(event.vehicle_id as string, event.ok ? "sent" : "refused");
        if (event.type === "uplink.refused") beam(event.vehicle_id as string, "refused");
        if (REFRESH_ON.has(event.type) && pending === undefined) {
          pending = window.setTimeout(() => {
            pending = undefined;
            void refresh();
          }, 150);
        }
      };
    };
    open();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      window.clearTimeout(pending);
      socket?.close();
    };
  }, [refresh, beam, upsertJob]);

  /** Follow a job until it finishes, whether or not the WebSocket reports it. */
  const follow = useCallback(
    async (job: CompileJob) => {
      let current = job;
      upsertJob(current);
      for (let attempt = 0; attempt < 240 && !FINISHED.has(current.status); attempt++) {
        await new Promise((resolve) => window.setTimeout(resolve, 500));
        current = await api.job(current.id);
        upsertJob(current);
      }
      return current;
    },
    [upsertJob],
  );

  const compile = useCallback(
    async (name: string, source: string, targets: string[]) => {
      if (targets.length === 0) return;
      setBusy((b) => ({ ...b, compile: true }));
      setOutcome((o) => ({ ...o, compile: { tone: "working", text: "Compiling" } }));
      try {
        const everyone = vehicles.length > 0 && vehicles.every((v) => targets.includes(v.id));
        const started = everyone
          ? await api.compileAll(name, source)
          : await Promise.all(targets.map((id) => api.compile(id, name, source)));
        const finished = await Promise.all(started.map(follow));
        const failed = finished.filter((j) => j.status === "failed");
        setOutcome((o) => ({
          ...o,
          compile:
            failed.length === 0
              ? { tone: "ok", text: `Compiled for ${finished.map((j) => j.vehicle_id).join(", ")}` }
              : { tone: "failed", text: failed.map((j) => `${j.vehicle_id}: ${j.error}`).join("; ") },
        }));
      } catch (problem) {
        setOutcome((o) => ({ ...o, compile: { tone: "failed", text: (problem as Error).message } }));
      } finally {
        setBusy((b) => ({ ...b, compile: false }));
        void refresh();
      }
    },
    [follow, refresh, vehicles],
  );

  const checkAndUplink = useCallback(
    async (compileId: number, vehicleId: string) => {
      const key = actionKey(compileId, vehicleId);
      const report = (value: Outcome) => setOutcome((o) => ({ ...o, [key]: value }));
      setBusy((b) => ({ ...b, [key]: true }));
      try {
        report({ tone: "working", text: "Checking" });
        const decision = await api.check(compileId, vehicleId);
        if (decision.result !== "PASS") {
          const first = decision.checks.find((c) => c.status === "BLOCK");
          report({
            tone: "blocked",
            text: `Blocked, nothing sent. ${reasonLine(decision)}${first ? `: ${first.message}` : ""}`,
          });
          beam(vehicleId, "refused");
          return;
        }
        report({ tone: "working", text: "Check passed. Uplinking and running" });
        const result = await api.uplink(compileId, vehicleId);
        if (result.refused) report({ tone: "blocked", text: `Refused at re-check, nothing sent. ${result.message}` });
        else if (result.ok) report({ tone: "ok", text: `Uplinked and ran. ${result.detail}` });
        else report({ tone: "failed", text: `Uplinked, but it did not complete. ${result.detail}` });
      } catch (problem) {
        report({ tone: "failed", text: (problem as Error).message });
      } finally {
        setBusy((b) => ({ ...b, [key]: false }));
        void refresh();
      }
    },
    [beam, refresh],
  );

  return {
    vehicles, detail, compiles, decisions, sequences, jobs, config, beams,
    connected, error, busy, outcome, compile, checkAndUplink,
  };
}

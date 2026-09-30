// Live state of the gate service: polled REST data, refreshed early whenever the
// WebSocket reports that something changed.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type Compile,
  type CompileJob,
  type Decision,
  type GateConfig,
  type GateEvent,
  type Sequence,
  type UplinkResponse,
  type Vehicle,
  type VehicleDetail,
} from "./api";

export interface Beam {
  kind: "sent" | "refused";
  until: number;
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
  notice: Record<string, string>;
  compileAll: (sequenceName: string) => Promise<void>;
  check: (compileId: number, vehicleId: string) => Promise<void>;
  uplink: (compileId: number, vehicleId: string) => Promise<void>;
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
  const [notice, setNotice] = useState<Record<string, string>>({});
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
      setError(`Cannot reach the gate service: ${(problem as Error).message}`);
    }
  }, []);

  // Static data, once.
  useEffect(() => {
    api.sequences().then(setSequences).catch(() => undefined);
    api.config().then(setConfig).catch(() => undefined);
  }, []);

  // Poll, so ages stay current even when nothing happens.
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [refresh, selectedId]);

  const beam = useCallback((vehicleId: string, kind: Beam["kind"]) => {
    setBeams((current) => ({ ...current, [vehicleId]: { kind, until: Date.now() + 4000 } }));
  }, []);

  // Event stream, reconnecting.
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
        if (event.type.startsWith("compile.")) {
          const job = event.job as CompileJob;
          setJobs((current) => [job, ...current.filter((j) => j.id !== job.id)].slice(0, 9));
        }
        if (event.type === "uplink.finished") {
          beam(event.vehicle_id as string, event.ok ? "sent" : "refused");
        }
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
  }, [refresh, beam]);

  const run = useCallback(
    async (key: string, action: () => Promise<string | void>) => {
      setBusy((current) => ({ ...current, [key]: true }));
      try {
        const message = await action();
        setNotice((current) => ({ ...current, [key]: message ?? "" }));
      } catch (problem) {
        setNotice((current) => ({ ...current, [key]: (problem as Error).message }));
      } finally {
        setBusy((current) => ({ ...current, [key]: false }));
        void refresh();
      }
    },
    [refresh],
  );

  const compileAll = useCallback(
    (sequenceName: string) =>
      run("compile", async () => {
        const started = await api.compileAll(sequenceName);
        setJobs((current) => {
          const known = new Set(current.map((j) => j.id));
          return [...started.filter((j) => !known.has(j.id)), ...current].slice(0, 9);
        });
      }),
    [run],
  );

  const check = useCallback(
    (compileId: number, vehicleId: string) =>
      run(`check-${compileId}-${vehicleId}`, async () => {
        await api.check(compileId, vehicleId);
      }),
    [run],
  );

  const uplink = useCallback(
    (compileId: number, vehicleId: string) =>
      run(`uplink-${compileId}-${vehicleId}`, async () => {
        const result: UplinkResponse = await api.uplink(compileId, vehicleId);
        if (result.refused) return `Refused: ${result.message}`;
        return result.ok ? `Ran on ${vehicleId}: ${result.detail}` : `Failed: ${result.detail}`;
      }),
    [run],
  );

  return {
    vehicles, detail, compiles, decisions, sequences, jobs, config, beams,
    connected, error, busy, notice, compileAll, check, uplink,
  };
}

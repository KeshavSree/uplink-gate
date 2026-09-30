// Panels drawn over the orbit view: the fleet, the compile tray and the gate log.

import { useEffect, useState } from "react";
import type { Vehicle } from "../api";
import { clockTime, gateState, reasonLine, shortHash, STATE_LABEL } from "../state";
import type { Gate } from "../useGate";

interface FleetProps {
  vehicles: Vehicle[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function FleetBar({ vehicles, selectedId, onSelect }: FleetProps) {
  return (
    <nav className="fleet" aria-label="Vehicles">
      {vehicles.map((vehicle) => {
        const state = gateState(vehicle);
        return (
          <button
            type="button"
            key={vehicle.id}
            className={`fleet-item fleet-${state}`}
            aria-pressed={vehicle.id === selectedId}
            onClick={() => onSelect(vehicle.id)}
          >
            <span className="fleet-dot" aria-hidden="true" />
            <span className="fleet-name">{vehicle.name}</span>
            <span className="fleet-version">{vehicle.state?.observed_project_version ?? "no telemetry"}</span>
            <span className="fleet-state">{STATE_LABEL[state]}</span>
          </button>
        );
      })}
    </nav>
  );
}

const JOB_STATUS: Record<string, string> = {
  queued: "Queued",
  running: "Compiling",
  done: "Compiled",
  failed: "Failed",
};

export function CompileTray({ gate }: { gate: Gate }) {
  const [sequence, setSequence] = useState("");
  useEffect(() => {
    if (!sequence && gate.sequences.length > 0) setSequence(gate.sequences[0].name);
  }, [gate.sequences, sequence]);
  const pacing = gate.config?.compile_pacing_s ?? 0;
  return (
    <section className="tray" aria-label="Compile">
      <div className="tray-controls">
        <label>
          Sequence
          <select value={sequence} onChange={(event) => setSequence(event.target.value)}>
            {gate.sequences.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="primary" disabled={!sequence || gate.busy.compile} onClick={() => void gate.compileAll(sequence)}>
          Compile for all vehicles
        </button>
      </div>
      {gate.jobs.length > 0 ? (
        <ul className="jobs">
          {gate.jobs.slice(0, 6).map((job) => (
            <li key={job.id} className={`job job-${job.status}`}>
              <span className="job-vehicle">{job.vehicle_id}</span>
              <span className="job-sequence">{job.sequence_name}</span>
              <span className="job-status">{JOB_STATUS[job.status]}</span>
              <span className="job-detail">
                {job.status === "failed"
                  ? job.error
                  : `dictionary ${shortHash(job.dictionary_hash, 8)}${job.worker_pid ? `, worker ${job.worker_pid}, ${job.duration_ms} ms` : ""}`}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="hint">Each vehicle gets its own compile, using the dictionary that vehicle is running.</p>
      )}
      {pacing > 0 ? <p className="hint">Each compile is delayed by {pacing} s so the overlap is visible (GATE_COMPILE_PACING_S).</p> : null}
    </section>
  );
}

export function GateLog({ gate, onSelect }: { gate: Gate; onSelect: (id: string) => void }) {
  return (
    <section className="log" aria-label="Gate decisions">
      <h3>Gate decisions</h3>
      {gate.decisions.length === 0 ? (
        <p className="hint">No decisions yet. Check a bytecode against a vehicle.</p>
      ) : (
        <ol>
          {gate.decisions.slice(0, 30).map((decision) => (
            <li key={decision.id} className={`log-row log-${decision.result.toLowerCase()}`}>
              <time>{clockTime(decision.decided_at)}</time>
              <button type="button" onClick={() => onSelect(decision.vehicle_id)}>
                {decision.vehicle_id}
              </button>
              <strong>{decision.result === "PASS" ? "Pass" : "Block"}</strong>
              <code>{shortHash(decision.bytecode_sha256, 8)}</code>
              <span>{reasonLine(decision)}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

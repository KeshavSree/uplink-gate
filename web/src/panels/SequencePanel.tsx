// Edit or pick a sequence and compile it, for one vehicle or for all of them at once.

import { useEffect, useState } from "react";
import type { Vehicle } from "../api";
import { shortHash } from "../state";
import type { Gate } from "../useGate";
import { Panel } from "./Panel";

const JOB_STATUS: Record<string, string> = {
  queued: "Queued",
  running: "Compiling",
  done: "Compiled",
  failed: "Failed",
};

export function SequencePanel({ gate, vehicles }: { gate: Gate; vehicles: Vehicle[] }) {
  const [name, setName] = useState("");
  const [source, setSource] = useState("");
  // Vehicles ticked for compiling. Every vehicle is ticked until the user changes it.
  const [chosen, setChosen] = useState<Set<string> | null>(null);
  const targets = chosen ?? new Set(vehicles.map((v) => v.id));
  const toggle = (id: string) => {
    const next = new Set(targets);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setChosen(next);
  };

  // Load the first library sequence once the list arrives.
  useEffect(() => {
    if (!name && gate.sequences.length > 0) {
      setName(gate.sequences[0].name);
      setSource(gate.sequences[0].source);
    }
  }, [gate.sequences, name]);

  const pick = (picked: string) => {
    setName(picked);
    setSource(gate.sequences.find((s) => s.name === picked)?.source ?? "");
  };

  const outcome = gate.outcome.compile;
  const failedLog = gate.jobs.find((j) => j.status === "failed" && j.log)?.log;
  const pacing = gate.config?.compile_pacing_s ?? 0;

  return (
    <Panel title="Sequence" className="sequence">
      <div className="form-row">
        <label>
          <span>Library</span>
          <select value={name} onChange={(e) => pick(e.target.value)}>
            {gate.sequences.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name}.fpy
              </option>
            ))}
          </select>
        </label>
      </div>
      <fieldset className="targets">
        <legend>Compile for</legend>
        {vehicles.map((v) => (
          <label key={v.id} className="target">
            <input type="checkbox" checked={targets.has(v.id)} onChange={() => toggle(v.id)} />
            <span>{v.name}</span>
            <span className="mono dim">{v.state?.observed_project_version ?? ""}</span>
          </label>
        ))}
        <span className="target-actions">
          <button type="button" className="link" onClick={() => setChosen(new Set(vehicles.map((v) => v.id)))}>
            All
          </button>
          <button type="button" className="link" onClick={() => setChosen(new Set())}>
            None
          </button>
        </span>
      </fieldset>
      <textarea
        className="source"
        spellCheck={false}
        wrap="off"
        value={source}
        onChange={(e) => setSource(e.target.value)}
        aria-label="Fpy source"
      />
      <div className="form-row">
        <button
          type="button"
          className="primary"
          disabled={!source.trim() || targets.size === 0 || gate.busy.compile}
          onClick={() => void gate.compile(name || "sequence", source, [...targets])}
        >
          {gate.busy.compile
            ? "Compiling"
            : `Compile for ${targets.size} vehicle${targets.size === 1 ? "" : "s"}`}
        </button>
        {outcome ? <p className={`status tone-${outcome.tone}`}>{outcome.text}</p> : null}
      </div>
      {failedLog && outcome?.tone === "failed" ? <pre className="compiler-log">{failedLog}</pre> : null}
      {gate.jobs.length > 0 ? (
        <table className="grid-table jobs">
          <thead>
            <tr>
              <th>Job</th>
              <th>Vehicle</th>
              <th>Status</th>
              <th>Dictionary</th>
              <th>Worker</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>
            {gate.jobs.slice(0, 6).map((job) => (
              <tr key={job.id}>
                <td className="mono dim">{job.id}</td>
                <td>{job.vehicle_id}</td>
                <td className={`job-${job.status}`}>{JOB_STATUS[job.status]}</td>
                <td className="mono dim">{shortHash(job.dictionary_hash, 8)}</td>
                <td className="mono dim">{job.worker_pid ?? ""}</td>
                <td className="mono dim">{job.duration_ms !== null ? `${job.duration_ms} ms` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {pacing > 0 ? <p className="note">Demo pacing: each compile waits {pacing} s first.</p> : null}
    </Panel>
  );
}

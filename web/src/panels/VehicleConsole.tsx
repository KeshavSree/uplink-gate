// The selected vehicle: what it runs, and the gate for each bytecode.

import type { CheckResult, Compile, Decision, Dictionary, VehicleDetail } from "../api";
import {
  ageText,
  clockTime,
  decisionFor,
  distinctBytecode,
  gateState,
  isCurrent,
  sequenceName,
  shortHash,
  STATE_LABEL,
} from "../state";
import { actionKey, type Gate } from "../useGate";
import { Panel } from "./Panel";

const CHECK_TITLE: Record<string, string> = {
  build_match: "Build match",
  testbed_equiv: "Testbed run",
  flight_rules: "Flight rules",
  simulation: "Simulation",
};

function Checks({ decision, current }: { decision: Decision; current: boolean }) {
  if (!current) {
    return (
      <p className="note">
        Last checked {clockTime(decision.decided_at)} against <span className="mono">{shortHash(decision.running_dictionary_hash, 8)}</span>.
        The vehicle has changed build since.
      </p>
    );
  }
  return (
    <table className="checks">
      <tbody>
        {decision.checks.map((check: CheckResult) => (
          <tr key={check.check} className={`check-${check.status.toLowerCase()}`}>
            <td className="check-status">{check.status === "SKIPPED" ? "SKIP" : check.status}</td>
            <td className="check-name">{CHECK_TITLE[check.check] ?? check.check}</td>
            <td className="check-message">{check.message}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function BytecodeRow({
  compile,
  compiledFor,
  vehicle,
  dictionaries,
  gate,
}: {
  compile: Compile;
  compiledFor: string[];
  vehicle: VehicleDetail;
  dictionaries: Record<string, Dictionary>;
  gate: Gate;
}) {
  const decision = decisionFor(gate.decisions, vehicle.id, compile.bytecode_sha256);
  const key = actionKey(compile.id, vehicle.id);
  const outcome = gate.outcome[key];
  const dictionary = dictionaries[compile.dictionary_hash];
  return (
    <li className="bytecode">
      <div className="bytecode-head">
        <div>
          <span className="bytecode-name">{sequenceName(compile.sequence_path)}</span>
          <span className="mono dim"> {shortHash(compile.bytecode_sha256, 8)}</span>
          <p className="dim">
            for {compiledFor.join(", ") || "chosen dictionary"} with{" "}
            <span className="mono">{shortHash(compile.dictionary_hash, 8)}</span>
            {dictionary ? ` ${dictionary.project_version}` : ""}
          </p>
        </div>
        <button
          type="button"
          className="primary"
          disabled={gate.busy[key]}
          onClick={() => void gate.checkAndUplink(compile.id, vehicle.id)}
        >
          {gate.busy[key] ? "Working" : "Check and uplink"}
        </button>
      </div>
      {outcome && outcome.tone !== "ok" ? <p className={`status tone-${outcome.tone}`}>{outcome.text}</p> : null}
      {decision ? <Checks decision={decision} current={isCurrent(decision, vehicle)} /> : null}
    </li>
  );
}

export function VehicleConsole({ vehicle, gate }: { vehicle: VehicleDetail | null; gate: Gate }) {
  if (!vehicle) {
    return (
      <Panel title="Vehicle" className="vehicle">
        <p className="note">Select a vehicle in the fleet list or the orbit view.</p>
      </Panel>
    );
  }
  const state = gateState(vehicle);
  const dictionaries = Object.fromEntries(
    gate.vehicles
      .map((v) => v.running_dictionary)
      .filter((d): d is Dictionary => d !== null)
      .map((d) => [d.hash, d]),
  );
  const freshness =
    vehicle.reading_age_s === null ? 0 : Math.max(0, 1 - vehicle.reading_age_s / vehicle.max_state_age_s);
  return (
    <Panel
      title={`Vehicle ${vehicle.name}`}
      className="vehicle"
      aside={<span className={`tag-state tone-${state}`}>{STATE_LABEL[state]}</span>}
    >
      <dl className="readout">
        <dt>Kind</dt>
        <dd>{vehicle.kind === "testbed" ? "Testbed" : "Spacecraft"}</dd>
        <dt>Project version</dt>
        <dd className="mono strong">{vehicle.state?.observed_project_version ?? "not reported"}</dd>
        <dt>Dictionary</dt>
        <dd className="mono" title={vehicle.running_dictionary?.hash}>
          {shortHash(vehicle.running_dictionary?.hash, 16)}
          <span className="dim">
            {vehicle.resolution.identified_by === "content" ? "  by content" : ""}
            {vehicle.resolution.identified_by === "label" ? "  by label only" : ""}
          </span>
        </dd>
        <dt>Identity read</dt>
        <dd>
          <span className="mono">{ageText(vehicle.reading_age_s)}</span>
          <span className="meter" title={`Stale after ${vehicle.max_state_age_s} s`}>
            <span style={{ width: `${freshness * 100}%` }} />
          </span>
        </dd>
        <dt>Link</dt>
        <dd className={vehicle.link?.connected ? "tone-pass" : "tone-warn"}>
          {vehicle.link?.connected ? `GDS ${vehicle.gds_endpoint}` : (vehicle.link?.error ?? "no link")}
        </dd>
      </dl>
      {!vehicle.resolution.ok ? <p className="status tone-blocked">{vehicle.resolution.message}</p> : null}

      <h3 className="subhead">Bytecode</h3>
      {gate.compiles.length === 0 ? (
        <p className="note">Nothing compiled yet. Compile a sequence in the Sequence panel.</p>
      ) : (
        <ul className="bytecodes">
          {distinctBytecode(gate.compiles)
            .slice(0, 5)
            .map(({ compile, compiledFor }) => (
              <BytecodeRow
                key={compile.bytecode_sha256}
                compile={compile}
                compiledFor={compiledFor}
                vehicle={vehicle}
                dictionaries={dictionaries}
                gate={gate}
              />
            ))}
        </ul>
      )}
    </Panel>
  );
}

// The console for the selected vehicle: what it is running, and the gate for each bytecode.

import type { CheckResult, Compile, Decision, Dictionary, VehicleDetail } from "../api";
import { ageText, clockTime, decisionFor, distinctBytecode, gateState, isCurrent, sequenceName, shortHash, STATE_LABEL } from "../state";
import type { Gate } from "../useGate";

const CHECK_TITLE: Record<string, string> = {
  build_match: "Build match",
  testbed_equiv: "Testbed run",
  flight_rules: "Flight rules",
  simulation: "Simulation",
};

const STATUS_WORD: Record<CheckResult["status"], string> = {
  PASS: "Pass",
  BLOCK: "Block",
  SKIPPED: "Skipped",
};

function CheckRow({ check }: { check: CheckResult }) {
  return (
    <li className={`check check-${check.status.toLowerCase()}`}>
      <span className="check-status">{STATUS_WORD[check.status]}</span>
      <span className="check-name">{CHECK_TITLE[check.check] ?? check.check}</span>
      <span className="check-message">{check.message}</span>
    </li>
  );
}

function DecisionView({ decision, current }: { decision: Decision; current: boolean }) {
  if (!current) {
    return (
      <p className="decision decision-stale">
        Last checked at {clockTime(decision.decided_at)}, when this vehicle was running{" "}
        <code>{shortHash(decision.running_dictionary_hash, 8)}</code>. It has changed build since, so check again.
      </p>
    );
  }
  return (
    <div className={`decision decision-${decision.result.toLowerCase()}`}>
      <p className="decision-head">
        <strong>{decision.result === "PASS" ? "Pass" : "Block"}</strong>
        <span>checked at {clockTime(decision.decided_at)}</span>
      </p>
      <ul className="checks">
        {decision.checks.map((check) => (
          <CheckRow key={check.check} check={check} />
        ))}
      </ul>
    </div>
  );
}

interface BytecodeRowProps {
  compile: Compile;
  compiledFor: string[];
  vehicle: VehicleDetail;
  dictionaries: Record<string, Dictionary>;
  gate: Gate;
}

function BytecodeRow({ compile, compiledFor, vehicle, dictionaries, gate }: BytecodeRowProps) {
  const decision = decisionFor(gate.decisions, vehicle.id, compile.bytecode_sha256);
  const dictionary = dictionaries[compile.dictionary_hash];
  const checkKey = `check-${compile.id}-${vehicle.id}`;
  const uplinkKey = `uplink-${compile.id}-${vehicle.id}`;
  const notice = gate.notice[uplinkKey] || gate.notice[checkKey];
  return (
    <li className="bytecode">
      <div className="bytecode-head">
        <div>
          <p className="bytecode-name">{sequenceName(compile.sequence_path)}</p>
          <p className="bytecode-meta">
            compiled for {compiledFor.length > 0 ? compiledFor.join(" and ") : "a chosen dictionary"} with{" "}
            <code title={compile.dictionary_hash}>{shortHash(compile.dictionary_hash, 8)}</code>
            {dictionary ? ` (${dictionary.project_version})` : ""}
          </p>
        </div>
        <div className="bytecode-actions">
          <button type="button" disabled={gate.busy[checkKey]} onClick={() => void gate.check(compile.id, vehicle.id)}>
            {gate.busy[checkKey] ? "Checking" : "Check"}
          </button>
          <button
            type="button"
            className="primary"
            disabled={gate.busy[uplinkKey]}
            onClick={() => void gate.uplink(compile.id, vehicle.id)}
          >
            {gate.busy[uplinkKey] ? "Uplinking" : "Uplink"}
          </button>
        </div>
      </div>
      {decision ? <DecisionView decision={decision} current={isCurrent(decision, vehicle)} /> : null}
      {notice ? <p className="notice">{notice}</p> : null}
    </li>
  );
}

export function VehicleConsole({ vehicle, gate }: { vehicle: VehicleDetail | null; gate: Gate }) {
  if (!vehicle) {
    return (
      <aside className="console">
        <p className="console-empty">Select a vehicle in the orbit view to see what it is running.</p>
      </aside>
    );
  }
  const state = gateState(vehicle);
  const dictionaries = Object.fromEntries(
    [vehicle.running_dictionary, ...gate.vehicles.map((v) => v.running_dictionary)]
      .filter((d): d is Dictionary => d !== null)
      .map((d) => [d.hash, d]),
  );
  const freshness = vehicle.reading_age_s === null ? 0 : Math.max(0, 1 - vehicle.reading_age_s / vehicle.max_state_age_s);
  return (
    <aside className="console" aria-label={`Vehicle ${vehicle.name}`}>
      <header className="console-header">
        <h2>{vehicle.name}</h2>
        <p>{vehicle.kind === "testbed" ? "Testbed" : "Spacecraft"}</p>
        <p className={`state state-${state}`}>{STATE_LABEL[state]}</p>
      </header>

      <section>
        <h3>Running build</h3>
        <dl className="facts">
          <dt>Project version</dt>
          <dd className="version">{vehicle.state?.observed_project_version ?? "not reported"}</dd>
          <dt>Dictionary hash</dt>
          <dd>
            <code title={vehicle.running_dictionary?.hash}>{shortHash(vehicle.running_dictionary?.hash)}</code>
            {vehicle.resolution.identified_by === "content" ? " matched by content" : null}
            {vehicle.resolution.identified_by === "label" ? " matched by version label only" : null}
          </dd>
          <dt>Telemetry</dt>
          <dd>
            reported {ageText(vehicle.reading_age_s)}
            <span className="freshness" role="img" aria-label={`Reading is ${ageText(vehicle.reading_age_s)}; stale after ${vehicle.max_state_age_s} seconds`}>
              <span style={{ width: `${freshness * 100}%` }} />
            </span>
          </dd>
        </dl>
        {!vehicle.resolution.ok ? <p className="problem">{vehicle.resolution.message}</p> : null}
        {vehicle.link && !vehicle.link.connected ? (
          <p className="problem">No link to this vehicle. {vehicle.link.error}</p>
        ) : null}
      </section>

      <section>
        <h3>Bytecode</h3>
        {gate.compiles.length === 0 ? (
          <p className="hint">Nothing compiled yet. Use “Compile for all vehicles” to make bytecode for each one.</p>
        ) : (
          <ul className="bytecodes">
            {distinctBytecode(gate.compiles).slice(0, 5).map(({ compile, compiledFor }) => (
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
      </section>

      <section>
        <h3>Flight software events</h3>
        {vehicle.events.length === 0 ? (
          <p className="hint">No events received yet.</p>
        ) : (
          <ol className="events">
            {vehicle.events.slice(-8).reverse().map((event, index) => (
              <li key={`${event.received_at}-${index}`} className={`event event-${event.severity.toLowerCase()}`}>
                <time>{clockTime(event.received_at)}</time>
                <span>{event.message}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </aside>
  );
}

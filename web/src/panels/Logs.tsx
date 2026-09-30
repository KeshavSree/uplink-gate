import type { VehicleDetail } from "../api";
import { clockTime, reasonLine, shortHash } from "../state";
import type { Gate } from "../useGate";
import { Panel } from "./Panel";

export function GateLog({ gate, onSelect }: { gate: Gate; onSelect: (id: string) => void }) {
  return (
    <Panel title="Gate decisions" className="gatelog">
      {gate.decisions.length === 0 ? (
        <p className="note">No decisions yet.</p>
      ) : (
        <table className="grid-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Vehicle</th>
              <th>Result</th>
              <th>Bytecode</th>
              <th>Reasons</th>
            </tr>
          </thead>
          <tbody>
            {gate.decisions.slice(0, 40).map((d) => (
              <tr key={d.id} onClick={() => onSelect(d.vehicle_id)}>
                <td className="mono dim">{clockTime(d.decided_at)}</td>
                <td>{d.vehicle_id}</td>
                <td className={d.result === "PASS" ? "tone-pass strong" : "tone-block strong"}>{d.result}</td>
                <td className="mono dim">{shortHash(d.bytecode_sha256, 8)}</td>
                <td className={d.result === "PASS" ? "dim" : undefined}>{reasonLine(d)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

export function Events({ vehicle }: { vehicle: VehicleDetail | null }) {
  return (
    <Panel title={vehicle ? `Events ${vehicle.name}` : "Events"} className="events">
      {!vehicle || vehicle.events.length === 0 ? (
        <p className="note">No flight software events yet.</p>
      ) : (
        <table className="grid-table">
          <tbody>
            {vehicle.events
              .slice(-40)
              .reverse()
              .map((e, index) => (
                <tr key={`${e.received_at}-${index}`} className={`sev-${e.severity.toLowerCase()}`}>
                  <td className="mono dim">{clockTime(e.received_at)}</td>
                  <td className="mono sev">{e.severity.replace("_", " ")}</td>
                  <td>{e.message}</td>
                </tr>
              ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

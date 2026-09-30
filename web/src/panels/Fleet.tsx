import type { Vehicle } from "../api";
import { ageText, gateState, shortHash, STATE_LABEL } from "../state";
import { Panel } from "./Panel";

export function Fleet({
  vehicles,
  selectedId,
  onSelect,
}: {
  vehicles: Vehicle[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <Panel title="Fleet" className="fleet">
      <table className="grid-table">
        <thead>
          <tr>
            <th aria-label="Gate state" />
            <th>Vehicle</th>
            <th>Build</th>
            <th>Dictionary</th>
            <th>Read</th>
          </tr>
        </thead>
        <tbody>
          {vehicles.map((vehicle) => {
            const state = gateState(vehicle);
            return (
              <tr
                key={vehicle.id}
                className={vehicle.id === selectedId ? "selected" : undefined}
                onClick={() => onSelect(vehicle.id)}
              >
                <td>
                  <span className={`lamp lamp-${state}`} title={STATE_LABEL[state]} />
                </td>
                <td>
                  <button type="button" className="link" onClick={() => onSelect(vehicle.id)}>
                    {vehicle.name}
                  </button>
                </td>
                <td className="mono">{vehicle.state?.observed_project_version ?? "none"}</td>
                <td className="mono dim">{shortHash(vehicle.running_dictionary?.hash, 8)}</td>
                <td className="mono dim">{ageText(vehicle.reading_age_s)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}

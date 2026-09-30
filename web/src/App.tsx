import { useEffect, useState } from "react";
import { CompileTray, FleetBar, GateLog } from "./panels/Overlays";
import { VehicleConsole } from "./panels/VehicleConsole";
import { Scene } from "./scene/Scene";
import { useGate } from "./useGate";

export function App() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const gate = useGate(selectedId);

  // Start on the first spacecraft once the fleet is known.
  useEffect(() => {
    if (selectedId === null && gate.vehicles.length > 0) {
      setSelectedId((gate.vehicles.find((v) => v.kind === "spacecraft") ?? gate.vehicles[0]).id);
    }
  }, [gate.vehicles, selectedId]);

  const detail = gate.detail && gate.detail.id === selectedId ? gate.detail : null;

  return (
    <div className="app">
      <main className="stage">
        <div className="view">
          <Scene vehicles={gate.vehicles} selectedId={selectedId} onSelect={setSelectedId} beams={gate.beams} />
        </div>
        <header className="masthead">
          <h1>Uplink gate</h1>
          <p>
            A sequence goes up only if it was compiled with the dictionary the vehicle is running.
          </p>
          <p className={`stream ${gate.connected ? "stream-on" : "stream-off"}`} role="status">
            {gate.error ?? (gate.connected ? "Live from telemetry" : "Reconnecting to the gate service")}
          </p>
        </header>
        <FleetBar vehicles={gate.vehicles} selectedId={selectedId} onSelect={setSelectedId} />
        <div className="dock">
          <CompileTray gate={gate} />
          <GateLog gate={gate} onSelect={setSelectedId} />
        </div>
      </main>
      <VehicleConsole vehicle={detail} gate={gate} />
    </div>
  );
}

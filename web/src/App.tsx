import { useEffect, useState } from "react";
import { Fleet } from "./panels/Fleet";
import { Events, GateLog } from "./panels/Logs";
import { Panel } from "./panels/Panel";
import { SequencePanel } from "./panels/SequencePanel";
import { TopBar } from "./panels/TopBar";
import { VehicleConsole } from "./panels/VehicleConsole";
import { Scene } from "./scene/Scene";
import { useGate } from "./useGate";

export function App() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const gate = useGate(selectedId);

  useEffect(() => {
    if (selectedId === null && gate.vehicles.length > 0) {
      setSelectedId((gate.vehicles.find((v) => v.kind === "spacecraft") ?? gate.vehicles[0]).id);
    }
  }, [gate.vehicles, selectedId]);

  const detail = gate.detail && gate.detail.id === selectedId ? gate.detail : null;

  return (
    <div className="console-grid">
      <TopBar gate={gate} />
      <div className="col-left">
        <Fleet vehicles={gate.vehicles} selectedId={selectedId} onSelect={setSelectedId} />
        <SequencePanel gate={gate} vehicles={gate.vehicles} />
      </div>
      <Panel title="Orbit view" className="orbit" aside={<span className="dim">not to scale</span>}>
        <Scene vehicles={gate.vehicles} selectedId={selectedId} onSelect={setSelectedId} beams={gate.beams} />
      </Panel>
      <GateLog gate={gate} onSelect={setSelectedId} />
      <Events vehicle={detail} />
      <div className="col-right">
        <VehicleConsole vehicle={detail} gate={gate} />
      </div>
    </div>
  );
}

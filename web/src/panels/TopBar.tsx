import { useEffect, useState } from "react";
import type { Gate } from "../useGate";

function utcClock(date: Date): string {
  return date.toISOString().slice(11, 19);
}

export function TopBar({ gate }: { gate: Gate }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const linked = gate.vehicles.filter((v) => v.link?.connected).length;
  return (
    <header className="topbar">
      <h1>Uplink gate</h1>
      <dl className="topbar-fields">
        <div>
          <dt>UTC</dt>
          <dd className="mono">{utcClock(now)}</dd>
        </div>
        <div>
          <dt>Service</dt>
          <dd className={gate.error ? "tone-block" : gate.connected ? "tone-pass" : "tone-warn"}>
            {gate.error ? "Unreachable" : gate.connected ? "Live" : "Reconnecting"}
          </dd>
        </div>
        <div>
          <dt>Vehicle links</dt>
          <dd className="mono">
            {linked}/{gate.vehicles.length}
          </dd>
        </div>
        <div>
          <dt>Checks</dt>
          <dd className="mono">{gate.config?.checks.join(" + ") ?? "…"}</dd>
        </div>
      </dl>
      {gate.error ? <p className="topbar-error">{gate.error}</p> : null}
    </header>
  );
}

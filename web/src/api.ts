// Types and calls for the gate service's REST API. Field names match the server's JSON.

export interface Dictionary {
  hash: string;
  deployment_name: string;
  project_version: string;
  framework_version: string;
}

export interface CheckResult {
  check: string;
  status: "PASS" | "BLOCK" | "SKIPPED";
  reason: string;
  message: string;
  details: Record<string, unknown>;
}

export interface Decision {
  id: number | null;
  result: "PASS" | "BLOCK";
  vehicle_id: string;
  bytecode_sha256: string;
  running_dictionary_hash: string | null;
  compile_id: number | null;
  decided_at: string;
  checks: CheckResult[];
}

export interface Vehicle {
  id: string;
  name: string;
  kind: "testbed" | "spacecraft";
  gds_endpoint: string | null;
  state: {
    observed_project_version: string;
    observed_dictionary_id: string | null;
    observed_at: string;
    source: string;
  } | null;
  reading_age_s: number | null;
  max_state_age_s: number;
  resolution: { ok: boolean; reason: string; message: string; identified_by: string | null };
  running_dictionary: Dictionary | null;
  link: { connected: boolean; last_data_at: string | null; error: string | null } | null;
  last_decision: Decision | null;
}

export interface FlightEvent {
  vehicle_id: string;
  name: string;
  severity: string;
  message: string;
  received_at: string;
}

export interface Compile {
  id: number;
  sequence_path: string;
  dictionary_hash: string;
  bytecode_sha256: string;
  vehicle_id: string | null;
  created_at: string;
}

export interface VehicleDetail extends Vehicle {
  compiles: Compile[];
  decisions: Decision[];
  events: FlightEvent[];
}

export interface CompileJob {
  id: number;
  sequence_name: string;
  vehicle_id: string | null;
  dictionary_hash: string;
  status: "queued" | "running" | "done" | "failed";
  compile: Compile | null;
  error: string | null;
  log: string;
  worker_pid: number | null;
  duration_ms: number | null;
}

export interface Sequence {
  name: string;
  source: string;
}

export interface UplinkResponse {
  refused: boolean;
  ok?: boolean;
  detail?: string;
  reason?: string;
  message?: string;
  vehicle_id: string;
  compile_id: number;
}

export interface GateConfig {
  checks: string[];
  max_state_age_s: number;
  max_decision_age_s: number;
  compile_pacing_s: number;
}

export interface GateEvent {
  seq: number;
  type: string;
  at: string;
  [key: string]: unknown;
}

async function request<T>(path: string, init?: RequestInit, accept: number[] = []): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok && !accept.includes(response.status)) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      const inner = body?.detail;
      detail = typeof inner === "string" ? inner : (inner?.message ?? detail);
    } catch {
      // keep the status text
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  vehicles: () => request<Vehicle[]>("/vehicles"),
  vehicle: (id: string) => request<VehicleDetail>(`/vehicles/${id}`),
  dictionaries: () => request<Dictionary[]>("/dictionaries"),
  sequences: () => request<Sequence[]>("/sequences"),
  compiles: () => request<Compile[]>("/compiles?limit=30"),
  decisions: () => request<Decision[]>("/decisions?limit=40"),
  config: () => request<GateConfig>("/config"),
  compileAll: (sequenceName: string) =>
    request<CompileJob[]>("/compile/all", post({ sequence_name: sequenceName })),
  check: (compileId: number, vehicleId: string) =>
    request<Decision>("/check", post({ compile_id: compileId, vehicle_id: vehicleId })),
  uplink: (compileId: number, vehicleId: string) =>
    request<UplinkResponse>(
      "/uplink",
      post({ compile_id: compileId, vehicle_id: vehicleId }),
      [409],
    ),
};

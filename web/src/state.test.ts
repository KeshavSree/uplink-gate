import { describe, expect, it } from "vitest";
import type { Compile, Decision, Vehicle } from "./api";
import { ageText, decisionFor, distinctBytecode, gateState, isCurrent, reasonLine, sequenceName, shortHash } from "./state";

const decision = (result: "PASS" | "BLOCK", vehicle = "sc1", sha = "aa"): Decision => ({
  id: 1,
  result,
  vehicle_id: vehicle,
  bytecode_sha256: sha,
  running_dictionary_hash: "dict-a",
  compile_id: null,
  decided_at: "2026-09-30T12:00:00Z",
  checks: [
    { check: "build_match", status: result, reason: result === "PASS" ? "OK" : "DICTIONARY_MISMATCH", message: "", details: {} },
    { check: "flight_rules", status: "PASS", reason: "OK", message: "", details: {} },
  ],
});

const vehicle = (over: Partial<Vehicle> = {}): Vehicle => ({
  id: "sc1",
  name: "sc1",
  kind: "spacecraft",
  gds_endpoint: null,
  state: null,
  reading_age_s: 2,
  max_state_age_s: 60,
  resolution: { ok: true, reason: "OK", message: "", identified_by: "content" },
  running_dictionary: { hash: "dict-a", deployment_name: "Ref.Ref", project_version: "v1", framework_version: "v1" },
  link: { connected: true, last_data_at: null, error: null },
  last_decision: null,
  ...over,
});

describe("gateState", () => {
  it("is idle before anything was checked", () => {
    expect(gateState(vehicle())).toBe("idle");
  });
  it("follows the latest decision", () => {
    expect(gateState(vehicle({ last_decision: decision("PASS") }))).toBe("open");
    expect(gateState(vehicle({ last_decision: decision("BLOCK") }))).toBe("blocked");
  });
  it("treats a decision made on an earlier build as history", () => {
    const reflashed = { hash: "dict-b", deployment_name: "Ref.Ref", project_version: "v2", framework_version: "v2" };
    const v = vehicle({ last_decision: decision("PASS"), running_dictionary: reflashed });
    expect(gateState(v)).toBe("idle");
    expect(isCurrent(decision("PASS"), v)).toBe(false);
  });
  it("is unknown when the build cannot be resolved, whatever the last decision was", () => {
    const unresolved = { ok: false, reason: "STALE_BUILD_IDENTITY", message: "", identified_by: null };
    expect(gateState(vehicle({ resolution: unresolved, last_decision: decision("PASS") }))).toBe("unknown");
  });
  it("is unknown when the link is down", () => {
    const link = { connected: false, last_data_at: null, error: "refused" };
    expect(gateState(vehicle({ link }))).toBe("unknown");
  });
});

describe("formatting", () => {
  it("shortens hashes and names sequences", () => {
    expect(shortHash("4bbb2268b356ad9e8ca4")).toBe("4bbb2268b356");
    expect(shortHash(null)).toBe("unknown");
    expect(sequenceName("/var/lib/gate/bytecode/job-000001/signal_setup.fpy")).toBe("signal_setup");
  });
  it("describes ages", () => {
    expect(ageText(null)).toBe("never");
    expect(ageText(0.4)).toBe("just now");
    expect(ageText(12.2)).toBe("12 s ago");
    expect(ageText(300)).toBe("5 min ago");
  });
  it("summarises blocking reasons", () => {
    expect(reasonLine(decision("PASS"))).toBe("All checks passed");
    expect(reasonLine(decision("BLOCK"))).toBe("build_match: DICTIONARY_MISMATCH");
  });
  it("finds the decision for a vehicle and bytecode", () => {
    const all = [decision("BLOCK", "sc2", "aa"), decision("PASS", "sc1", "aa")];
    expect(decisionFor(all, "sc1", "aa")?.result).toBe("PASS");
    expect(decisionFor(all, "sc1", "bb")).toBeUndefined();
  });
});

describe("distinctBytecode", () => {
  const compile = (id: number, sha: string, vehicle: string | null): Compile => ({
    id,
    sequence_path: "/x/seq.fpy",
    dictionary_hash: "dict-a",
    bytecode_sha256: sha,
    vehicle_id: vehicle,
    created_at: "2026-09-30T12:00:00Z",
  });
  it("groups compiles with the same bytes and keeps the newest record", () => {
    const grouped = distinctBytecode([
      compile(5, "aa", "sc1"),
      compile(4, "aa", "testbed"),
      compile(3, "bb", "sc2"),
      compile(2, "aa", "sc1"),
      compile(1, "cc", null),
    ]);
    expect(grouped.map((b) => b.compile.id)).toEqual([5, 3, 1]);
    expect(grouped[0].compiledFor).toEqual(["sc1", "testbed"]);
    expect(grouped[2].compiledFor).toEqual([]);
  });
});

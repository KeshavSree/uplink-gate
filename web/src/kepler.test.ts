import { describe, expect, it } from "vitest";
import { type Elements, orbitPath, positionAt, radius, solveKepler } from "./kepler";

const base: Elements = { a: 3, e: 0.4, i: 0.5, raan: 1.1, argp: 0.7, m0: 0, period: 60 };

describe("solveKepler", () => {
  it("satisfies Kepler's equation across anomalies and eccentricities", () => {
    for (const e of [0, 0.1, 0.5, 0.85, 0.97]) {
      for (let m = 0; m < 2 * Math.PI; m += 0.37) {
        const eccentric = solveKepler(m, e);
        expect(eccentric - e * Math.sin(eccentric)).toBeCloseTo(m, 9);
      }
    }
  });

  it("wraps anomalies outside one revolution", () => {
    expect(solveKepler(2 * Math.PI + 1, 0.3)).toBeCloseTo(solveKepler(1, 0.3), 10);
    expect(solveKepler(-1, 0.3)).toBeCloseTo(solveKepler(2 * Math.PI - 1, 0.3), 10);
  });
});

describe("positionAt", () => {
  it("is at periapsis at t = 0 and apoapsis half a period later", () => {
    expect(radius(positionAt(base, 0))).toBeCloseTo(base.a * (1 - base.e), 9);
    expect(radius(positionAt(base, base.period / 2))).toBeCloseTo(base.a * (1 + base.e), 9);
  });

  it("repeats after one period", () => {
    const now = positionAt(base, 17.3);
    const later = positionAt(base, 17.3 + base.period);
    for (let k = 0; k < 3; k++) expect(later[k]).toBeCloseTo(now[k], 9);
  });

  it("keeps a circular orbit at constant radius", () => {
    const circular = { ...base, e: 0 };
    for (let t = 0; t < 60; t += 7) expect(radius(positionAt(circular, t))).toBeCloseTo(3, 9);
  });

  it("stays in the reference plane when the inclination is zero", () => {
    const flat = { ...base, i: 0 };
    for (let t = 0; t < 60; t += 7) expect(positionAt(flat, t)[1]).toBeCloseTo(0, 9);
  });

  it("sweeps equal areas in equal times", () => {
    // Kepler's second law: |r x v| is constant along the orbit.
    const momentum = (t: number) => {
      const dt = 1e-4;
      const r = positionAt(base, t);
      const next = positionAt(base, t + dt);
      const v = [(next[0] - r[0]) / dt, (next[1] - r[1]) / dt, (next[2] - r[2]) / dt];
      return Math.hypot(
        r[1] * v[2] - r[2] * v[1],
        r[2] * v[0] - r[0] * v[2],
        r[0] * v[1] - r[1] * v[0],
      );
    };
    expect(momentum(3)).toBeCloseTo(momentum(29), 3);
  });
});

describe("orbitPath", () => {
  it("is closed and stays between periapsis and apoapsis", () => {
    const path = orbitPath(base, 64);
    expect(path).toHaveLength(65);
    for (let k = 0; k < 3; k++) expect(path[64][k]).toBeCloseTo(path[0][k], 9);
    for (const point of path) {
      expect(radius(point)).toBeGreaterThanOrEqual(base.a * (1 - base.e) - 1e-9);
      expect(radius(point)).toBeLessThanOrEqual(base.a * (1 + base.e) + 1e-9);
    }
  });
});

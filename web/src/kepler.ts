// Two-body orbit propagation from fixed Keplerian elements.
//
// This is for display only: the elements are made up, distances are not to scale, and
// nothing here feeds the gate. It is still a real Kepler solver, so the vehicles speed up
// near periapsis and slow down near apoapsis the way they should.

export interface Elements {
  /** Semi-major axis, scene units. */
  a: number;
  /** Eccentricity, 0 <= e < 1. */
  e: number;
  /** Inclination, radians. */
  i: number;
  /** Right ascension of the ascending node, radians. */
  raan: number;
  /** Argument of periapsis, radians. */
  argp: number;
  /** Mean anomaly at t = 0, radians. */
  m0: number;
  /** Orbital period, seconds of scene time. */
  period: number;
}

export type Vec3 = [number, number, number];

/** Solve Kepler's equation M = E - e sin E for the eccentric anomaly E (Newton's method). */
export function solveKepler(meanAnomaly: number, e: number): number {
  const twoPi = 2 * Math.PI;
  const m = ((meanAnomaly % twoPi) + twoPi) % twoPi;
  let eccentric = e < 0.8 ? m : Math.PI;
  for (let step = 0; step < 20; step++) {
    const delta = (eccentric - e * Math.sin(eccentric) - m) / (1 - e * Math.cos(eccentric));
    eccentric -= delta;
    if (Math.abs(delta) < 1e-12) break;
  }
  return eccentric;
}

/** Position at an eccentric anomaly, in the scene frame (Y up, orbits near the XZ plane). */
function positionAtEccentric(el: Elements, eccentric: number): Vec3 {
  // Perifocal frame: p towards periapsis, q 90 degrees ahead in the orbit plane.
  const p = el.a * (Math.cos(eccentric) - el.e);
  const q = el.a * Math.sqrt(1 - el.e * el.e) * Math.sin(eccentric);

  const cosO = Math.cos(el.raan), sinO = Math.sin(el.raan);
  const cosI = Math.cos(el.i), sinI = Math.sin(el.i);
  const cosW = Math.cos(el.argp), sinW = Math.sin(el.argp);

  // Rz(raan) Rx(i) Rz(argp) applied to (p, q, 0), in a Z-up inertial frame.
  const x = (cosO * cosW - sinO * sinW * cosI) * p + (-cosO * sinW - sinO * cosW * cosI) * q;
  const y = (sinO * cosW + cosO * sinW * cosI) * p + (-sinO * sinW + cosO * cosW * cosI) * q;
  const z = sinW * sinI * p + cosW * sinI * q;

  // Z-up to the renderer's Y-up.
  return [x, z, -y];
}

export function positionAt(el: Elements, t: number): Vec3 {
  const meanAnomaly = el.m0 + (2 * Math.PI * t) / el.period;
  return positionAtEccentric(el, solveKepler(meanAnomaly, el.e));
}

/** Points along the whole orbit, closed, for drawing it. */
export function orbitPath(el: Elements, segments = 128): Vec3[] {
  const points: Vec3[] = [];
  for (let k = 0; k <= segments; k++) {
    points.push(positionAtEccentric(el, (2 * Math.PI * k) / segments));
  }
  return points;
}

export function radius(v: Vec3): number {
  return Math.hypot(v[0], v[1], v[2]);
}

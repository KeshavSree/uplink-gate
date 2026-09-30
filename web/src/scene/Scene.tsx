// The orbit view: Earth, Moon and one marker per vehicle, coloured by gate state.
//
// Orbits are fixed Keplerian elements propagated over time (see kepler.ts). They are for
// orientation only and are not to scale.

import { Html, Line, OrbitControls, Stars } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import type { Vehicle } from "../api";
import { type Elements, orbitPath, positionAt } from "../kepler";
import { gateState, STATE_COLOR, STATE_LABEL } from "../state";
import type { Beam } from "../useGate";
import { Earth, Moon, SUN_DIRECTION } from "./bodies";

const DEG = Math.PI / 180;
const MOON_DISTANCE = 9.5;
const MOON_RADIUS = 0.42;

const MOON_ORBIT: Elements = {
  a: MOON_DISTANCE, e: 0.055, i: 5.1 * DEG, raan: 40 * DEG, argp: 0, m0: 2.4, period: 420,
};
// A transfer-like ellipse around Earth and a low orbit around the Moon.
const EARTH_ORBITS: Elements[] = [
  { a: 2.9, e: 0.36, i: 28.5 * DEG, raan: 200 * DEG, argp: 300 * DEG, m0: 0.6, period: 46 },
  { a: 4.1, e: 0.22, i: 51.0 * DEG, raan: 80 * DEG, argp: 120 * DEG, m0: 2.0, period: 71 },
];
const LUNAR_ORBITS: Elements[] = [
  { a: 0.95, e: 0.12, i: 62 * DEG, raan: 15 * DEG, argp: 40 * DEG, m0: 1.0, period: 27 },
  { a: 1.35, e: 0.05, i: 98 * DEG, raan: 140 * DEG, argp: 0, m0: 3.0, period: 41 },
];
// Where the testbed sits on Earth, as a direction in Earth's own frame.
const TESTBED_SITE = new THREE.Vector3(0.52, 0.5, 0.69).normalize();

type Placement =
  | { kind: "ground" }
  | { kind: "earth"; orbit: Elements }
  | { kind: "moon"; orbit: Elements };

/** Testbeds sit on the ground. Spacecraft alternate between Earth orbit and lunar orbit. */
export function placements(vehicles: Vehicle[]): Record<string, Placement> {
  const result: Record<string, Placement> = {};
  let spacecraft = 0;
  for (const vehicle of vehicles) {
    if (vehicle.kind === "testbed") {
      result[vehicle.id] = { kind: "ground" };
      continue;
    }
    const slot = Math.floor(spacecraft / 2);
    result[vehicle.id] =
      spacecraft % 2 === 0
        ? { kind: "earth", orbit: EARTH_ORBITS[slot % EARTH_ORBITS.length] }
        : { kind: "moon", orbit: LUNAR_ORBITS[slot % LUNAR_ORBITS.length] };
    spacecraft++;
  }
  return result;
}

type Positions = Record<string, THREE.Vector3>;

interface MarkerProps {
  vehicle: Vehicle;
  selected: boolean;
  onSelect: (id: string) => void;
  shape: "craft" | "site";
}

function Marker({ vehicle, selected, onSelect, shape }: MarkerProps) {
  const state = gateState(vehicle);
  const color = STATE_COLOR[state];
  const halo = useRef<THREE.Mesh>(null);
  useFrame(({ camera, clock }) => {
    if (!halo.current) return;
    halo.current.quaternion.copy(camera.quaternion);
    const pulse = selected ? 1 + 0.12 * Math.sin(clock.elapsedTime * 3) : 1;
    halo.current.scale.setScalar(pulse);
  });
  return (
    <group>
      <mesh
        onClick={(event) => {
          event.stopPropagation();
          onSelect(vehicle.id);
        }}
        onPointerOver={() => (document.body.style.cursor = "pointer")}
        onPointerOut={() => (document.body.style.cursor = "")}
      >
        <sphereGeometry args={[0.34, 12, 12]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      <mesh rotation={[0.4, 0.6, 0]}>
        {shape === "craft" ? <octahedronGeometry args={[0.1, 0]} /> : <coneGeometry args={[0.07, 0.2, 4]} />}
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={1.6} />
      </mesh>
      <mesh ref={halo}>
        <ringGeometry args={selected ? [0.2, 0.235, 48] : [0.17, 0.185, 48]} />
        <meshBasicMaterial color={color} transparent opacity={selected ? 0.95 : 0.55} side={THREE.DoubleSide} />
      </mesh>
      <Html position={[0.3, 0.16, 0]} zIndexRange={[5, 0]} style={{ pointerEvents: "none" }}>
        <div className={`tag tag-${state}${selected ? " tag-selected" : ""}`}>
          <strong>{vehicle.name}</strong>
          <span>{vehicle.state?.observed_project_version ?? "no telemetry"}</span>
          <em>{STATE_LABEL[state]}</em>
        </div>
      </Html>
    </group>
  );
}

function OrbitLine({ orbit, color, selected }: { orbit: Elements; color: string; selected: boolean }) {
  const points = useMemo(() => orbitPath(orbit, 160), [orbit]);
  return <Line points={points} color={color} lineWidth={selected ? 2.4 : 1.2} transparent opacity={selected ? 0.95 : 0.5} />;
}

interface FleetProps {
  vehicles: Vehicle[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  positions: React.MutableRefObject<Positions>;
  speed: number;
}

function Fleet({ vehicles, selectedId, onSelect, positions, speed }: FleetProps) {
  const placed = useMemo(() => placements(vehicles), [vehicles]);
  const moon = useRef<THREE.Group>(null);
  const crafts = useRef<Record<string, THREE.Group | null>>({});
  const sites = useRef<Record<string, THREE.Group | null>>({});
  const time = useRef(0);

  useFrame((_, delta) => {
    time.current += delta * speed;
    const t = time.current;
    const moonAt = positionAt(MOON_ORBIT, t);
    moon.current?.position.set(...moonAt);
    for (const vehicle of vehicles) {
      const place = placed[vehicle.id];
      const store = (positions.current[vehicle.id] ??= new THREE.Vector3());
      if (place.kind === "ground") {
        sites.current[vehicle.id]?.getWorldPosition(store);
        continue;
      }
      const local = positionAt(place.orbit, t);
      crafts.current[vehicle.id]?.position.set(...local);
      store.set(...local);
      if (place.kind === "moon") store.add(new THREE.Vector3(...moonAt));
    }
  });

  const ground = vehicles.filter((v) => placed[v.id].kind === "ground");
  const aroundEarth = vehicles.filter((v) => placed[v.id].kind === "earth");
  const aroundMoon = vehicles.filter((v) => placed[v.id].kind === "moon");
  const orbitOf = (vehicle: Vehicle) => (placed[vehicle.id] as { orbit: Elements }).orbit;

  return (
    <>
      <Earth speed={speed}>
        {ground.map((vehicle, index) => (
          <group
            key={vehicle.id}
            ref={(node) => void (sites.current[vehicle.id] = node)}
            position={TESTBED_SITE.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), index * 0.9).multiplyScalar(1.06)}
          >
            <Marker vehicle={vehicle} selected={vehicle.id === selectedId} onSelect={onSelect} shape="site" />
          </group>
        ))}
      </Earth>

      <OrbitLine orbit={MOON_ORBIT} color="#4a5896" selected={false} />
      {aroundEarth.map((vehicle) => (
        <group key={vehicle.id}>
          <OrbitLine orbit={orbitOf(vehicle)} color={STATE_COLOR[gateState(vehicle)]} selected={vehicle.id === selectedId} />
          <group ref={(node) => void (crafts.current[vehicle.id] = node)}>
            <Marker vehicle={vehicle} selected={vehicle.id === selectedId} onSelect={onSelect} shape="craft" />
          </group>
        </group>
      ))}

      <group ref={moon}>
        <Moon radius={MOON_RADIUS} />
        {aroundMoon.map((vehicle) => (
          <group key={vehicle.id}>
            <OrbitLine orbit={orbitOf(vehicle)} color={STATE_COLOR[gateState(vehicle)]} selected={vehicle.id === selectedId} />
            <group ref={(node) => void (crafts.current[vehicle.id] = node)}>
              <Marker vehicle={vehicle} selected={vehicle.id === selectedId} onSelect={onSelect} shape="craft" />
            </group>
          </group>
        ))}
      </group>
    </>
  );
}

/** A line from the ground to a vehicle while an uplink is shown: green if it ran, red if refused. */
function UplinkBeam({ target, beam, positions, from }: { target: string; beam: Beam; positions: React.MutableRefObject<Positions>; from: string | null }) {
  const line = useRef<THREE.Line>(null);
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6), 3));
    return g;
  }, []);
  const material = useMemo(
    () => new THREE.LineBasicMaterial({ color: beam.kind === "sent" ? "#4be3a0" : "#ff6b70", transparent: true }),
    [beam.kind],
  );
  const object = useMemo(() => new THREE.Line(geometry, material), [geometry, material]);
  useFrame(({ clock }) => {
    const end = positions.current[target];
    const start = (from && positions.current[from]) || new THREE.Vector3();
    if (!end) return;
    const attribute = geometry.getAttribute("position") as THREE.BufferAttribute;
    attribute.setXYZ(0, start.x, start.y, start.z);
    attribute.setXYZ(1, end.x, end.y, end.z);
    attribute.needsUpdate = true;
    const remaining = Math.max(0, beam.until - Date.now()) / 4000;
    material.opacity = remaining * (0.55 + 0.45 * Math.sin(clock.elapsedTime * 14));
    object.visible = remaining > 0 && from !== target;
  });
  return <primitive ref={line} object={object} />;
}

function Rig({ selectedId, positions }: { selectedId: string | null; positions: React.MutableRefObject<Positions> }) {
  const controls = useRef<any>(null);
  const origin = useMemo(() => new THREE.Vector3(), []);
  useFrame(() => {
    if (!controls.current) return;
    const goal = (selectedId && positions.current[selectedId]) || origin;
    controls.current.target.lerp(goal, 0.05);
    controls.current.update();
  });
  return <OrbitControls ref={controls} enablePan={false} minDistance={3} maxDistance={34} enableDamping dampingFactor={0.08} />;
}

export interface SceneProps {
  vehicles: Vehicle[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  beams: Record<string, Beam>;
}

export function Scene({ vehicles, selectedId, onSelect, beams }: SceneProps) {
  const positions = useRef<Positions>({});
  const reducedMotion = useMemo(
    () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    [],
  );
  const ground = vehicles.find((v) => v.kind === "testbed")?.id ?? null;
  return (
    <Canvas camera={{ position: [6, 5, 12.5], fov: 40 }} dpr={[1, 2]}>
      <color attach="background" args={["#0b1130"]} />
      <ambientLight intensity={0.5} />
      <directionalLight position={SUN_DIRECTION.clone().multiplyScalar(30)} intensity={2.4} />
      <Stars radius={90} depth={40} count={2600} factor={3.2} saturation={0.4} fade speed={reducedMotion ? 0 : 0.4} />
      <Fleet vehicles={vehicles} selectedId={selectedId} onSelect={onSelect} positions={positions} speed={reducedMotion ? 0.15 : 1} />
      {Object.entries(beams).map(([vehicleId, beam]) => (
        <UplinkBeam key={`${vehicleId}-${beam.until}`} target={vehicleId} beam={beam} positions={positions} from={ground} />
      ))}
      <Rig selectedId={selectedId} positions={positions} />
    </Canvas>
  );
}

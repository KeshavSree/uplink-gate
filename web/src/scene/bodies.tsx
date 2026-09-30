// Earth and Moon. Drawn procedurally so the app ships no image assets.

import { useFrame } from "@react-three/fiber";
import { type ReactNode, useMemo, useRef } from "react";
import * as THREE from "three";

export const SUN_DIRECTION = new THREE.Vector3(1, 0.28, 0.45).normalize();

const NOISE = /* glsl */ `
  float hash(vec3 p) {
    p = fract(p * 0.3183099 + 0.1);
    p *= 17.0;
    return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
  }
  float noise(vec3 x) {
    vec3 i = floor(x);
    vec3 f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    return mix(
      mix(mix(hash(i), hash(i + vec3(1, 0, 0)), f.x),
          mix(hash(i + vec3(0, 1, 0)), hash(i + vec3(1, 1, 0)), f.x), f.y),
      mix(mix(hash(i + vec3(0, 0, 1)), hash(i + vec3(1, 0, 1)), f.x),
          mix(hash(i + vec3(0, 1, 1)), hash(i + vec3(1, 1, 1)), f.x), f.y),
      f.z);
  }
  float fbm(vec3 p) {
    float value = 0.0;
    float amplitude = 0.5;
    for (int octave = 0; octave < 5; octave++) {
      value += amplitude * noise(p);
      p *= 2.03;
      amplitude *= 0.5;
    }
    return value;
  }
`;

const VERTEX = /* glsl */ `
  varying vec3 vLocal;
  varying vec3 vWorldNormal;
  varying vec3 vView;
  void main() {
    vLocal = position;
    vWorldNormal = normalize(mat3(modelMatrix) * normal);
    vec4 world = modelMatrix * vec4(position, 1.0);
    vView = normalize(cameraPosition - world.xyz);
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const EARTH_FRAGMENT = /* glsl */ `
  uniform vec3 sunDirection;
  varying vec3 vLocal;
  varying vec3 vWorldNormal;
  varying vec3 vView;
  ${NOISE}
  void main() {
    vec3 p = normalize(vLocal);
    float land = smoothstep(0.50, 0.56, fbm(p * 2.1 + 4.0));
    float ice = smoothstep(0.78, 0.92, abs(p.y));
    float cloud = smoothstep(0.58, 0.78, fbm(p * 3.7 + 11.0));

    vec3 ocean = vec3(0.04, 0.16, 0.42);
    vec3 ground = mix(vec3(0.12, 0.30, 0.16), vec3(0.42, 0.36, 0.22), fbm(p * 6.0));
    vec3 surface = mix(ocean, ground, land);
    surface = mix(surface, vec3(0.86, 0.90, 0.95), ice);
    surface = mix(surface, vec3(0.95), cloud * 0.55);

    float light = dot(vWorldNormal, sunDirection);
    float day = smoothstep(-0.12, 0.30, light);
    vec3 night = vec3(0.012, 0.022, 0.07) + land * (1.0 - cloud) * vec3(0.10, 0.075, 0.03);
    vec3 color = mix(night, surface * (0.22 + 0.95 * max(light, 0.0)), day);

    float rim = pow(1.0 - max(dot(vWorldNormal, vView), 0.0), 3.0);
    color += vec3(0.20, 0.42, 1.0) * rim * (0.25 + 0.75 * day);
    gl_FragColor = vec4(color, 1.0);
  }
`;

const MOON_FRAGMENT = /* glsl */ `
  uniform vec3 sunDirection;
  varying vec3 vLocal;
  varying vec3 vWorldNormal;
  ${NOISE}
  void main() {
    vec3 p = normalize(vLocal);
    float maria = smoothstep(0.46, 0.62, fbm(p * 1.9 + 2.0));
    float grain = fbm(p * 9.0);
    vec3 regolith = mix(vec3(0.62, 0.61, 0.58), vec3(0.34, 0.34, 0.35), maria);
    regolith *= 0.82 + 0.36 * grain;
    float light = max(dot(vWorldNormal, sunDirection), 0.0);
    gl_FragColor = vec4(regolith * (0.05 + 1.05 * light), 1.0);
  }
`;

const ATMOSPHERE_FRAGMENT = /* glsl */ `
  uniform vec3 sunDirection;
  varying vec3 vWorldNormal;
  varying vec3 vView;
  void main() {
    float rim = pow(0.72 - dot(vWorldNormal, -vView), 3.0);
    float lit = 0.35 + 0.65 * smoothstep(-0.3, 0.5, dot(-vWorldNormal, sunDirection));
    gl_FragColor = vec4(vec3(0.25, 0.48, 1.0) * rim * lit, 1.0);
  }
`;

export function Earth({ speed, children }: { speed: number; children?: ReactNode }) {
  const spin = useRef<THREE.Group>(null);
  const uniforms = useMemo(() => ({ sunDirection: { value: SUN_DIRECTION } }), []);
  useFrame((_, delta) => {
    if (spin.current) spin.current.rotation.y += delta * 0.03 * speed;
  });
  return (
    <group>
      <group ref={spin} rotation={[0.18, 0, -0.41]}>
        <mesh>
          <sphereGeometry args={[1, 96, 96]} />
          <shaderMaterial vertexShader={VERTEX} fragmentShader={EARTH_FRAGMENT} uniforms={uniforms} />
        </mesh>
        {children}
      </group>
      <mesh scale={1.16}>
        <sphereGeometry args={[1, 48, 48]} />
        <shaderMaterial
          vertexShader={VERTEX}
          fragmentShader={ATMOSPHERE_FRAGMENT}
          uniforms={uniforms}
          side={THREE.BackSide}
          blending={THREE.AdditiveBlending}
          transparent
          depthWrite={false}
        />
      </mesh>
    </group>
  );
}

export function Moon({ radius }: { radius: number }) {
  const uniforms = useMemo(() => ({ sunDirection: { value: SUN_DIRECTION } }), []);
  return (
    <mesh>
      <sphereGeometry args={[radius, 64, 64]} />
      <shaderMaterial vertexShader={VERTEX} fragmentShader={MOON_FRAGMENT} uniforms={uniforms} />
    </mesh>
  );
}

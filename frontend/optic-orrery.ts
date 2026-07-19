// Direction 02 "Cognitive Orrery" — the optic as an instrument. The eye sits
// at the center of an orrery of brass-and-glass calibration rings, satellite
// lens discs orbit along the horizontal axis, and a live waveform beam runs
// the full midline of the stage through the eye. The container is a
// full-bleed band behind the bridge chrome (pointer-events: none), so
// parallax listens on window rather than the container.

import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

import type { HalOpticApi, HalToolKind, HalVisualState } from "./optic-api";

interface MovingRing {
  readonly object: THREE.Object3D;
  readonly speed: number;
  readonly axis: "z" | "y";
}

interface StateProfile {
  readonly bloom: number;
  readonly core: number;
  readonly beam: number;
  readonly motion: number;
}

const STATE_PROFILES: Record<HalVisualState, StateProfile> = {
  idle: { bloom: 0.34, core: 1.7, beam: 0.2, motion: 0.26 },
  listening: { bloom: 0.66, core: 3.9, beam: 1, motion: 0.52 },
  thinking: { bloom: 0.24, core: 1.05, beam: 0.32, motion: 1.4 },
  speaking: { bloom: 0.74, core: 4.5, beam: 0.85, motion: 0.48 },
  denied: { bloom: 0.12, core: 0.5, beam: 0.07, motion: 0.05 }
};

const TOOL_COLORS: Record<Exclude<HalToolKind, null>, number> = {
  fetch: 0xff3b24,
  execute: 0xff541f,
  search: 0xe9271c,
  read: 0xff2d20
};

// The mandala is ~9.2 units tall; the beam plane overflows any viewport.
const COMPOSITION_HEIGHT = 9.6;
const COMPOSITION_WIDTH = 10.4;

const VERTEX_SHADER = /* glsl */ `
  varying vec2 vUv;

  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const IRIS_FRAGMENT_SHADER = /* glsl */ `
  varying vec2 vUv;
  uniform float uTime;
  uniform float uEnergy;
  uniform float uState;
  uniform float uToolPulse;

  float line(float value, float width) {
    float d = abs(fract(value) - 0.5);
    return 1.0 - smoothstep(width, width + 0.035, d);
  }

  void main() {
    vec2 p = (vUv - 0.5) * 2.0;
    float radius = length(p);
    if (radius > 1.0) discard;

    float angle = atan(p.y, p.x);
    float radialGrid = line(radius * 8.0 - uTime * 0.04, 0.025);
    float spokes = line((angle / 6.2831853 + 0.5) * 18.0, 0.02);
    float pulse = 0.5 + 0.5 * sin(9.0 * radius - uTime * (1.0 + uState));
    float center = pow(max(0.0, 1.0 - radius), 2.2);
    float rim = smoothstep(1.0, 0.55, radius);
    float energy = clamp(uEnergy, 0.0, 1.0);
    float tool = uToolPulse * smoothstep(0.7, 0.2, abs(radius - 0.5));

    vec3 deepRed = vec3(0.05, 0.002, 0.0);
    vec3 signalRed = vec3(0.96, 0.05, 0.015);
    vec3 warmCore = vec3(1.0, 0.74, 0.55);
    vec3 color = mix(deepRed, signalRed, center * (0.8 + 0.2 * pulse));
    color += signalRed * (radialGrid * 0.1 + spokes * 0.08) * rim;
    color += signalRed * energy * pulse * 0.3;
    color += vec3(1.0, 0.2, 0.05) * tool * 0.42;
    color = mix(color, warmCore, pow(max(0.0, 1.0 - radius * 4.4), 5.0));

    float alpha = smoothstep(1.0, 0.9, radius) * (0.9 + center * 0.1);
    gl_FragColor = vec4(color, alpha);
  }
`;

const CORE_FRAGMENT_SHADER = /* glsl */ `
  varying vec2 vUv;
  uniform float uIntensity;

  void main() {
    vec2 p = (vUv - 0.5) * 2.0;
    float radius = length(p);
    if (radius > 1.0) discard;
    float glow = pow(max(0.0, 1.0 - radius), 3.1);
    vec3 color = mix(vec3(1.0, 0.09, 0.015), vec3(1.0, 0.9, 0.76), glow);
    gl_FragColor = vec4(color * (0.7 + uIntensity * 0.14), glow * 0.92);
  }
`;

// The signature element: a camera-facing ribbon across the whole stage whose
// centerline is displaced by summed travelling waves scaled by voice energy.
const BEAM_FRAGMENT_SHADER = /* glsl */ `
  varying vec2 vUv;
  uniform float uTime;
  uniform float uEnergy;
  uniform float uLevel;

  float wave(float x, float t) {
    return sin(x * 5.1 - t * 2.1) * 0.42
         + sin(x * 11.7 + t * 1.3) * 0.27
         + sin(x * 23.0 - t * 3.7) * 0.18
         + sin(x * 47.0 + t * 5.3) * 0.13;
  }

  void main() {
    float x = (vUv.x - 0.5) * 44.0;
    float t = uTime;
    float amp = 0.035 + uLevel * 0.1 + uEnergy * 0.62;
    // Waves tighten near the center so the eye reads as the source.
    float focus = 1.0 - 0.55 * exp(-abs(x) * 0.16);
    float displaced = wave(x * 0.55, t) * amp * focus;
    float y = (vUv.y - 0.5) * 3.2;
    float d = abs(y - displaced);

    float coreLine = 1.0 - smoothstep(0.0, 0.028 + uEnergy * 0.02, d);
    float glow = exp(-d * (7.0 - uEnergy * 2.4)) * (0.24 + uEnergy * 0.5 + uLevel * 0.22);
    float baseline = exp(-abs(y) * 26.0) * 0.11 * uLevel;

    vec3 signalRed = vec3(1.0, 0.12, 0.05);
    vec3 hot = vec3(1.0, 0.82, 0.62);
    vec3 color = signalRed * (glow + baseline) + mix(signalRed, hot, uEnergy) * coreLine * (0.4 + uLevel * 0.6);
    float alpha = clamp(coreLine + glow + baseline, 0.0, 1.0);
    gl_FragColor = vec4(color, alpha);
  }
`;

class OrreryOptic {
  private readonly camera = new THREE.PerspectiveCamera(30, 1, 0.1, 60);
  private readonly timer = new THREE.Timer();
  private readonly composer: EffectComposer;
  private readonly bloomPass: UnrealBloomPass;
  private readonly beamMaterial: THREE.ShaderMaterial;
  private readonly coreGlow: THREE.ShaderMaterial;
  private readonly coreLight = new THREE.PointLight(0xff2a18, 3.2, 8, 2);
  private readonly coreMaterial = new THREE.MeshStandardMaterial({
    color: 0xffb09a,
    emissive: 0xff2a18,
    emissiveIntensity: 2.2,
    metalness: 0,
    roughness: 0.18
  });
  private readonly container: HTMLElement;
  private readonly eye: HTMLElement;
  private readonly irisMaterial: THREE.ShaderMaterial;
  private readonly lensMaterial = new THREE.MeshPhysicalMaterial({
    color: 0x8c0802,
    emissive: 0x6a0300,
    emissiveIntensity: 0.2,
    metalness: 0,
    roughness: 0.1,
    transmission: 0.3,
    thickness: 1.3,
    ior: 1.52,
    clearcoat: 1,
    clearcoatRoughness: 0.09,
    transparent: true,
    opacity: 0.8,
    depthWrite: false
  });
  private readonly movingRings: MovingRing[] = [];
  private readonly satellites: THREE.Group[] = [];
  private readonly reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  private readonly renderer: THREE.WebGLRenderer;
  private readonly root = new THREE.Group();
  private readonly scene = new THREE.Scene();
  private readonly targetPointer = new THREE.Vector2();

  private audioEnergy = 0;
  private beamLevel = 0.2;
  private destroyed = false;
  private frameId = 0;
  private pointer = new THREE.Vector2();
  private resizeObserver: ResizeObserver;
  private state: HalVisualState = "idle";
  private stateObserver: MutationObserver;
  private targetAudioEnergy = 0;
  private targetToolPulse = 0;
  private toolKind: HalToolKind = null;

  constructor(container: HTMLElement, eye: HTMLElement) {
    this.container = container;
    this.eye = eye;
    this.renderer = new THREE.WebGLRenderer({
      alpha: false,
      antialias: true,
      powerPreference: "high-performance"
    });
    this.renderer.domElement.className = "optic-webgl-canvas";
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.85;
    this.renderer.setClearColor(0x020202, 1);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    this.container.appendChild(this.renderer.domElement);

    this.scene.fog = new THREE.FogExp2(0x020202, 0.04);
    this.scene.add(this.root);
    this.timer.connect(document);

    this.irisMaterial = new THREE.ShaderMaterial({
      uniforms: {
        uTime: { value: 0 },
        uEnergy: { value: 0 },
        uState: { value: 0 },
        uToolPulse: { value: 0 }
      },
      vertexShader: VERTEX_SHADER,
      fragmentShader: IRIS_FRAGMENT_SHADER,
      transparent: true,
      depthWrite: false
    });
    this.coreGlow = new THREE.ShaderMaterial({
      uniforms: { uIntensity: { value: 1 } },
      vertexShader: VERTEX_SHADER,
      fragmentShader: CORE_FRAGMENT_SHADER,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending
    });
    this.beamMaterial = new THREE.ShaderMaterial({
      uniforms: {
        uTime: { value: 0 },
        uEnergy: { value: 0 },
        uLevel: { value: 0.2 }
      },
      vertexShader: VERTEX_SHADER,
      fragmentShader: BEAM_FRAGMENT_SHADER,
      transparent: true,
      depthWrite: false,
      depthTest: true,
      blending: THREE.AdditiveBlending
    });

    this.buildScene();

    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloomPass = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.5, 0.6, 0.5);
    this.composer.addPass(this.bloomPass);
    this.composer.addPass(new OutputPass());

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.container);
    this.stateObserver = new MutationObserver(() => this.syncStateFromDom());
    this.stateObserver.observe(this.eye, { attributes: true, attributeFilter: ["class"] });
    window.addEventListener("pointermove", this.onPointerMove);
    window.addEventListener("blur", this.onPointerLeave);
    this.reducedMotionQuery.addEventListener("change", this.onMotionPreferenceChange);

    this.resize();
    this.syncStateFromDom();
    this.container.closest(".eye-module")?.classList.add("webgl-ready");
    this.frameId = requestAnimationFrame(this.animate);
  }

  setAudioEnergy(energy: number): void {
    this.targetAudioEnergy = THREE.MathUtils.clamp(energy, 0, 1);
  }

  setState(state: HalVisualState): void {
    this.state = state;
    this.updateStatusLabel(state);
  }

  setToolKind(kind: HalToolKind): void {
    this.toolKind = kind;
    if (kind) this.targetToolPulse = 1;
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    cancelAnimationFrame(this.frameId);
    this.resizeObserver.disconnect();
    this.stateObserver.disconnect();
    window.removeEventListener("pointermove", this.onPointerMove);
    window.removeEventListener("blur", this.onPointerLeave);
    this.reducedMotionQuery.removeEventListener("change", this.onMotionPreferenceChange);
    this.timer.dispose();

    const materials = new Set<THREE.Material>();
    this.scene.traverse((object) => {
      if (!(object instanceof THREE.Mesh)) return;
      object.geometry.dispose();
      const owned = Array.isArray(object.material) ? object.material : [object.material];
      for (const material of owned) materials.add(material);
    });
    for (const material of materials) material.dispose();
    this.composer.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
    this.container.closest(".eye-module")?.classList.remove("webgl-ready");
  }

  private buildScene(): void {
    const ambient = new THREE.AmbientLight(0x2a1c12, 1.1);
    const key = new THREE.DirectionalLight(0xffd9b0, 3);
    key.position.set(-4, 4.5, 7);
    const rim = new THREE.DirectionalLight(0xff2a18, 0.6);
    rim.position.set(4.5, -2.5, 4);
    const fill = new THREE.DirectionalLight(0x9aa4ae, 0.9);
    fill.position.set(4, -4.5, 6);
    this.scene.add(ambient, key, rim, fill);

    // Metals stay below full metalness — there is no environment map, so
    // fully metallic rings would render near-black; the faint warm emissive
    // keeps thin rings legible against the void.
    const brass = new THREE.MeshStandardMaterial({
      color: 0x8a6134,
      emissive: 0x140b03,
      emissiveIntensity: 1,
      metalness: 0.78,
      roughness: 0.38
    });
    const darkBrass = new THREE.MeshStandardMaterial({
      color: 0x4a3620,
      emissive: 0x0c0703,
      emissiveIntensity: 1,
      metalness: 0.8,
      roughness: 0.42
    });
    const steel = new THREE.MeshStandardMaterial({
      color: 0x383b3e,
      emissive: 0x050607,
      emissiveIntensity: 1,
      metalness: 0.82,
      roughness: 0.34
    });
    const glassRing = new THREE.MeshPhysicalMaterial({
      color: 0x241b12,
      metalness: 0.1,
      roughness: 0.12,
      transmission: 0.55,
      thickness: 0.6,
      ior: 1.5,
      transparent: true,
      opacity: 0.6,
      depthWrite: false
    });
    const redMetal = new THREE.MeshStandardMaterial({
      color: 0x2b0301,
      emissive: 0xc10c04,
      emissiveIntensity: 0.5,
      metalness: 0.76,
      roughness: 0.25
    });

    // Central assembly: rim, iris, lens, core — the eye at the orrery's heart.
    const rimTorus = new THREE.Mesh(new THREE.TorusGeometry(1.72, 0.13, 24, 144), steel);
    rimTorus.position.z = 0.32;
    this.root.add(rimTorus);
    const rimAccent = new THREE.Mesh(new THREE.TorusGeometry(1.9, 0.035, 18, 144), brass);
    rimAccent.position.z = 0.36;
    this.root.add(rimAccent);

    const iris = new THREE.Mesh(new THREE.CircleGeometry(1.58, 128), this.irisMaterial);
    iris.position.z = 0.3;
    this.root.add(iris);

    const lens = new THREE.Mesh(new THREE.SphereGeometry(1.54, 96, 48), this.lensMaterial);
    lens.scale.set(1, 1, 0.3);
    lens.position.z = 0.5;
    this.root.add(lens);

    const core = new THREE.Mesh(new THREE.SphereGeometry(0.11, 40, 24), this.coreMaterial);
    core.position.z = 1.05;
    this.root.add(core);
    this.coreLight.position.set(0, 0, 1.45);
    this.root.add(this.coreLight);

    const glow = new THREE.Mesh(new THREE.PlaneGeometry(0.66, 0.66), this.coreGlow);
    glow.position.z = 1;
    this.root.add(glow);

    const reflectionMaterial = new THREE.MeshBasicMaterial({
      color: 0xffeee6,
      transparent: true,
      opacity: 0.2,
      depthWrite: false,
      blending: THREE.AdditiveBlending
    });
    const highlight = new THREE.Mesh(new THREE.SphereGeometry(0.11, 32, 20), reflectionMaterial);
    highlight.scale.set(1.1, 0.68, 0.2);
    highlight.position.set(-0.4, 0.44, 1.05);
    this.root.add(highlight);

    // Mandala rings: alternating brass / steel / glass, slight z scatter and
    // tilts for depth, a few with calibration gaps. Independent slow spins.
    const rings: ReadonlyArray<
      [number, number, number, THREE.Material, number, number, "z" | "y"]
    > = [
      // radius, tube, z, material, speed, arc, spin axis
      [2.2, 0.045, 0.12, darkBrass, -0.05, Math.PI * 2, "z"],
      [2.52, 0.02, -0.05, brass, 0.065, Math.PI * 2, "z"],
      [2.86, 0.06, 0.06, steel, -0.04, Math.PI * 2, "z"],
      [3.22, 0.016, -0.16, brass, 0.09, Math.PI * 1.62, "z"],
      [3.58, 0.05, -0.02, glassRing, 0.03, Math.PI * 2, "z"],
      [3.94, 0.022, 0.08, darkBrass, -0.075, Math.PI * 1.8, "z"],
      [4.32, 0.014, -0.1, brass, 0.055, Math.PI * 2, "z"],
      [4.6, 0.008, 0, redMetal, -0.11, Math.PI * 1.45, "z"]
    ];
    for (const [radius, tube, z, material, speed, arc, axis] of rings) {
      const ring = new THREE.Mesh(new THREE.TorusGeometry(radius, tube, 16, 160, arc), material);
      ring.position.z = z;
      ring.rotation.z = Math.random() * Math.PI * 2;
      ring.rotation.x = (Math.random() - 0.5) * 0.1;
      this.root.add(ring);
      this.movingRings.push({ object: ring, speed, axis });
    }

    // One ring tilted into depth — the orrery's ecliptic.
    const ecliptic = new THREE.Mesh(new THREE.TorusGeometry(3.4, 0.012, 12, 200), brass);
    ecliptic.rotation.x = Math.PI / 2.22;
    this.root.add(ecliptic);
    this.movingRings.push({ object: ecliptic, speed: 0.14, axis: "y" });

    this.addCalibrationTicks(brass, 3.76);
    this.addCalibrationTicks(steel, 2.38);

    // Satellite lens discs receding along the beam axis, both sides.
    const satelliteSpecs: ReadonlyArray<[number, number, number]> = [
      // x, disc radius, z
      [-3.45, 0.85, -0.5],
      [-4.55, 0.58, -0.85],
      [-5.5, 0.4, -1.15],
      [3.45, 0.85, -0.5],
      [4.55, 0.58, -0.85],
      [5.5, 0.4, -1.15]
    ];
    for (const [x, radius, z] of satelliteSpecs) {
      const satellite = new THREE.Group();
      const holder = new THREE.Mesh(new THREE.TorusGeometry(radius, radius * 0.09, 16, 96), steel);
      const inner = new THREE.Mesh(new THREE.TorusGeometry(radius * 0.66, radius * 0.045, 12, 72), brass);
      const disc = new THREE.Mesh(
        new THREE.CircleGeometry(radius * 0.92, 64),
        new THREE.MeshPhysicalMaterial({
          color: 0x150a06,
          metalness: 0.2,
          roughness: 0.12,
          transmission: 0.5,
          thickness: 0.5,
          ior: 1.5,
          transparent: true,
          opacity: 0.55,
          depthWrite: false
        })
      );
      const pupil = new THREE.Mesh(
        new THREE.CircleGeometry(radius * 0.16, 32),
        new THREE.MeshStandardMaterial({
          color: 0x1c0301,
          emissive: 0xb00c04,
          emissiveIntensity: 0.9,
          metalness: 0.4,
          roughness: 0.3
        })
      );
      pupil.position.z = 0.02;
      satellite.add(holder, inner, disc, pupil);
      satellite.position.set(x, 0, z);
      this.root.add(satellite);
      this.satellites.push(satellite);
      this.movingRings.push({ object: inner, speed: x > 0 ? -0.22 : 0.22, axis: "z" });
    }

    // The beam: overflows every viewport, occluded by the central assembly.
    const beam = new THREE.Mesh(new THREE.PlaneGeometry(44, 3.2), this.beamMaterial);
    beam.position.z = -0.55;
    beam.renderOrder = -1;
    this.root.add(beam);

    this.root.rotation.x = -0.02;
  }

  private addCalibrationTicks(material: THREE.Material, radius: number): void {
    const ticks = new THREE.Group();
    const geometry = new THREE.BoxGeometry(0.015, 0.09, 0.015);
    for (let index = 0; index < 72; index += 1) {
      const angle = (index / 72) * Math.PI * 2;
      const major = index % 6 === 0;
      const tick = new THREE.Mesh(geometry, material);
      tick.position.set(Math.cos(angle) * radius, Math.sin(angle) * radius, -0.06);
      tick.rotation.z = angle + Math.PI / 2;
      if (major) tick.scale.y = 1.9;
      ticks.add(tick);
    }
    this.root.add(ticks);
    this.movingRings.push({ object: ticks, speed: radius > 3 ? 0.018 : -0.024, axis: "z" });
  }

  private resize(): void {
    const { width, height } = this.container.getBoundingClientRect();
    if (width < 2 || height < 2) return;
    this.camera.aspect = width / height;
    const halfFov = THREE.MathUtils.degToRad(this.camera.fov / 2);
    const fitHeight = COMPOSITION_HEIGHT / 2 / Math.tan(halfFov);
    const fitWidth = COMPOSITION_WIDTH / 2 / (Math.tan(halfFov) * this.camera.aspect);
    this.camera.position.z = Math.max(fitHeight, fitWidth);
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    this.composer.setSize(width, height);
  }

  private syncStateFromDom(): void {
    let state: HalVisualState = "idle";
    if (this.eye.classList.contains("denied")) state = "denied";
    else if (this.eye.classList.contains("listening")) state = "listening";
    else if (this.eye.classList.contains("speaking")) state = "speaking";
    else if (this.eye.classList.contains("thinking")) state = "thinking";
    this.setState(state);

    const kind = this.eye.classList.contains("kind-execute")
      ? "execute"
      : this.eye.classList.contains("kind-fetch")
        ? "fetch"
        : this.eye.classList.contains("kind-search")
          ? "search"
          : this.eye.classList.contains("kind-read")
            ? "read"
            : null;
    if (kind !== this.toolKind) this.setToolKind(kind);
  }

  private updateStatusLabel(state: HalVisualState): void {
    const label = document.getElementById("optic-status-label");
    if (!label) return;
    label.textContent = {
      idle: "Standby",
      listening: "Listening",
      thinking: "Thinking",
      speaking: "Speaking",
      denied: "Permission denied"
    }[state];
    label.dataset.state = state;
  }

  private onPointerMove = (event: PointerEvent): void => {
    if (this.reducedMotionQuery.matches) return;
    this.targetPointer.set(
      THREE.MathUtils.clamp((event.clientX / window.innerWidth) * 2 - 1, -1, 1),
      THREE.MathUtils.clamp(-((event.clientY / window.innerHeight) * 2 - 1), -1, 1)
    );
  };

  private onPointerLeave = (): void => {
    this.targetPointer.set(0, 0);
  };

  private onMotionPreferenceChange = (): void => {
    if (this.reducedMotionQuery.matches) {
      this.targetPointer.set(0, 0);
      this.pointer.set(0, 0);
    }
  };

  private animate = (timestamp?: number): void => {
    if (this.destroyed) return;
    this.frameId = requestAnimationFrame(this.animate);
    this.timer.update(timestamp);
    const delta = Math.min(this.timer.getDelta(), 0.05);
    const elapsed = this.timer.getElapsed();
    const profile = STATE_PROFILES[this.state];
    const motion = this.reducedMotionQuery.matches ? 0 : profile.motion;

    this.audioEnergy = THREE.MathUtils.damp(this.audioEnergy, this.targetAudioEnergy, 8, delta);
    this.targetAudioEnergy = THREE.MathUtils.damp(this.targetAudioEnergy, 0, 2.6, delta);
    this.targetToolPulse = THREE.MathUtils.damp(this.targetToolPulse, 0, 2.8, delta);
    this.pointer.x = THREE.MathUtils.damp(this.pointer.x, this.targetPointer.x, 5, delta);
    this.pointer.y = THREE.MathUtils.damp(this.pointer.y, this.targetPointer.y, 5, delta);
    this.beamLevel = THREE.MathUtils.damp(this.beamLevel, profile.beam, 5, delta);

    for (const ring of this.movingRings) {
      ring.object.rotation[ring.axis] += ring.speed * motion * delta;
    }
    for (let index = 0; index < this.satellites.length; index += 1) {
      const satellite = this.satellites[index];
      if (!satellite) continue;
      satellite.position.y =
        this.reducedMotionQuery.matches ? 0 : Math.sin(elapsed * 0.5 + index * 1.7) * 0.05;
    }

    const breathing = this.reducedMotionQuery.matches
      ? 0
      : Math.sin(elapsed * (this.state === "listening" ? 3.6 : 1.25)) * 0.01;
    const desiredScale = 1 + breathing + this.audioEnergy * 0.03;
    this.root.scale.setScalar(THREE.MathUtils.damp(this.root.scale.x, desiredScale, 7, delta));
    this.root.rotation.y = THREE.MathUtils.damp(this.root.rotation.y, this.pointer.x * 0.06, 5, delta);
    this.root.rotation.x = THREE.MathUtils.damp(
      this.root.rotation.x,
      -0.02 - this.pointer.y * 0.05,
      5,
      delta
    );

    const stateIndex =
      this.state === "thinking" ? 1 : this.state === "speaking" ? 0.7 : this.state === "listening" ? 0.55 : 0.2;
    this.irisMaterial.uniforms.uTime!.value = elapsed;
    this.irisMaterial.uniforms.uEnergy!.value = this.audioEnergy;
    this.irisMaterial.uniforms.uState!.value = stateIndex;
    this.irisMaterial.uniforms.uToolPulse!.value = this.targetToolPulse;

    // Speaking rides an internal cadence so the beam sings even between
    // setAudioEnergy frames; listening tracks the mic energy directly.
    const speakingPulse =
      this.state === "speaking" && !this.reducedMotionQuery.matches
        ? (0.5 + 0.5 * Math.sin(elapsed * 7.3) * Math.sin(elapsed * 2.9)) * 0.3
        : 0;
    this.beamMaterial.uniforms.uTime!.value = elapsed;
    this.beamMaterial.uniforms.uEnergy!.value = Math.min(1, this.audioEnergy + speakingPulse);
    this.beamMaterial.uniforms.uLevel!.value = this.beamLevel;

    const activity = Math.max(this.audioEnergy, this.targetToolPulse * 0.55);
    this.bloomPass.strength = THREE.MathUtils.damp(
      this.bloomPass.strength,
      profile.bloom + activity * 0.42,
      6,
      delta
    );
    this.coreMaterial.emissiveIntensity = THREE.MathUtils.damp(
      this.coreMaterial.emissiveIntensity,
      profile.core + activity * 3.4,
      7,
      delta
    );
    this.lensMaterial.emissiveIntensity = THREE.MathUtils.damp(
      this.lensMaterial.emissiveIntensity,
      0.2 + profile.core * 0.1 + activity * 0.6,
      7,
      delta
    );
    this.coreLight.intensity = THREE.MathUtils.damp(
      this.coreLight.intensity,
      6 + profile.core * 1.5 + activity * 8,
      7,
      delta
    );
    this.coreGlow.uniforms.uIntensity!.value = profile.core + activity * 2.1;

    const toolColor = this.toolKind ? TOOL_COLORS[this.toolKind] : 0xff2a18;
    this.coreMaterial.emissive.lerp(new THREE.Color(toolColor), Math.min(1, delta * 4));

    this.composer.render();
  };
}

export function createOptic(container: HTMLElement, eye: HTMLElement): HalOpticApi {
  const optic = new OrreryOptic(container, eye);
  return {
    destroy: () => optic.destroy(),
    setAudioEnergy: (energy) => optic.setAudioEnergy(energy),
    setState: (state) => optic.setState(state),
    setToolKind: (kind) => optic.setToolKind(kind)
  };
}

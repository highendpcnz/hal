// Direction 04 "Ember Chorus" — HAL as a murmuration. Tens of thousands of
// stateless GPU particles drift in a flow field; every change in their
// behavior has a real cause (voice energy, state, tool calls, missions,
// history), and the eye exists only while HAL actually speaks, when the
// swarm condenses into an iris formation and dissolves again after.
// Design: docs/plans/2026-07-19-ember-chorus-design.md.
//
// Statelessness is the architecture: position is a pure vertex-shader
// function of (seed, time, uniforms), so nothing can diverge over an
// hours-long session, condensation is one eased uniform, reduced motion is
// a frozen clock, and a perf tier is just a draw range.

import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

import type { HalOpticApi, HalToolKind, HalVisualState } from "./optic-api";

interface StateProfile {
  readonly attend: number;
  readonly vortex: number;
  readonly chill: number;
  readonly coherence: number;
  readonly flow: number;
  readonly brightness: number;
  readonly bloom: number;
}

const STATE_PROFILES: Record<HalVisualState, StateProfile> = {
  idle: { attend: 0, vortex: 0, chill: 0, coherence: 0, flow: 0.3, brightness: 0.5, bloom: 0.3 },
  listening: { attend: 1, vortex: 0, chill: 0, coherence: 0, flow: 0.55, brightness: 0.85, bloom: 0.5 },
  thinking: { attend: 0.15, vortex: 1, chill: 0, coherence: 0, flow: 1, brightness: 0.6, bloom: 0.34 },
  speaking: { attend: 0.2, vortex: 0, chill: 0, coherence: 1, flow: 0.45, brightness: 0.9, bloom: 0.5 },
  denied: { attend: 0, vortex: 0, chill: 1, coherence: 0, flow: 0.04, brightness: 0.3, bloom: 0.12 }
};

const TOOL_COLORS: Record<Exclude<HalToolKind, null>, THREE.Color> = {
  fetch: new THREE.Color(0xff3b24),
  execute: new THREE.Color(0xff6a1f),
  search: new THREE.Color(0xe9271c),
  read: new THREE.Color(0xff2d20)
};

const MAX_PARTICLES = 48000;
const SATELLITE_SLOTS = 3;
const SATELLITE_SIZE = 700;
// The condensation eye forms right of center, toward the camera.
const EYE_CENTER = new THREE.Vector3(1.15, 0.1, 1.4);

const VERTEX_SHADER = /* glsl */ `
  attribute vec3 seed;
  attribute vec3 formation;
  attribute float heat;
  attribute float group;

  uniform float uFlowTime;
  uniform float uRealTime;
  uniform float uTurbulence;
  uniform float uAttend;
  uniform float uVortex;
  uniform float uChill;
  uniform float uCoherence;
  uniform float uShimmer;
  uniform float uPulseAge;
  uniform float uMissions;
  uniform float uSpreadScale;
  uniform float uSize;
  uniform vec3 uEyeCenter;

  varying float vHeat;
  varying float vBrightness;
  varying float vPulse;
  varying float vChill;

  void main() {
    float phase = seed.x * 6.2831853 + seed.y * 12.566371;

    // Home: an ellipsoid cloud, denser toward the middle.
    vec3 centered = seed - 0.5;
    vec3 home = centered * length(centered) * 2.0 * vec3(10.5, 5.6, 4.2) * uSpreadScale;

    // Drift: layered trig flow — cheap curl. Only its clock is stateful,
    // and the CPU owns the clock (frozen when denied or reduced-motion).
    vec3 wander = vec3(
      sin(uFlowTime * 0.23 + phase + home.y * 0.34),
      sin(uFlowTime * 0.27 + phase * 1.7 + home.x * 0.27),
      sin(uFlowTime * 0.19 + phase * 2.3 + home.z * 0.41)
    ) * 1.35;

    // Turbulence: voice energy roughens the field.
    vec3 turb = vec3(
      sin(uRealTime * 2.9 + phase * 3.1),
      cos(uRealTime * 3.7 + phase * 2.3),
      sin(uRealTime * 3.3 + phase * 4.7)
    ) * uTurbulence * 1.6;

    vec3 pos = home + wander + turb;

    // Vortex (thinking): wind the field around the depth axis.
    float radius = length(pos.xy) + 0.001;
    float spin = uVortex * uFlowTime * (1.4 / (1.0 + radius * 0.45));
    float cs = cos(spin);
    float sn = sin(spin);
    pos = vec3(pos.x * cs - pos.y * sn, pos.x * sn + pos.y * cs, pos.z);

    // Attention (listening): tighten and lean toward the viewer.
    pos = mix(pos, pos * vec3(0.72, 0.72, 0.85) + vec3(0.0, 0.0, 1.6), uAttend);

    // Chill (denied): a fixed per-seed scatter, cold and still.
    vec3 scatter = normalize(centered + 0.001) * (1.5 + seed.z * 2.5);
    pos += scatter * uChill;

    // Mission satellites: reserved groups orbit the main body while their
    // mission is genuinely running; otherwise they rejoin the flock.
    if (group > 0.5) {
      float slot = group - 1.0;
      float orbitActive = step(slot + 0.5, uMissions);
      float orbitPhase = uFlowTime * (0.32 + slot * 0.07) + slot * 2.0944;
      vec3 orbitCenter = vec3(cos(orbitPhase) * 6.4, sin(orbitPhase * 1.31) * 3.1, sin(orbitPhase) * 1.8);
      vec3 local = (seed - 0.5) * 1.7 + vec3(
        sin(uFlowTime * 0.9 + phase),
        cos(uFlowTime * 1.1 + phase * 1.3),
        sin(uFlowTime * 0.7 + phase * 2.1)
      ) * 0.35;
      pos = mix(pos, orbitCenter + local, orbitActive);
    }

    // Coherence (speaking): fold into the iris formation. The mix happens
    // against the flowing position, so the eye assembles out of weather.
    vec3 seat = uEyeCenter + formation;
    seat.xy += vec2(
      sin(uRealTime * 9.0 + phase * 5.0),
      cos(uRealTime * 11.0 + phase * 7.0)
    ) * uShimmer * 0.05 * (0.4 + heat);
    pos = mix(pos, seat, uCoherence);

    // Tool pulse: one front sweeps outward from the middle; particles
    // near the front brighten as it passes.
    float front = uPulseAge * 9.0;
    float band = 1.0 - smoothstep(0.0, 1.4, abs(length(pos) - front));
    vPulse = band * step(0.01, uPulseAge) * step(uPulseAge, 2.2);

    vHeat = heat;
    vChill = uChill;
    vBrightness = 0.55 + 0.45 * sin(phase * 3.0 + uFlowTime * 0.5);

    vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
    // Condensing multiplies per-pixel density enormously; particles must
    // shrink into formation or the additive eye blows out to white.
    float size = uSize * (0.7 + heat * 0.9) * mix(1.0, 0.62 + heat * 0.25, uCoherence);
    gl_PointSize = size * (34.0 / -mvPosition.z);
    gl_Position = projectionMatrix * mvPosition;
  }
`;

const FRAGMENT_SHADER = /* glsl */ `
  uniform float uBrightness;
  uniform float uCoolBias;
  uniform float uCoherence;
  uniform vec3 uPulseColor;

  varying float vHeat;
  varying float vBrightness;
  varying float vPulse;
  varying float vChill;

  void main() {
    vec2 p = gl_PointCoord - 0.5;
    float d = length(p);
    if (d > 0.5) discard;
    float falloff = smoothstep(0.5, 0.06, d);

    vec3 ember = vec3(1.0, 0.16, 0.05);
    vec3 hot = vec3(1.0, 0.78, 0.55);
    vec3 cool = vec3(0.62, 0.2, 0.2);
    vec3 frozen = vec3(0.45, 0.42, 0.45);

    // Adrift, the field stays ember-red; heat only runs hot as the eye
    // coheres, and quadratically — white belongs to the core alone.
    vec3 color = mix(ember, hot, vHeat * vHeat * (0.3 + uCoherence * 0.55));
    color = mix(color, cool, uCoolBias * 0.55);
    color = mix(color, frozen, vChill * 0.75);
    color += uPulseColor * vPulse * 1.6;

    // Energy conservation for the condensation eye: the same light shared
    // by far denser coverage, so per-sprite alpha drops with coherence.
    float alpha = falloff * vBrightness * uBrightness * mix(1.0, 0.34, uCoherence);
    gl_FragColor = vec4(color * alpha, alpha);
  }
`;

class ChorusOptic {
  private readonly camera = new THREE.PerspectiveCamera(34, 1, 0.1, 80);
  private readonly timer = new THREE.Timer();
  private readonly composer: EffectComposer;
  private readonly bloomPass: UnrealBloomPass;
  private readonly container: HTMLElement;
  private readonly eye: HTMLElement;
  private readonly geometry = new THREE.BufferGeometry();
  private readonly material: THREE.ShaderMaterial;
  private readonly reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly targetPointer = new THREE.Vector2();

  private audioEnergy = 0;
  private destroyed = false;
  private domPollTimer = 0;
  private flowSpeedSeed = 1;
  private flowTime = 0;
  private frameId = 0;
  private missionCount = 0;
  private lagCool = 0;
  private pointer = new THREE.Vector2();
  private populationScale = 1;
  private pulseAge = 99;
  private resizeObserver: ResizeObserver;
  private state: HalVisualState = "idle";
  private stateObserver: MutationObserver;
  private targetAudioEnergy = 0;
  private tier = MAX_PARTICLES;
  private toolKind: HalToolKind = null;

  constructor(container: HTMLElement, eye: HTMLElement) {
    this.container = container;
    this.eye = eye;
    this.renderer = new THREE.WebGLRenderer({
      alpha: false,
      antialias: false,
      powerPreference: "high-performance"
    });
    this.renderer.domElement.className = "optic-webgl-canvas";
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.92;
    this.renderer.setClearColor(0x010101, 1);
    this.container.appendChild(this.renderer.domElement);
    this.scene.fog = null;
    this.timer.connect(document);

    this.buildAttributes();

    this.material = new THREE.ShaderMaterial({
      uniforms: {
        uFlowTime: { value: 0 },
        uRealTime: { value: 0 },
        uTurbulence: { value: 0 },
        uAttend: { value: 0 },
        uVortex: { value: 0 },
        uChill: { value: 0 },
        uCoherence: { value: 0 },
        uShimmer: { value: 0 },
        uPulseAge: { value: 99 },
        uPulseColor: { value: new THREE.Color(0xff2d20) },
        uMissions: { value: 0 },
        uSpreadScale: { value: 1 },
        uSize: { value: 1.9 },
        uBrightness: { value: 0.5 },
        uCoolBias: { value: 0 },
        uEyeCenter: { value: EYE_CENTER.clone() }
      },
      vertexShader: VERTEX_SHADER,
      fragmentShader: FRAGMENT_SHADER,
      transparent: true,
      depthWrite: false,
      depthTest: false,
      blending: THREE.AdditiveBlending
    });

    const points = new THREE.Points(this.geometry, this.material);
    points.frustumCulled = false;
    this.scene.add(points);

    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloomPass = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.3, 0.7, 0.55);
    this.composer.addPass(this.bloomPass);
    this.composer.addPass(new OutputPass());

    this.seedFromHistory();
    this.pollDom();
    this.domPollTimer = window.setInterval(() => this.pollDom(), 5000);

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
    if (kind) {
      // One real event, one front. Re-arming only on a fresh tool call
      // keeps the pulse honest.
      this.pulseAge = 0;
      (this.material.uniforms.uPulseColor!.value as THREE.Color).copy(TOOL_COLORS[kind]);
    }
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    cancelAnimationFrame(this.frameId);
    window.clearInterval(this.domPollTimer);
    this.resizeObserver.disconnect();
    this.stateObserver.disconnect();
    window.removeEventListener("pointermove", this.onPointerMove);
    window.removeEventListener("blur", this.onPointerLeave);
    this.reducedMotionQuery.removeEventListener("change", this.onMotionPreferenceChange);
    this.timer.dispose();
    this.geometry.dispose();
    this.material.dispose();
    this.composer.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
    this.container.closest(".eye-module")?.classList.remove("webgl-ready");
  }

  private buildAttributes(): void {
    const positions = new Float32Array(MAX_PARTICLES * 3);
    const seeds = new Float32Array(MAX_PARTICLES * 3);
    const formations = new Float32Array(MAX_PARTICLES * 3);
    const heats = new Float32Array(MAX_PARTICLES);
    const groups = new Float32Array(MAX_PARTICLES);

    for (let index = 0; index < MAX_PARTICLES; index += 1) {
      seeds[index * 3] = Math.random();
      seeds[index * 3 + 1] = Math.random();
      seeds[index * 3 + 2] = Math.random();

      // Iris seats: sqrt-distributed rings; ~8% collapse to the hot core.
      const core = Math.random() < 0.08;
      const radius = core ? Math.random() * 0.22 : 0.45 + Math.sqrt(Math.random()) * 1.35;
      const angle = Math.random() * Math.PI * 2;
      formations[index * 3] = Math.cos(angle) * radius;
      formations[index * 3 + 1] = Math.sin(angle) * radius;
      formations[index * 3 + 2] = (Math.random() - 0.5) * 0.14;
      heats[index] = core ? 1 : Math.max(0, 0.65 - radius * 0.3) + Math.random() * 0.15;

      // The head of the buffer is reserved for mission satellites, so any
      // draw range includes them; inactive groups fold into the flock in
      // the shader.
      groups[index] =
        index < SATELLITE_SLOTS * SATELLITE_SIZE ? Math.floor(index / SATELLITE_SIZE) + 1 : 0;
    }

    this.geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    this.geometry.setAttribute("seed", new THREE.BufferAttribute(seeds, 3));
    this.geometry.setAttribute("formation", new THREE.BufferAttribute(formations, 3));
    this.geometry.setAttribute("heat", new THREE.BufferAttribute(heats, 1));
    this.geometry.setAttribute("group", new THREE.BufferAttribute(groups, 1));
  }

  // Population and tempo are seeded once from what actually happened
  // before this boot: a worked session is denser and calmer than a
  // fresh one.
  private seedFromHistory(): void {
    const entries = document.querySelectorAll("#mlog-entries > *").length;
    this.populationScale = 0.7 + Math.min(entries, 80) / 80 * 0.3;
    this.flowSpeedSeed = 1.15 - Math.min(entries, 80) / 80 * 0.3;
  }

  private pollDom(): void {
    try {
      this.missionCount = Math.min(
        document.querySelectorAll("#mission-cards .mission-card").length,
        SATELLITE_SLOTS
      );
      const lagText = document.getElementById("telem-lag")?.textContent ?? "";
      const lagMs = parseInt(lagText, 10);
      this.lagCool = Number.isFinite(lagMs) ? THREE.MathUtils.clamp((lagMs - 4000) / 12000, 0, 1) : 0;
    } catch {
      this.missionCount = 0;
      this.lagCool = 0;
    }
  }

  private resize(): void {
    const { width, height } = this.container.getBoundingClientRect();
    if (width < 2 || height < 2) return;
    this.renderer.setPixelRatio(
      Math.min(window.devicePixelRatio || 1, width >= 1101 ? 1.75 : width >= 761 ? 1.5 : 1.25)
    );
    this.tier = width >= 1101 ? 40000 : width >= 761 ? 24000 : 10000;
    const satelliteReserve = SATELLITE_SLOTS * SATELLITE_SIZE;
    const mainCount = Math.floor(
      Math.min(this.tier, MAX_PARTICLES - satelliteReserve) * this.populationScale
    );
    this.geometry.setDrawRange(0, satelliteReserve + mainCount);

    this.camera.aspect = width / height;
    const portrait = this.camera.aspect < 1.05;
    this.material.uniforms.uSpreadScale!.value = portrait ? 0.55 : 1;
    const fitHeight = portrait ? 7.6 : 8.2;
    const fitWidth = portrait ? 6.4 : 12.4;
    const halfFov = THREE.MathUtils.degToRad(this.camera.fov / 2);
    const byHeight = fitHeight / 2 / Math.tan(halfFov);
    const byWidth = fitWidth / 2 / (Math.tan(halfFov) * this.camera.aspect);
    this.camera.position.set(0, 0, Math.max(byHeight, byWidth));
    this.camera.lookAt(0, 0, 0);
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
    const reduced = this.reducedMotionQuery.matches;

    this.audioEnergy = THREE.MathUtils.damp(this.audioEnergy, this.targetAudioEnergy, 8, delta);
    this.targetAudioEnergy = THREE.MathUtils.damp(this.targetAudioEnergy, 0, 2.6, delta);
    this.pointer.x = THREE.MathUtils.damp(this.pointer.x, this.targetPointer.x, 5, delta);
    this.pointer.y = THREE.MathUtils.damp(this.pointer.y, this.targetPointer.y, 5, delta);

    // The flow clock is the only stateful thing, and states own its speed:
    // denied freezes it; reduced motion stops it entirely.
    if (!reduced) this.flowTime += delta * profile.flow * this.flowSpeedSeed;
    this.pulseAge += delta;

    const u = this.material.uniforms;
    u.uFlowTime!.value = this.flowTime;
    u.uRealTime!.value = reduced ? 0 : elapsed;
    u.uTurbulence!.value = THREE.MathUtils.damp(
      u.uTurbulence!.value as number,
      reduced ? 0 : this.audioEnergy,
      7,
      delta
    );
    // Barge-in must shatter the eye fast; forming is slower than shedding.
    const coherenceRate = profile.coherence > (u.uCoherence!.value as number) ? 2.6 : 9;
    u.uCoherence!.value = THREE.MathUtils.damp(
      u.uCoherence!.value as number,
      profile.coherence,
      reduced ? 20 : coherenceRate,
      delta
    );
    u.uAttend!.value = THREE.MathUtils.damp(u.uAttend!.value as number, profile.attend, 4, delta);
    u.uVortex!.value = THREE.MathUtils.damp(u.uVortex!.value as number, profile.vortex, 4, delta);
    u.uChill!.value = THREE.MathUtils.damp(
      u.uChill!.value as number,
      profile.chill,
      profile.chill > 0 ? 10 : 4,
      delta
    );
    u.uShimmer!.value = this.state === "speaking" && !reduced ? this.audioEnergy : 0;
    u.uPulseAge!.value = reduced ? (this.pulseAge < 0.6 ? 0.4 : 99) : this.pulseAge;
    u.uMissions!.value = this.missionCount;
    u.uBrightness!.value = THREE.MathUtils.damp(
      u.uBrightness!.value as number,
      profile.brightness + this.audioEnergy * 0.4,
      5,
      delta
    );
    u.uCoolBias!.value = THREE.MathUtils.damp(u.uCoolBias!.value as number, this.lagCool, 2, delta);

    // Pointer parallax on the camera, not the field — you look around the
    // weather; you don't stir it.
    this.camera.position.x = THREE.MathUtils.damp(this.camera.position.x, this.pointer.x * 0.5, 4, delta);
    this.camera.position.y = THREE.MathUtils.damp(this.camera.position.y, this.pointer.y * 0.32, 4, delta);
    this.camera.lookAt(0, 0, 0);

    this.bloomPass.strength = THREE.MathUtils.damp(
      this.bloomPass.strength,
      profile.bloom + this.audioEnergy * 0.3,
      6,
      delta
    );

    this.composer.render();
  };
}

export function createOptic(container: HTMLElement, eye: HTMLElement): HalOpticApi {
  const optic = new ChorusOptic(container, eye);
  return {
    destroy: () => optic.destroy(),
    setAudioEnergy: (energy) => optic.setAudioEnergy(energy),
    setState: (state) => optic.setState(state),
    setToolKind: (kind) => optic.setToolKind(kind)
  };
}

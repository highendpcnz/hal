// Direction 05 "Logic Memory Center" — you are inside the machine.
//
// Design: docs/plans/2026-07-26-logic-memory-center-design.md
//
// The chamber's walls are racked arrays of translucent memory blocks, one
// seat per stored exchange. Unlike 04's stateless particle field, the blocks
// are discrete and individually addressable — sequential access and the
// extraction are per-block choreography, which a pure position function
// cannot express. So: one InstancedBufferGeometry draw, static seats, and
// three dynamic per-instance attributes written from CPU-side arrays.
//
// Palette departure (deliberate, see the design doc): blocks are cold
// (#cfe6ea), the room is HAL red. Red never lights a block; cyan never
// lights the room. When they meet, it reads as a fault.

import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

import type { HalOpticApi, HalToolKind, HalVisualState } from "./optic-api";

interface Tier {
  readonly depth: number;
  readonly rows: number;
  readonly dpr: number;
}

// Three surfaces (left wall, right wall, ceiling) × depth × rows.
const TIERS: Record<"high" | "mid" | "low", Tier> = {
  high: { depth: 20, rows: 7, dpr: 1.75 },
  mid: { depth: 16, rows: 6, dpr: 1.5 },
  low: { depth: 10, rows: 4, dpr: 1.25 }
};

const SURFACES = 3;
const MEMORY = new THREE.Color(0xcfe6ea);
const MEMORY_DIM = new THREE.Color(0x3f4d52);
const SIGNAL = new THREE.Color(0xff2d1f);

const TOOL_COLORS: Record<Exclude<HalToolKind, null>, THREE.Color> = {
  fetch: new THREE.Color(0x7fd4e0),
  execute: new THREE.Color(0xffb27a),
  search: new THREE.Color(0xbfa6ff),
  read: new THREE.Color(0xa8e6b0)
};

// How much history fills the chamber. Beyond this the racks are simply full;
// the point is "a worked session is dense", not a precise gauge.
const HISTORY_FULL = 60;

const VERTEX_SHADER = /* glsl */ `
  attribute vec3 aSeat;
  attribute vec3 aSize;
  attribute vec3 aOut;      // outward normal of the surface this block sits on
  attribute float aSeed;
  attribute float aActivation;
  attribute float aFlare;
  attribute float aExtract;

  uniform float uTime;
  uniform float uDrift;
  uniform float uAttend;    // listening: racks lean in
  uniform vec2  uParallax;

  varying float vActivation;
  varying float vFlare;
  varying float vExtract;
  varying float vSeed;
  varying vec3  vLocal;

  void main() {
    vActivation = aActivation;
    vFlare = aFlare;
    vExtract = aExtract;
    vSeed = aSeed;
    vLocal = position;

    vec3 local = position * aSize;
    vec3 world = aSeat + local;

    // Breathing, out of phase per block — the racks are never quite still.
    world += aOut * sin(uTime * 0.6 + aSeed * 6.283) * 0.006;

    // Listening leans the racks toward the viewer.
    world -= aOut * uAttend * 0.09;

    // Extraction: unseat outward, then run past the camera. Two phases so
    // the block visibly leaves its socket before it travels.
    float unseat = smoothstep(0.0, 0.22, aExtract);
    float travel = smoothstep(0.18, 1.0, aExtract);
    world += aOut * unseat * 0.28;
    world.z += travel * 9.0;
    world.x += travel * aOut.x * 0.5;

    // The chamber drifts forward slowly; parallax is held, not orbited —
    // you cannot leave.
    world.z += uDrift;
    world.x += uParallax.x * 0.12;
    world.y += uParallax.y * 0.08;

    gl_Position = projectionMatrix * modelViewMatrix * vec4(world, 1.0);
  }
`;

const FRAGMENT_SHADER = /* glsl */ `
  precision highp float;

  uniform vec3  uMemory;
  uniform vec3  uMemoryDim;
  uniform vec3  uFlareColor;
  uniform float uChill;     // denied: drain toward cold grey
  uniform float uVoice;     // speaking/mic energy
  uniform float uTime;

  varying float vActivation;
  varying float vFlare;
  varying float vExtract;
  varying float vSeed;
  varying vec3  vLocal;

  void main() {
    // Edge-lit slab: bright at the rim, translucent through the middle.
    float edge = max(max(abs(vLocal.x), abs(vLocal.y)), abs(vLocal.z)) * 2.0;
    float rim = smoothstep(0.72, 1.0, edge);

    float shimmer = 1.0 + uVoice * 0.35 * sin(uTime * 9.0 + vSeed * 12.0);
    vec3 lit = mix(uMemoryDim, uMemory, vActivation) * shimmer;
    lit = mix(lit, uFlareColor, vFlare * 0.85);

    // Denial drains the colour out rather than reddening it: the blocks are
    // the one place HAL's red is not allowed to reach.
    lit = mix(lit, vec3(dot(lit, vec3(0.3, 0.59, 0.11))) * 0.55, uChill);

    // Empty sockets stay visible: the racks physically exist whether or not
    // they hold memory, and the chamber has to read as architecture even in
    // a brand-new session. Occupancy is what lights them, not what draws them.
    float body = 0.30 + 0.70 * rim;
    float alpha = (0.26 + 0.55 * vActivation) * body * (1.0 - vExtract * 0.9);
    if (alpha < 0.004) discard;

    gl_FragColor = vec4(lit * (0.42 + 0.58 * vActivation + vFlare * 0.9), alpha);
  }
`;

class LmcOptic {
  private readonly camera = new THREE.PerspectiveCamera(58, 1, 0.1, 60);
  private readonly timer = new THREE.Timer();
  private readonly composer: EffectComposer;
  private readonly bloomPass: UnrealBloomPass;
  private readonly container: HTMLElement;
  private readonly eye: HTMLElement;
  private readonly geometry: THREE.InstancedBufferGeometry;
  private readonly material: THREE.ShaderMaterial;
  private readonly reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly targetPointer = new THREE.Vector2();

  private activation!: Float32Array;
  private activationAttr!: THREE.InstancedBufferAttribute;
  private flare!: Float32Array;
  private flareAttr!: THREE.InstancedBufferAttribute;
  private extract!: Float32Array;
  private extractAttr!: THREE.InstancedBufferAttribute;
  private column!: Float32Array; // depth index per seat, for column flares

  private audioEnergy = 0;
  private destroyed = false;
  private domPollTimer = 0;
  private drift = 0;
  private extracting = false;
  private frameId = 0;
  private lagCool = 0;
  private missionCount = 0;
  private occupancy = 0; // 0..1 of seats holding a memory
  private pointer = new THREE.Vector2();
  private resizeObserver: ResizeObserver;
  private scanHead = 0;
  private seatCount = 0;
  private state: HalVisualState = "idle";
  private stateObserver: MutationObserver;
  private targetAudioEnergy = 0;
  private tier: Tier = TIERS.high;
  private toolColumn = -1;

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
    this.renderer.toneMappingExposure = 0.95;
    this.renderer.setClearColor(0x050506, 1);
    this.container.appendChild(this.renderer.domElement);
    this.timer.connect(document);

    this.tier = this.pickTier();
    this.geometry = this.buildRacks(this.tier);

    this.material = new THREE.ShaderMaterial({
      uniforms: {
        uTime: { value: 0 },
        uDrift: { value: 0 },
        uAttend: { value: 0 },
        uParallax: { value: new THREE.Vector2() },
        uMemory: { value: MEMORY.clone() },
        uMemoryDim: { value: MEMORY_DIM.clone() },
        uFlareColor: { value: MEMORY.clone() },
        uChill: { value: 0 },
        uVoice: { value: 0 }
      },
      vertexShader: VERTEX_SHADER,
      fragmentShader: FRAGMENT_SHADER,
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
      // Normal, not additive. These are translucent slabs, not emissive
      // points: additive accumulates every overlapping block along the
      // corridor and the near racks blow out to white.
      blending: THREE.NormalBlending
    });

    const racks = new THREE.Mesh(this.geometry, this.material);
    racks.frustumCulled = false;
    this.scene.add(racks);

    // The room itself is red — a wash behind the cold racks, never on them.
    const wash = new THREE.PointLight(SIGNAL.getHex(), 0);
    this.scene.add(wash);
    // Inside the corridor: the nearest rings pass outside the frustum and
    // become the enclosing walls, while the fill still starts in view.
    this.camera.position.set(0, 0, 2.6);

    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    // High threshold: only lit block faces bloom, never the whole corridor.
    this.bloomPass = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.32, 0.7, 0.82);
    this.composer.addPass(this.bloomPass);
    this.composer.addPass(new OutputPass());

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
    if (kind) {
      // One real tool call, one column. Picking the column from the scan
      // head means the flare lands where HAL was already reading.
      this.toolColumn = Math.floor(this.scanHead) % Math.max(1, this.tier.depth);
      (this.material.uniforms.uFlareColor!.value as THREE.Color).copy(TOOL_COLORS[kind]);
    } else {
      this.toolColumn = -1;
    }
  }

  /**
   * The extraction. Blocks unseat newest-first and run past the camera while
   * the chamber empties. Resolves when the sequence is done (or immediately
   * under reduced motion) so resetSession can reload behind it.
   */
  playSessionEnd(): Promise<void> {
    if (this.destroyed || this.extracting) return Promise.resolve();
    this.extracting = true;

    if (this.reducedMotionQuery.matches) {
      // No travel: the racks simply go out.
      this.activation.fill(0);
      this.activationAttr.needsUpdate = true;
      return Promise.resolve();
    }

    const DURATION = 2500;
    const started = performance.now();
    return new Promise<void>((resolve) => {
      let settled = false;
      const finish = (): void => {
        if (settled) return;
        settled = true;
        window.removeEventListener("keydown", skip);
        window.removeEventListener("pointerdown", skip);
        resolve();
      };
      // Skippable: this is a transition, not a cutscene to sit through.
      const skip = (): void => finish();
      window.addEventListener("keydown", skip, { once: true });
      window.addEventListener("pointerdown", skip, { once: true });

      const step = (): void => {
        if (this.destroyed) return finish();
        const t = (performance.now() - started) / DURATION;
        if (t >= 1) return finish();
        // Newest memory goes first: walk the wave from the last occupied
        // seat backwards, the order the film plays it in.
        const occupied = Math.floor(this.seatCount * this.occupancy);
        const head = (1 - t) * occupied;
        for (let i = 0; i < this.seatCount; i += 1) {
          if (i >= occupied) continue;
          const lead = THREE.MathUtils.clamp((head - i) / -6 + 1, 0, 1);
          this.extract[i] = Math.max(this.extract[i]!, lead);
        }
        this.extractAttr.needsUpdate = true;
        requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });
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

  private pickTier(): Tier {
    const width = window.innerWidth;
    if (width < 900) return TIERS.low;
    if (width < 1500) return TIERS.mid;
    return TIERS.high;
  }

  /** Seats are chronological: index 0 is the oldest memory, so the
   *  extraction can simply walk backwards from the newest. */
  private buildRacks(tier: Tier): THREE.InstancedBufferGeometry {
    const count = SURFACES * tier.depth * tier.rows;
    this.seatCount = count;

    const box = new THREE.BoxGeometry(1, 1, 1);
    const geometry = new THREE.InstancedBufferGeometry();
    geometry.index = box.index;
    geometry.attributes.position = box.attributes.position!;
    geometry.instanceCount = count;

    const seat = new Float32Array(count * 3);
    const size = new Float32Array(count * 3);
    const outward = new Float32Array(count * 3);
    const seed = new Float32Array(count);
    this.activation = new Float32Array(count);
    this.flare = new Float32Array(count);
    this.extract = new Float32Array(count);
    this.column = new Float32Array(count);

    // Wide and tall enough that the walls leave the frustum: the frame must
    // be filled by chamber, not contain a box. The racks also start *behind*
    // the camera so the corridor encloses rather than recedes.
    const HALF_W = 2.35;
    const CEIL_H = 1.62;
    // Depth-major, so consecutive memories fill a *ring* of the chamber
    // across all three surfaces before moving further back. Surface-major
    // ordering made a sparse session light one wall end-to-end and leave the
    // rest black — occupancy read as a progress bar instead of a population.
    let i = 0;
    for (let d = 0; d < tier.depth; d += 1) {
      for (let surface = 0; surface < SURFACES; surface += 1) {
        for (let r = 0; r < tier.rows; r += 1) {
          // d=0 is the FAR end: the oldest memory sits deepest, and the
          // chamber lights toward you as the thread grows. Newest-first
          // extraction then starts at the blocks nearest the camera.
          const z = 1.9 - (tier.depth - 1 - d) * 0.72;
          const span = (r / Math.max(1, tier.rows - 1) - 0.5) * 2;
          if (surface === 0 || surface === 1) {
            const side = surface === 0 ? -1 : 1;
            seat[i * 3] = side * HALF_W;
            seat[i * 3 + 1] = span * 0.92;
            seat[i * 3 + 2] = z;
            size[i * 3] = 0.07;
            size[i * 3 + 1] = 0.2;
            size[i * 3 + 2] = 0.42;
            outward[i * 3] = side;
          } else {
            seat[i * 3] = span * 1.2;
            seat[i * 3 + 1] = CEIL_H;
            seat[i * 3 + 2] = z;
            size[i * 3] = 0.24;
            size[i * 3 + 1] = 0.07;
            size[i * 3 + 2] = 0.42;
            outward[i * 3 + 1] = 1;
          }
          seed[i] = Math.random();
          this.column[i] = d;
          i += 1;
        }
      }
    }

    const inst = (array: Float32Array, itemSize: number): THREE.InstancedBufferAttribute =>
      new THREE.InstancedBufferAttribute(array, itemSize);

    geometry.setAttribute("aSeat", inst(seat, 3));
    geometry.setAttribute("aSize", inst(size, 3));
    geometry.setAttribute("aOut", inst(outward, 3));
    geometry.setAttribute("aSeed", inst(seed, 1));
    this.activationAttr = inst(this.activation, 1);
    this.flareAttr = inst(this.flare, 1);
    this.extractAttr = inst(this.extract, 1);
    geometry.setAttribute("aActivation", this.activationAttr);
    geometry.setAttribute("aFlare", this.flareAttr);
    geometry.setAttribute("aExtract", this.extractAttr);
    box.dispose();
    return geometry;
  }

  private pollDom(): void {
    try {
      const entries = document.querySelectorAll("#mlog-entries > *").length;
      this.occupancy = Math.min(entries, HISTORY_FULL) / HISTORY_FULL;
      this.missionCount = document.querySelectorAll("#mission-cards .mission-card").length;
      const lagText = document.getElementById("telem-lag")?.textContent ?? "";
      const lagMs = parseInt(lagText, 10);
      this.lagCool = Number.isFinite(lagMs)
        ? THREE.MathUtils.clamp((lagMs - 4000) / 12000, 0, 1)
        : 0;
    } catch {
      this.occupancy = 0;
      this.missionCount = 0;
      this.lagCool = 0;
    }
  }

  private resize(): void {
    const { width, height } = this.container.getBoundingClientRect();
    if (width < 2 || height < 2) return;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, this.tier.dpr));
    this.renderer.setSize(width, height, false);
    this.composer.setSize(width, height);
    this.bloomPass.setSize(width, height);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  private syncStateFromDom(): void {
    const classes = this.eye.classList;
    const state: HalVisualState = classes.contains("denied")
      ? "denied"
      : classes.contains("speaking")
        ? "speaking"
        : classes.contains("thinking")
          ? "thinking"
          : classes.contains("listening")
            ? "listening"
            : "idle";
    if (state !== this.state) this.setState(state);
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
    if (this.reducedMotionQuery.matches) this.targetPointer.set(0, 0);
  };

  private animate = (): void => {
    if (this.destroyed) return;
    this.frameId = requestAnimationFrame(this.animate);
    this.timer.update();
    const delta = Math.min(this.timer.getDelta(), 0.1);
    const time = this.timer.getElapsed();
    const reduced = this.reducedMotionQuery.matches;
    const uniforms = this.material.uniforms;

    this.audioEnergy += (this.targetAudioEnergy - this.audioEnergy) * Math.min(1, delta * 9);
    this.pointer.lerp(this.targetPointer, Math.min(1, delta * 3));

    // Slow forward drift; the chamber never arrives anywhere.
    if (!reduced && !this.extracting) this.drift = (this.drift + delta * 0.06) % 0.62;

    // Sequential access: the scan only advances while HAL is thinking, and
    // laggy turns read more slowly. Nothing scans in an idle session.
    const scanning = this.state === "thinking";
    if (scanning) this.scanHead += delta * (10 - this.lagCool * 6);

    const occupied = this.seatCount * this.occupancy;
    for (let i = 0; i < this.seatCount; i += 1) {
      // Occupancy eases rather than snapping — a new exchange lights its
      // block instead of popping it into existence.
      const target = i < occupied ? 1 : 0;
      const current = this.activation[i]!;
      this.activation[i] = current + (target - current) * Math.min(1, delta * 2.4);

      let flare = this.flare[i]! * (1 - Math.min(1, delta * 3.5));
      if (scanning && !reduced) {
        const distance = Math.abs(((this.scanHead - i) % this.seatCount) / 4);
        if (distance < 1) flare = Math.max(flare, 1 - distance);
      }
      if (this.toolColumn >= 0 && this.column[i] === this.toolColumn) {
        flare = Math.max(flare, 0.75);
      }
      // Missions live off-rack: the last N seats pulse out of phase.
      if (this.missionCount > 0 && i >= this.seatCount - this.missionCount) {
        flare = Math.max(flare, 0.35 + 0.25 * Math.sin(time * 2.2 + i));
      }
      this.flare[i] = flare;
    }
    this.activationAttr.needsUpdate = true;
    this.flareAttr.needsUpdate = true;

    const attend = this.state === "listening" ? 1 : 0;
    const chill = this.state === "denied" ? 1 : 0;
    const voice = this.state === "speaking" || this.state === "listening" ? this.audioEnergy : 0;

    uniforms.uTime!.value = time;
    uniforms.uDrift!.value = this.drift;
    uniforms.uAttend!.value += (attend - uniforms.uAttend!.value) * Math.min(1, delta * 4);
    uniforms.uChill!.value += (chill - uniforms.uChill!.value) * Math.min(1, delta * 5);
    uniforms.uVoice!.value += (voice - uniforms.uVoice!.value) * Math.min(1, delta * 8);
    (uniforms.uParallax!.value as THREE.Vector2).copy(this.pointer);

    // The room's red answers the voice, but stays a low warm floor — push it
    // any further and it washes the whole frame, which kills the one idea
    // this direction is built on: cold memory inside a hot mind.
    this.bloomPass.strength = 0.32 + voice * 0.28 + (this.extracting ? -0.15 : 0);
    this.renderer.setClearColor(
      new THREE.Color(0x050506).lerp(new THREE.Color(0x2a0906), 0.07 + voice * 0.16),
      1
    );

    this.composer.render();
  };
}

export function createOptic(container: HTMLElement, eye: HTMLElement): HalOpticApi {
  const optic = new LmcOptic(container, eye);
  return {
    destroy: () => optic.destroy(),
    setAudioEnergy: (energy) => optic.setAudioEnergy(energy),
    setState: (state) => optic.setState(state),
    setToolKind: (kind) => optic.setToolKind(kind),
    playSessionEnd: () => optic.playSessionEnd()
  };
}

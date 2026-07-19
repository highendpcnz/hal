// Direction 03 "Signal Vault" — a film still. The optic sits recessed in a
// beveled vault frame on the right of a black angular corridor, raking a
// red beam across the frame toward the viewer; a glossy floor carries its
// reflection. The container is a full-bleed band behind the chrome
// (pointer-events: none) — the hero transcript type and console band are
// DOM, not scene. Parallax listens on window; perf tiers pick pixel ratio
// and composition framing from the container size.

import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

import type { HalOpticApi, HalToolKind, HalVisualState } from "./optic-api";

interface StateProfile {
  readonly bloom: number;
  readonly core: number;
  readonly beam: number;
  readonly motion: number;
}

const STATE_PROFILES: Record<HalVisualState, StateProfile> = {
  idle: { bloom: 0.34, core: 1.6, beam: 0.5, motion: 0.3 },
  listening: { bloom: 0.72, core: 4, beam: 1, motion: 0.6 },
  thinking: { bloom: 0.28, core: 1.1, beam: 0.72, motion: 1.2 },
  speaking: { bloom: 0.78, core: 4.6, beam: 0.9, motion: 0.5 },
  denied: { bloom: 0.1, core: 0.4, beam: 0.06, motion: 0.06 }
};

const TOOL_COLORS: Record<Exclude<HalToolKind, null>, number> = {
  fetch: 0xff3b24,
  execute: 0xff541f,
  search: 0xe9271c,
  read: 0xff2d20
};

// Where the optic assembly lives in world units.
const OPTIC_X = 2.6;
const OPTIC_Y = 0.25;

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
    float radialGrid = line(radius * 10.5 - uTime * 0.05, 0.022);
    float spokes = line((angle / 6.2831853 + 0.5) * 28.0, 0.016);
    float pulse = 0.5 + 0.5 * sin(11.0 * radius - uTime * (1.2 + uState));
    float center = pow(max(0.0, 1.0 - radius), 2.0);
    float rim = smoothstep(1.0, 0.5, radius);
    float energy = clamp(uEnergy, 0.0, 1.0);
    float tool = uToolPulse * smoothstep(0.7, 0.2, abs(radius - 0.52));

    vec3 deepRed = vec3(0.06, 0.0, 0.0);
    vec3 signalRed = vec3(1.0, 0.06, 0.02);
    vec3 hotCore = vec3(1.0, 0.8, 0.62);
    vec3 color = mix(deepRed, signalRed, center * (0.85 + 0.15 * pulse));
    color += signalRed * (radialGrid * 0.14 + spokes * 0.1) * rim;
    color += signalRed * energy * pulse * 0.3;
    color += vec3(1.0, 0.2, 0.05) * tool * 0.42;
    color = mix(color, hotCore, pow(max(0.0, 1.0 - radius * 4.2), 5.0));

    float alpha = smoothstep(1.0, 0.9, radius) * (0.92 + center * 0.08);
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
    float glow = pow(max(0.0, 1.0 - radius), 3.0);
    vec3 color = mix(vec3(1.0, 0.08, 0.015), vec3(1.0, 0.92, 0.8), glow);
    gl_FragColor = vec4(color * (0.72 + uIntensity * 0.15), glow * 0.94);
  }
`;

class VaultOptic {
  private readonly camera = new THREE.PerspectiveCamera(33, 1, 0.1, 80);
  private readonly timer = new THREE.Timer();
  private readonly composer: EffectComposer;
  private readonly bloomPass: UnrealBloomPass;
  private readonly beamGroup = new THREE.Group();
  private readonly beamMaterials: THREE.MeshBasicMaterial[] = [];
  private readonly coreGlow: THREE.ShaderMaterial;
  private readonly coreLight = new THREE.PointLight(0xff2214, 9, 16, 2);
  private readonly coreMaterial = new THREE.MeshStandardMaterial({
    color: 0xffb09a,
    emissive: 0xff2214,
    emissiveIntensity: 2.4,
    metalness: 0,
    roughness: 0.16
  });
  private readonly container: HTMLElement;
  private readonly eye: HTMLElement;
  private readonly floorGlowMaterial: THREE.MeshBasicMaterial;
  private readonly irisMaterial: THREE.ShaderMaterial;
  private readonly lensMaterial = new THREE.MeshPhysicalMaterial({
    color: 0x8c0802,
    emissive: 0x6a0300,
    emissiveIntensity: 0.22,
    metalness: 0,
    roughness: 0.08,
    transmission: 0.3,
    thickness: 1.4,
    ior: 1.52,
    clearcoat: 1,
    clearcoatRoughness: 0.07,
    transparent: true,
    opacity: 0.82,
    depthWrite: false
  });
  private readonly reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  private readonly renderer: THREE.WebGLRenderer;
  private readonly root = new THREE.Group();
  private readonly scene = new THREE.Scene();
  private readonly targetPointer = new THREE.Vector2();

  private audioEnergy = 0;
  private beamLevel = 0.5;
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
    this.renderer.toneMappingExposure = 0.9;
    this.renderer.setClearColor(0x010101, 1);
    this.container.appendChild(this.renderer.domElement);

    this.scene.fog = new THREE.FogExp2(0x010101, 0.028);
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
    this.floorGlowMaterial = new THREE.MeshBasicMaterial({
      color: 0xff1d10,
      transparent: true,
      opacity: 0.16,
      depthWrite: false,
      blending: THREE.AdditiveBlending
    });

    this.buildScene();

    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloomPass = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.5, 0.62, 0.5);
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
    // Near-darkness: the eye is the light source. A faint cool top light
    // draws the panel edges out of the black.
    const ambient = new THREE.AmbientLight(0x0d080a, 0.9);
    const edgeLight = new THREE.DirectionalLight(0x323a44, 2.2);
    edgeLight.position.set(-2, 8, 5);
    const kicker = new THREE.DirectionalLight(0x552018, 0.6);
    kicker.position.set(6, -3, 6);
    // A soft red spill in front of the vault sells the eye lighting the
    // corridor. Decay 1 (not physical 2) so the wash actually reaches the
    // receding slabs instead of dying within a couple of units.
    const spill = new THREE.PointLight(0xff1d10, 3.2, 22, 1);
    spill.position.set(1.8, 0.6, 2.8);
    this.scene.add(ambient, edgeLight, kicker, spill);
    this.coreLight.position.set(OPTIC_X, OPTIC_Y, 1.6);
    this.root.add(this.coreLight);

    const panel = new THREE.MeshStandardMaterial({
      color: 0x15171a,
      metalness: 0.5,
      roughness: 0.32
    });
    const panelDark = new THREE.MeshStandardMaterial({
      color: 0x0a0b0c,
      metalness: 0.5,
      roughness: 0.5
    });
    const frameMetal = new THREE.MeshStandardMaterial({
      color: 0x16181b,
      emissive: 0x0d0f12,
      emissiveIntensity: 1,
      metalness: 0.85,
      roughness: 0.28
    });
    // Barely-luminous cool strips: the concept's panel-edge catch-lights.
    const edgeGlow = new THREE.MeshStandardMaterial({
      color: 0x22262c,
      emissive: 0x181c22,
      emissiveIntensity: 1,
      metalness: 0.6,
      roughness: 0.4
    });
    const redTrim = new THREE.MeshStandardMaterial({
      color: 0x1c0301,
      emissive: 0xb00c04,
      emissiveIntensity: 0.8,
      metalness: 0.7,
      roughness: 0.3
    });

    // The corridor: vertical slabs marching from front-left toward the
    // vault, each yawed so a face catches the eye's red spill.
    const slabSpecs: ReadonlyArray<[number, number, number, number]> = [
      // x, z, width, depth
      [-7.6, 2.6, 0.9, 1.6],
      [-6.3, 2.0, 0.7, 1.4],
      [-5.1, 1.5, 0.8, 1.3],
      [-4.0, 1.0, 0.6, 1.2],
      [-3.05, 0.55, 0.7, 1.1],
      [-2.2, 0.15, 0.5, 1.0],
      [-1.45, -0.2, 0.6, 0.95],
      [-0.75, -0.5, 0.45, 0.9]
    ];
    for (const [x, z, width, depth] of slabSpecs) {
      const material = Math.abs(x) % 2 < 1 ? panel : panelDark;
      const slab = new THREE.Mesh(new THREE.BoxGeometry(width, 11, depth), material);
      slab.position.set(x, 0.3, z);
      slab.rotation.y = -0.52;
      this.root.add(slab);
      const strip = new THREE.Mesh(new THREE.BoxGeometry(0.03, 11, 0.03), edgeGlow);
      strip.position.set(x + width * 0.42, 0.3, z + depth * 0.5);
      strip.rotation.y = -0.52;
      this.root.add(strip);
    }

    // The vault frame: beveled square recess around the optic.
    const frame = new THREE.Group();
    const frameBar = (w: number, h: number, x: number, y: number, z: number) => {
      const bar = new THREE.Mesh(new THREE.BoxGeometry(w, h, 0.55), frameMetal);
      bar.position.set(x, y, z);
      frame.add(bar);
    };
    frameBar(4.9, 0.5, 0, 2.25, -0.4);
    frameBar(4.9, 0.5, 0, -2.25, -0.4);
    frameBar(0.5, 4.0, -2.25, 0, -0.4);
    frameBar(0.5, 4.0, 2.25, 0, -0.4);
    // Chamfer corners.
    for (const [cx, cy] of [[-2.05, 2.05], [2.05, 2.05], [-2.05, -2.05], [2.05, -2.05]] as const) {
      const corner = new THREE.Mesh(new THREE.BoxGeometry(0.72, 0.72, 0.5), panelDark);
      corner.position.set(cx, cy, -0.36);
      corner.rotation.z = Math.PI / 4;
      frame.add(corner);
    }
    const backPlate = new THREE.Mesh(new THREE.BoxGeometry(4.4, 4.4, 0.14), panelDark);
    backPlate.position.z = -0.72;
    frame.add(backPlate);
    const trimRing = new THREE.Mesh(new THREE.TorusGeometry(1.98, 0.03, 14, 120), redTrim);
    trimRing.position.z = -0.12;
    frame.add(trimRing);
    frame.position.set(OPTIC_X, OPTIC_Y, 0);
    frame.rotation.y = -0.16;
    this.root.add(frame);

    // The optic itself, inside the frame.
    const optic = new THREE.Group();
    const rim = new THREE.Mesh(new THREE.TorusGeometry(1.62, 0.14, 24, 144), frameMetal);
    optic.add(rim);
    const innerRim = new THREE.Mesh(new THREE.TorusGeometry(1.38, 0.05, 18, 120), redTrim);
    innerRim.position.z = 0.1;
    optic.add(innerRim);
    const iris = new THREE.Mesh(new THREE.CircleGeometry(1.5, 128), this.irisMaterial);
    iris.position.z = 0.02;
    optic.add(iris);
    const lens = new THREE.Mesh(new THREE.SphereGeometry(1.46, 96, 48), this.lensMaterial);
    lens.scale.set(1, 1, 0.32);
    lens.position.z = 0.2;
    optic.add(lens);
    const core = new THREE.Mesh(new THREE.SphereGeometry(0.11, 40, 24), this.coreMaterial);
    core.position.z = 0.75;
    optic.add(core);
    const glow = new THREE.Mesh(new THREE.PlaneGeometry(1.05, 1.05), this.coreGlow);
    glow.position.z = 0.7;
    optic.add(glow);
    optic.position.set(OPTIC_X, OPTIC_Y, -0.05);
    optic.rotation.y = -0.16;
    this.root.add(optic);

    // The raking beam: a bright core ray and a soft sheath, from the core
    // toward the lower front-left. Group origin sits at the core so state
    // animation can pitch/yaw the whole ray.
    const beamDirection = new THREE.Vector3(-0.78, -0.3, 0.55).normalize();
    const beamLength = 11;
    const makeRay = (radius: number, opacity: number) => {
      const material = new THREE.MeshBasicMaterial({
        color: 0xff2416,
        transparent: true,
        opacity,
        depthWrite: false,
        blending: THREE.AdditiveBlending
      });
      this.beamMaterials.push(material);
      const geometry = new THREE.CylinderGeometry(radius, radius * 3.2, beamLength, 12, 1, true);
      const ray = new THREE.Mesh(geometry, material);
      ray.position.y = -beamLength / 2;
      return ray;
    };
    const beamCarrier = new THREE.Group();
    beamCarrier.add(makeRay(0.012, 0.85), makeRay(0.05, 0.22), makeRay(0.14, 0.07));
    beamCarrier.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), beamDirection);
    this.beamGroup.add(beamCarrier);
    this.beamGroup.position.set(OPTIC_X, OPTIC_Y, 0.72);
    this.root.add(this.beamGroup);

    // The floor: glossy black slab plus the eye's cheap "reflection" —
    // an additive pool of red and a mirrored core glow.
    const floor = new THREE.Mesh(
      new THREE.BoxGeometry(60, 0.3, 14),
      new THREE.MeshStandardMaterial({ color: 0x060707, metalness: 0.9, roughness: 0.22 })
    );
    floor.position.set(0, -4.85, 1.5);
    this.root.add(floor);
    const pool = new THREE.Mesh(new THREE.PlaneGeometry(7, 3.4), this.floorGlowMaterial);
    pool.rotation.x = -Math.PI / 2;
    pool.position.set(OPTIC_X - 0.4, -4.68, 1.4);
    this.root.add(pool);
    const mirroredGlow = new THREE.Mesh(new THREE.PlaneGeometry(1.4, 0.5), this.coreGlow);
    mirroredGlow.rotation.x = -Math.PI / 2.4;
    mirroredGlow.position.set(OPTIC_X, -4.6, 1.15);
    this.root.add(mirroredGlow);
  }

  private resize(): void {
    const { width, height } = this.container.getBoundingClientRect();
    if (width < 2 || height < 2) return;
    // Perf tier by surface width; the film-still framing tightens onto the
    // optic when the container goes portrait (the shared mobile card).
    this.renderer.setPixelRatio(
      Math.min(window.devicePixelRatio || 1, width >= 1101 ? 1.75 : width >= 761 ? 1.5 : 1.25)
    );
    this.camera.aspect = width / height;
    const portrait = this.camera.aspect < 1.05;
    const fitWidth = portrait ? 8.2 : 13.4;
    const fitHeight = portrait ? 8.6 : 10.2;
    const focusX = portrait ? OPTIC_X - 0.4 : 0.3;
    const halfFov = THREE.MathUtils.degToRad(this.camera.fov / 2);
    const byHeight = fitHeight / 2 / Math.tan(halfFov);
    const byWidth = fitWidth / 2 / (Math.tan(halfFov) * this.camera.aspect);
    this.camera.position.set(focusX, -0.2, Math.max(byHeight, byWidth));
    this.camera.lookAt(focusX, -0.1, 0);
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
    this.beamLevel = THREE.MathUtils.damp(this.beamLevel, profile.beam, 4, delta);

    // Camera drift: a slow film-camera float plus pointer parallax.
    const drift = this.reducedMotionQuery.matches ? 0 : 1;
    this.root.rotation.y = THREE.MathUtils.damp(
      this.root.rotation.y,
      this.pointer.x * 0.035 * drift + Math.sin(elapsed * 0.11) * 0.012 * drift,
      4,
      delta
    );
    this.root.rotation.x = THREE.MathUtils.damp(
      this.root.rotation.x,
      -this.pointer.y * 0.028 * drift + Math.cos(elapsed * 0.09) * 0.008 * drift,
      4,
      delta
    );

    // The beam is the state's tell: listening pitches it toward the
    // viewer, thinking scans it across the corridor, denied cuts it.
    const speakingPulse =
      this.state === "speaking" && !this.reducedMotionQuery.matches
        ? (0.5 + 0.5 * Math.sin(elapsed * 7.1) * Math.sin(elapsed * 3.1)) * 0.35
        : 0;
    const flare = Math.min(1, this.audioEnergy + speakingPulse);
    const scan =
      this.state === "thinking" && !this.reducedMotionQuery.matches
        ? Math.sin(elapsed * 0.85) * 0.3
        : 0;
    const towardViewer = this.state === "listening" ? 0.22 : 0;
    this.beamGroup.rotation.y = THREE.MathUtils.damp(this.beamGroup.rotation.y, scan, 3, delta);
    this.beamGroup.rotation.x = THREE.MathUtils.damp(
      this.beamGroup.rotation.x,
      towardViewer,
      3,
      delta
    );
    const beamStrength = this.beamLevel * (0.75 + flare * 0.6 + (motion ? Math.sin(elapsed * 13) * 0.04 : 0));
    const baseOpacities = [0.85, 0.22, 0.07];
    for (let index = 0; index < this.beamMaterials.length; index += 1) {
      const material = this.beamMaterials[index];
      if (!material) continue;
      material.opacity = (baseOpacities[index] ?? 0.1) * beamStrength;
    }
    this.floorGlowMaterial.opacity = 0.05 + this.beamLevel * 0.1 + flare * 0.1;

    const stateIndex =
      this.state === "thinking" ? 1 : this.state === "speaking" ? 0.7 : this.state === "listening" ? 0.55 : 0.2;
    this.irisMaterial.uniforms.uTime!.value = elapsed;
    this.irisMaterial.uniforms.uEnergy!.value = this.audioEnergy;
    this.irisMaterial.uniforms.uState!.value = stateIndex;
    this.irisMaterial.uniforms.uToolPulse!.value = this.targetToolPulse;

    const activity = Math.max(flare, this.targetToolPulse * 0.55);
    this.bloomPass.strength = THREE.MathUtils.damp(
      this.bloomPass.strength,
      profile.bloom + activity * 0.4,
      6,
      delta
    );
    // Denied leaves embers: a slow irregular flicker on a dim core.
    const ember =
      this.state === "denied" && !this.reducedMotionQuery.matches
        ? (0.5 + 0.5 * Math.sin(elapsed * 2.3) * Math.sin(elapsed * 5.7)) * 0.25
        : 0;
    this.coreMaterial.emissiveIntensity = THREE.MathUtils.damp(
      this.coreMaterial.emissiveIntensity,
      profile.core + activity * 3.4 + ember,
      7,
      delta
    );
    this.coreLight.intensity = THREE.MathUtils.damp(
      this.coreLight.intensity,
      4 + profile.core * 2.4 + activity * 9 + ember * 2,
      7,
      delta
    );
    this.coreGlow.uniforms.uIntensity!.value = profile.core + activity * 2.2 + ember;

    const toolColor = this.toolKind ? TOOL_COLORS[this.toolKind] : 0xff2214;
    this.coreMaterial.emissive.lerp(new THREE.Color(toolColor), Math.min(1, delta * 4));

    this.composer.render();
  };
}

export function createOptic(container: HTMLElement, eye: HTMLElement): HalOpticApi {
  const optic = new VaultOptic(container, eye);
  return {
    destroy: () => optic.destroy(),
    setAudioEnergy: (energy) => optic.setAudioEnergy(energy),
    setState: (state) => optic.setState(state),
    setToolKind: (kind) => optic.setToolKind(kind)
  };
}

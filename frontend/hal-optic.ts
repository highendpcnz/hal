import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

import { ACTIVE_DIRECTION, BRIDGE_DIRECTIONS } from "./directions";

type HalVisualState = "idle" | "listening" | "thinking" | "speaking" | "denied";
type HalToolKind = "fetch" | "execute" | "search" | "read" | null;

interface HalOpticApi {
  destroy: () => void;
  setAudioEnergy: (energy: number) => void;
  setState: (state: HalVisualState) => void;
  setToolKind: (kind: HalToolKind) => void;
}

declare global {
  interface Window {
    HALOptic?: HalOpticApi;
  }
}

interface MovingRing {
  readonly object: THREE.Object3D;
  readonly speed: number;
}

interface StateProfile {
  readonly bloom: number;
  readonly core: number;
  readonly lens: number;
  readonly motion: number;
}

const STATE_PROFILES: Record<HalVisualState, StateProfile> = {
  idle: { bloom: 0.24, core: 1.45, lens: 0.2, motion: 0.22 },
  listening: { bloom: 0.68, core: 4.1, lens: 0.66, motion: 0.72 },
  thinking: { bloom: 0.18, core: 0.95, lens: 0.14, motion: 1.08 },
  speaking: { bloom: 0.78, core: 4.8, lens: 0.78, motion: 0.58 },
  denied: { bloom: 0.12, core: 0.5, lens: 0.08, motion: 1.45 }
};

const TOOL_COLORS: Record<Exclude<HalToolKind, null>, number> = {
  fetch: 0xff3b24,
  execute: 0xff541f,
  search: 0xe9271c,
  read: 0xff2d20
};

const OPTIC_CAMERA_DISTANCE = 10.8;

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
    float radialGrid = line(radius * 9.5 - uTime * 0.05, 0.025);
    float spokes = line((angle / 6.2831853 + 0.5) * 24.0, 0.018);
    float pulse = 0.5 + 0.5 * sin(10.0 * radius - uTime * (1.1 + uState));
    float center = pow(max(0.0, 1.0 - radius), 2.35);
    float rim = smoothstep(1.0, 0.58, radius);
    float energy = clamp(uEnergy, 0.0, 1.0);
    float tool = uToolPulse * smoothstep(0.72, 0.2, abs(radius - 0.53));

    vec3 deepRed = vec3(0.055, 0.0, 0.0);
    vec3 signalRed = vec3(0.95, 0.045, 0.015);
    vec3 whiteCore = vec3(1.0, 0.72, 0.58);
    vec3 color = mix(deepRed, signalRed, center * (0.78 + 0.22 * pulse));
    color += signalRed * (radialGrid * 0.11 + spokes * 0.075) * rim;
    color += signalRed * energy * pulse * 0.28;
    color += vec3(1.0, 0.18, 0.04) * tool * 0.42;
    color = mix(color, whiteCore, pow(max(0.0, 1.0 - radius * 4.8), 5.0));

    float alpha = smoothstep(1.0, 0.91, radius) * (0.9 + center * 0.1);
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
    float glow = pow(max(0.0, 1.0 - radius), 3.2);
    vec3 color = mix(vec3(1.0, 0.08, 0.015), vec3(1.0, 0.92, 0.8), glow);
    gl_FragColor = vec4(color * (0.72 + uIntensity * 0.14), glow * 0.92);
  }
`;

class ApertureOptic {
  private readonly camera = new THREE.PerspectiveCamera(31, 1, 0.1, 40);
  private readonly timer = new THREE.Timer();
  private readonly composer: EffectComposer;
  private readonly bloomPass: UnrealBloomPass;
  private readonly coreGlow: THREE.ShaderMaterial;
  private readonly coreLight = new THREE.PointLight(0xff2a18, 3.5, 7, 2);
  private readonly coreMaterial = new THREE.MeshStandardMaterial({
    color: 0xffb09a,
    emissive: 0xff2a18,
    emissiveIntensity: 2.4,
    metalness: 0,
    roughness: 0.16
  });
  private readonly container: HTMLElement;
  private readonly eye: HTMLElement;
  private readonly irisMaterial: THREE.ShaderMaterial;
  private readonly ledMaterials: THREE.MeshStandardMaterial[] = [];
  private readonly lensMaterial = new THREE.MeshPhysicalMaterial({
    color: 0x8c0802,
    emissive: 0x6a0300,
    emissiveIntensity: 0.2,
    metalness: 0,
    roughness: 0.09,
    transmission: 0.28,
    thickness: 1.4,
    ior: 1.52,
    clearcoat: 1,
    clearcoatRoughness: 0.08,
    transparent: true,
    opacity: 0.78,
    depthWrite: false
  });
  private readonly movingRings: MovingRing[] = [];
  private readonly reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  private readonly renderer: THREE.WebGLRenderer;
  private readonly root = new THREE.Group();
  private readonly scene = new THREE.Scene();
  private readonly targetPointer = new THREE.Vector2();

  private audioEnergy = 0;
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
    this.renderer.toneMappingExposure = 0.82;
    this.renderer.setClearColor(0x020202, 1);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    this.container.appendChild(this.renderer.domElement);

    this.scene.fog = new THREE.FogExp2(0x020202, 0.055);
    this.camera.position.set(0, 0, OPTIC_CAMERA_DISTANCE);
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

    this.buildScene();

    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloomPass = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.48, 0.62, 0.52);
    this.composer.addPass(this.bloomPass);
    this.composer.addPass(new OutputPass());

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.container);
    this.stateObserver = new MutationObserver(() => this.syncStateFromDom());
    this.stateObserver.observe(this.eye, { attributes: true, attributeFilter: ["class"] });
    this.container.addEventListener("pointermove", this.onPointerMove);
    this.container.addEventListener("pointerleave", this.onPointerLeave);
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
    this.container.removeEventListener("pointermove", this.onPointerMove);
    this.container.removeEventListener("pointerleave", this.onPointerLeave);
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
    const ambient = new THREE.AmbientLight(0x241412, 0.8);
    const key = new THREE.DirectionalLight(0xffe0ca, 2.8);
    key.position.set(-3.5, 4.2, 7);
    const rim = new THREE.DirectionalLight(0xff2a18, 0.65);
    rim.position.set(4, -2.5, 4);
    const fill = new THREE.DirectionalLight(0xa2aab0, 1.1);
    fill.position.set(4, -4, 6);
    this.scene.add(ambient, key, rim, fill);

    const blackMetal = new THREE.MeshStandardMaterial({
      color: 0x0b0c0d,
      metalness: 0.94,
      roughness: 0.36
    });
    const brushedMetal = new THREE.MeshStandardMaterial({
      color: 0x35383a,
      metalness: 0.97,
      roughness: 0.24
    });
    const edgeMetal = new THREE.MeshStandardMaterial({
      color: 0x6b6d70,
      metalness: 1,
      roughness: 0.2
    });
    const redMetal = new THREE.MeshStandardMaterial({
      color: 0x2b0301,
      emissive: 0xc10c04,
      emissiveIntensity: 0.46,
      metalness: 0.76,
      roughness: 0.25
    });

    this.addCylinderPlate(2.82, 0.24, -0.58, blackMetal);
    this.addCylinderPlate(2.6, 0.2, -0.42, brushedMetal);
    this.addCylinderPlate(2.32, 0.18, -0.27, blackMetal);

    const rings: ReadonlyArray<[number, number, number, THREE.Material, number]> = [
      [2.68, 0.075, -0.38, edgeMetal, 0.025],
      [2.48, 0.09, -0.18, blackMetal, -0.034],
      [2.25, 0.055, -0.02, brushedMetal, 0.045],
      [2.07, 0.065, 0.07, redMetal, -0.062],
      [1.88, 0.045, 0.14, edgeMetal, 0.072],
      [1.69, 0.035, 0.2, redMetal, -0.088],
      [1.42, 0.022, 0.64, edgeMetal, 0.11],
      [1.09, 0.018, 0.76, redMetal, -0.14],
      [0.73, 0.014, 0.82, edgeMetal, 0.18]
    ];
    for (const [radius, tube, z, material, speed] of rings) {
      const ring = new THREE.Mesh(new THREE.TorusGeometry(radius, tube, 18, 144), material);
      ring.position.z = z;
      this.root.add(ring);
      this.movingRings.push({ object: ring, speed });
    }

    this.addSegmentedRetainer(brushedMetal, blackMetal);
    this.addCalibrationTicks(edgeMetal);
    this.addIndicatorLights();

    const iris = new THREE.Mesh(new THREE.CircleGeometry(1.66, 128), this.irisMaterial);
    iris.position.z = 0.3;
    this.root.add(iris);

    const lens = new THREE.Mesh(new THREE.SphereGeometry(1.62, 96, 48), this.lensMaterial);
    lens.scale.set(1, 1, 0.31);
    lens.position.z = 0.52;
    this.root.add(lens);

    const core = new THREE.Mesh(new THREE.SphereGeometry(0.12, 40, 24), this.coreMaterial);
    core.position.z = 1.12;
    this.root.add(core);
    this.coreLight.position.set(0, 0, 1.5);
    this.root.add(this.coreLight);

    const glow = new THREE.Mesh(new THREE.PlaneGeometry(0.72, 0.72), this.coreGlow);
    glow.position.z = 1.06;
    this.root.add(glow);

    const reflectionMaterial = new THREE.MeshBasicMaterial({
      color: 0xffeee6,
      transparent: true,
      opacity: 0.22,
      depthWrite: false,
      blending: THREE.AdditiveBlending
    });
    const primaryReflection = new THREE.Mesh(new THREE.SphereGeometry(0.12, 32, 20), reflectionMaterial);
    primaryReflection.scale.set(1.15, 0.72, 0.2);
    primaryReflection.position.set(-0.43, 0.48, 1.12);
    this.root.add(primaryReflection);
    const secondaryReflection = primaryReflection.clone();
    secondaryReflection.scale.set(0.55, 0.34, 0.12);
    secondaryReflection.position.set(-0.24, 0.24, 1.15);
    this.root.add(secondaryReflection);

    this.root.rotation.x = -0.025;
    this.root.rotation.y = 0.035;
  }

  private addCylinderPlate(radius: number, depth: number, z: number, material: THREE.Material): void {
    const plate = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, depth, 128), material);
    plate.rotation.x = Math.PI / 2;
    plate.position.z = z;
    this.root.add(plate);
  }

  private addSegmentedRetainer(brushedMetal: THREE.Material, blackMetal: THREE.Material): void {
    const segmentGeometry = new THREE.BoxGeometry(0.52, 0.16, 0.16);
    const segmentMesh = new THREE.InstancedMesh(segmentGeometry, brushedMetal, 12);
    const shadowGeometry = new THREE.BoxGeometry(0.36, 0.1, 0.12);
    const shadowMesh = new THREE.InstancedMesh(shadowGeometry, blackMetal, 12);
    const dummy = new THREE.Object3D();
    for (let index = 0; index < 12; index += 1) {
      const angle = (index / 12) * Math.PI * 2;
      dummy.position.set(Math.cos(angle) * 2.5, Math.sin(angle) * 2.5, -0.02);
      dummy.rotation.set(0, 0, angle + Math.PI / 2);
      dummy.updateMatrix();
      segmentMesh.setMatrixAt(index, dummy.matrix);

      dummy.position.set(Math.cos(angle) * 2.18, Math.sin(angle) * 2.18, 0.09);
      dummy.updateMatrix();
      shadowMesh.setMatrixAt(index, dummy.matrix);
    }
    segmentMesh.instanceMatrix.needsUpdate = true;
    shadowMesh.instanceMatrix.needsUpdate = true;
    this.root.add(segmentMesh, shadowMesh);
    this.movingRings.push({ object: segmentMesh, speed: 0.012 });
    this.movingRings.push({ object: shadowMesh, speed: -0.018 });
  }

  private addCalibrationTicks(material: THREE.Material): void {
    const tickGeometry = new THREE.BoxGeometry(0.025, 0.14, 0.025);
    const ticks = new THREE.InstancedMesh(tickGeometry, material, 72);
    const dummy = new THREE.Object3D();
    for (let index = 0; index < 72; index += 1) {
      const angle = (index / 72) * Math.PI * 2;
      const longTick = index % 6 === 0;
      dummy.position.set(Math.cos(angle) * 2.01, Math.sin(angle) * 2.01, 0.22);
      dummy.rotation.set(0, 0, angle - Math.PI / 2);
      dummy.scale.set(1, longTick ? 1.7 : 0.75, 1);
      dummy.updateMatrix();
      ticks.setMatrixAt(index, dummy.matrix);
    }
    ticks.instanceMatrix.needsUpdate = true;
    this.root.add(ticks);
    this.movingRings.push({ object: ticks, speed: 0.022 });
  }

  private addIndicatorLights(): void {
    const geometry = new THREE.SphereGeometry(0.055, 24, 16);
    for (let index = 0; index < 6; index += 1) {
      const material = new THREE.MeshStandardMaterial({
        color: 0xff2a18,
        emissive: 0xff1608,
        emissiveIntensity: 2.5,
        metalness: 0.15,
        roughness: 0.25
      });
      const angle = (index / 6) * Math.PI * 2 + Math.PI / 6;
      const led = new THREE.Mesh(geometry, material);
      led.position.set(Math.cos(angle) * 2.36, Math.sin(angle) * 2.36, 0.13);
      this.ledMaterials.push(material);
      this.root.add(led);
    }
  }

  private resize(): void {
    const { width, height } = this.container.getBoundingClientRect();
    if (width < 2 || height < 2) return;
    this.camera.aspect = width / height;
    this.camera.position.z = OPTIC_CAMERA_DISTANCE * Math.max(1, 0.92 / this.camera.aspect);
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
    const rect = this.container.getBoundingClientRect();
    this.targetPointer.set(
      THREE.MathUtils.clamp(((event.clientX - rect.left) / rect.width) * 2 - 1, -1, 1),
      THREE.MathUtils.clamp(-(((event.clientY - rect.top) / rect.height) * 2 - 1), -1, 1)
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

    for (const ring of this.movingRings) {
      ring.object.rotation.z += ring.speed * motion * delta;
    }

    const breathing = this.reducedMotionQuery.matches
      ? 0
      : Math.sin(elapsed * (this.state === "listening" ? 3.8 : 1.35)) * 0.012;
    const energyScale = this.audioEnergy * 0.035;
    const desiredScale = 1 + breathing + energyScale;
    const currentScale = this.root.scale.x;
    const nextScale = THREE.MathUtils.damp(currentScale, desiredScale, 7, delta);
    this.root.scale.setScalar(nextScale);
    this.root.rotation.y = THREE.MathUtils.damp(this.root.rotation.y, this.pointer.x * 0.085, 5, delta);
    this.root.rotation.x = THREE.MathUtils.damp(this.root.rotation.x, -this.pointer.y * 0.07, 5, delta);

    const stateIndex = this.state === "thinking" ? 1 : this.state === "speaking" ? 0.7 : this.state === "listening" ? 0.55 : 0.2;
    this.irisMaterial.uniforms.uTime!.value = elapsed;
    this.irisMaterial.uniforms.uEnergy!.value = this.audioEnergy;
    this.irisMaterial.uniforms.uState!.value = stateIndex;
    this.irisMaterial.uniforms.uToolPulse!.value = this.targetToolPulse;

    const activity = Math.max(this.audioEnergy, this.targetToolPulse * 0.55);
    this.bloomPass.strength = THREE.MathUtils.damp(
      this.bloomPass.strength,
      profile.bloom + activity * 0.46,
      6,
      delta
    );
    this.coreMaterial.emissiveIntensity = THREE.MathUtils.damp(
      this.coreMaterial.emissiveIntensity,
      profile.core + activity * 3.5,
      7,
      delta
    );
    this.lensMaterial.emissiveIntensity = THREE.MathUtils.damp(
      this.lensMaterial.emissiveIntensity,
      profile.lens + activity * 0.65,
      7,
      delta
    );
    this.coreLight.intensity = THREE.MathUtils.damp(
      this.coreLight.intensity,
      7 + profile.core * 1.55 + activity * 8,
      7,
      delta
    );
    this.coreGlow.uniforms.uIntensity!.value = profile.core + activity * 2.2;

    const toolColor = this.toolKind ? TOOL_COLORS[this.toolKind] : 0xff2a18;
    this.coreMaterial.emissive.lerp(new THREE.Color(toolColor), Math.min(1, delta * 4));
    for (let index = 0; index < this.ledMaterials.length; index += 1) {
      const material = this.ledMaterials[index];
      if (!material) continue;
      const phase = 0.5 + 0.5 * Math.sin(elapsed * 2.2 + index * 1.4);
      material.emissiveIntensity = 1.4 + profile.motion * 1.3 + phase * (0.5 + activity);
    }

    this.composer.render();
  };
}

function setupDirectionSelector(): void {
  document.documentElement.dataset.bridgeDirection = ACTIVE_DIRECTION;
  const buttons = document.querySelectorAll<HTMLButtonElement>("[data-direction-id]");
  for (const button of buttons) {
    const id = button.dataset.directionId;
    const manifest = BRIDGE_DIRECTIONS.find((direction) => direction.id === id);
    if (!manifest) continue;
    button.textContent = manifest.shortLabel;
    button.title = manifest.ready ? manifest.label : `${manifest.label} — in development`;
    button.disabled = !manifest.ready;
    button.setAttribute("aria-pressed", String(manifest.id === ACTIVE_DIRECTION));
  }
}

function setupBridgeRail(): void {
  const bridgeCommand = document.getElementById("bridge-cmd") as HTMLInputElement | null;
  const mobileCommand = document.getElementById("cmd-input") as HTMLInputElement | null;
  const mobileCommandLine = document.getElementById("cmdline");
  const bridgeLeft = document.querySelector<HTMLElement>(".bridge-left");
  const chessPanel = document.getElementById("chess-panel");
  const actions = document.querySelectorAll<HTMLButtonElement>("[data-bridge-action]");
  for (const action of actions) {
    action.addEventListener("click", () => {
      switch (action.dataset.bridgeAction) {
        case "missions":
          if (bridgeLeft) delete bridgeLeft.dataset.activeSurface;
          const command = window.matchMedia("(min-width: 761px)").matches ? bridgeCommand : mobileCommand;
          if (command) {
            if (!command.value) command.value = "/mission ";
            if (command === mobileCommand) mobileCommandLine?.classList.add("open");
            command.focus();
          }
          break;
        case "chess":
          if (bridgeLeft) bridgeLeft.dataset.activeSurface = "chess";
          document.getElementById("chess-new")?.click();
          break;
        case "viewscreen":
          if (bridgeLeft) bridgeLeft.dataset.activeSurface = "viewscreen";
          break;
        case "systems":
          document.getElementById("monitor-tab")?.click();
          break;
      }
    });
  }

  if (bridgeLeft && chessPanel) {
    const chessObserver = new MutationObserver(() => {
      if (chessPanel.classList.contains("on")) bridgeLeft.dataset.activeSurface = "chess";
      else if (bridgeLeft.dataset.activeSurface === "chess") delete bridgeLeft.dataset.activeSurface;
    });
    chessObserver.observe(chessPanel, { attributes: true, attributeFilter: ["class"] });
  }
}

function bootAperture(): void {
  setupDirectionSelector();
  setupBridgeRail();
  const container = document.getElementById("optic-stage");
  const eye = document.getElementById("eye");
  if (!container || !eye) return;

  try {
    window.HALOptic?.destroy();
    const optic = new ApertureOptic(container, eye);
    window.HALOptic = {
      destroy: () => optic.destroy(),
      setAudioEnergy: (energy) => optic.setAudioEnergy(energy),
      setState: (state) => optic.setState(state),
      setToolKind: (kind) => optic.setToolKind(kind)
    };
    window.addEventListener("pagehide", () => window.HALOptic?.destroy(), { once: true });
  } catch (error) {
    console.warn("HAL optic renderer unavailable; retaining the CSS fallback.", error);
  }
}

bootAperture();

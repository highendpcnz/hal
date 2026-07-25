import{A as e,F as t,I as n,N as r,O as i,T as a,a as o,c as s,d as c,i as l,j as u,k as d,l as f,n as p,r as m,t as h,y as g}from"./UnrealBloomPass.js";var _={idle:{attend:0,vortex:0,chill:0,coherence:0,flow:.3,brightness:.5,bloom:.3},listening:{attend:1,vortex:0,chill:0,coherence:0,flow:.55,brightness:.85,bloom:.5},thinking:{attend:.15,vortex:1,chill:0,coherence:0,flow:1,brightness:.6,bloom:.34},speaking:{attend:.2,vortex:0,chill:0,coherence:1,flow:.45,brightness:.9,bloom:.5},denied:{attend:0,vortex:0,chill:1,coherence:0,flow:.04,brightness:.3,bloom:.12}},v={fetch:new c(16726820),execute:new c(16738847),search:new c(15279900),read:new c(16723232)},y=48e3,b=3,x=700,S=new n(1.15,.1,1.4),C=`
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
`,w=`
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
`,T=class{camera=new a(34,1,.1,80);timer=new r;composer;bloomPass;container;eye;geometry=new f;material;reducedMotionQuery=window.matchMedia(`(prefers-reduced-motion: reduce)`);renderer;scene=new e;targetPointer=new t;audioEnergy=0;destroyed=!1;domPollTimer=0;flowSpeedSeed=1;flowTime=0;frameId=0;missionCount=0;lagCool=0;pointer=new t;populationScale=1;pulseAge=99;resizeObserver;state=`idle`;stateObserver;targetAudioEnergy=0;tier=y;toolKind=null;constructor(e,n){this.container=e,this.eye=n,this.renderer=new o({alpha:!1,antialias:!1,powerPreference:`high-performance`}),this.renderer.domElement.className=`optic-webgl-canvas`,this.renderer.outputColorSpace=d,this.renderer.toneMapping=4,this.renderer.toneMappingExposure=.92,this.renderer.setClearColor(65793,1),this.container.appendChild(this.renderer.domElement),this.scene.fog=null,this.timer.connect(document),this.buildAttributes(),this.material=new u({uniforms:{uFlowTime:{value:0},uRealTime:{value:0},uTurbulence:{value:0},uAttend:{value:0},uVortex:{value:0},uChill:{value:0},uCoherence:{value:0},uShimmer:{value:0},uPulseAge:{value:99},uPulseColor:{value:new c(16723232)},uMissions:{value:0},uSpreadScale:{value:1},uSize:{value:1.9},uBrightness:{value:.5},uCoolBias:{value:0},uEyeCenter:{value:S.clone()}},vertexShader:C,fragmentShader:w,transparent:!0,depthWrite:!1,depthTest:!1,blending:2});let r=new i(this.geometry,this.material);r.frustumCulled=!1,this.scene.add(r),this.composer=new l(this.renderer),this.composer.addPass(new p(this.scene,this.camera)),this.bloomPass=new h(new t(1,1),.3,.7,.55),this.composer.addPass(this.bloomPass),this.composer.addPass(new m),this.seedFromHistory(),this.pollDom(),this.domPollTimer=window.setInterval(()=>this.pollDom(),5e3),this.resizeObserver=new ResizeObserver(()=>this.resize()),this.resizeObserver.observe(this.container),this.stateObserver=new MutationObserver(()=>this.syncStateFromDom()),this.stateObserver.observe(this.eye,{attributes:!0,attributeFilter:[`class`]}),window.addEventListener(`pointermove`,this.onPointerMove),window.addEventListener(`blur`,this.onPointerLeave),this.reducedMotionQuery.addEventListener(`change`,this.onMotionPreferenceChange),this.resize(),this.syncStateFromDom(),this.container.closest(`.eye-module`)?.classList.add(`webgl-ready`),this.frameId=requestAnimationFrame(this.animate)}setAudioEnergy(e){this.targetAudioEnergy=g.clamp(e,0,1)}setState(e){this.state=e,this.updateStatusLabel(e)}setToolKind(e){this.toolKind=e,e&&(this.pulseAge=0,this.material.uniforms.uPulseColor.value.copy(v[e]))}destroy(){this.destroyed||(this.destroyed=!0,cancelAnimationFrame(this.frameId),window.clearInterval(this.domPollTimer),this.resizeObserver.disconnect(),this.stateObserver.disconnect(),window.removeEventListener(`pointermove`,this.onPointerMove),window.removeEventListener(`blur`,this.onPointerLeave),this.reducedMotionQuery.removeEventListener(`change`,this.onMotionPreferenceChange),this.timer.dispose(),this.geometry.dispose(),this.material.dispose(),this.composer.dispose(),this.renderer.dispose(),this.renderer.domElement.remove(),this.container.closest(`.eye-module`)?.classList.remove(`webgl-ready`))}buildAttributes(){let e=new Float32Array(y*3),t=new Float32Array(y*3),n=new Float32Array(y*3),r=new Float32Array(y),i=new Float32Array(y);for(let e=0;e<y;e+=1){t[e*3]=Math.random(),t[e*3+1]=Math.random(),t[e*3+2]=Math.random();let a=Math.random()<.08,o=a?Math.random()*.22:.45+Math.sqrt(Math.random())*1.35,s=Math.random()*Math.PI*2;n[e*3]=Math.cos(s)*o,n[e*3+1]=Math.sin(s)*o,n[e*3+2]=(Math.random()-.5)*.14,r[e]=a?1:Math.max(0,.65-o*.3)+Math.random()*.15,i[e]=e<b*x?Math.floor(e/x)+1:0}this.geometry.setAttribute(`position`,new s(e,3)),this.geometry.setAttribute(`seed`,new s(t,3)),this.geometry.setAttribute(`formation`,new s(n,3)),this.geometry.setAttribute(`heat`,new s(r,1)),this.geometry.setAttribute(`group`,new s(i,1))}seedFromHistory(){let e=document.querySelectorAll(`#mlog-entries > *`).length;this.populationScale=.7+Math.min(e,80)/80*.3,this.flowSpeedSeed=1.15-Math.min(e,80)/80*.3}pollDom(){try{this.missionCount=Math.min(document.querySelectorAll(`#mission-cards .mission-card`).length,b);let e=document.getElementById(`telem-lag`)?.textContent??``,t=parseInt(e,10);this.lagCool=Number.isFinite(t)?g.clamp((t-4e3)/12e3,0,1):0}catch{this.missionCount=0,this.lagCool=0}}resize(){let{width:e,height:t}=this.container.getBoundingClientRect();if(e<2||t<2)return;this.renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,e>=1101?1.75:e>=761?1.5:1.25)),this.tier=e>=1101?4e4:e>=761?24e3:1e4;let n=b*x,r=Math.floor(Math.min(this.tier,y-n)*this.populationScale);this.geometry.setDrawRange(0,n+r),this.camera.aspect=e/t;let i=this.camera.aspect<1.05;this.material.uniforms.uSpreadScale.value=i?.55:1;let a=i?7.6:8.2,o=i?6.4:12.4,s=g.degToRad(this.camera.fov/2),c=a/2/Math.tan(s),l=o/2/(Math.tan(s)*this.camera.aspect);this.camera.position.set(0,0,Math.max(c,l)),this.camera.lookAt(0,0,0),this.camera.updateProjectionMatrix(),this.renderer.setSize(e,t,!1),this.composer.setSize(e,t)}syncStateFromDom(){let e=`idle`;this.eye.classList.contains(`denied`)?e=`denied`:this.eye.classList.contains(`listening`)?e=`listening`:this.eye.classList.contains(`speaking`)?e=`speaking`:this.eye.classList.contains(`thinking`)&&(e=`thinking`),this.setState(e);let t=this.eye.classList.contains(`kind-execute`)?`execute`:this.eye.classList.contains(`kind-fetch`)?`fetch`:this.eye.classList.contains(`kind-search`)?`search`:this.eye.classList.contains(`kind-read`)?`read`:null;t!==this.toolKind&&this.setToolKind(t)}updateStatusLabel(e){let t=document.getElementById(`optic-status-label`);t&&(t.textContent={idle:`Standby`,listening:`Listening`,thinking:`Thinking`,speaking:`Speaking`,denied:`Permission denied`}[e],t.dataset.state=e)}onPointerMove=e=>{this.reducedMotionQuery.matches||this.targetPointer.set(g.clamp(e.clientX/window.innerWidth*2-1,-1,1),g.clamp(-(e.clientY/window.innerHeight*2-1),-1,1))};onPointerLeave=()=>{this.targetPointer.set(0,0)};onMotionPreferenceChange=()=>{this.reducedMotionQuery.matches&&(this.targetPointer.set(0,0),this.pointer.set(0,0))};animate=e=>{if(this.destroyed)return;this.frameId=requestAnimationFrame(this.animate),this.timer.update(e);let t=Math.min(this.timer.getDelta(),.05),n=this.timer.getElapsed(),r=_[this.state],i=this.reducedMotionQuery.matches;this.audioEnergy=g.damp(this.audioEnergy,this.targetAudioEnergy,8,t),this.targetAudioEnergy=g.damp(this.targetAudioEnergy,0,2.6,t),this.pointer.x=g.damp(this.pointer.x,this.targetPointer.x,5,t),this.pointer.y=g.damp(this.pointer.y,this.targetPointer.y,5,t),i||(this.flowTime+=t*r.flow*this.flowSpeedSeed),this.pulseAge+=t;let a=this.material.uniforms;a.uFlowTime.value=this.flowTime,a.uRealTime.value=i?0:n,a.uTurbulence.value=g.damp(a.uTurbulence.value,i?0:this.audioEnergy,7,t);let o=r.coherence>a.uCoherence.value?2.6:9;a.uCoherence.value=g.damp(a.uCoherence.value,r.coherence,i?20:o,t),a.uAttend.value=g.damp(a.uAttend.value,r.attend,4,t),a.uVortex.value=g.damp(a.uVortex.value,r.vortex,4,t),a.uChill.value=g.damp(a.uChill.value,r.chill,r.chill>0?10:4,t),a.uShimmer.value=this.state===`speaking`&&!i?this.audioEnergy:0,a.uPulseAge.value=i?this.pulseAge<.6?.4:99:this.pulseAge,a.uMissions.value=this.missionCount,a.uBrightness.value=g.damp(a.uBrightness.value,r.brightness+this.audioEnergy*.4,5,t),a.uCoolBias.value=g.damp(a.uCoolBias.value,this.lagCool,2,t),this.camera.position.x=g.damp(this.camera.position.x,this.pointer.x*.5,4,t),this.camera.position.y=g.damp(this.camera.position.y,this.pointer.y*.32,4,t),this.camera.lookAt(0,0,0),this.bloomPass.strength=g.damp(this.bloomPass.strength,r.bloom+this.audioEnergy*.3,6,t),this.composer.render()}};function E(e,t){let n=new T(e,t);return{destroy:()=>n.destroy(),setAudioEnergy:e=>n.setAudioEnergy(e),setState:e=>n.setState(e),setToolKind:e=>n.setToolKind(e)}}export{E as createOptic};
//# sourceMappingURL=optic-chorus.js.map
import{A as e,D as t,F as n,N as r,T as i,_ as a,a as o,b as s,d as c,g as l,i as u,j as d,k as f,n as p,r as m,s as h,t as g,y as _}from"./UnrealBloomPass.js";var v={high:{depth:20,rows:7,dpr:1.75},mid:{depth:16,rows:6,dpr:1.5},low:{depth:10,rows:4,dpr:1.25}},y=3,b=new c(13625066),x=new c(4148562),S=new c(16723231),C={fetch:new c(8377568),execute:new c(16757370),search:new c(12560127),read:new c(11069104)},w=60,T=`
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
`,E=`
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
`,D=class{camera=new i(58,1,.1,60);timer=new r;composer;bloomPass;container;eye;geometry;material;reducedMotionQuery=window.matchMedia(`(prefers-reduced-motion: reduce)`);renderer;scene=new e;targetPointer=new n;activation;activationAttr;flare;flareAttr;extract;extractAttr;column;audioEnergy=0;destroyed=!1;domPollTimer=0;drift=0;extracting=!1;frameId=0;lagCool=0;missionCount=0;occupancy=0;pointer=new n;resizeObserver;scanHead=0;seatCount=0;state=`idle`;stateObserver;targetAudioEnergy=0;tier=v.high;toolColumn=-1;constructor(e,r){this.container=e,this.eye=r,this.renderer=new o({alpha:!1,antialias:!1,powerPreference:`high-performance`}),this.renderer.domElement.className=`optic-webgl-canvas`,this.renderer.outputColorSpace=f,this.renderer.toneMapping=4,this.renderer.toneMappingExposure=.95,this.renderer.setClearColor(328966,1),this.container.appendChild(this.renderer.domElement),this.timer.connect(document),this.tier=this.pickTier(),this.geometry=this.buildRacks(this.tier),this.material=new d({uniforms:{uTime:{value:0},uDrift:{value:0},uAttend:{value:0},uParallax:{value:new n},uMemory:{value:b.clone()},uMemoryDim:{value:x.clone()},uFlareColor:{value:b.clone()},uChill:{value:0},uVoice:{value:0}},vertexShader:T,fragmentShader:E,transparent:!0,depthWrite:!1,side:2,blending:1});let i=new s(this.geometry,this.material);i.frustumCulled=!1,this.scene.add(i);let a=new t(S.getHex(),0);this.scene.add(a),this.camera.position.set(0,0,2.6),this.composer=new u(this.renderer),this.composer.addPass(new p(this.scene,this.camera)),this.bloomPass=new g(new n(1,1),.32,.7,.82),this.composer.addPass(this.bloomPass),this.composer.addPass(new m),this.pollDom(),this.domPollTimer=window.setInterval(()=>this.pollDom(),5e3),this.resizeObserver=new ResizeObserver(()=>this.resize()),this.resizeObserver.observe(this.container),this.stateObserver=new MutationObserver(()=>this.syncStateFromDom()),this.stateObserver.observe(this.eye,{attributes:!0,attributeFilter:[`class`]}),window.addEventListener(`pointermove`,this.onPointerMove),window.addEventListener(`blur`,this.onPointerLeave),this.reducedMotionQuery.addEventListener(`change`,this.onMotionPreferenceChange),this.resize(),this.syncStateFromDom(),this.container.closest(`.eye-module`)?.classList.add(`webgl-ready`),this.frameId=requestAnimationFrame(this.animate)}setAudioEnergy(e){this.targetAudioEnergy=_.clamp(e,0,1)}setState(e){this.state=e,this.updateStatusLabel(e)}setToolKind(e){e?(this.toolColumn=Math.floor(this.scanHead)%Math.max(1,this.tier.depth),this.material.uniforms.uFlareColor.value.copy(C[e])):this.toolColumn=-1}playSessionEnd(){if(this.destroyed||this.extracting)return Promise.resolve();if(this.extracting=!0,this.reducedMotionQuery.matches)return this.activation.fill(0),this.activationAttr.needsUpdate=!0,Promise.resolve();let e=performance.now();return new Promise(t=>{let n=!1,r=()=>{n||(n=!0,window.removeEventListener(`keydown`,i),window.removeEventListener(`pointerdown`,i),t())},i=()=>r();window.addEventListener(`keydown`,i,{once:!0}),window.addEventListener(`pointerdown`,i,{once:!0});let a=()=>{if(this.destroyed)return r();let t=(performance.now()-e)/2500;if(t>=1)return r();let n=Math.floor(this.seatCount*this.occupancy),i=(1-t)*n;for(let e=0;e<this.seatCount;e+=1){if(e>=n)continue;let t=_.clamp((i-e)/-6+1,0,1);this.extract[e]=Math.max(this.extract[e],t)}this.extractAttr.needsUpdate=!0,requestAnimationFrame(a)};requestAnimationFrame(a)})}destroy(){this.destroyed||(this.destroyed=!0,cancelAnimationFrame(this.frameId),window.clearInterval(this.domPollTimer),this.resizeObserver.disconnect(),this.stateObserver.disconnect(),window.removeEventListener(`pointermove`,this.onPointerMove),window.removeEventListener(`blur`,this.onPointerLeave),this.reducedMotionQuery.removeEventListener(`change`,this.onMotionPreferenceChange),this.timer.dispose(),this.geometry.dispose(),this.material.dispose(),this.composer.dispose(),this.renderer.dispose(),this.renderer.domElement.remove(),this.container.closest(`.eye-module`)?.classList.remove(`webgl-ready`))}pickTier(){let e=window.innerWidth;return e<900?v.low:e<1500?v.mid:v.high}buildRacks(e){let t=y*e.depth*e.rows;this.seatCount=t;let n=new h(1,1,1),r=new a;r.index=n.index,r.attributes.position=n.attributes.position,r.instanceCount=t;let i=new Float32Array(t*3),o=new Float32Array(t*3),s=new Float32Array(t*3),c=new Float32Array(t);this.activation=new Float32Array(t),this.flare=new Float32Array(t),this.extract=new Float32Array(t),this.column=new Float32Array(t);let u=0;for(let t=0;t<e.depth;t+=1)for(let n=0;n<y;n+=1)for(let r=0;r<e.rows;r+=1){let a=1.9-(e.depth-1-t)*.72,l=(r/Math.max(1,e.rows-1)-.5)*2;if(n===0||n===1){let e=n===0?-1:1;i[u*3]=e*2.35,i[u*3+1]=l*.92,i[u*3+2]=a,o[u*3]=.07,o[u*3+1]=.2,o[u*3+2]=.42,s[u*3]=e}else i[u*3]=l*1.2,i[u*3+1]=1.62,i[u*3+2]=a,o[u*3]=.24,o[u*3+1]=.07,o[u*3+2]=.42,s[u*3+1]=1;c[u]=Math.random(),this.column[u]=t,u+=1}let d=(e,t)=>new l(e,t);return r.setAttribute(`aSeat`,d(i,3)),r.setAttribute(`aSize`,d(o,3)),r.setAttribute(`aOut`,d(s,3)),r.setAttribute(`aSeed`,d(c,1)),this.activationAttr=d(this.activation,1),this.flareAttr=d(this.flare,1),this.extractAttr=d(this.extract,1),r.setAttribute(`aActivation`,this.activationAttr),r.setAttribute(`aFlare`,this.flareAttr),r.setAttribute(`aExtract`,this.extractAttr),n.dispose(),r}pollDom(){try{let e=document.querySelectorAll(`#mlog-entries > *`).length;this.occupancy=Math.min(e,w)/w,this.missionCount=document.querySelectorAll(`#mission-cards .mission-card`).length;let t=document.getElementById(`telem-lag`)?.textContent??``,n=parseInt(t,10);this.lagCool=Number.isFinite(n)?_.clamp((n-4e3)/12e3,0,1):0}catch{this.occupancy=0,this.missionCount=0,this.lagCool=0}}resize(){let{width:e,height:t}=this.container.getBoundingClientRect();e<2||t<2||(this.renderer.setPixelRatio(Math.min(window.devicePixelRatio,this.tier.dpr)),this.renderer.setSize(e,t,!1),this.composer.setSize(e,t),this.bloomPass.setSize(e,t),this.camera.aspect=e/t,this.camera.updateProjectionMatrix())}syncStateFromDom(){let e=this.eye.classList,t=e.contains(`denied`)?`denied`:e.contains(`speaking`)?`speaking`:e.contains(`thinking`)?`thinking`:e.contains(`listening`)?`listening`:`idle`;t!==this.state&&this.setState(t)}updateStatusLabel(e){let t=document.getElementById(`optic-status-label`);t&&(t.textContent={idle:`Standby`,listening:`Listening`,thinking:`Thinking`,speaking:`Speaking`,denied:`Permission denied`}[e],t.dataset.state=e)}onPointerMove=e=>{this.reducedMotionQuery.matches||this.targetPointer.set(_.clamp(e.clientX/window.innerWidth*2-1,-1,1),_.clamp(-(e.clientY/window.innerHeight*2-1),-1,1))};onPointerLeave=()=>{this.targetPointer.set(0,0)};onMotionPreferenceChange=()=>{this.reducedMotionQuery.matches&&this.targetPointer.set(0,0)};animate=()=>{if(this.destroyed)return;this.frameId=requestAnimationFrame(this.animate),this.timer.update();let e=Math.min(this.timer.getDelta(),.1),t=this.timer.getElapsed(),n=this.reducedMotionQuery.matches,r=this.material.uniforms;this.audioEnergy+=(this.targetAudioEnergy-this.audioEnergy)*Math.min(1,e*9),this.pointer.lerp(this.targetPointer,Math.min(1,e*3)),!n&&!this.extracting&&(this.drift=(this.drift+e*.06)%.62);let i=this.state===`thinking`;i&&(this.scanHead+=e*(10-this.lagCool*6));let a=this.seatCount*this.occupancy;for(let r=0;r<this.seatCount;r+=1){let o=+(r<a),s=this.activation[r];this.activation[r]=s+(o-s)*Math.min(1,e*2.4);let c=this.flare[r]*(1-Math.min(1,e*3.5));if(i&&!n){let e=Math.abs((this.scanHead-r)%this.seatCount/4);e<1&&(c=Math.max(c,1-e))}this.toolColumn>=0&&this.column[r]===this.toolColumn&&(c=Math.max(c,.75)),this.missionCount>0&&r>=this.seatCount-this.missionCount&&(c=Math.max(c,.35+.25*Math.sin(t*2.2+r))),this.flare[r]=c}this.activationAttr.needsUpdate=!0,this.flareAttr.needsUpdate=!0;let o=+(this.state===`listening`),s=+(this.state===`denied`),l=this.state===`speaking`||this.state===`listening`?this.audioEnergy:0;r.uTime.value=t,r.uDrift.value=this.drift,r.uAttend.value+=(o-r.uAttend.value)*Math.min(1,e*4),r.uChill.value+=(s-r.uChill.value)*Math.min(1,e*5),r.uVoice.value+=(l-r.uVoice.value)*Math.min(1,e*8),r.uParallax.value.copy(this.pointer),this.bloomPass.strength=.32+l*.28+(this.extracting?-.15:0),this.renderer.setClearColor(new c(328966).lerp(new c(2754822),.07+l*.16),1),this.composer.render()}};function O(e,t){let n=new D(e,t);return{destroy:()=>n.destroy(),setAudioEnergy:e=>n.setAudioEnergy(e),setState:e=>n.setState(e),setToolKind:e=>n.setToolKind(e),playSessionEnd:()=>n.playSessionEnd()}}export{O as createOptic};
//# sourceMappingURL=optic-lmc.js.map
import{A as e,C as t,D as n,M as r,N as i,O as a,T as o,_ as s,a as c,b as l,d as u,h as d,i as f,j as p,k as m,m as h,n as g,o as _,p as v,r as y,s as b,t as x,u as S,v as C,w,x as T,y as E}from"./UnrealBloomPass.js";var D={idle:{bloom:.34,core:1.7,beam:.2,motion:.26},listening:{bloom:.66,core:3.9,beam:1,motion:.52},thinking:{bloom:.24,core:1.05,beam:.32,motion:1.4},speaking:{bloom:.74,core:4.5,beam:.85,motion:.48},denied:{bloom:.12,core:.5,beam:.07,motion:.05}},O={fetch:16726820,execute:16733215,search:15279900,read:16723232},k=9.6,A=10.4,j=`
  varying vec2 vUv;

  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`,M=`
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
`,N=`
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
`,P=`
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
`,F=class{camera=new t(30,1,.1,60);timer=new p;composer;bloomPass;beamMaterial;coreGlow;coreLight=new o(16722456,3.2,8,2);coreMaterial=new T({color:16756890,emissive:16722456,emissiveIntensity:2.2,metalness:0,roughness:.18});container;eye;irisMaterial;lensMaterial=new l({color:9177090,emissive:6947584,emissiveIntensity:.2,metalness:0,roughness:.1,transmission:.3,thickness:1.3,ior:1.52,clearcoat:1,clearcoatRoughness:.09,transparent:!0,opacity:.8,depthWrite:!1});movingRings=[];satellites=[];reducedMotionQuery=window.matchMedia(`(prefers-reduced-motion: reduce)`);renderer;root=new d;scene=new a;targetPointer=new i;audioEnergy=0;beamLevel=.2;destroyed=!1;frameId=0;pointer=new i;resizeObserver;state=`idle`;stateObserver;targetAudioEnergy=0;targetToolPulse=0;toolKind=null;constructor(e,t){this.container=e,this.eye=t,this.renderer=new c({alpha:!1,antialias:!0,powerPreference:`high-performance`}),this.renderer.domElement.className=`optic-webgl-canvas`,this.renderer.outputColorSpace=n,this.renderer.toneMapping=4,this.renderer.toneMappingExposure=.85,this.renderer.setClearColor(131586,1),this.renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,1.75)),this.container.appendChild(this.renderer.domElement),this.scene.fog=new h(131586,.04),this.scene.add(this.root),this.timer.connect(document),this.irisMaterial=new m({uniforms:{uTime:{value:0},uEnergy:{value:0},uState:{value:0},uToolPulse:{value:0}},vertexShader:j,fragmentShader:M,transparent:!0,depthWrite:!1}),this.coreGlow=new m({uniforms:{uIntensity:{value:1}},vertexShader:j,fragmentShader:N,transparent:!0,depthWrite:!1,blending:2}),this.beamMaterial=new m({uniforms:{uTime:{value:0},uEnergy:{value:0},uLevel:{value:.2}},vertexShader:j,fragmentShader:P,transparent:!0,depthWrite:!1,depthTest:!0,blending:2}),this.buildScene(),this.composer=new f(this.renderer),this.composer.addPass(new g(this.scene,this.camera)),this.bloomPass=new x(new i(1,1),.5,.6,.5),this.composer.addPass(this.bloomPass),this.composer.addPass(new y),this.resizeObserver=new ResizeObserver(()=>this.resize()),this.resizeObserver.observe(this.container),this.stateObserver=new MutationObserver(()=>this.syncStateFromDom()),this.stateObserver.observe(this.eye,{attributes:!0,attributeFilter:[`class`]}),window.addEventListener(`pointermove`,this.onPointerMove),window.addEventListener(`blur`,this.onPointerLeave),this.reducedMotionQuery.addEventListener(`change`,this.onMotionPreferenceChange),this.resize(),this.syncStateFromDom(),this.container.closest(`.eye-module`)?.classList.add(`webgl-ready`),this.frameId=requestAnimationFrame(this.animate)}setAudioEnergy(e){this.targetAudioEnergy=s.clamp(e,0,1)}setState(e){this.state=e,this.updateStatusLabel(e)}setToolKind(e){this.toolKind=e,e&&(this.targetToolPulse=1)}destroy(){if(this.destroyed)return;this.destroyed=!0,cancelAnimationFrame(this.frameId),this.resizeObserver.disconnect(),this.stateObserver.disconnect(),window.removeEventListener(`pointermove`,this.onPointerMove),window.removeEventListener(`blur`,this.onPointerLeave),this.reducedMotionQuery.removeEventListener(`change`,this.onMotionPreferenceChange),this.timer.dispose();let e=new Set;this.scene.traverse(t=>{if(!(t instanceof C))return;t.geometry.dispose();let n=Array.isArray(t.material)?t.material:[t.material];for(let t of n)e.add(t)});for(let t of e)t.dispose();this.composer.dispose(),this.renderer.dispose(),this.renderer.domElement.remove(),this.container.closest(`.eye-module`)?.classList.remove(`webgl-ready`)}buildScene(){let t=new _(2759698,1.1),n=new v(16767408,3);n.position.set(-4,4.5,7);let i=new v(16722456,.6);i.position.set(4.5,-2.5,4);let a=new v(10134702,.9);a.position.set(4,-4.5,6),this.scene.add(t,n,i,a);let o=new T({color:9068852,emissive:1313539,emissiveIntensity:1,metalness:.78,roughness:.38}),s=new T({color:4863520,emissive:788227,emissiveIntensity:1,metalness:.8,roughness:.42}),c=new T({color:3685182,emissive:329223,emissiveIntensity:1,metalness:.82,roughness:.34}),u=new l({color:2366226,metalness:.1,roughness:.12,transmission:.55,thickness:.6,ior:1.5,transparent:!0,opacity:.6,depthWrite:!1}),f=new T({color:2818817,emissive:12651524,emissiveIntensity:.5,metalness:.76,roughness:.25}),p=new C(new r(1.72,.13,24,144),c);p.position.z=.32,this.root.add(p);let m=new C(new r(1.9,.035,18,144),o);m.position.z=.36,this.root.add(m);let h=new C(new S(1.58,128),this.irisMaterial);h.position.z=.3,this.root.add(h);let g=new C(new e(1.54,96,48),this.lensMaterial);g.scale.set(1,1,.3),g.position.z=.5,this.root.add(g);let y=new C(new e(.11,40,24),this.coreMaterial);y.position.z=1.05,this.root.add(y),this.coreLight.position.set(0,0,1.45),this.root.add(this.coreLight);let b=new C(new w(.66,.66),this.coreGlow);b.position.z=1,this.root.add(b);let x=new E({color:16772838,transparent:!0,opacity:.2,depthWrite:!1,blending:2}),D=new C(new e(.11,32,20),x);D.scale.set(1.1,.68,.2),D.position.set(-.4,.44,1.05),this.root.add(D);let O=[[2.2,.045,.12,s,-.05,Math.PI*2,`z`],[2.52,.02,-.05,o,.065,Math.PI*2,`z`],[2.86,.06,.06,c,-.04,Math.PI*2,`z`],[3.22,.016,-.16,o,.09,Math.PI*1.62,`z`],[3.58,.05,-.02,u,.03,Math.PI*2,`z`],[3.94,.022,.08,s,-.075,Math.PI*1.8,`z`],[4.32,.014,-.1,o,.055,Math.PI*2,`z`],[4.6,.008,0,f,-.11,Math.PI*1.45,`z`]];for(let[e,t,n,i,a,o,s]of O){let c=new C(new r(e,t,16,160,o),i);c.position.z=n,c.rotation.z=Math.random()*Math.PI*2,c.rotation.x=(Math.random()-.5)*.1,this.root.add(c),this.movingRings.push({object:c,speed:a,axis:s})}let k=new C(new r(3.4,.012,12,200),o);k.rotation.x=Math.PI/2.22,this.root.add(k),this.movingRings.push({object:k,speed:.14,axis:`y`}),this.addCalibrationTicks(o,3.76),this.addCalibrationTicks(c,2.38);for(let[e,t,n]of[[-3.45,.85,-.5],[-4.55,.58,-.85],[-5.5,.4,-1.15],[3.45,.85,-.5],[4.55,.58,-.85],[5.5,.4,-1.15]]){let i=new d,a=new C(new r(t,t*.09,16,96),c),s=new C(new r(t*.66,t*.045,12,72),o),u=new C(new S(t*.92,64),new l({color:1378822,metalness:.2,roughness:.12,transmission:.5,thickness:.5,ior:1.5,transparent:!0,opacity:.55,depthWrite:!1})),f=new C(new S(t*.16,32),new T({color:1835777,emissive:11537412,emissiveIntensity:.9,metalness:.4,roughness:.3}));f.position.z=.02,i.add(a,s,u,f),i.position.set(e,0,n),this.root.add(i),this.satellites.push(i),this.movingRings.push({object:s,speed:e>0?-.22:.22,axis:`z`})}let A=new C(new w(44,3.2),this.beamMaterial);A.position.z=-.55,A.renderOrder=-1,this.root.add(A),this.root.rotation.x=-.02}addCalibrationTicks(e,t){let n=new d,r=new b(.015,.09,.015);for(let i=0;i<72;i+=1){let a=i/72*Math.PI*2,o=i%6==0,s=new C(r,e);s.position.set(Math.cos(a)*t,Math.sin(a)*t,-.06),s.rotation.z=a+Math.PI/2,o&&(s.scale.y=1.9),n.add(s)}this.root.add(n),this.movingRings.push({object:n,speed:t>3?.018:-.024,axis:`z`})}resize(){let{width:e,height:t}=this.container.getBoundingClientRect();if(e<2||t<2)return;this.camera.aspect=e/t;let n=s.degToRad(this.camera.fov/2),r=k/2/Math.tan(n),i=A/2/(Math.tan(n)*this.camera.aspect);this.camera.position.z=Math.max(r,i),this.camera.updateProjectionMatrix(),this.renderer.setSize(e,t,!1),this.composer.setSize(e,t)}syncStateFromDom(){let e=`idle`;this.eye.classList.contains(`denied`)?e=`denied`:this.eye.classList.contains(`listening`)?e=`listening`:this.eye.classList.contains(`speaking`)?e=`speaking`:this.eye.classList.contains(`thinking`)&&(e=`thinking`),this.setState(e);let t=this.eye.classList.contains(`kind-execute`)?`execute`:this.eye.classList.contains(`kind-fetch`)?`fetch`:this.eye.classList.contains(`kind-search`)?`search`:this.eye.classList.contains(`kind-read`)?`read`:null;t!==this.toolKind&&this.setToolKind(t)}updateStatusLabel(e){let t=document.getElementById(`optic-status-label`);t&&(t.textContent={idle:`Standby`,listening:`Listening`,thinking:`Thinking`,speaking:`Speaking`,denied:`Permission denied`}[e],t.dataset.state=e)}onPointerMove=e=>{this.reducedMotionQuery.matches||this.targetPointer.set(s.clamp(e.clientX/window.innerWidth*2-1,-1,1),s.clamp(-(e.clientY/window.innerHeight*2-1),-1,1))};onPointerLeave=()=>{this.targetPointer.set(0,0)};onMotionPreferenceChange=()=>{this.reducedMotionQuery.matches&&(this.targetPointer.set(0,0),this.pointer.set(0,0))};animate=e=>{if(this.destroyed)return;this.frameId=requestAnimationFrame(this.animate),this.timer.update(e);let t=Math.min(this.timer.getDelta(),.05),n=this.timer.getElapsed(),r=D[this.state],i=this.reducedMotionQuery.matches?0:r.motion;this.audioEnergy=s.damp(this.audioEnergy,this.targetAudioEnergy,8,t),this.targetAudioEnergy=s.damp(this.targetAudioEnergy,0,2.6,t),this.targetToolPulse=s.damp(this.targetToolPulse,0,2.8,t),this.pointer.x=s.damp(this.pointer.x,this.targetPointer.x,5,t),this.pointer.y=s.damp(this.pointer.y,this.targetPointer.y,5,t),this.beamLevel=s.damp(this.beamLevel,r.beam,5,t);for(let e of this.movingRings)e.object.rotation[e.axis]+=e.speed*i*t;for(let e=0;e<this.satellites.length;e+=1){let t=this.satellites[e];t&&(t.position.y=this.reducedMotionQuery.matches?0:Math.sin(n*.5+e*1.7)*.05)}let a=1+(this.reducedMotionQuery.matches?0:Math.sin(n*(this.state===`listening`?3.6:1.25))*.01)+this.audioEnergy*.03;this.root.scale.setScalar(s.damp(this.root.scale.x,a,7,t)),this.root.rotation.y=s.damp(this.root.rotation.y,this.pointer.x*.06,5,t),this.root.rotation.x=s.damp(this.root.rotation.x,-.02-this.pointer.y*.05,5,t);let o=this.state===`thinking`?1:this.state===`speaking`?.7:this.state===`listening`?.55:.2;this.irisMaterial.uniforms.uTime.value=n,this.irisMaterial.uniforms.uEnergy.value=this.audioEnergy,this.irisMaterial.uniforms.uState.value=o,this.irisMaterial.uniforms.uToolPulse.value=this.targetToolPulse;let c=this.state===`speaking`&&!this.reducedMotionQuery.matches?(.5+.5*Math.sin(n*7.3)*Math.sin(n*2.9))*.3:0;this.beamMaterial.uniforms.uTime.value=n,this.beamMaterial.uniforms.uEnergy.value=Math.min(1,this.audioEnergy+c),this.beamMaterial.uniforms.uLevel.value=this.beamLevel;let l=Math.max(this.audioEnergy,this.targetToolPulse*.55);this.bloomPass.strength=s.damp(this.bloomPass.strength,r.bloom+l*.42,6,t),this.coreMaterial.emissiveIntensity=s.damp(this.coreMaterial.emissiveIntensity,r.core+l*3.4,7,t),this.lensMaterial.emissiveIntensity=s.damp(this.lensMaterial.emissiveIntensity,.2+r.core*.1+l*.6,7,t),this.coreLight.intensity=s.damp(this.coreLight.intensity,6+r.core*1.5+l*8,7,t),this.coreGlow.uniforms.uIntensity.value=r.core+l*2.1;let d=this.toolKind?O[this.toolKind]:16722456;this.coreMaterial.emissive.lerp(new u(d),Math.min(1,t*4)),this.composer.render()}};function I(e,t){let n=new F(e,t);return{destroy:()=>n.destroy(),setAudioEnergy:e=>n.setAudioEnergy(e),setState:e=>n.setState(e),setToolKind:e=>n.setToolKind(e)}}export{I as createOptic};
//# sourceMappingURL=optic-orrery.js.map
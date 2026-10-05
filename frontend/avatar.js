// ORBIT avatar (Phase 16): a procedural three.js character. No external model files;
// everything is built from primitives so it loads fast (also in the Quest browser).
// createAvatar(el)  — a self-contained view for a web page (the assistant page);
// buildAvatar()     — just the character, to place in any scene (the AR room, Phase 18);
//                     the caller runs its update(now) once per frame.
// API of both: setState("idle"|"listening"|"thinking"|"speaking"), setAlert(bool),
//              gesture("WAVE"|"NOD"|"EXPLAIN"|"THINK"|"SHRUG"|"ASK"|"ALERT"), mouth(0..1),
//              lookAt(x, y) with x, y in [-1, 1] (where the person is, relative to Orbi).
import * as THREE from "three";

const STATE_COLORS = { idle: 0x7aa2ff, listening: 0x4ade80, thinking: 0xfbbf24, speaking: 0x7aa2ff, alert: 0xf87171 };
const ease = (x) => 0.5 - Math.cos(Math.PI * Math.min(1, Math.max(0, x))) / 2;
const lerp = (a, b, t) => a + (b - a) * t;

export function addLights(scene) {
  scene.add(new THREE.HemisphereLight(0xdfe8ff, 0x30343c, 1.6));
  const key = new THREE.DirectionalLight(0xffffff, 1.6);
  key.position.set(2, 3, 3);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0x7aa2ff, 1.1);
  rim.position.set(-2.5, 2, -2);
  scene.add(rim);
}

export function createAvatar(container) {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 50);
  camera.position.set(0, 1.35, 4.3);
  camera.lookAt(0, 1.15, 0);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  container.appendChild(renderer.domElement);
  addLights(scene);
  const avatar = buildAvatar();
  scene.add(avatar.object);

  container.addEventListener("pointermove", (e) => {
    const r = container.getBoundingClientRect();
    avatar.lookAt(((e.clientX - r.left) / r.width - 0.5) * 2, ((e.clientY - r.top) / r.height - 0.5) * 2);
  });
  container.addEventListener("pointerleave", () => avatar.lookAt(0, 0));

  function resize() {
    const w = container.clientWidth || 300, h = container.clientHeight || 300;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(container);
  resize();

  function frame(now) {
    avatar.update(now);
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  return avatar;
}

export function buildAvatar() {
  const holder = new THREE.Group(); // the caller positions this; the body sways inside it
  const mat = (color, opts = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.45, metalness: 0.1, ...opts });
  const shell = mat(0xeef1f6), dark = mat(0x1d2330, { roughness: 0.3 }), accent = mat(0x7aa2ff, { emissive: 0x7aa2ff, emissiveIntensity: 0.35 });

  // ---------------------------------------------------------------- body
  const root = new THREE.Group();
  holder.add(root);
  const shadow = new THREE.Mesh(new THREE.CircleGeometry(0.75, 48), new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.18 }));
  shadow.rotation.x = -Math.PI / 2;
  root.add(shadow);

  const torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.42, 0.55, 8, 24), shell);
  torso.position.y = 0.85;
  torso.scale.set(1, 1, 0.75);
  root.add(torso);
  const badge = new THREE.Mesh(new THREE.TorusGeometry(0.09, 0.022, 12, 32), accent);
  badge.position.set(0, 1.05, 0.31);
  root.add(badge);

  const neck = new THREE.Group();
  neck.position.y = 1.45;
  root.add(neck);
  const head = new THREE.Group();
  head.position.y = 0.33;
  neck.add(head);
  const skull = new THREE.Mesh(new THREE.SphereGeometry(0.4, 40, 32), shell);
  skull.scale.set(1.05, 0.95, 0.95);
  head.add(skull);
  const face = new THREE.Mesh(new THREE.SphereGeometry(0.3, 40, 32), dark); // a glossy visor
  face.position.z = 0.25;
  face.scale.set(1.1, 0.8, 0.5);
  head.add(face);
  const FZ = 0.405; // features float just in front of the visor

  // Eyes: glowing discs with lids (scale.y) and pupils that follow the user.
  const eyes = [-0.12, 0.12].map((x) => {
    const g = new THREE.Group();
    g.position.set(x, 0.04, FZ);
    const glow = new THREE.Mesh(new THREE.CircleGeometry(0.062, 32), new THREE.MeshBasicMaterial({ color: 0xbfe0ff }));
    const pupil = new THREE.Mesh(new THREE.CircleGeometry(0.028, 24), new THREE.MeshBasicMaterial({ color: 0x0b1020 }));
    pupil.position.z = 0.002;
    g.add(glow, pupil);
    head.add(g);
    return { g, glow, pupil };
  });
  const brows = [-0.12, 0.12].map((x) => {
    const b = new THREE.Mesh(new THREE.BoxGeometry(0.11, 0.018, 0.01), new THREE.MeshBasicMaterial({ color: 0xbfe0ff }));
    b.position.set(x, 0.14, FZ);
    head.add(b);
    return b;
  });
  const mouth = new THREE.Mesh(new THREE.CircleGeometry(0.06, 32), new THREE.MeshBasicMaterial({ color: 0xbfe0ff }));
  mouth.position.set(0, -0.1, FZ - 0.01);
  head.add(mouth);
  const ears = [-1, 1].map((s) => {
    const e = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 0.06, 24), accent);
    e.rotation.z = Math.PI / 2;
    e.position.set(s * 0.41, 0.02, 0);
    head.add(e);
    return e;
  });

  // Arms: shoulder pivots so gestures rotate the whole arm.
  const arms = [-1, 1].map((s) => {
    const shoulder = new THREE.Group();
    shoulder.position.set(s * 0.5, 1.28, 0);
    root.add(shoulder);
    const upper = new THREE.Mesh(new THREE.CapsuleGeometry(0.085, 0.42, 6, 16), shell);
    upper.position.y = -0.27;
    shoulder.add(upper);
    const hand = new THREE.Mesh(new THREE.SphereGeometry(0.1, 20, 16), shell);
    hand.position.y = -0.58;
    shoulder.add(hand);
    return { shoulder, side: s };
  });

  // The orbit ring: ORBIT's mark, coloured by state.
  const ringMat = new THREE.MeshBasicMaterial({ color: STATE_COLORS.idle, transparent: true, opacity: 0.85 });
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.95, 0.012, 8, 128), ringMat);
  ring.position.y = 1.15;
  ring.rotation.x = Math.PI / 2.3;
  root.add(ring);
  const moon = new THREE.Mesh(new THREE.SphereGeometry(0.045, 16, 12), ringMat);
  root.add(moon);

  // ---------------------------------------------------------------- state
  let state = "idle", alert = false, mouthTarget = 0, mouthOpen = 0, speakingSince = 0;
  let current = null; // { name, start, dur }
  const look = { x: 0, y: 0, tx: 0, ty: 0 };
  let nextBlink = performance.now() + 2500, blinkStart = 0;
  const color = new THREE.Color(STATE_COLORS.idle);

  // Each gesture returns pose offsets for progress p in [0, 1].
  const GESTURES = {
    WAVE: { dur: 1800, pose: (p) => ({ armR: [-2.6 + Math.sin(p * Math.PI * 6) * 0.35 * (1 - p), 0.25], tilt: 0.12 * Math.sin(p * Math.PI) }) },
    NOD: { dur: 900, pose: (p) => ({ pitch: Math.sin(p * Math.PI * 2) * 0.22 * (1 - p * 0.3), browUp: 0.01 }) },
    EXPLAIN: { dur: 1600, pose: (p) => { const k = Math.sin(p * Math.PI); return { armR: [-0.7 * k, 0.35 * k], armL: [-0.45 * k, -0.25 * k], tilt: 0.05 * k }; } },
    THINK: { dur: 1800, pose: (p) => { const k = Math.sin(Math.min(1, p * 1.4) * Math.PI / 2) * (p > 0.85 ? (1 - p) / 0.15 : 1); return { armR: [-2.1 * k, -0.9 * k], look: [0.5 * k, -0.6 * k], tilt: -0.15 * k, browUp: 0.02 * k }; } },
    SHRUG: { dur: 1300, pose: (p) => { const k = Math.sin(p * Math.PI); return { shrug: 0.09 * k, armR: [-0.3 * k, 0.6 * k], armL: [-0.3 * k, -0.6 * k], tilt: 0.12 * k, browUp: 0.025 * k }; } },
    ASK: { dur: 1500, pose: (p) => { const k = Math.sin(Math.min(1, p * 2) * Math.PI / 2) * (p > 0.8 ? (1 - p) / 0.2 : 1); return { tilt: 0.2 * k, browUp: 0.03 * k, armR: [-0.55 * k, 0.5 * k] }; } },
    ALERT: { dur: 1400, pose: (p) => { const k = Math.sin(p * Math.PI); return { browDown: 0.025 * k, pitch: -0.08 * k, armL: [-0.4 * k, -0.2 * k], armR: [-0.4 * k, 0.2 * k] }; } },
  };

  function update(now) {
    const t = now / 1000;
    let pose = {};
    if (current) {
      const p = (now - current.start) / current.dur;
      if (p >= 1) current = null;
      else pose = GESTURES[current.name].pose(p);
    }
    // idle life: breathing, sway
    const breathe = Math.sin(t * 1.6) * 0.012;
    torso.scale.y = 1 + breathe;
    root.rotation.y = Math.sin(t * 0.35) * 0.06;
    root.position.y = Math.sin(t * 1.6) * 0.008;

    // head follows the pointer, plus gesture offsets
    look.x = lerp(look.x, pose.look ? pose.look[0] : look.tx, 0.08);
    look.y = lerp(look.y, pose.look ? pose.look[1] : look.ty, 0.08);
    head.rotation.y = look.x * 0.35;
    head.rotation.x = look.y * 0.18 + (pose.pitch || 0) + (state === "thinking" ? -0.08 : 0);
    head.rotation.z = (pose.tilt || 0) + (state === "listening" ? 0.08 : 0);
    neck.position.y = 1.45 + (pose.shrug ? -pose.shrug * 0.6 : 0);
    eyes.forEach(({ pupil }) => { pupil.position.x = look.x * 0.018; pupil.position.y = -look.y * 0.014; });

    // brows
    const browY = 0.14 + (pose.browUp || 0) - (pose.browDown || 0) + (state === "listening" ? 0.012 : 0);
    brows[0].position.y = brows[1].position.y = browY;
    brows[0].rotation.z = pose.browDown ? -0.35 : pose.browUp ? 0.15 : 0;
    brows[1].rotation.z = -brows[0].rotation.z;

    // arms (x: forward swing, z: outward)
    const rest = (s) => [Math.sin(t * 1.6 + s) * 0.03, s * 0.12];
    arms.forEach(({ shoulder, side }) => {
      const target = (side > 0 ? pose.armR : pose.armL) || rest(side);
      shoulder.rotation.x = lerp(shoulder.rotation.x, target[0], 0.2);
      shoulder.rotation.z = lerp(shoulder.rotation.z, side > 0 ? target[1] : target[1], 0.2);
      shoulder.position.y = 1.28 + (pose.shrug || 0);
    });

    // blink
    if (now > nextBlink) { blinkStart = now; nextBlink = now + 2500 + Math.random() * 3500; }
    const b = (now - blinkStart) / 160;
    const lid = b < 1 ? Math.abs(Math.cos(b * Math.PI)) : 1;
    eyes.forEach(({ g }) => { g.scale.y = Math.max(0.08, lid) * (state === "thinking" ? 0.8 : 1); });

    // mouth: driven by speech when speaking, else a small smile line
    if (state === "speaking") {
      const jitter = 0.25 + 0.25 * Math.abs(Math.sin(t * 17 + Math.sin(t * 7)));
      mouthTarget = Math.max(mouthTarget * 0.9, jitter * (now - speakingSince > 150 ? 1 : 0));
    } else {
      mouthTarget = 0;
    }
    mouthOpen = lerp(mouthOpen, mouthTarget, 0.35);
    mouth.scale.set(1 - mouthOpen * 0.25, 0.18 + mouthOpen * 1.1, 1);

    // ring colour and motion
    color.lerp(new THREE.Color(alert ? STATE_COLORS.alert : STATE_COLORS[state]), 0.08);
    ringMat.color.copy(color);
    accent.color.copy(color);
    accent.emissive.copy(color);
    const speed = state === "thinking" ? 2.4 : state === "listening" ? 1.4 : 0.6;
    ring.rotation.z = t * speed * 0.3;
    const a = t * speed;
    moon.position.set(Math.cos(a) * 0.95, 1.15 + Math.sin(a) * 0.95 * Math.cos(Math.PI / 2.3), Math.sin(a) * 0.95 * Math.sin(Math.PI / 2.3));
    ringMat.opacity = state === "listening" ? 0.6 + 0.35 * Math.abs(Math.sin(t * 4)) : 0.85;
    ears.forEach((e) => { e.scale.y = state === "listening" ? 1.25 : 1; });
  }

  return {
    object: holder,
    update,
    lookAt(x, y) { look.tx = Math.max(-1, Math.min(1, x)); look.ty = Math.max(-1, Math.min(1, y)); },
    setState(next) {
      if (next === "speaking" && state !== "speaking") speakingSince = performance.now();
      state = STATE_COLORS[next] ? next : "idle";
    },
    setAlert(on) { alert = !!on; },
    gesture(name) {
      if (GESTURES[name]) current = { name, start: performance.now(), dur: GESTURES[name].dur };
    },
    mouth(v) { mouthTarget = Math.max(mouthTarget, Math.min(1, v)); },
  };
}

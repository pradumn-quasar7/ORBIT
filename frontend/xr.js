// ORBIT on Meta Quest (Phase 18): WebXR passthrough AR with ORBIT places pinned to the
// real room (persistent anchors), live evidence labels over them, Orbi standing in the
// room, voice, and consent buttons. On a computer the same scene runs as a 3D preview.
// Quest Browser gives web pages no camera pixels, so perception stays with the webcam
// client; the headset is ORBIT's window into the room (ADR-043).
import * as THREE from "three";
import { addLights, buildAvatar } from "./avatar.js";

const core = window.OrbitXRCore;
const $ = (id) => document.getElementById(id);
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

const state = {
  places: [], conversation: null, pending: null, session: null, refSpace: null,
  anchors: new Map(), // place id → XRAnchor (this session)
  missing: new Set(), // pinned elsewhere / not found in this room
  placing: null, // place id waiting for the user to point and pull the trigger
  pendingPlacement: null, // { id, point } — created in the next XR frame
  hitSources: new Map(), // XRInputSource → XRHitTestSource
  inputs: [null, null], // controller index → XRInputSource
  active: 1, // controller the user last used
  voice: null, busy: false, laidOut: false,
};

// ------------------------------------------------------------------ renderer
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.xr.enabled = true;
$("view").appendChild(renderer.domElement);
const scene = new THREE.Scene();
addLights(scene);
const camera = new THREE.PerspectiveCamera(55, 1, 0.05, 60);
function framePreview(aspect) {
  // Back off on narrow screens so the whole arc of places stays in view.
  const back = 1.25 + Math.max(0, 1.6 - aspect) * 1.6;
  camera.position.set(0, 1.55, back);
  camera.lookAt(0, 1.05, -1.0);
}
framePreview(1.6);

// Preview room (computer only): floor grid; the whole preview turns with a drag.
const world = new THREE.Group();
scene.add(world);
const floor = new THREE.GridHelper(8, 32, 0x3a4250, 0x232934);
world.add(floor);

// ------------------------------------------------------------------- panels
const PPM = 1400; // canvas pixels per metre — crisp at arm's length in the headset

function panel(w, h) {
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(w * PPM);
  canvas.height = Math.round(h * PPM);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, transparent: true }));
  return { mesh, canvas, ctx: canvas.getContext("2d"), tex };
}

function rounded(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function fit(ctx, text, max) {
  if (ctx.measureText(text).width <= max) return text;
  let t = text;
  while (t.length > 1 && ctx.measureText(t + "…").width > max) t = t.slice(0, -1);
  return t + "…";
}

function wrap(ctx, text, max, maxLines) {
  const words = String(text).split(/\s+/);
  const lines = [];
  let line = "";
  for (const w of words) {
    const next = line ? `${line} ${w}` : w;
    if (ctx.measureText(next).width > max && line) { lines.push(line); line = w; } else line = next;
  }
  if (line) lines.push(line);
  if (lines.length > maxLines) {
    const kept = lines.slice(0, maxLines);
    kept[maxLines - 1] = fit(ctx, kept[maxLines - 1] + " …", max);
    return kept;
  }
  return lines;
}

function drawCard(p, card) {
  const { ctx, canvas } = p;
  const W = canvas.width, H = canvas.height, pad = 22;
  ctx.clearRect(0, 0, W, H);
  rounded(ctx, 2, 2, W - 4, H - 4, 26);
  ctx.fillStyle = "rgba(14,17,22,0.86)";
  ctx.fill();
  ctx.lineWidth = 4;
  ctx.strokeStyle = card.tone;
  ctx.stroke();
  ctx.fillStyle = card.tone;
  ctx.fillRect(pad, pad + 4, 10, 46);
  ctx.fillStyle = "#e7eaee";
  ctx.font = "600 44px system-ui, sans-serif";
  ctx.fillText(fit(ctx, card.title, W - 3 * pad - 10), pad + 24, pad + 42);
  ctx.fillStyle = "#9aa4b2";
  ctx.font = "28px system-ui, sans-serif";
  ctx.fillText(fit(ctx, card.subtitle, W - 2 * pad), pad, pad + 92);
  let y = pad + 146;
  ctx.font = "31px system-ui, sans-serif";
  for (const line of card.lines) {
    ctx.fillStyle = line.tone;
    ctx.beginPath();
    ctx.arc(pad + 9, y - 10, 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#e7eaee";
    ctx.fillText(fit(ctx, line.text, W - 2 * pad - 30), pad + 30, y);
    y += 46;
  }
  if (card.more) {
    ctx.fillStyle = "#9aa4b2";
    ctx.fillText(`+ ${card.more} more`, pad + 30, y);
  }
  p.tex.needsUpdate = true;
}

function drawBubble(p, text, tone = "#7aa2ff") {
  const { ctx, canvas } = p;
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  if (!text) { p.tex.needsUpdate = true; p.mesh.visible = false; return; }
  p.mesh.visible = true;
  ctx.font = "34px system-ui, sans-serif";
  const lines = wrap(ctx, text, W - 60, 4);
  const h = 40 + lines.length * 46;
  rounded(ctx, 2, H - h - 2, W - 4, h, 24);
  ctx.fillStyle = "rgba(14,17,22,0.9)";
  ctx.fill();
  ctx.lineWidth = 4;
  ctx.strokeStyle = tone;
  ctx.stroke();
  ctx.fillStyle = "#e7eaee";
  lines.forEach((l, i) => ctx.fillText(l, 30, H - h + 52 + i * 46));
  p.tex.needsUpdate = true;
}

function drawButton(p, label, { hover = false, kind = "" } = {}) {
  const { ctx, canvas } = p;
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  rounded(ctx, 2, 2, W - 4, H - 4, 22);
  ctx.fillStyle = kind === "yes" ? (hover ? "#22c55e" : "#16a34a") : kind === "no" ? (hover ? "#ef4444" : "#b91c1c")
    : hover ? "rgba(122,162,255,0.95)" : "rgba(20,24,31,0.9)";
  ctx.fill();
  ctx.lineWidth = 3;
  ctx.strokeStyle = "#7aa2ff";
  ctx.stroke();
  ctx.fillStyle = hover && !kind ? "#0b0e12" : "#e7eaee";
  ctx.font = "600 32px system-ui, sans-serif";
  ctx.fillText(fit(ctx, label, W - 40), 22, H / 2 + 11);
  p.tex.needsUpdate = true;
}

// --------------------------------------------------------------------- Orbi
const orbi = buildAvatar();
const orbiGroup = new THREE.Group();
orbi.object.scale.setScalar(0.55);
orbiGroup.add(orbi.object);
const bubble = panel(0.62, 0.26);
bubble.mesh.position.set(0, 1.42, 0);
orbiGroup.add(bubble.mesh);
const orbiHit = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.3, 1.1), new THREE.MeshBasicMaterial({ visible: false }));
orbiHit.position.y = 0.55;
orbiHit.userData = { action: "talk" };
orbiGroup.add(orbiHit);
world.add(orbiGroup);
drawBubble(bubble, "");

let speechChain = Promise.resolve();
const synth = "speechSynthesis" in window ? window.speechSynthesis : null;
function say(text, gesture, tone) {
  drawBubble(bubble, text, tone);
  if (gesture) orbi.gesture(gesture);
  orbi.setAlert(gesture === "ALERT");
  speechChain = speechChain.then(() => new Promise((resolve) => {
    if (!synth && text) return serverVoice(text).then(resolve);
    if (!synth || !text) { orbi.setState("speaking"); setTimeout(() => { orbi.setState("idle"); resolve(); }, Math.min(5000, 500 + text.length * 40)); return; }
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.03;
    u.onstart = () => orbi.setState("speaking");
    u.onboundary = () => orbi.mouth(0.6);
    u.onend = u.onerror = () => { orbi.setState("idle"); resolve(); };
    synth.speak(u);
  }));
}

// The Quest browser has no speechSynthesis: the ORBIT server (your computer) speaks
// for Orbi and the headset plays the audio, mouth moving with the sound.
let audioCtx = null;
function serverVoice(text) {
  return new Promise((resolve) => {
    const audio = new Audio(`/speech?text=${encodeURIComponent(text.slice(0, 400))}`);
    let raf = 0;
    const done = () => { cancelAnimationFrame(raf); orbi.setState("idle"); resolve(); };
    audio.onplay = () => {
      orbi.setState("speaking");
      try {
        audioCtx = audioCtx || new AudioContext();
        const an = audioCtx.createAnalyser();
        an.fftSize = 512;
        const src = audioCtx.createMediaElementSource(audio);
        src.connect(an);
        an.connect(audioCtx.destination);
        const data = new Float32Array(an.fftSize);
        const tick = () => {
          an.getFloatTimeDomainData(data);
          let s = 0;
          for (const v of data) s += v * v;
          orbi.mouth(Math.min(1, Math.sqrt(s / data.length) * 8));
          raf = requestAnimationFrame(tick);
        };
        tick();
      } catch { /* audio still plays; the mouth uses its default motion */ }
    };
    audio.onended = done;
    audio.onerror = done;
    audio.play().catch(done);
  });
}

// -------------------------------------------------------------- place cards
const cards = new Map(); // place id → { group, panel, hit }
const CARD_W = 0.42, CARD_H = 0.36;

function cardFor(place) {
  let c = cards.get(place.id);
  if (!c) {
    const p = panel(CARD_W, CARD_H);
    const group = new THREE.Group();
    p.mesh.position.y = 0.26; // float above the pinned point
    p.mesh.userData = { action: "place", id: place.id };
    group.add(p.mesh);
    const pin = new THREE.Mesh(new THREE.SphereGeometry(0.018, 16, 12), new THREE.MeshBasicMaterial({ color: 0x7aa2ff }));
    group.add(pin);
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.003, 0.003, 0.08), new THREE.MeshBasicMaterial({ color: 0x7aa2ff, transparent: true, opacity: 0.7 }));
    stem.position.y = 0.04;
    group.add(stem);
    c = { group, panel: p, pin };
    group.visible = !state.session; // in AR a label appears only once its anchor is found
    cards.set(place.id, c);
    world.add(group);
  }
  const card = core.placeCard(place, Date.now());
  drawCard(c.panel, card);
  c.pin.material.color.set(card.tone);
  return c;
}

function layoutPreview() {
  // Computer preview: places on an arc around Orbi, as if around a room.
  const list = core.pinnable(state.places).filter((p) => p.items.length || p.xr);
  list.forEach((place, i) => {
    const c = cardFor(place);
    const a = list.length === 1 ? 0 : (-0.75 + (1.5 * i) / (list.length - 1));
    c.group.position.set(Math.sin(a) * 1.35, 1.12, -Math.cos(a) * 1.35);
    c.group.visible = true;
  });
  orbiGroup.position.set(0, 0, -0.5); // in front, below the labels
  for (const [id, c] of cards) if (!list.find((p) => p.id === id)) c.group.visible = false;
}

// -------------------------------------------------------------------- menu
const menu = new THREE.Group();
let buttons = [];
function buildMenu() {
  buttons.forEach((b) => menu.remove(b.mesh));
  const items = core.menuItems(state.places);
  if (state.pending) {
    items.unshift({ action: "no", label: "No", kind: "no" }, { action: "yes", label: "Yes, authorize", kind: "yes" });
  }
  buttons = items.map((item, i) => {
    const p = panel(0.3, 0.055);
    p.mesh.position.set(0, -i * 0.064, 0);
    p.mesh.userData = { ...item };
    drawButton(p, item.label, { kind: item.kind });
    menu.add(p.mesh);
    return { ...p, item, hover: false };
  });
}

// ------------------------------------------------------------- controllers
const raycaster = new THREE.Raycaster();
const tmp = new THREE.Matrix4();
const controllers = [0, 1].map((i) => {
  const c = renderer.xr.getController(i);
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, -1)]),
    new THREE.LineBasicMaterial({ color: 0x7aa2ff, transparent: true, opacity: 0.8 }));
  line.scale.z = 1.5;
  c.add(line);
  c.addEventListener("connected", (e) => { state.inputs[i] = e.data; requestHitSource(e.data); });
  c.addEventListener("disconnected", () => { state.inputs[i] = null; });
  c.addEventListener("select", () => { state.active = i; onSelect(c); });
  c.addEventListener("squeezestart", () => { state.active = i; startTalking(); });
  c.addEventListener("squeezeend", () => { if (state.voice && state.voice.listening && state.voice.engine !== "local") state.voice.toggle(); });
  scene.add(c);
  return c;
});
const reticle = new THREE.Mesh(new THREE.RingGeometry(0.035, 0.05, 32).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial({ color: 0x4ade80 }));
reticle.visible = false;
scene.add(reticle);

function rayFrom(c) {
  tmp.identity().extractRotation(c.matrixWorld);
  raycaster.ray.origin.setFromMatrixPosition(c.matrixWorld);
  raycaster.ray.direction.set(0, 0, -1).applyMatrix4(tmp);
  return raycaster;
}

function pickables() {
  return [...buttons.map((b) => b.mesh), ...[...cards.values()].filter((c) => c.group.visible).map((c) => c.panel.mesh), orbiHit];
}

function onSelect(controller) {
  const hit = rayFrom(controller).intersectObjects(pickables(), false)[0];
  if (hit) return act(hit.object.userData);
  if (state.placing) {
    state.pendingPlacement = { id: state.placing, point: reticle.position.toArray() };
  }
}

async function act(data) {
  switch (data.action) {
    case "talk": return startTalking();
    case "exit": return state.session && state.session.end();
    case "pin": {
      state.placing = data.id;
      const place = state.places.find((p) => p.id === data.id);
      say(`Point at where ${place ? place.name : data.id} really is, and pull the trigger.`, "EXPLAIN");
      return;
    }
    case "place": {
      const place = state.places.find((p) => p.id === data.id);
      if (place) say(core.placeSpeech(place, Date.now()), "EXPLAIN");
      return;
    }
    case "yes": return send("yes");
    case "no": return send("no");
    default: return;
  }
}

function requestHitSource(inputSource) {
  const s = state.session;
  if (!s || !s.requestHitTestSource || !inputSource || state.hitSources.has(inputSource)) return;
  s.requestHitTestSource({ space: inputSource.targetRaySpace }).then((src) => state.hitSources.set(inputSource, src)).catch(() => {});
}

// Where the active controller points: hit test, else a detected table/wall, else 1 m out.
function placementPoint(frame) {
  const input = state.inputs[state.active];
  const src = input && state.hitSources.get(input);
  if (src) {
    const r = frame.getHitTestResults(src)[0];
    const pose = r && r.getPose(state.refSpace);
    if (pose) return [pose.transform.position.x, pose.transform.position.y, pose.transform.position.z];
  }
  const c = controllers[state.active];
  const ray = rayFrom(c).ray;
  const planes = [];
  if (frame.detectedPlanes) {
    frame.detectedPlanes.forEach((plane) => {
      const pose = frame.getPose(plane.planeSpace, state.refSpace);
      if (pose) planes.push({ matrix: Array.from(pose.transform.matrix), polygon: plane.polygon.map((p) => ({ x: p.x, z: p.z })), label: plane.semanticLabel });
    });
  }
  return core.placementTarget(ray.origin.toArray(), ray.direction.toArray(), planes, 1.0).point;
}

async function createAnchor(frame, { id, point }) {
  state.placing = null;
  reticle.visible = false;
  const place = state.places.find((p) => p.id === id);
  try {
    const pose = new XRRigidTransform({ x: point[0], y: point[1], z: point[2] }, { x: 0, y: 0, z: 0, w: 1 });
    const anchor = await frame.createAnchor(pose, state.refSpace);
    let handle = null;
    if (anchor.requestPersistentHandle) {
      if (place && place.xr && state.session.deletePersistentAnchor) state.session.deletePersistentAnchor(place.xr.handle).catch(() => {});
      handle = await anchor.requestPersistentHandle();
    }
    state.anchors.set(id, anchor);
    state.missing.delete(id);
    if (handle) await api("PUT", `/xr/places/${encodeURIComponent(id)}`, { handle, device: "quest" });
    say(handle ? `${place ? place.name : id} is pinned here. I'll find it next time.` : `Pinned for this session only: this browser can't save anchors.`, "NOD");
    await refreshPlaces();
  } catch (err) {
    say(`I couldn't pin that: ${err.message}`, "ALERT");
  }
}

async function restoreAnchors() {
  const s = state.session;
  if (!s || !s.restorePersistentAnchor) return;
  for (const place of state.places) {
    if (!place.xr || state.anchors.has(place.id) || state.missing.has(place.id)) continue;
    state.missing.add(place.id); // until found
    s.restorePersistentAnchor(place.xr.handle)
      .then((anchor) => { state.anchors.set(place.id, anchor); state.missing.delete(place.id); })
      .catch(() => { /* pinned in another room or the space was reset */ });
  }
}

// ---------------------------------------------------------------- the loop
const viewer = new THREE.Vector3();
const viewDir = new THREE.Vector3();
let dragging = null;

function loop(t, frame) {
  orbi.update(t);
  if (frame && state.session) {
    const xrCam = renderer.xr.getCamera();
    xrCam.getWorldPosition(viewer);
    xrCam.getWorldDirection(viewDir);
    if (!state.laidOut) layoutAR();
    if (state.pendingPlacement) {
      const p = state.pendingPlacement;
      state.pendingPlacement = null;
      createAnchor(frame, p);
    }
    if (state.placing) {
      reticle.position.fromArray(placementPoint(frame));
      reticle.visible = true;
    }
    for (const [id, anchor] of state.anchors) {
      const c = cards.get(id);
      const pose = c && frame.getPose(anchor.anchorSpace, state.refSpace);
      if (!pose) continue;
      c.group.visible = true;
      c.group.position.set(pose.transform.position.x, pose.transform.position.y, pose.transform.position.z);
    }
    followMenu();
    hover();
  }
  // Labels and Orbi turn to face whoever is looking.
  const eye = state.session ? viewer : camera.getWorldPosition(new THREE.Vector3());
  for (const c of cards.values()) {
    if (!c.group.visible) continue;
    const pos = c.group.getWorldPosition(new THREE.Vector3());
    c.group.rotation.y = core.faceYaw(pos.toArray(), eye.toArray()) - world.rotation.y;
  }
  const op = orbiGroup.getWorldPosition(new THREE.Vector3());
  orbiGroup.rotation.y = core.faceYaw(op.toArray(), eye.toArray()) - world.rotation.y;
  orbi.lookAt(0, -0.2);
  renderer.render(scene, camera);
}

function layoutAR() {
  // Orbi stands on the floor 1.6 m ahead; the menu floats at the left hand.
  state.laidOut = true;
  const ahead = new THREE.Vector3(viewDir.x, 0, viewDir.z).normalize();
  orbiGroup.position.set(viewer.x + ahead.x * 1.6, 0, viewer.z + ahead.z * 1.6);
  menu.visible = true;
  placeMenu(true);
}

function placeMenu(force) {
  const ahead = new THREE.Vector3(viewDir.x, 0, viewDir.z).normalize();
  const left = new THREE.Vector3(ahead.z, 0, -ahead.x);
  const target = new THREE.Vector3(viewer.x + ahead.x * 0.65 + left.x * 0.32, viewer.y - 0.05, viewer.z + ahead.z * 0.65 + left.z * 0.32);
  if (force) menu.position.copy(target);
  else menu.position.lerp(target, 0.06);
  menu.rotation.y = core.faceYaw(menu.position.toArray(), viewer.toArray());
}

function followMenu() {
  // Lazy follow: the menu comes back only when it has drifted out of reach or view.
  const to = menu.position.clone().sub(viewer);
  const angle = to.clone().setY(0).angleTo(new THREE.Vector3(viewDir.x, 0, viewDir.z));
  if (to.length() > 1.4 || angle > 1.1) placeMenu(false);
}

function hover() {
  const hits = new Set();
  for (const c of controllers) {
    const h = rayFrom(c).intersectObjects(buttons.map((b) => b.mesh), false)[0];
    if (h) hits.add(h.object);
  }
  for (const b of buttons) {
    const on = hits.has(b.mesh);
    if (on !== b.hover) { b.hover = on; drawButton(b, b.item.label, { hover: on, kind: b.item.kind }); }
  }
}

// ---------------------------------------------------------------- session
async function enterAR() {
  if (state.session) return;
  try {
    const session = await navigator.xr.requestSession("immersive-ar", {
      requiredFeatures: ["local-floor"],
      optionalFeatures: ["anchors", "plane-detection", "hit-test", "hand-tracking"],
    });
    state.session = session;
    renderer.xr.setReferenceSpaceType("local-floor");
    await renderer.xr.setSession(session);
    state.refSpace = renderer.xr.getReferenceSpace();
    state.laidOut = false;
    world.rotation.y = 0;
    floor.visible = false; // the real floor is visible through passthrough
    for (const c of cards.values()) c.group.visible = false; // until their anchors are found
    scene.add(menu);
    buildMenu();
    session.addEventListener("inputsourceschange", (e) => e.added.forEach(requestHitSource));
    session.addEventListener("end", leaveAR);
    $("mode").textContent = "in the headset";
    await restoreAnchors();
    const unpinned = state.places.filter((p) => !p.xr).length;
    say(state.places.some((p) => p.xr) ? "Welcome back. Finding your pinned places." :
      `Hi! Use the menu to pin ${unpinned ? "your places" : "places"} to the real room.`, "WAVE");
  } catch (err) {
    status(`Could not start AR: ${err.message}`, "error");
  }
}

function leaveAR() {
  state.session = null;
  state.anchors.clear();
  state.missing.clear();
  state.hitSources.clear();
  state.placing = null;
  reticle.visible = false;
  scene.remove(menu);
  floor.visible = true;
  $("mode").textContent = "preview";
  layoutPreview();
}

// ------------------------------------------------------------------- voice
async function setupVoice() {
  try {
    const { Voice, explain } = await import("./voice.js");
    state.voice = new Voice({
      onFinal(text) { send(text); },
      onState(s) {
        if (s === "listening") { orbi.setState("listening"); drawBubble(bubble, "Listening…", "#4ade80"); }
        else if (s === "transcribing" || s === "loading") orbi.setState("thinking");
        else if (!state.busy) orbi.setState("idle");
      },
      onError(code, message) { say(message, "SHRUG", "#f87171"); status(message, "error"); },
      onInfo(text) { status(text, ""); },
    });
    if (store.get("orbit.voice")) state.voice.engine = store.get("orbit.voice");
  } catch {
    state.voice = null;
  }
}

// Realtime conversation (Phase 19): with a Gemini key on the ORBIT server, Talk opens a
// live, interruptible conversation; otherwise one utterance at a time (voice.js).
const DEVICE = /OculusBrowser|Quest/.test(navigator.userAgent) ? "quest" : "mac";
let liveAvailable = false;
let liveEngine = null; // "groq" | "gemini"
let liveSession = null;

async function startTalking() {
  if (liveAvailable) return toggleLive();
  if (!state.voice) return say("Voice isn't available in this browser.", "SHRUG");
  if (synth) synth.cancel();
  state.voice.toggle();
}

async function toggleLive() {
  if (liveSession) { liveSession.stop(); return; }
  const Engine = liveEngine === "groq" ? (await import("./conversation.js")).HostedConversation : (await import("./live.js")).LiveSession;
  if (!state.conversation) await startConversation();
  let heardLine = "";
  liveSession = new Engine({
    device: DEVICE,
    userId: $("user").value.trim() || "operator",
    conversationId: state.conversation.id,
    on: {
      state(s) {
        if (s === "connecting") drawBubble(bubble, "Connecting…", "#9aa4b2");
        if (s === "listening") { orbi.setState("listening"); if (!heardLine) drawBubble(bubble, "I'm listening — just talk.", "#4ade80"); }
        if (s === "thinking") orbi.setState("thinking");
        if (s === "speaking") orbi.setState("speaking");
        if (s === "closed") { liveSession = null; orbi.setState("idle"); drawBubble(bubble, "Live conversation ended. Talk again any time.", "#9aa4b2"); }
      },
      userText(text) { heardLine = text; drawBubble(bubble, `“${text}”`, "#9aa4b2"); },
      orbiText(text, final) { drawBubble(bubble, text); if (final) heardLine = ""; },
      tool(name, args, result) {
        if (!result.ok) { orbi.gesture("SHRUG"); status(result.error, "error"); }
        else if (result.waiting_for_user_consent) { orbi.gesture("ASK"); state.pending = { summary: result.waiting_for_user_consent }; buildMenu(); }
        else orbi.gesture("NOD");
        if (name === "orbit") soon(); // the world model may have changed
      },
      level(v) { if (v > 0.02) orbi.mouth(v); },
      error(message) { status(message, "error"); drawBubble(bubble, message, "#f87171"); },
    },
  });
  try {
    await liveSession.start();
    if (liveSession && liveSession.conversationId !== state.conversation.id) state.conversation = { id: liveSession.conversationId };
  } catch (err) {
    say(`I can't start the live conversation: ${err.message}`, "ALERT", "#f87171");
    if (liveSession) liveSession.stop();
  }
}

// -------------------------------------------------------------- assistant
async function api(method, path, body) {
  const res = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
  return data;
}

let assistantLive = null;
async function startConversation() {
  const user = $("user").value.trim() || "operator";
  store.set("orbit.user", user);
  state.conversation = await api("POST", "/assistant/conversations", { user_id: user });
  if (assistantLive) assistantLive.close();
  assistantLive = OrbitLive.connect(`/stream?topics=assistant&conversation=${encodeURIComponent(state.conversation.id)}`, {
    onMessage(msg) {
      if (msg.type !== "NOTICE") return;
      if (liveSession) { liveSession.inform(msg.data.speech || msg.data.reply); orbi.gesture(msg.data.gesture); }
      else say(msg.data.speech || msg.data.reply, msg.data.gesture);
    },
  });
}

async function send(text) {
  if (!text || state.busy) return;
  state.busy = true;
  orbi.setState("thinking");
  drawBubble(bubble, `“${text}”`, "#9aa4b2");
  try {
    if (!state.conversation) await startConversation();
    let turn;
    try {
      turn = await api("POST", `/assistant/conversations/${encodeURIComponent(state.conversation.id)}/messages`, { text });
    } catch (err) {
      if (!/not found/i.test(err.message)) throw err;
      await startConversation(); // the server restarted
      turn = await api("POST", `/assistant/conversations/${encodeURIComponent(state.conversation.id)}/messages`, { text });
    }
    const hadPending = !!state.pending;
    state.pending = turn.pending;
    if (state.session && hadPending !== !!state.pending) buildMenu(); // show / hide Yes and No
    say(turn.speech || turn.reply, turn.gesture, turn.gesture === "ALERT" ? "#f87171" : undefined);
  } catch (err) {
    say(`Something went wrong: ${err.message}`, "ALERT", "#f87171");
  } finally {
    state.busy = false;
  }
}

// ------------------------------------------------------------- places data
let refreshing = null;
async function refreshPlaces() {
  try {
    state.places = await api("GET", "/xr/places");
  } catch (err) {
    status(`Cannot load places: ${err.message}`, "error");
    return;
  }
  for (const place of state.places) cardFor(place);
  if (state.session) {
    await restoreAnchors();
    buildMenu();
  } else {
    layoutPreview();
  }
  renderPlaceList();
}

function soon() {
  clearTimeout(refreshing);
  refreshing = setTimeout(refreshPlaces, 300);
}

function renderPlaceList() {
  const list = $("places");
  list.replaceChildren();
  const pinned = state.places.filter((p) => p.xr).length;
  $("pinned-count").textContent = `${pinned} of ${state.places.length} pinned in a room`;
  for (const p of state.places) {
    const li = document.createElement("li");
    const grow = document.createElement("span");
    grow.className = "grow";
    grow.textContent = `${p.name} — ${core.placeCard(p, Date.now()).subtitle}`;
    li.append(grow);
    const badge = document.createElement("span");
    badge.className = `badge ${p.xr ? "OBSERVED" : "UNKNOWN"}`;
    badge.textContent = p.xr ? "pinned" : "not pinned";
    li.append(badge);
    if (p.xr) {
      const un = document.createElement("button");
      un.type = "button";
      un.className = "link";
      un.textContent = "unpin";
      un.addEventListener("click", async () => { await api("DELETE", `/xr/places/${encodeURIComponent(p.id)}`); refreshPlaces(); });
      li.append(un);
    }
    list.append(li);
  }
}

// ------------------------------------------------------------- page setup
function status(text, kind) {
  const el = $("xr-status");
  el.textContent = text;
  el.className = `xr-status ${kind || ""}`;
}

function resize() {
  const v = $("view");
  const w = v.clientWidth || 600, h = v.clientHeight || 420;
  if (!renderer.xr.isPresenting) renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  framePreview(camera.aspect);
}
new ResizeObserver(resize).observe($("view"));
resize();

// Drag to look around the preview.
$("view").addEventListener("pointerdown", (e) => { dragging = { x: e.clientX, rot: world.rotation.y }; $("view").setPointerCapture(e.pointerId); });
$("view").addEventListener("pointermove", (e) => { if (dragging) world.rotation.y = dragging.rot + (e.clientX - dragging.x) * 0.006; });
$("view").addEventListener("pointerup", () => { dragging = null; });

async function connectPanel() {
  const body = $("connect-body");
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
  let info = { enabled: false };
  try { info = await api("GET", "/xr/pairing"); } catch { /* older server */ }
  body.replaceChildren();
  const p = (text, cls) => { const el = document.createElement("p"); if (cls) el.className = cls; el.textContent = text; body.append(el); return el; };
  if (!local) {
    p("✓ This headset is paired with ORBIT.", "xr-status ok");
    return;
  }
  if (!info.enabled) {
    p("The headset reaches this computer over Wi-Fi, and WebXR needs HTTPS. Restart ORBIT for the network:");
    const pre = document.createElement("pre");
    pre.className = "pair-url";
    pre.textContent = "scripts/run_demo.sh --lan";
    body.append(pre);
    p("Then reload this page: the address and a pairing code appear here.", "hint");
    return;
  }
  p("1. Put the Quest on the same Wi-Fi and open this address in its browser:");
  p(info.url, "pair-url");
  p("2. It warns about the certificate once (it is made by this computer): choose Advanced → Proceed.", "hint");
  p("3. Type this pairing code:");
  p(info.code || "—", "pair-code");
  p("4. Press Enter AR in the headset.", "hint");
}

async function init() {
  const saved = store.get("orbit.user");
  if (saved) $("user").value = saved;
  $("user").addEventListener("change", () => { state.conversation = null; });
  renderer.setAnimationLoop(loop);
  connectPanel();
  await refreshPlaces();
  setupVoice();
  import("./conversation.js").then(async ({ voiceStatus }) => {
    const st = await voiceStatus();
    liveEngine = st && st.engines.groq ? "groq" : st && st.engines.gemini ? "gemini" : null;
    liveAvailable = !!liveEngine;
    if (liveAvailable) status("Realtime conversation is on: press the grip once in AR (or Talk) and just talk. Press again to stop.", "ok");
  });
  OrbitLive.connect("/stream?topics=world,observation", {
    onMessage: soon,
    onStatus(s) { OrbitLive.pill($("live"), s); if (s === "live") soon(); },
  });
  $("mic").addEventListener("click", async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true }); // ask now: prompts may not show inside AR
      stream.getTracks().forEach((t) => t.stop());
      $("mic").textContent = "Microphone allowed";
      $("mic").disabled = true;
    } catch (err) {
      status(`Microphone: ${err.message}`, "error");
    }
  });
  say("Hi! I'm Orbi. Pin your places and I'll keep track of them.", "WAVE");
  const supported = navigator.xr ? await navigator.xr.isSessionSupported("immersive-ar").catch(() => false) : false;
  if (supported) {
    $("enter").disabled = false;
    $("enter").addEventListener("click", enterAR);
    status("Mixed reality is available. Allow the microphone, then press Enter AR.", "ok");
  } else if (!window.isSecureContext) {
    status("AR needs a secure page: open the https:// address shown under “Connect your Quest”.", "error");
  } else {
    status("This browser has no mixed reality — this is a 3D preview. Open the page in the Quest browser to use AR.", "");
  }
}

init();

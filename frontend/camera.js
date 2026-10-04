// ORBIT live camera client (Phase 15). Runs COCO-SSD (TensorFlow.js) on the webcam in
// this browser; only stable detections — type, colour, image box, optional QR id — are
// sent to ORBIT. All text goes in via textContent.
(function () {
  "use strict";
  const core = window.OrbitCameraCore;
  const $ = (id) => document.getElementById(id);
  const TICK_MS = 400;
  const QR_EVERY = 3;

  const state = {
    device: "webcam", view: "desk", regions: [], model: null, stream: null, running: false,
    tracker: new core.Tracker(), lastSig: null, lastSentAt: 0, sending: false, tick: 0, codes: [],
    draft: null, drawing: false, session: sessionId(), lastStable: [], frames: 0, fpsAt: performance.now(),
  };
  const video = $("video"), overlay = $("overlay"), ctx = overlay.getContext("2d");
  const grab = document.createElement("canvas"), gctx = grab.getContext("2d", { willReadFrequently: true });
  const barcode = "BarcodeDetector" in window ? new window.BarcodeDetector({ formats: ["qr_code"] }) : null;

  function sessionId() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return `webcam-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
  }

  function log(text, kind) {
    const li = document.createElement("li");
    li.textContent = `${new Date().toLocaleTimeString()} — ${text}`;
    if (kind) li.className = kind;
    $("log").prepend(li);
    while ($("log").children.length > 30) $("log").lastChild.remove();
  }

  async function api(method, path, body) {
    const res = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  // ------------------------------------------------------------- calibration
  async function loadConfig() {
    state.device = $("device").value.trim() || "webcam";
    const cfg = await api("GET", `/cameras/${encodeURIComponent(state.device)}/config`);
    state.regions = cfg.regions;
    if (cfg.view) { state.view = cfg.view; $("view").value = cfg.view; }
    renderRegions();
    return cfg;
  }

  async function saveConfig() {
    state.view = $("view").value.trim() || "desk";
    const cfg = await api("PUT", `/cameras/${encodeURIComponent(state.device)}/config`, {
      view: state.view, regions: state.regions.map((r) => ({ id: r.id, name: r.name || r.id, bbox: r.bbox })),
    });
    state.regions = cfg.regions;
    state.lastSig = null; // places changed: next snapshot must go out
    renderRegions();
    log(`calibration saved: ${state.regions.length} region(s) in ${state.view}`);
  }

  function renderRegions() {
    const list = $("regions");
    list.replaceChildren();
    if (!state.regions.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "No regions yet — everything is placed at the whole view.";
      list.append(li);
    }
    for (const r of state.regions) {
      const li = document.createElement("li");
      li.textContent = r.id + " ";
      const del = document.createElement("button");
      del.type = "button"; del.className = "link"; del.textContent = "remove"; del.setAttribute("aria-label", `remove ${r.id}`);
      del.addEventListener("click", async () => {
        state.regions = state.regions.filter((x) => x.id !== r.id);
        try { await saveConfig(); } catch (e) { log(`could not save: ${e.message}`, "err"); }
      });
      li.append(del);
      list.append(li);
    }
    const sel = $("scan-region");
    sel.replaceChildren(...[state.view, ...state.regions.map((r) => r.id)].map((id) => {
      const o = document.createElement("option"); o.value = id; o.textContent = id; return o;
    }));
  }

  // --------------------------------------------------------------- drawing
  function toNorm(evt) {
    const rect = overlay.getBoundingClientRect();
    const cw = overlay.width, ch = overlay.height;
    const scale = Math.min(rect.width / cw, rect.height / ch);
    const dw = cw * scale, dh = ch * scale;
    const x = (evt.clientX - rect.left - (rect.width - dw) / 2) / dw;
    const y = (evt.clientY - rect.top - (rect.height - dh) / 2) / dh;
    return [Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y))];
  }

  overlay.addEventListener("pointerdown", (e) => {
    if (!state.drawing) return;
    const p = toNorm(e);
    state.draft = { start: p, bbox: [p[0], p[1], p[0], p[1]] };
    overlay.setPointerCapture(e.pointerId);
  });
  overlay.addEventListener("pointermove", (e) => {
    if (!state.drawing || !state.draft) return;
    const p = toNorm(e), s = state.draft.start;
    state.draft.bbox = [Math.min(s[0], p[0]), Math.min(s[1], p[1]), Math.max(s[0], p[0]), Math.max(s[1], p[1])];
  });
  overlay.addEventListener("pointerup", () => {
    if (!state.drawing || !state.draft) return;
    const b = state.draft.bbox;
    if (b[2] - b[0] < 0.03 || b[3] - b[1] < 0.03) { state.draft = null; return; }
    $("region-form").hidden = false;
    $("region-name").focus();
  });

  $("draw").addEventListener("click", () => {
    state.drawing = !state.drawing;
    $("stage").classList.toggle("drawing", state.drawing);
    $("draw").textContent = state.drawing ? "Stop drawing" : "Draw a region";
  });
  $("region-cancel").addEventListener("click", () => { state.draft = null; $("region-form").hidden = true; });
  $("region-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("region-name").value.trim();
    if (!id || !state.draft) return;
    state.regions = state.regions.filter((r) => r.id !== id).concat([{ id, name: id, bbox: state.draft.bbox.map((v) => Math.round(v * 10000) / 10000) }]);
    state.draft = null;
    $("region-form").hidden = true;
    $("region-name").value = "";
    try { await saveConfig(); } catch (err) { log(`could not save region: ${err.message}`, "err"); }
  });

  // -------------------------------------------------------------- detection
  async function detectFrame() {
    const w = video.videoWidth, h = video.videoHeight;
    if (!w || !h) return [];
    if (grab.width !== w) { grab.width = w; grab.height = h; overlay.width = w; overlay.height = h; }
    gctx.drawImage(video, 0, 0, w, h);
    const preds = await state.model.detect(video, 20, 0.4);
    const dets = [];
    for (const p of preds) {
      if (core.EXCLUDED_LABELS.has(p.class) || !core.ALLOWED_LABELS.has(p.class)) continue;
      const [x, y, bw, bh] = p.bbox;
      const cx = Math.max(0, Math.round(x + bw * 0.25)), cy = Math.max(0, Math.round(y + bh * 0.25));
      const cw = Math.max(4, Math.round(bw * 0.5)), chh = Math.max(4, Math.round(bh * 0.5));
      const { color, share } = core.dominantColor(gctx.getImageData(cx, cy, Math.min(cw, w - cx), Math.min(chh, h - cy)).data);
      dets.push({ label: p.class, score: p.score, bbox: [x / w, y / h, (x + bw) / w, (y + bh) / h], color: share >= 0.35 ? color : null });
    }
    if (state.tick % QR_EVERY === 0) state.codes = await readCodes(w, h);
    return core.attachMarkers(dets, state.codes);
  }

  async function readCodes(w, h) {
    try {
      if (barcode) {
        const found = await barcode.detect(grab);
        return found.map((c) => ({ text: c.rawValue, bbox: [c.boundingBox.x / w, c.boundingBox.y / h, (c.boundingBox.x + c.boundingBox.width) / w, (c.boundingBox.y + c.boundingBox.height) / h] }));
      }
      if (window.jsQR) {
        const img = gctx.getImageData(0, 0, w, h);
        const code = window.jsQR(img.data, w, h, { inversionAttempts: "dontInvert" });
        if (!code) return [];
        const xs = [code.location.topLeftCorner.x, code.location.bottomRightCorner.x, code.location.topRightCorner.x, code.location.bottomLeftCorner.x];
        const ys = [code.location.topLeftCorner.y, code.location.bottomRightCorner.y, code.location.topRightCorner.y, code.location.bottomLeftCorner.y];
        return [{ text: code.data, bbox: [Math.min(...xs) / w, Math.min(...ys) / h, Math.max(...xs) / w, Math.max(...ys) / h] }];
      }
    } catch (err) { /* a failed read is just "no code this frame" */ }
    return [];
  }

  function draw(stable) {
    const w = overlay.width, h = overlay.height;
    ctx.clearRect(0, 0, w, h);
    ctx.lineWidth = Math.max(2, w / 480);
    ctx.font = `${Math.max(14, w / 60)}px system-ui, sans-serif`;
    for (const r of state.regions) {
      const [x0, y0, x1, y1] = r.bbox;
      ctx.setLineDash([8, 6]); ctx.strokeStyle = "rgba(122,162,255,0.9)";
      ctx.strokeRect(x0 * w, y0 * h, (x1 - x0) * w, (y1 - y0) * h);
      ctx.setLineDash([]); ctx.fillStyle = "rgba(122,162,255,0.95)";
      ctx.fillText(r.id, x0 * w + 6, y0 * h + 18);
    }
    if (state.draft) {
      const [x0, y0, x1, y1] = state.draft.bbox;
      ctx.setLineDash([4, 4]); ctx.strokeStyle = "#fbbf24";
      ctx.strokeRect(x0 * w, y0 * h, (x1 - x0) * w, (y1 - y0) * h);
      ctx.setLineDash([]);
    }
    const stableIds = new Set(stable.map((t) => t.id));
    for (const t of state.tracker.tracks) {
      const [x0, y0, x1, y1] = t.bbox;
      const ok = stableIds.has(t.id);
      ctx.strokeStyle = ok ? "#4ade80" : "rgba(255,255,255,0.45)";
      ctx.strokeRect(x0 * w, y0 * h, (x1 - x0) * w, (y1 - y0) * h);
      const color = state.tracker.color(t);
      const label = `${core.normaliseLabel(t.marker && t.marker.type ? t.marker.type : t.label)}${color ? " · " + color : ""}${t.marker ? " · #" + t.marker.id : ""}`;
      const tw = ctx.measureText(label).width + 10;
      ctx.fillStyle = ok ? "rgba(21,128,61,0.85)" : "rgba(60,60,60,0.7)";
      ctx.fillRect(x0 * w, Math.max(0, y0 * h - 22), tw, 22);
      ctx.fillStyle = "#fff";
      ctx.fillText(label, x0 * w + 5, Math.max(16, y0 * h - 6));
    }
  }

  // ---------------------------------------------------------------- sending
  async function send(force, detections) {
    if (state.sending) return;
    state.sending = true;
    try {
      const res = await api("POST", `/cameras/${encodeURIComponent(state.device)}/snapshot`, { detections, session_id: state.session });
      state.lastSentAt = Date.now();
      $("last-sent").textContent = new Date().toLocaleTimeString();
      const changes = res.events.map((e) => e.event_type.toLowerCase().replace(/_/g, " "));
      const dropped = res.dropped ? ` (${res.dropped} not forwarded)` : "";
      log(`${force ? "sent" : "scene changed — sent"} ${detections.length} object(s)${dropped}; ${changes.length ? changes.join(", ") : "no change in belief"}`);
      renderSeen(res.seen);
    } catch (err) {
      log(`send failed: ${err.message}`, "err");
      state.lastSig = null;
    } finally {
      state.sending = false;
    }
  }

  function renderSeen(seen) {
    const list = $("seen");
    list.replaceChildren();
    if (!seen.length) {
      const li = document.createElement("li"); li.className = "empty"; li.textContent = "Nothing stable in view."; list.append(li);
    }
    for (const s of seen) {
      const li = document.createElement("li");
      const b = document.createElement("span"); b.className = "badge " + (s.method === "NEW_AMBIGUOUS" ? "STALE" : "OBSERVED"); b.textContent = s.method.toLowerCase().replace(/_/g, " ");
      li.append(`${s.entity_id} (${s.label}) at ${s.location || state.view} `, b);
      list.append(li);
    }
  }

  async function loop() {
    if (!state.running) return;
    const started = performance.now();
    try {
      const dets = await detectFrame();
      const stable = state.tracker.update(dets);
      state.lastStable = stable;
      draw(stable);
      $("stable-count").textContent = String(stable.length);
      const detections = core.toDetections(stable, state.tracker);
      const sig = core.sceneSignature(detections, state.regions);
      if ($("auto").checked && core.shouldSend(state.lastSig, sig, state.lastSentAt, Date.now())) {
        state.lastSig = sig;
        send(false, detections);
      }
      state.frames += 1;
      if (performance.now() - state.fpsAt > 2000) {
        $("fps").textContent = `${(state.frames * 1000 / (performance.now() - state.fpsAt)).toFixed(1)} detections/s`;
        state.frames = 0; state.fpsAt = performance.now();
      }
    } catch (err) {
      log(`detection error: ${err.message}`, "err");
    }
    state.tick += 1;
    setTimeout(loop, Math.max(0, TICK_MS - (performance.now() - started)));
  }

  // ------------------------------------------------------------- controls
  // Download the detector as soon as the page opens; the camera can be allowed meanwhile.
  let modelPromise = null;
  function loadModel() {
    if (!modelPromise) {
      modelPromise = window.cocoSsd
        ? window.cocoSsd.load({ base: "lite_mobilenet_v2" })
        : Promise.reject(new Error("detector library did not load (needs internet for the first load)"));
      modelPromise.catch(() => { modelPromise = null; }); // allow a retry on the next Start
    }
    return modelPromise;
  }

  async function start() {
    $("start").disabled = true;
    try {
      const cfg = await loadConfig();
      if (!cfg.view) await saveConfig(); // first use: the camera's view becomes an ORBIT place
      state.stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
      video.srcObject = state.stream;
      await video.play();
      $("stage-empty").hidden = true;
      if (!state.model) {
        $("model-status").textContent = "loading detector… (first time can take ~30 s)";
        state.model = await loadModel();
      }
      $("model-status").textContent = "COCO-SSD (lite) running on this device";
      $("qr-status").textContent = barcode ? "native BarcodeDetector" : window.jsQR ? "jsQR (fallback)" : "unavailable";
      state.running = true;
      ["stop", "draw", "send", "scan"].forEach((id) => { $(id).disabled = false; });
      log("camera started");
      loop();
    } catch (err) {
      $("start").disabled = false;
      const msg = err.name === "NotAllowedError" ? "camera permission was denied — allow it in the address bar (and in macOS Privacy settings)" : err.message;
      $("model-status").textContent = msg;
      log(msg, "err");
    }
  }

  function stop() {
    state.running = false;
    if (state.stream) state.stream.getTracks().forEach((t) => t.stop());
    state.stream = null;
    video.srcObject = null;
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    $("stage-empty").hidden = false;
    $("start").disabled = false;
    ["stop", "draw", "send", "scan"].forEach((id) => { $(id).disabled = true; });
    log("camera stopped (objects out of view are not assumed removed)");
  }

  $("start").addEventListener("click", start);
  $("stop").addEventListener("click", stop);
  $("send").addEventListener("click", () => {
    const detections = core.toDetections(state.lastStable, state.tracker);
    state.lastSig = core.sceneSignature(detections, state.regions);
    send(true, detections);
  });
  $("scan").addEventListener("click", async () => {
    const region = $("scan-region").value;
    try {
      const detections = core.toDetections(state.lastStable, state.tracker);
      const cov = await api("POST", `/cameras/${encodeURIComponent(state.device)}/scan`, { region, detections, session_id: state.session });
      const parts = [`found ${cov.found.length}`];
      if (cov.confirmed_absent.length) parts.push(`confirmed absent: ${cov.confirmed_absent.join(", ")}`);
      if (cov.inconclusive.length) parts.push(`inconclusive: ${cov.inconclusive.join(", ")}`);
      log(`scanned ${region}: ${parts.join("; ")}`);
    } catch (err) {
      log(`scan failed: ${err.message}`, "err");
    }
  });
  $("device").addEventListener("change", () => loadConfig().catch((e) => log(e.message, "err")));
  $("view").addEventListener("change", () => { if (state.regions.length) saveConfig().catch((e) => log(e.message, "err")); });

  $("session").textContent = state.session;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    $("model-status").textContent = "this browser cannot open a camera here (needs https or localhost)";
    $("start").disabled = true;
  }
  loadConfig().catch((e) => log(`could not load calibration: ${e.message}`, "err"));
  $("model-status").textContent = "downloading detector…";
  loadModel().then(
    () => { if (!state.running) $("model-status").textContent = "detector ready — start the camera"; },
    (e) => { $("model-status").textContent = e.message; },
  );
})();

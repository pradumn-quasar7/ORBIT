// ORBIT realtime conversation (Phase 19): a Gemini Live session from this page.
// The microphone streams to Gemini as 16 kHz PCM; Orbi's voice streams back as 24 kHz
// PCM and plays as it arrives; speaking over Orbi interrupts it. Gemini's tool calls
// (ask ORBIT, open an app, search, prepare/send a message) run on the ORBIT server,
// which also checks consent against the user's own transcribed words (ADR-044).
//
//   const s = new LiveSession({ device: "mac", userId: "operator", on: { … } }); await s.start(); … s.stop();
//   on: state(s) "connecting"|"listening"|"speaking"|"closed", userText(t, final), orbiText(t, final),
//       tool(name, args, result), level(v) (Orbi's voice 0..1), micLevel(v), error(message)
const core = window.OrbitLiveCore;
const CAPTURE = `
class OrbitCapture extends AudioWorkletProcessor {
  process(inputs) { const ch = inputs[0] && inputs[0][0]; if (ch) this.port.postMessage(ch.slice(0)); return true; }
}
registerProcessor("orbit-capture", OrbitCapture);`;

// Google's close reasons, in plain words with the fix.
export function explainClose(e) {
  const r = e.reason || "";
  if (/credit|billing|prepay/i.test(r)) return "Your Gemini project has no credit left. Add credit at ai.studio/projects (Billing), then press Talk again.";
  if (/quota|rate|exhaust/i.test(r)) return "Gemini's usage limit was reached for now. Wait a minute, or raise the limit at ai.studio.";
  if (/api key|permission|denied|unauth/i.test(r)) return "Gemini refused the key. Check GEMINI_API_KEY in the .env file.";
  return r || `Gemini closed the connection (${e.code}).`;
}

export async function liveStatus() {
  try {
    const res = await fetch("/live/status");
    return res.ok ? await res.json() : { configured: false };
  } catch { return { configured: false }; }
}

export class LiveSession {
  constructor({ device, userId, conversationId = null, on = {} }) {
    this.device = device;
    this.userId = userId;
    this.conversationId = conversationId;
    this.on = on;
    this.heard = ""; // the user's own words this turn (sent with tool calls for consent)
    this.said = "";
    this.active = false;
    this.sources = new Set();
    this.nextTime = 0;
    // Quest mics are ~20x quieter than a laptop's (Phase 18.1): lift them before sending.
    this.gain = device === "quest" ? 10 : 1;
  }

  emit(name, ...args) { const fn = this.on[name]; if (fn) fn(...args); }

  async start() {
    this.emit("state", "connecting");
    const res = await fetch("/live/session", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: this.userId, device: this.device, conversation_id: this.conversationId }),
    });
    const s = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(s.detail || `HTTP ${res.status}`);
    this.conversationId = s.conversation_id;
    try {
      await this.connect(s.ws_url, s.setup);
    } catch (first) {
      // The ephemeral-token endpoint's API version has moved before: try the other one once.
      await this.connect(s.ws_url_fallback, s.setup).catch(() => { throw first; });
    }
    this.active = true;
    await this.startPlayer();
    await this.startMic();
    this.emit("state", "listening");
  }

  connect(url, setup) {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(url);
      let ready = false;
      const timer = setTimeout(() => { if (!ready) { ws.close(); reject(new Error("Gemini did not answer the session setup")); } }, 12000);
      ws.onopen = () => ws.send(JSON.stringify(setup));
      ws.onmessage = async (e) => {
        const text = typeof e.data === "string" ? e.data : await e.data.text();
        let msg;
        try { msg = JSON.parse(text); } catch { return; }
        if (msg.setupComplete !== undefined && !ready) {
          ready = true;
          clearTimeout(timer);
          this.ws = ws;
          resolve();
          return;
        }
        this.handle(msg);
      };
      ws.onerror = () => { if (!ready) { clearTimeout(timer); reject(new Error("could not connect to Gemini Live")); } };
      ws.onclose = (e) => {
        if (!ready) { clearTimeout(timer); reject(new Error(explainClose(e))); return; }
        if (this.active) {
          this.emit("error", e.reason ? `Gemini ended the session: ${e.reason}` : "The live session ended.");
          this.stop();
        }
      };
    });
  }

  send(obj) { if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(obj)); }

  // -------------------------------------------------------------- incoming
  async handle(msg) {
    const sc = msg.serverContent;
    if (sc) {
      if (sc.interrupted) this.flushPlayback(); // the user spoke over Orbi
      if (sc.inputTranscription && sc.inputTranscription.text) {
        this.heard += sc.inputTranscription.text;
        this.emit("userText", this.heard.trim(), false);
      }
      if (sc.outputTranscription && sc.outputTranscription.text) {
        this.said += sc.outputTranscription.text;
        this.emit("orbiText", this.said.trim(), false);
      }
      for (const part of (sc.modelTurn && sc.modelTurn.parts) || []) {
        if (part.inlineData && part.inlineData.data && /audio/.test(part.inlineData.mimeType || "audio")) this.play(part.inlineData.data);
      }
      if (sc.turnComplete) {
        if (this.heard.trim()) this.emit("userText", this.heard.trim(), true);
        if (this.said.trim()) this.emit("orbiText", this.said.trim(), true);
        this.heard = "";
        this.said = "";
      }
    }
    if (msg.toolCall) await this.runTools(msg.toolCall.functionCalls || []);
    if (msg.goAway) this.emit("error", "Gemini will end this session soon; press Talk again to continue.");
  }

  async runTools(calls) {
    // The user's words may still be arriving as a transcript: give them a moment, since
    // consent for actions and messages is judged on exactly those words.
    if (!this.heard.trim()) await new Promise((r) => setTimeout(r, 700));
    const responses = [];
    for (const fc of calls) {
      let result;
      try {
        const res = await fetch("/live/tool", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ conversation_id: this.conversationId, device: this.device, name: fc.name, args: fc.args || {}, heard: this.heard.trim() }),
        });
        result = await res.json();
        if (!res.ok) result = { ok: false, error: result.detail || `HTTP ${res.status}` };
      } catch (err) {
        result = { ok: false, error: err.message };
      }
      this.emit("tool", fc.name, fc.args || {}, result);
      responses.push({ id: fc.id, name: fc.name, response: result });
    }
    this.send({ toolResponse: { functionResponses: responses } });
  }

  // ------------------------------------------------------------ microphone
  async startMic() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.micCtx = new AudioContext();
    await this.micCtx.resume();
    const url = URL.createObjectURL(new Blob([CAPTURE], { type: "application/javascript" }));
    await this.micCtx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    const src = this.micCtx.createMediaStreamSource(this.stream);
    const node = new AudioWorkletNode(this.micCtx, "orbit-capture");
    src.connect(node);
    const rate = this.micCtx.sampleRate;
    const chunk = Math.round(rate / 10); // send every ~100 ms
    let buf = new Float32Array(0);
    node.port.onmessage = (e) => {
      if (!this.active) return;
      const merged = new Float32Array(buf.length + e.data.length);
      merged.set(buf);
      merged.set(e.data, buf.length);
      buf = merged;
      while (buf.length >= chunk) {
        const piece = buf.subarray(0, chunk);
        this.emit("micLevel", Math.min(1, core.rms(piece) * this.gain * 8));
        this.send({ realtimeInput: { audio: { data: core.encodeChunk(piece, rate, this.gain), mimeType: "audio/pcm;rate=16000" } } });
        buf = buf.slice(chunk);
      }
    };
    this.micNode = node;
  }

  // -------------------------------------------------------------- playback
  async startPlayer() {
    this.outCtx = new AudioContext({ sampleRate: 24000 });
    await this.outCtx.resume();
    this.analyser = this.outCtx.createAnalyser();
    this.analyser.fftSize = 512;
    this.analyser.connect(this.outCtx.destination);
    const data = new Float32Array(this.analyser.fftSize);
    let speaking = false;
    const tick = () => {
      if (!this.outCtx) return;
      this.analyser.getFloatTimeDomainData(data);
      const level = Math.min(1, core.rms(data) * 6);
      this.emit("level", level);
      const now = this.outCtx.currentTime;
      const isSpeaking = this.nextTime > now + 0.02;
      if (isSpeaking !== speaking && this.active) { speaking = isSpeaking; this.emit("state", speaking ? "speaking" : "listening"); }
      this.raf = requestAnimationFrame(tick);
    };
    tick();
  }

  play(b64) {
    if (!this.outCtx) return;
    const samples = core.pcm16ToFloat(core.base64ToPcm16(b64));
    if (!samples.length) return;
    const buffer = this.outCtx.createBuffer(1, samples.length, 24000);
    buffer.copyToChannel(samples, 0);
    const src = this.outCtx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.analyser);
    const at = Math.max(this.outCtx.currentTime + 0.03, this.nextTime);
    src.start(at);
    this.nextTime = at + buffer.duration;
    this.sources.add(src);
    src.onended = () => this.sources.delete(src);
  }

  flushPlayback() {
    for (const s of this.sources) { try { s.stop(); } catch { /* already stopped */ } }
    this.sources.clear();
    if (this.outCtx) this.nextTime = this.outCtx.currentTime;
  }

  // An ORBIT notice for Orbi to pass on, in its own voice. Not the user's words: it
  // does not touch `heard`, so it can never count as consent.
  inform(text) {
    this.send({ clientContent: { turns: [{ role: "user", parts: [{ text: `[ORBIT notice — not from the user; tell them briefly] ${text}` }] }], turnComplete: true } });
  }

  // Typed text into the same conversation (counts as the user's own words).
  sendText(text) {
    this.heard = text;
    this.send({ clientContent: { turns: [{ role: "user", parts: [{ text }] }], turnComplete: true } });
  }

  stop() {
    if (!this.active && !this.ws) return;
    this.active = false;
    this.flushPlayback();
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    if (this.micCtx) this.micCtx.close().catch(() => {});
    if (this.outCtx) { cancelAnimationFrame(this.raf); this.outCtx.close().catch(() => {}); this.outCtx = null; }
    if (this.ws) { try { this.ws.close(); } catch { /* closed */ } this.ws = null; }
    this.emit("state", "closed");
  }
}

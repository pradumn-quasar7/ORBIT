// ORBIT hands-free conversation on hosted models (Phase 19.1, Groq free plan).
// Listen → (you pause) → send the utterance → Orbi answers aloud → listen again.
// Speaking over Orbi stops it and starts your next turn (barge-in). Speech detection
// adapts to the room's noise floor (headset mics are ~20x quieter than a laptop's);
// half a second before you started speaking is kept so first words are not cut.
// Same callbacks as LiveSession: state, userText, orbiText, tool, level, micLevel, error.
const core = window.OrbitLiveCore;
const CAPTURE = `
class OrbitCapture extends AudioWorkletProcessor {
  process(inputs) { const ch = inputs[0] && inputs[0][0]; if (ch) this.port.postMessage(ch.slice(0)); return true; }
}
registerProcessor("orbit-capture-hosted", OrbitCapture);`;

const BLOCK_MS = 50;
const PREROLL_MS = 500;
const END_SILENCE_MS = 900;
const MIN_SPEECH_MS = 250;
const MAX_UTTERANCE_MS = 15000;
const IDLE_STOP_MS = 90000; // nobody talked to Orbi for 90 s: stop listening

export async function voiceStatus() {
  try {
    const res = await fetch("/voice/status");
    return res.ok ? await res.json() : null;
  } catch { return null; }
}

export class HostedConversation {
  constructor({ device, userId, conversationId = null, on = {} }) {
    this.device = device;
    this.userId = userId;
    this.conversationId = conversationId;
    this.on = on;
    this.phase = "off"; // listening | recording | thinking | speaking
    this.active = false;
  }

  emit(name, ...args) {
    if (name === "error") this.diag("error", args[0]);
    const fn = this.on[name]; if (fn) fn(...args);
  }

  // A readable snapshot for diagnosing "Orbi isn't hearing me" (window.__orbiVoice).
  diag(key, value) {
    const d = (window.__orbiVoice = window.__orbiVoice || { events: [] });
    d[key] = value;
    if (key === "error" || key === "phase") d.events.push(`${new Date().toISOString().slice(11, 19)} ${key}: ${value}`);
    d.events = d.events.slice(-30);
  }

  setPhase(p) {
    this.diag("phase", p);
    this.phase = p;
    this.emit("state", p === "recording" ? "listening" : p);
  }

  async start() {
    this.emit("state", "connecting");
    this.diag("phase", "starting: asking for the microphone");
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.ctx = new AudioContext();
    this.diag("phase", `audio engine ${this.ctx.state}`);
    // Inside VR a controller press may not count as a page click, so resume() can wait
    // forever: never block on it (capture still runs once the engine starts).
    await Promise.race([this.ctx.resume(), new Promise((r) => setTimeout(r, 1500))]);
    this.diag("audioState", this.ctx.state);
    const url = URL.createObjectURL(new Blob([CAPTURE], { type: "application/javascript" }));
    await this.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    const node = new AudioWorkletNode(this.ctx, "orbit-capture-hosted");
    this.ctx.createMediaStreamSource(this.stream).connect(node);
    this.rate = this.ctx.sampleRate;
    const blockLen = Math.round((this.rate * BLOCK_MS) / 1000);
    let pending = new Float32Array(0);
    this.ring = [];
    this.utterance = [];
    this.floor = [];
    this.threshold = null;
    this.speechMs = 0;
    this.silenceMs = 0;
    this.lastTalk = Date.now();
    this.active = true;
    node.port.onmessage = (e) => {
      if (!this.active) return;
      const merged = new Float32Array(pending.length + e.data.length);
      merged.set(pending);
      merged.set(e.data, pending.length);
      pending = merged;
      while (pending.length >= blockLen) {
        this.block(pending.slice(0, blockLen));
        pending = pending.slice(blockLen);
      }
    };
    this.node = node;
    this.setPhase("listening");
  }

  // One 50 ms block of microphone audio through the turn-taking state machine.
  block(samples) {
    const rms = core.rms(samples);
    const d = window.__orbiVoice || {};
    d.blocks = (d.blocks || 0) + 1;
    d.rms = Math.round(rms * 100000) / 100000;
    d.peakRms = Math.max(d.peakRms || 0, d.rms);
    d.threshold = this.threshold;
    window.__orbiVoice = d;
    if (this.threshold === null) { // learn the room's quiet level first (~0.5 s)
      this.floor.push(rms);
      if (this.floor.length >= 10) {
        const sorted = [...this.floor].sort((a, b) => a - b);
        this.threshold = Math.max(0.0012, sorted[5] * 3.5);
      }
      return;
    }
    this.emit("micLevel", Math.min(1, rms / (this.threshold * 4)));
    if (this.phase === "listening" && Date.now() - this.lastTalk > IDLE_STOP_MS) {
      // Said out loud: a silent note was easy to miss in the headset.
      const msg = "I'll stop listening now. Press the grip or click me to talk again.";
      this.lastTalk = Date.now();
      this.emit("orbiText", msg, true);
      this.say(msg).then(() => this.stop());
      return;
    }
    const loud = rms > this.threshold * (this.phase === "speaking" ? 2.2 : 1); // Orbi's own voice must not trigger
    if (this.phase === "listening") {
      this.ring.push(samples);
      if (this.ring.length > PREROLL_MS / BLOCK_MS) this.ring.shift();
      this.speechMs = loud ? this.speechMs + BLOCK_MS : 0;
      if (this.speechMs >= 100) { this.utterance = [...this.ring]; this.silenceMs = 0; this.setPhase("recording"); }
    } else if (this.phase === "recording") {
      this.utterance.push(samples);
      this.silenceMs = loud ? 0 : this.silenceMs + BLOCK_MS;
      if (loud) this.speechMs += BLOCK_MS;
      const length = this.utterance.length * BLOCK_MS;
      if (this.silenceMs >= END_SILENCE_MS || length >= MAX_UTTERANCE_MS) this.finish();
    } else if (this.phase === "speaking") {
      this.speechMs = loud ? this.speechMs + BLOCK_MS : 0;
      if (this.speechMs >= 250) { // the user talks over Orbi: stop and listen
        this.stopAudio();
        this.ring = [];
        this.utterance = [samples];
        this.silenceMs = 0;
        this.setPhase("recording");
      }
    }
  }

  async finish() {
    const speech = this.speechMs;
    const audio = core.concat(this.utterance);
    this.utterance = [];
    this.speechMs = 0;
    if (speech < MIN_SPEECH_MS) { this.setPhase("listening"); return; } // a click or a cough
    this.setPhase("thinking");
    const wav = core.wav(core.normalise(core.resample(audio, this.rate, 16000)), 16000);
    this.diag("sent", (this.diag.sent = (this.diag.sent || 0) + 1));
    let out;
    try {
      const q = new URLSearchParams({ device: this.device, user_id: this.userId });
      if (this.conversationId) q.set("conversation_id", this.conversationId);
      const res = await fetch(`/voice/turn?${q}`, { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav });
      out = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(out.detail || `HTTP ${res.status}`);
    } catch (err) {
      this.emit("error", err.message);
      if (this.active) this.setPhase("listening");
      return;
    }
    if (!this.active) return;
    this.conversationId = out.conversation_id;
    if (!out.heard) { this.setPhase("listening"); return; } // noise, not words
    this.lastTalk = Date.now();
    this.emit("userText", out.heard, true);
    if (out.end_session || core.isStop(out.heard)) { // "stop": a short goodbye, then mic off
      this.emit("orbiText", out.reply || "Okay, I'll stop listening.", true);
      await this.say(out.reply || "Okay, I'll stop listening.");
      this.stop();
      return;
    }
    for (const t of out.tools || []) this.emit("tool", t.name, t.args, t.result);
    this.emit("orbiText", out.reply, true);
    await this.say(out.reply);
    if (out.song_url && this.active) await this.play(out.song_url); // Orbi sings
  }

  // Speak through ORBIT's /speech voice; the mouth follows the real audio level.
  say(text) {
    if (!text || !this.active) { if (this.active) this.setPhase("listening"); return Promise.resolve(); }
    return this.play(`/speech?text=${encodeURIComponent(text.slice(0, 400))}`);
  }

  play(url) {
    return new Promise((resolve) => {
      const audio = new Audio(url);
      this.audio = audio;
      let raf = 0;
      const done = () => {
        cancelAnimationFrame(raf);
        if (this.audio === audio) this.audio = null;
        if (this.active && this.phase === "speaking") { this.ring = []; this.setPhase("listening"); }
        resolve();
      };
      audio.onplay = () => {
        this.setPhase("speaking");
        try {
          const src = this.ctx.createMediaElementSource(audio);
          const an = this.ctx.createAnalyser();
          an.fftSize = 512;
          src.connect(an);
          an.connect(this.ctx.destination);
          const data = new Float32Array(an.fftSize);
          const tick = () => { an.getFloatTimeDomainData(data); this.emit("level", Math.min(1, core.rms(data) * 6)); raf = requestAnimationFrame(tick); };
          tick();
        } catch { /* plays without the mouth meter */ }
      };
      audio.onended = done;
      audio.onerror = () => { this.emit("error", "Orbi's voice could not play"); done(); };
      audio.onpause = done;
      audio.play().catch(done);
    });
  }

  stopAudio() {
    if (this.audio) { const a = this.audio; this.audio = null; a.pause(); }
  }

  // An ORBIT notice, said aloud when Orbi isn't busy (never counts as the user's words).
  inform(text) {
    if (this.phase === "listening") { this.emit("orbiText", text, true); this.say(text); }
  }

  stop() {
    if (!this.active) return;
    this.active = false;
    this.stopAudio();
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    if (this.ctx) this.ctx.close().catch(() => {});
    this.phase = "off";
    this.emit("state", "closed");
  }
}

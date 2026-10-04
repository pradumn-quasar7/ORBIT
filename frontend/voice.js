// ORBIT voice input (Phase 17.1): two engines behind one interface.
//  - "browser": the Web Speech API (fast, streaming; in Chrome/Edge an online service).
//  - "local":   Whisper running in this page (transformers.js); private and works where
//               the Web Speech API is missing or its service is unavailable. The model
//               (~80 MB) downloads once and is then cached by the browser.
// "auto" uses the browser engine and falls back to local when the service fails.
// Also exposes a live input-level meter so the user can see the microphone works.

const WHISPER = "onnx-community/whisper-base.en";
const TRANSFORMERS = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.7.2";
const BrowserRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

export const inClaudeApp = /\bClaude\//.test(navigator.userAgent);

// Plain-language explanation for each failure, with the fix.
export function explain(code) {
  if (inClaudeApp && (code === "not-allowed" || code === "NotAllowedError")) {
    return "The microphone is blocked inside the Claude app's built-in browser. Open this page in Chrome or Edge instead.";
  }
  return {
    "not-allowed": "Microphone access is blocked. Click the icon at the left of the address bar → allow Microphone. On a Mac also check System Settings → Privacy & Security → Microphone → enable your browser.",
    NotAllowedError: "Microphone access is blocked. Click the icon at the left of the address bar → allow Microphone. On a Mac also check System Settings → Privacy & Security → Microphone → enable your browser.",
    "service-not-allowed": "This browser doesn't allow its speech service here; switching to on-device recognition.",
    network: "The browser's online speech service can't be reached; switching to on-device recognition.",
    "language-not-supported": "The browser's speech service doesn't support this language; switching to on-device recognition.",
    "no-speech": "I didn't hear anything. Check the input level meter, and your input device in System Settings → Sound → Input.",
    "audio-capture": "No microphone was found. Plug one in or pick an input device in System Settings → Sound → Input.",
    NotFoundError: "No microphone was found. Plug one in or pick an input device in System Settings → Sound → Input.",
    NotReadableError: "The microphone is in use by another app (or blocked by the system). Close other apps using it and try again.",
    unsupported: "This browser has no microphone support on this page.",
  }[code] || `Voice error: ${code}`;
}
const FALLBACK_CODES = new Set(["network", "service-not-allowed", "language-not-supported"]);

export class Voice {
  constructor({ onPartial, onFinal, onState, onError, onLevel, onInfo, lang } = {}) {
    this.cb = { onPartial, onFinal, onState, onError, onLevel, onInfo };
    this.lang = lang || navigator.language || "en-US";
    this.engine = BrowserRecognition ? "auto" : "local";
    this.active = null; // { stop() }
    this.transcriber = null;
  }

  get listening() { return this.active !== null; }
  static get browserAvailable() { return !!BrowserRecognition; }
  static get micAvailable() { return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia); }

  async toggle() {
    if (this.active) { this.active.stop(); return; }
    if (!Voice.micAvailable) return this._fail("unsupported");
    const engine = this.engine === "auto" ? (BrowserRecognition ? "browser" : "local") : this.engine;
    if (engine === "browser") this._startBrowser();
    else await this._startLocal();
  }

  _emit(name, ...args) { const fn = this.cb[name]; if (fn) fn(...args); }
  _fail(code) { this._emit("onError", code, explain(code)); }

  // ------------------------------------------------------------- level meter
  async _meter(stream) {
    const ctx = new AudioContext();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const data = new Float32Array(analyser.fftSize);
    let raf = 0;
    const loop = () => {
      analyser.getFloatTimeDomainData(data);
      let sum = 0;
      for (const v of data) sum += v * v;
      this.level = Math.min(1, Math.sqrt(sum / data.length) * 6);
      this._emit("onLevel", this.level);
      raf = requestAnimationFrame(loop);
    };
    loop();
    return () => { cancelAnimationFrame(raf); ctx.close(); this._emit("onLevel", 0); };
  }

  // --------------------------------------------------------- browser engine
  _startBrowser() {
    const rec = new BrowserRecognition();
    rec.lang = this.lang;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    let stopMeter = null, failed = null, stream = null;
    this.active = { stop: () => rec.stop() };
    this._emit("onState", "listening", "browser");
    // A parallel capture only for the level meter (Chrome allows both).
    if (Voice.micAvailable) {
      navigator.mediaDevices.getUserMedia({ audio: true }).then(async (s) => {
        stream = s;
        if (this.active) stopMeter = await this._meter(s); else s.getTracks().forEach((t) => t.stop());
      }).catch(() => {});
    }
    rec.onresult = (e) => {
      const r = e.results[e.results.length - 1];
      if (r.isFinal) this._emit("onFinal", r[0].transcript);
      else this._emit("onPartial", r[0].transcript);
    };
    rec.onerror = (e) => { failed = e.error; };
    rec.onend = async () => {
      this.active = null;
      if (stopMeter) stopMeter();
      if (stream) stream.getTracks().forEach((t) => t.stop());
      this._emit("onState", "idle", "browser");
      if (failed && failed !== "aborted") {
        this._fail(failed);
        if (FALLBACK_CODES.has(failed) && this.engine === "auto") {
          this.engine = "local"; // remember for the rest of the session
          this._emit("onInfo", "Using on-device speech recognition from now on.");
          await this._startLocal();
        }
      }
    };
    try { rec.start(); } catch (err) { this.active = null; this._fail(err.name || "unsupported"); }
  }

  // ----------------------------------------------------------- local engine
  async _loadWhisper() {
    if (this.transcriber) return this.transcriber;
    this._emit("onState", "loading", "local");
    const { pipeline } = await import(TRANSFORMERS);
    let last = 0;
    this.transcriber = await pipeline("automatic-speech-recognition", WHISPER, {
      dtype: "q8",
      progress_callback: (p) => {
        if (p.status === "progress" && p.total && Date.now() - last > 300) {
          last = Date.now();
          this._emit("onInfo", `Downloading the on-device speech model (once): ${Math.round((p.loaded / p.total) * 100)}% of ${p.file}`);
        }
      },
    });
    this._emit("onInfo", "On-device speech model ready.");
    return this.transcriber;
  }

  async _startLocal() {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
    } catch (err) {
      return this._fail(err.name || "NotAllowedError");
    }
    const loading = this._loadWhisper().catch((err) => { this._fail(`model: ${err.message}`); return null; });
    const recorder = new MediaRecorder(stream);
    const chunks = [];
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    const stopMeter = await this._meter(stream);
    // End of speech: ~1.2 s of quiet after something was said, or 15 s at most.
    let heard = false, quietSince = 0;
    const started = performance.now();
    const vad = setInterval(() => {
      const now = performance.now();
      if (this.level > 0.08) { heard = true; quietSince = 0; }
      else if (heard && !quietSince) quietSince = now;
      if ((heard && quietSince && now - quietSince > 1200) || now - started > 15000 || (!heard && now - started > 6000)) stop();
    }, 100);
    const stop = () => { if (recorder.state === "recording") recorder.stop(); };
    this.active = { stop };
    recorder.onstop = async () => {
      clearInterval(vad);
      stopMeter();
      stream.getTracks().forEach((t) => t.stop());
      this.active = null;
      if (!heard) { this._emit("onState", "idle", "local"); return this._fail("no-speech"); }
      this._emit("onState", "transcribing", "local");
      try {
        const asr = await loading;
        if (!asr) return;
        const buf = await new Blob(chunks).arrayBuffer();
        const ctx = new OfflineAudioContext(1, 16000, 16000);
        const audio = (await ctx.decodeAudioData(buf)).getChannelData(0);
        const text = ((await asr(audio)).text || "").trim();
        if (text && !/^\[.*\]$|^\(.*\)$/.test(text)) this._emit("onFinal", text); // Whisper marks silence as [BLANK_AUDIO]
        else this._fail("no-speech");
      } catch (err) {
        this._fail(`transcription: ${err.message}`);
      } finally {
        this._emit("onState", "idle", "local");
      }
    };
    recorder.start();
    this._emit("onState", "listening", "local");
  }
}

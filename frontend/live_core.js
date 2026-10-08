// ORBIT live-audio core (Phase 19): pure conversions for the Gemini Live stream.
// Microphone → 16 kHz 16-bit PCM → base64; Orbi's voice ← 24 kHz 16-bit PCM base64.
// No DOM: runs in the browser (window.OrbitLiveCore) and under Node tests.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OrbitLiveCore = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Linear-interpolation resampler (mic contexts often run at 44.1/48 kHz).
  function resample(input, fromRate, toRate) {
    if (fromRate === toRate) return input;
    const ratio = fromRate / toRate;
    const out = new Float32Array(Math.floor(input.length / ratio));
    for (let i = 0; i < out.length; i++) {
      const x = i * ratio, i0 = Math.floor(x), f = x - i0;
      const a = input[i0], b = input[Math.min(i0 + 1, input.length - 1)];
      out[i] = a + (b - a) * f;
    }
    return out;
  }

  // Soft-clipped gain: headset mics are ~20x quieter than a laptop's (Phase 18.1).
  function applyGain(input, gain) {
    if (gain === 1) return input;
    const out = new Float32Array(input.length);
    for (let i = 0; i < input.length; i++) out[i] = Math.tanh(input[i] * gain);
    return out;
  }

  function floatToPcm16(input) {
    const out = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]));
      out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
    }
    return out;
  }

  function pcm16ToFloat(pcm) {
    const out = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) out[i] = pcm[i] / (pcm[i] < 0 ? 0x8000 : 0x7fff);
    return out;
  }

  const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  function bytesToBase64(bytes) {
    let out = "";
    let i = 0;
    for (; i + 2 < bytes.length; i += 3) {
      const n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
      out += B64[n >> 18] + B64[(n >> 12) & 63] + B64[(n >> 6) & 63] + B64[n & 63];
    }
    const rest = bytes.length - i;
    if (rest === 1) { const n = bytes[i] << 16; out += B64[n >> 18] + B64[(n >> 12) & 63] + "=="; }
    else if (rest === 2) { const n = (bytes[i] << 16) | (bytes[i + 1] << 8); out += B64[n >> 18] + B64[(n >> 12) & 63] + B64[(n >> 6) & 63] + "="; }
    return out;
  }

  function base64ToBytes(b64) {
    const clean = b64.replace(/[^A-Za-z0-9+/]/g, "");
    const bytes = new Uint8Array(Math.floor((clean.length * 3) / 4));
    let j = 0;
    for (let i = 0; i < clean.length; i += 4) {
      const n = (B64.indexOf(clean[i]) << 18) | (B64.indexOf(clean[i + 1]) << 12) |
        ((B64.indexOf(clean[i + 2]) & 63) << 6) | (B64.indexOf(clean[i + 3]) & 63);
      bytes[j++] = (n >> 16) & 255;
      if (clean[i + 2] !== undefined && j < bytes.length) bytes[j++] = (n >> 8) & 255;
      if (clean[i + 3] !== undefined && j < bytes.length) bytes[j++] = n & 255;
    }
    return bytes.subarray(0, j);
  }

  // Little-endian 16-bit PCM <-> base64 (what the Live API sends and expects).
  function pcm16ToBase64(pcm) {
    const bytes = new Uint8Array(pcm.length * 2);
    for (let i = 0; i < pcm.length; i++) { bytes[2 * i] = pcm[i] & 255; bytes[2 * i + 1] = (pcm[i] >> 8) & 255; }
    return bytesToBase64(bytes);
  }

  function base64ToPcm16(b64) {
    const bytes = base64ToBytes(b64);
    const out = new Int16Array(bytes.length >> 1);
    for (let i = 0; i < out.length; i++) {
      const v = bytes[2 * i] | (bytes[2 * i + 1] << 8);
      out[i] = v >= 0x8000 ? v - 0x10000 : v;
    }
    return out;
  }

  function rms(samples) {
    let s = 0;
    for (const v of samples) s += v * v;
    return samples.length ? Math.sqrt(s / samples.length) : 0;
  }

  // One microphone chunk ready to send: resample → gain → 16-bit PCM → base64.
  function encodeChunk(float32, rate, gain = 1) {
    return pcm16ToBase64(floatToPcm16(applyGain(resample(float32, rate, 16000), gain)));
  }

  function concat(chunks) {
    const out = new Float32Array(chunks.reduce((n, c) => n + c.length, 0));
    let o = 0;
    for (const c of chunks) { out.set(c, o); o += c.length; }
    return out;
  }

  // Peak-normalise a quiet utterance (headset mics) so speech recognition hears it clearly.
  function normalise(samples, target = 0.9, maxGain = 40) {
    let peak = 0;
    for (const v of samples) peak = Math.max(peak, Math.abs(v));
    if (peak === 0) return samples;
    const gain = Math.min(target / peak, maxGain);
    if (gain <= 1) return samples;
    const out = new Float32Array(samples.length);
    for (let i = 0; i < samples.length; i++) out[i] = samples[i] * gain;
    return out;
  }

  // A complete 16-bit mono WAV file (bytes) — what the hosted speech recogniser receives.
  function wav(float32, rate) {
    const pcm = floatToPcm16(float32);
    const bytes = new Uint8Array(44 + pcm.length * 2);
    const view = new DataView(bytes.buffer);
    const str = (o, s) => { for (let i = 0; i < s.length; i++) bytes[o + i] = s.charCodeAt(i); };
    str(0, "RIFF"); view.setUint32(4, 36 + pcm.length * 2, true); str(8, "WAVE");
    str(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    str(36, "data"); view.setUint32(40, pcm.length * 2, true);
    for (let i = 0; i < pcm.length; i++) view.setInt16(44 + i * 2, pcm[i], true);
    return bytes;
  }

  // "Stop" means: stop talking and stop listening (end the conversation) — unless it is
  // clearly about something else ("stop the video" pauses the video instead).
  const STOP = /^(?:(?:ok(?:ay)?|hey|hi|please|orbi|orby|orbie|orbee|orbit|arby|r\.? ?b\.?)[ ,]+)*(?:stop(?: (?:listening|talking|it|now|please|orbi|orbit|that))*|be quiet|quiet|shut up|enough|that'?s (?:all|enough|it)|go to sleep|sleep|goodbye(?: orbi| orby| orbit)?|bye(?: orbi| orby| orbit)?|thanks?,? that'?s all|bas(?: karo| kar do)?|chup(?: ho jao| raho)?|ruk(?: jao)?|band karo)[.!? ]*$/i;
  function isStop(text) {
    return STOP.test(String(text || "").trim().replace(/[“”"]/g, ""));
  }

  return { isStop, concat, normalise, wav, resample, applyGain, floatToPcm16, pcm16ToFloat, bytesToBase64, base64ToBytes, pcm16ToBase64, base64ToPcm16, rms, encodeChunk };
});

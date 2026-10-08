// node --test frontend/tests/live_core.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("../live_core.js");

test("base64 matches Node's encoder in both directions", () => {
  for (const n of [0, 1, 2, 3, 4, 5, 255, 1000]) {
    const bytes = Uint8Array.from({ length: n }, (_, i) => (i * 37 + 11) & 255);
    const b64 = core.bytesToBase64(bytes);
    assert.equal(b64, Buffer.from(bytes).toString("base64"), `length ${n}`);
    assert.deepEqual(Array.from(core.base64ToBytes(b64)), Array.from(bytes));
  }
});

test("16-bit PCM is little-endian and round-trips", () => {
  const pcm = Int16Array.from([0, 1, -1, 32767, -32768, 12345, -12345]);
  const b64 = core.pcm16ToBase64(pcm);
  assert.equal(b64, Buffer.from(pcm.buffer).toString("base64")); // same bytes as the platform (LE)
  assert.deepEqual(Array.from(core.base64ToPcm16(b64)), Array.from(pcm));
});

test("float <-> PCM clamps and scales", () => {
  const pcm = core.floatToPcm16(Float32Array.from([0, 1, -1, 2, -2, 0.5]));
  assert.deepEqual(Array.from(pcm), [0, 32767, -32768, 32767, -32768, 16384]);
  const back = core.pcm16ToFloat(pcm);
  assert.ok(Math.abs(back[5] - 0.5) < 1e-3);
});

test("resampling 48 kHz to 16 kHz keeps a third of the samples and the waveform", () => {
  const n = 4800;
  const tone = Float32Array.from({ length: n }, (_, i) => Math.sin((2 * Math.PI * 440 * i) / 48000));
  const out = core.resample(tone, 48000, 16000);
  assert.equal(out.length, 1600);
  for (let i = 0; i < out.length; i += 97) assert.ok(Math.abs(out[i] - Math.sin((2 * Math.PI * 440 * i) / 16000)) < 0.02);
});

test("gain lifts a quiet headset mic without hard clipping", () => {
  const quiet = Float32Array.from({ length: 160 }, (_, i) => 0.003 * Math.sin(i / 3));
  const loud = core.applyGain(quiet, 12);
  assert.ok(core.rms(loud) > 10 * core.rms(quiet));
  assert.ok(Math.max(...core.applyGain(Float32Array.from([0.9, -0.9]), 12).map(Math.abs)) <= 1); // soft clip: never beyond full scale
  const chunk = core.encodeChunk(Float32Array.from({ length: 4800 }, () => 0.1), 48000, 1);
  assert.equal(core.base64ToPcm16(chunk).length, 1600);
});

test("utterances become normalised 16 kHz WAV files", () => {
  const quiet = core.concat([Float32Array.from({ length: 800 }, () => 0.025), Float32Array.from({ length: 800 }, () => -0.05)]);
  assert.equal(quiet.length, 1600);
  const loud = core.normalise(quiet);
  assert.ok(Math.abs(Math.max(...loud.map(Math.abs)) - 0.9) < 1e-5); // float32 precision
  assert.equal(core.normalise(Float32Array.from([0.95, -0.5]))[0], Math.fround(0.95)); // already loud: unchanged
  assert.ok(Math.max(...core.normalise(Float32Array.from([0.001, -0.001])).map(Math.abs)) <= 0.0401); // gain capped at 40x: noise stays small
  const bytes = core.wav(loud, 16000);
  const b = Buffer.from(bytes);
  assert.equal(b.toString("ascii", 0, 4), "RIFF");
  assert.equal(b.toString("ascii", 8, 12), "WAVE");
  assert.equal(b.readUInt32LE(24), 16000);
  assert.equal(b.readUInt16LE(34), 16);
  assert.equal(b.readUInt32LE(40), 1600 * 2);
  assert.equal(bytes.length, 44 + 3200);
});

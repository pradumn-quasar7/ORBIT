// Node unit tests for the camera decision logic: `node --test frontend/tests`
const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("../camera_core.js");

test("colour naming covers the basic hues and neutrals", () => {
  assert.equal(core.colorName(220, 30, 30), "red");
  assert.equal(core.colorName(30, 160, 60), "green");
  assert.equal(core.colorName(30, 80, 220), "blue");
  assert.equal(core.colorName(240, 240, 240), "white");
  assert.equal(core.colorName(20, 20, 20), "black");
  assert.equal(core.colorName(128, 128, 128), "gray");
});

test("dominant colour reports a share", () => {
  const px = [];
  for (let i = 0; i < 90; i++) px.push(30, 80, 220, 255);
  for (let i = 0; i < 30; i++) px.push(240, 240, 240, 255);
  const { color, share } = core.dominantColor(px);
  assert.equal(color, "blue");
  assert.ok(share > 0.6);
});

test("marker parsing", () => {
  assert.deepEqual(core.parseMarker("orbit:m17"), { type: null, id: "m17" });
  assert.deepEqual(core.parseMarker("ORBIT:cable:c4"), { type: "cable", id: "c4" });
  assert.equal(core.parseMarker("https://example.com"), null);
  assert.equal(core.parseMarker("orbit:bad id with spaces"), null);
});

test("regions pick the smallest containing box", () => {
  const regions = [{ id: "desk", bbox: [0, 0, 1, 1] }, { id: "desk_left", bbox: [0, 0, 0.5, 1] }];
  assert.equal(core.regionOf([0.1, 0.1, 0.2, 0.2], regions), "desk_left");
  assert.equal(core.regionOf([0.7, 0.1, 0.8, 0.2], regions), "desk");
});

test("tracker needs several hits, drops people, survives one-frame flicker", () => {
  const tr = new core.Tracker({ minHits: 3, maxMisses: 2 });
  const bottle = { label: "bottle", score: 0.8, bbox: [0.1, 0.1, 0.2, 0.3], color: "blue" };
  const person = { label: "person", score: 0.99, bbox: [0.5, 0, 0.9, 1] };
  assert.equal(tr.update([bottle, person]).length, 0);
  assert.equal(tr.update([bottle]).length, 0);
  let stable = tr.update([bottle]);
  assert.equal(stable.length, 1);
  assert.ok(tr.tracks.every((t) => t.label !== "person"));
  stable = tr.update([]); // one missed frame: still stable
  assert.equal(stable.length, 1);
  tr.update([]); tr.update([]);
  assert.equal(tr.tracks.length, 0); // gone after maxMisses
});

test("colour is a majority vote, omitted when undecided", () => {
  const tr = new core.Tracker({ minHits: 1 });
  const at = (color) => ({ label: "cup", score: 0.9, bbox: [0.4, 0.4, 0.5, 0.5], color });
  ["red", "red", "red", "orange"].forEach((c) => tr.update([at(c)]));
  assert.equal(tr.color(tr.tracks[0]), "red");
  const flaky = new core.Tracker({ minHits: 1 });
  const at2 = (color) => ({ label: "cup", score: 0.9, bbox: [0.4, 0.4, 0.5, 0.5], color });
  ["red", "orange", "red", "orange"].forEach((c) => flaky.update([at2(c)]));
  assert.equal(flaky.color(flaky.tracks[0]), null);
});

test("snapshot detections and change signature", () => {
  const tr = new core.Tracker({ minHits: 1 });
  tr.update([{ label: "cell phone", score: 0.9, bbox: [0.1, 0.1, 0.2, 0.2], color: "black" }]);
  tr.update([{ label: "cell phone", score: 0.7, bbox: [0.1, 0.1, 0.2, 0.2], color: "black" }]);
  tr.update([{ label: "cell phone", score: 0.8, bbox: [0.1, 0.1, 0.2, 0.2], color: "black" }]);
  const dets = core.toDetections(tr.stable(), tr);
  assert.equal(dets[0].label, "phone"); // COCO "cell phone" → ORBIT "phone"
  assert.equal(dets[0].confidence, 0.8);
  assert.deepEqual(dets[0].attributes, { color: "black" });
  const regions = [{ id: "left", bbox: [0, 0, 0.5, 1] }];
  const sig = core.sceneSignature(dets, regions);
  assert.equal(sig, "phone|black|left|");
  assert.equal(core.shouldSend(null, sig, 0, 1000), true);
  assert.equal(core.shouldSend(sig, sig, 0, 1000), false);
  assert.equal(core.shouldSend(sig, sig, 0, 61000), true); // heartbeat
  assert.equal(core.shouldSend(sig, "other", 0, 1000), true);
});

test("QR markers tag their host object or stand alone", () => {
  const dets = [{ label: "bottle", score: 0.9, bbox: [0.1, 0.1, 0.3, 0.5] }];
  const out = core.attachMarkers(dets, [
    { text: "orbit:bottle_a", bbox: [0.15, 0.2, 0.2, 0.25] },
    { text: "orbit:cable:c4", bbox: [0.7, 0.7, 0.75, 0.75] },
    { text: "not ours", bbox: [0.5, 0.5, 0.6, 0.6] },
  ]);
  assert.deepEqual(out[0].marker, { type: null, id: "bottle_a" });
  assert.equal(out.length, 2);
  assert.equal(out[1].label, "cable");
  const tr = new core.Tracker({ minHits: 1 });
  tr.update(out);
  const sent = core.toDetections(tr.stable(), tr);
  assert.deepEqual(sent.map((d) => d.marker_id).sort(), ["bottle_a", "c4"]);
});

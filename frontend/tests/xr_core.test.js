// Node tests for the headset view's logic: node --test frontend/tests/xr_core.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("../xr_core.js");

const NOW = Date.parse("2026-10-05T10:00:00Z");
const item = (name, status, extra = {}) => ({ entity_id: name.toLowerCase(), name, type: "thing", status, state: {}, reason: "", ...extra });

test("labels say only what the evidence supports", () => {
  assert.equal(core.itemLine(item("valve", "OBSERVED", { state: { state: "closed" } }), NOW).text, "valve — closed");
  assert.equal(core.itemLine(item("Tool T1", "STALE", { last_supported_at: "2026-10-05T07:00:00Z" }), NOW).text,
    "Tool T1 — seen 3 h ago, may have moved");
  assert.equal(core.itemLine(item("Cable C4", "UNKNOWN", { reason: "confirmed absent from bench_3 by search cov_1" }), NOW).text,
    "Cable C4 — confirmed not here");
  assert.equal(core.itemLine(item("M17", "CONTRADICTED"), NOW).text, "M17 — sources disagree");
  assert.equal(core.itemLine(item("pump", "VERIFIED", { state: { model_number: "CP-200" } }), NOW).text, "pump — model number CP-200");
  assert.equal(core.itemLine(item("x", "OBSERVED"), NOW).tone, core.TONES.OBSERVED);
});

test("a place card leads with what needs attention", () => {
  const place = { id: "bench_3", name: "Bench 3", conflicts: 1, items: [
    item("valve", "OBSERVED"), item("M17", "CONTRADICTED"), item("Tool", "STALE"),
    item("a", "OBSERVED"), item("b", "OBSERVED"), item("c", "OBSERVED"),
  ] };
  const card = core.placeCard(place, NOW);
  assert.equal(card.title, "Bench 3");
  assert.equal(card.subtitle, "6 things · 1 conflict");
  assert.equal(card.lines[0].status, "CONTRADICTED");
  assert.equal(card.lines[1].status, "STALE");
  assert.equal(card.lines.length, 5);
  assert.equal(card.more, 1);
  assert.equal(card.tone, core.TONES.CONTRADICTED);
  assert.equal(core.placeCard({ name: "Shelf", items: [], conflicts: 0 }, NOW).subtitle, "nothing ORBIT knows of here");
  assert.match(core.placeSpeech(place, NOW), /^Bench 3\. M17, sources disagree\./);
});

test("relative times", () => {
  assert.equal(core.relativeTime("2026-10-05T09:59:30Z", NOW), "just now");
  assert.equal(core.relativeTime("2026-10-05T09:45:00Z", NOW), "15 min ago");
  assert.equal(core.relativeTime("2026-10-04T08:00:00Z", NOW), "yesterday");
});

test("pointing at a table finds the table, else a point in front", () => {
  // A 1 m x 1 m table top at height 0.75, centred 1 m in front (−z), identity orientation.
  const table = { label: "table", matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0.75, -1, 1],
    polygon: [{ x: -0.5, z: -0.5 }, { x: 0.5, z: -0.5 }, { x: 0.5, z: 0.5 }, { x: -0.5, z: 0.5 }] };
  const eye = [0, 1.5, 0];
  const down = (() => { const d = [0, -0.75, -1]; const n = Math.hypot(...d); return d.map((v) => v / n); })();
  const hit = core.placementTarget(eye, down, [table]);
  assert.equal(hit.label, "table");
  assert.ok(Math.abs(hit.point[1] - 0.75) < 1e-9 && Math.abs(hit.point[2] + 1) < 1e-9);
  const miss = core.placementTarget(eye, [0, 0, -1], [table], 1.0); // parallel to the table: fallback
  assert.equal(miss.label, null);
  assert.deepEqual(miss.point, [0, 1.5, -1]);
  const beside = core.placementTarget(eye, [0.8, -0.6, 0], [table], 0.5); // hits the plane outside the polygon
  assert.equal(beside.label, null);
});

test("geometry helpers", () => {
  assert.equal(core.rayPlane([0, 1, 0], [0, -1, 0], [0, 0, 0], [0, 1, 0]), 1);
  assert.equal(core.rayPlane([0, 1, 0], [0, 1, 0], [0, 0, 0], [0, 1, 0]), null); // behind
  assert.ok(Math.abs(core.faceYaw([0, 0, 0], [0, 0, 2])) < 1e-9);
  assert.ok(Math.abs(core.faceYaw([0, 0, 0], [2, 0, 0]) - Math.PI / 2) < 1e-9);
});

test("menu lists unpinned places first", () => {
  const items = core.menuItems([{ id: "b", name: "B", xr: { handle: "h" } }, { id: "a", name: "A" }, { id: "lab", name: "Lab", anchor_type: "room" }]);
  assert.deepEqual(items.map((i) => i.label), ["🎙 Talk to Orbi", "Pin A", "Move B", "Leave AR"]);
});

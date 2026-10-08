// ORBIT mixed-reality core (Phase 18): pure logic for the headset view — no DOM, no
// three.js — so it runs under Node tests. Builds the wording of the labels that float
// over real places (only what the evidence supports, with how sure ORBIT is) and the
// geometry for pinning a place where the user points.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OrbitXRCore = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Status → label colour (matches the dashboard's badge colours, readable on passthrough).
  const TONES = {
    VERIFIED: "#4ade80", OBSERVED: "#7aa2ff", INFERRED: "#c4b5fd", STALE: "#fbbf24",
    CONTRADICTED: "#f87171", UNKNOWN: "#9aa4b2",
  };
  const SEVERITY = { CONTRADICTED: 5, UNKNOWN: 4, STALE: 3, INFERRED: 2, OBSERVED: 1, VERIFIED: 0 };

  function relativeTime(iso, nowMs) {
    if (!iso) return "";
    const s = Math.max(0, (nowMs - new Date(iso).getTime()) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    if (s < 172800) return "yesterday";
    return `${Math.round(s / 86400)} d ago`;
  }

  function show(v) {
    if (v === true) return "yes";
    if (v === false) return "no";
    return String(v).replace(/_/g, " ");
  }

  // One line per object: what ORBIT can say about it here, never more.
  function itemLine(item, nowMs) {
    const name = item.name;
    const facts = Object.entries(item.state || {}).map(([k, v]) => (k === "state" || k === "power" ? show(v) : `${k.replace(/_/g, " ")} ${show(v)}`));
    const when = relativeTime(item.last_supported_at, nowMs);
    let text;
    switch (item.status) {
      case "CONTRADICTED": text = `${name} — sources disagree`; break;
      case "UNKNOWN":
        text = /confirmed absent/.test(item.reason || "") ? `${name} — confirmed not here` : `${name} — whereabouts unknown`;
        break;
      case "STALE": text = `${name} — seen ${when || "long ago"}, may have moved`; break;
      case "INFERRED": text = `${name} (inferred)`; break;
      default: text = facts.length ? `${name} — ${facts.join(", ")}` : name;
    }
    return { text, status: item.status, tone: TONES[item.status] || TONES.UNKNOWN };
  }

  function placeCard(place, nowMs, maxLines = 5) {
    const items = [...place.items].sort((a, b) => (SEVERITY[b.status] || 0) - (SEVERITY[a.status] || 0) || a.name.localeCompare(b.name));
    const worst = items.reduce((w, i) => ((SEVERITY[i.status] || 0) > (SEVERITY[w] || 0) ? i.status : w), "VERIFIED");
    const n = items.length;
    const subtitle = n === 0 ? "nothing ORBIT knows of here"
      : `${n} thing${n === 1 ? "" : "s"}` + (place.conflicts ? ` · ${place.conflicts} conflict${place.conflicts === 1 ? "" : "s"}` : "");
    return {
      title: place.name,
      subtitle,
      lines: items.slice(0, maxLines).map((i) => itemLine(i, nowMs)),
      more: Math.max(0, n - maxLines),
      tone: n ? TONES[worst] : TONES.UNKNOWN,
    };
  }

  // What Orbi says when the user points at a place.
  function placeSpeech(place, nowMs) {
    const card = placeCard(place, nowMs, 3);
    if (!place.items.length) return `${place.name}: I don't know of anything there.`;
    const said = card.lines.map((l) => l.text.replace(" — ", ", ")).join(". ");
    return `${place.name}. ${said}.` + (card.more ? ` And ${card.more} more.` : "");
  }

  // ------------------------------------------------------------- geometry
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];

  // Distance t along the ray to the plane (point, normal); null if parallel or behind.
  function rayPlane(origin, dir, point, normal) {
    const denom = dot(dir, normal);
    if (Math.abs(denom) < 1e-6) return null;
    const t = dot(sub(point, origin), normal) / denom;
    return t > 0 ? t : null;
  }

  function inPolygon(x, z, polygon) {
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      const a = polygon[i], b = polygon[j];
      if ((a.z > z) !== (b.z > z) && x < ((b.x - a.x) * (z - a.z)) / (b.z - a.z) + a.x) inside = !inside;
    }
    return inside;
  }

  // planes: [{matrix: 16 numbers (column-major pose of the plane), polygon: [{x, z}], label?}]
  // A WebXR plane is its pose's x–z plane, normal along the pose's y axis.
  // Returns the nearest {point, t, label} hit inside a plane polygon, or the fallback
  // point `fallback` metres along the ray.
  function placementTarget(origin, dir, planes, fallback = 1.0, maxDistance = 6) {
    let best = null;
    for (const p of planes || []) {
      const m = p.matrix;
      const pos = [m[12], m[13], m[14]], xAxis = [m[0], m[1], m[2]], yAxis = [m[4], m[5], m[6]], zAxis = [m[8], m[9], m[10]];
      const t = rayPlane(origin, dir, pos, yAxis);
      if (t === null || t > maxDistance || (best && t >= best.t)) continue;
      const hit = [origin[0] + dir[0] * t, origin[1] + dir[1] * t, origin[2] + dir[2] * t];
      const local = sub(hit, pos);
      if (inPolygon(dot(local, xAxis), dot(local, zAxis), p.polygon)) best = { point: hit, t, label: p.label || null };
    }
    if (best) return best;
    return { point: [origin[0] + dir[0] * fallback, origin[1] + dir[1] * fallback, origin[2] + dir[2] * fallback], t: fallback, label: null };
  }

  // Yaw (radians) that turns an object at `from` to face a viewer at `to` (y-up).
  function faceYaw(from, to) {
    return Math.atan2(to[0] - from[0], to[2] - from[2]);
  }

  // Rooms contain everything; a label for a whole room is noise. Pin surfaces and regions.
  function pinnable(places) {
    return places.filter((p) => p.anchor_type !== "room");
  }

  // Menu: places not yet pinned first, then the rest; plus talking and leaving.
  function menuItems(places, labelsShown = false) {
    const sorted = pinnable(places).sort((a, b) => Number(!!a.xr) - Number(!!b.xr) || a.name.localeCompare(b.name));
    return [
      { action: "talk", label: "🎙 Talk to Orbi" },
      { action: "labels_show", label: `${labelsShown ? "✓ " : ""}Show place labels` },
      { action: "labels_hide", label: `${labelsShown ? "" : "✓ "}Hide place labels` },
      ...sorted.map((p) => ({ action: "pin", id: p.id, label: `${p.xr ? "Move" : "Pin"} ${p.name}` })),
      { action: "exit", label: "Leave AR" },
    ];
  }

  // "show labels" / "hide the place labels" spoken to Orbi → "show" | "hide" | null.
  function labelCommand(text) {
    const m = String(text || "").toLowerCase().match(/\b(show|display|turn on|bring back|hide|remove|turn off)\b.*\blabels?\b/);
    if (!m) return null;
    return /^(show|display|turn on|bring back)$/.test(m[1]) ? "show" : "hide";
  }

  return { labelCommand, TONES, relativeTime, itemLine, placeCard, placeSpeech, rayPlane, inPolygon, placementTarget, faceYaw, pinnable, menuItems };
});

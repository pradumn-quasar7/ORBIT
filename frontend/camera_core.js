// ORBIT camera core — pure decision logic for the live webcam client (Phase 15).
// No DOM access: runs in the browser (window.OrbitCameraCore) and under Node tests.
//
// Raw detector output flickers frame to frame. This module turns it into a small set
// of *stable* detections: tracked across frames by IoU, confirmed after several hits,
// colour decided by majority vote, QR markers made sticky. A snapshot is sent to ORBIT
// only when the stable scene changes (or as a periodic heartbeat to keep facts fresh).
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OrbitCameraCore = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // COCO-SSD classes worth remembering on a work surface. "person" is never sent
  // (privacy, spec §17) — the server enforces the same rule.
  const ALLOWED_LABELS = new Set([
    "bottle", "cup", "wine glass", "bowl", "laptop", "cell phone", "keyboard", "mouse", "remote", "book",
    "scissors", "clock", "vase", "potted plant", "backpack", "handbag", "teddy bear", "toothbrush",
    "banana", "apple", "orange", "tv", "tie", "umbrella", "sports ball", "knife", "spoon", "fork",
  ]);
  const EXCLUDED_LABELS = new Set(["person"]);
  const LABEL_ALIASES = { "cell phone": "phone", "tv": "monitor", "book": "notebook" };

  function normaliseLabel(label) {
    return LABEL_ALIASES[label] || label;
  }

  function iou(a, b) {
    const x0 = Math.max(a[0], b[0]), y0 = Math.max(a[1], b[1]);
    const x1 = Math.min(a[2], b[2]), y1 = Math.min(a[3], b[3]);
    const inter = Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
    const area = (r) => Math.max(0, r[2] - r[0]) * Math.max(0, r[3] - r[1]);
    const union = area(a) + area(b) - inter;
    return union > 0 ? inter / union : 0;
  }

  // ------------------------------------------------------------------ colour
  function rgbToHsv(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    let h = 0;
    if (d) {
      if (max === r) h = ((g - b) / d) % 6;
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60;
      if (h < 0) h += 360;
    }
    return [h, max ? d / max : 0, max];
  }

  function colorName(r, g, b) {
    const [h, s, v] = rgbToHsv(r, g, b);
    if (v < 0.2) return "black";
    if (s < 0.18) return v > 0.8 ? "white" : "gray";
    if (h < 15 || h >= 345) return "red";
    if (h < 40) return "orange";
    if (h < 70) return "yellow";
    if (h < 170) return "green";
    if (h < 260) return "blue";
    if (h < 300) return "purple";
    return "pink";
  }

  // pixels: RGBA bytes of the object's central crop. Returns {color, share}.
  function dominantColor(pixels) {
    const counts = {};
    let n = 0;
    for (let i = 0; i + 3 < pixels.length; i += 4 * 3) { // every third pixel is plenty
      const name = colorName(pixels[i], pixels[i + 1], pixels[i + 2]);
      counts[name] = (counts[name] || 0) + 1;
      n += 1;
    }
    let best = null;
    for (const [name, c] of Object.entries(counts)) if (!best || c > counts[best]) best = name;
    return { color: best, share: n ? counts[best] / n : 0 };
  }

  // ------------------------------------------------------------------ markers
  // "orbit:<id>" or "orbit:<type>:<id>" (ids: letters, digits, _ and -).
  function parseMarker(text) {
    if (typeof text !== "string") return null;
    const m = text.trim().match(/^orbit:(?:([a-z][a-z0-9 _-]*):)?([A-Za-z0-9_-]{1,64})$/i);
    if (!m) return null;
    return { type: m[1] ? m[1].toLowerCase() : null, id: m[2] };
  }

  // ------------------------------------------------------------------ regions
  function regionOf(bbox, regions) {
    const cx = (bbox[0] + bbox[2]) / 2, cy = (bbox[1] + bbox[3]) / 2;
    let best = null, bestArea = Infinity;
    for (const r of regions) {
      const [x0, y0, x1, y1] = r.bbox;
      if (cx >= x0 && cx <= x1 && cy >= y0 && cy <= y1) {
        const area = (x1 - x0) * (y1 - y0);
        if (area < bestArea) { best = r.id; bestArea = area; }
      }
    }
    return best;
  }

  // ------------------------------------------------------------------ tracker
  class Tracker {
    constructor(opts = {}) {
      this.minHits = opts.minHits ?? 3; // consecutive-ish sightings before an object counts
      this.maxMisses = opts.maxMisses ?? 4; // frames an object may vanish before it is dropped
      this.matchIou = opts.matchIou ?? 0.3;
      this.minColorShare = opts.minColorShare ?? 0.6;
      this.tracks = [];
      this.nextId = 1;
    }

    // dets: [{label, score, bbox:[x0,y0,x1,y1] normalised, color?, marker?:{id,type}}]
    update(dets) {
      const usable = dets.filter((d) => !EXCLUDED_LABELS.has(d.label));
      const unmatched = new Set(this.tracks.map((_, i) => i));
      const pairs = [];
      usable.forEach((d, di) => this.tracks.forEach((t, ti) => {
        if (t.label === d.label) {
          const o = iou(t.bbox, d.bbox);
          if (o >= this.matchIou) pairs.push([o, ti, di]);
        }
      }));
      pairs.sort((a, b) => b[0] - a[0]);
      const usedDet = new Set();
      for (const [, ti, di] of pairs) {
        if (!unmatched.has(ti) || usedDet.has(di)) continue;
        unmatched.delete(ti);
        usedDet.add(di);
        this._absorb(this.tracks[ti], usable[di]);
      }
      for (const ti of unmatched) this.tracks[ti].misses += 1;
      usable.forEach((d, di) => {
        if (!usedDet.has(di)) {
          const t = { id: `t${this.nextId++}`, label: d.label, bbox: d.bbox.slice(), hits: 0, misses: 0, scores: [], colors: {}, marker: null };
          this._absorb(t, d);
          this.tracks.push(t);
        }
      });
      this.tracks = this.tracks.filter((t) => t.misses <= this.maxMisses);
      return this.stable();
    }

    _absorb(t, d) {
      t.hits += 1;
      t.misses = 0;
      t.bbox = t.bbox.map((v, i) => 0.6 * v + 0.4 * d.bbox[i]); // smooth jitter
      t.scores.push(d.score);
      if (t.scores.length > 10) t.scores.shift();
      if (d.color) t.colors[d.color] = (t.colors[d.color] || 0) + 1;
      if (d.marker) t.marker = d.marker; // markers are sticky
    }

    color(t) {
      const total = Object.values(t.colors).reduce((a, b) => a + b, 0);
      if (total < 3) return null;
      let best = null;
      for (const [c, n] of Object.entries(t.colors)) if (!best || n > t.colors[best]) best = c;
      return t.colors[best] / total >= this.minColorShare ? best : null; // undecided colour is omitted
    }

    stable() {
      return this.tracks.filter((t) => t.hits >= this.minHits && t.misses <= 1);
    }
  }

  // Stable tracks → detections for POST /cameras/{id}/snapshot.
  function toDetections(tracks, tracker) {
    return tracks.map((t) => {
      const color = tracker.color(t);
      const d = {
        label: normaliseLabel(t.marker && t.marker.type ? t.marker.type : t.label),
        confidence: Math.round((t.scores.reduce((a, b) => a + b, 0) / t.scores.length) * 1000) / 1000,
        bbox: t.bbox.map((v) => Math.round(Math.min(1, Math.max(0, v)) * 10000) / 10000),
        attributes: color ? { color } : {},
      };
      if (t.marker) d.marker_id = t.marker.id;
      return d;
    });
  }

  // What would ORBIT be told? Used to send only when this changes.
  function sceneSignature(detections, regions) {
    return detections
      .map((d) => [d.label, d.attributes.color || "", regionOf(d.bbox, regions) || "", d.marker_id || ""].join("|"))
      .sort()
      .join(";");
  }

  function shouldSend(previous, current, lastSentAt, now, heartbeatMs = 60000) {
    if (previous === null) return true;
    if (current !== previous) return true;
    return now - lastSentAt >= heartbeatMs;
  }

  // QR codes that sit on a detected object tag that object; a QR naming a type with no
  // detection around it becomes an object of its own (e.g. a cable COCO cannot see).
  function attachMarkers(dets, codes) {
    const out = dets.map((d) => ({ ...d }));
    for (const code of codes) {
      const marker = parseMarker(code.text);
      if (!marker) continue;
      const cx = (code.bbox[0] + code.bbox[2]) / 2, cy = (code.bbox[1] + code.bbox[3]) / 2;
      const host = out
        .filter((d) => cx >= d.bbox[0] && cx <= d.bbox[2] && cy >= d.bbox[1] && cy <= d.bbox[3])
        .sort((a, b) => (a.bbox[2] - a.bbox[0]) * (a.bbox[3] - a.bbox[1]) - (b.bbox[2] - b.bbox[0]) * (b.bbox[3] - b.bbox[1]))[0];
      if (host) host.marker = marker;
      else if (marker.type) out.push({ label: marker.type, score: 1, bbox: code.bbox.slice(), marker });
    }
    return out;
  }

  return {
    ALLOWED_LABELS, EXCLUDED_LABELS, normaliseLabel, iou, colorName, dominantColor, parseMarker,
    regionOf, Tracker, toDetections, sceneSignature, shouldSend, attachMarkers,
  };
});

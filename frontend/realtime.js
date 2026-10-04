// ORBIT live connection (Phase 17): one EventSource per page, auto-reconnecting.
// The browser resends Last-Event-ID on reconnect, so the server replays what was missed.
// Usage: OrbitLive.connect("/stream?topics=world,observation", { onMessage(msg), onStatus(state) })
//        → { close() }. state: "connecting" | "live" | "reconnecting" | "unsupported".
(function (root) {
  "use strict";

  function connect(url, handlers) {
    const onMessage = handlers.onMessage || function () {};
    const onStatus = handlers.onStatus || function () {};
    if (!("EventSource" in root)) {
      onStatus("unsupported");
      return { close() {} };
    }
    onStatus("connecting");
    const source = new EventSource(url);
    source.onopen = () => onStatus("live");
    source.onerror = () => onStatus(source.readyState === EventSource.CLOSED ? "closed" : "reconnecting");
    const deliver = (e) => {
      try { onMessage(JSON.parse(e.data)); } catch (err) { /* a malformed frame is skipped */ }
    };
    ["world", "observation", "assistant"].forEach((topic) => source.addEventListener(topic, deliver));
    // Messages were dropped because this tab fell behind: ask the page to resync.
    source.addEventListener("overflow", () => onMessage({ topic: "overflow", type: "OVERFLOW", data: {} }));
    return { close() { source.close(); onStatus("closed"); } };
  }

  // Render a connection pill: <span class="live-pill" data-state="live">● live</span>
  function pill(el, state) {
    if (!el) return;
    el.dataset.state = state;
    el.textContent = { live: "● live", connecting: "○ connecting", reconnecting: "○ reconnecting", closed: "○ offline", unsupported: "○ no live updates" }[state] || state;
    el.title = state === "live" ? "Updates arrive as soon as ORBIT commits them" : "Not receiving live updates";
  }

  root.OrbitLive = { connect, pill };
})(typeof self !== "undefined" ? self : this);

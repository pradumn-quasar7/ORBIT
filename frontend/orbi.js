// Orbi on the desktop (Phase 19.6): the avatar alone, for Orbi.app's floating window.
// Click Orbi to start a hands-free conversation (Groq, else Gemini Live); click again
// to stop. ORBIT's notices appear and are spoken even when you didn't ask.
import { createAvatar } from "./avatar.js";

const $ = (id) => document.getElementById(id);
const native = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.orbi;
const tell = (msg) => { try { if (native) native.postMessage(msg); } catch { /* not in Orbi.app */ } };

const avatar = createAvatar($("stage"));
let session = null, engine = null, conversation = null, hideTimer = null;

function bubble(html, { alert = false, keep = false } = {}) {
  const b = $("bubble");
  b.replaceChildren(...html);
  b.classList.toggle("alert", alert);
  b.classList.add("show");
  clearTimeout(hideTimer);
  if (!keep) hideTimer = setTimeout(() => b.classList.remove("show"), 9000);
}
const text = (s, cls) => { const span = document.createElement("span"); if (cls) span.className = cls; span.textContent = s; return span; };

function state(s, label) {
  $("ring").dataset.state = s;
  $("state").textContent = label;
  avatar.setState({ listening: "listening", thinking: "thinking", speaking: "speaking" }[s] || "idle");
  tell({ type: "state", state: s });
}

async function api(method, path, body) {
  const res = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
  return data;
}

async function toggle() {
  if (session) { session.stop(); return; }
  if (!engine) {
    bubble([text("Add a Groq key (or Gemini) to ORBIT's .env to talk to me.")], { alert: true });
    return;
  }
  const Engine = engine === "groq" ? (await import("./conversation.js")).HostedConversation : (await import("./live.js")).LiveSession;
  session = new Engine({
    device: "mac", userId: "operator", conversationId: conversation,
    on: {
      state(s) {
        if (s === "connecting") state("thinking", "starting…");
        else if (s === "listening") state("listening", "listening — just talk");
        else if (s === "thinking") state("thinking", "thinking…");
        else if (s === "speaking") state("speaking", "speaking — talk to interrupt");
        else if (s === "closed") { session = null; state("off", "click me to talk"); }
      },
      userText(t) { bubble([text(`“${t}”`, "you")], { keep: true }); },
      orbiText(t) { bubble([text(t)]); },
      tool(name, args, result) {
        if (!result.ok) { avatar.gesture("SHRUG"); bubble([text(result.error)], { alert: true }); }
        else avatar.gesture(result.waiting_for_user_consent ? "ASK" : name === "sing" ? "WAVE" : "NOD");
      },
      level(v) { if (v > 0.02) avatar.mouth(v); },
      error(m) { bubble([text(m)], { alert: true }); avatar.gesture("SHRUG"); },
    },
  });
  try {
    await session.start();
    conversation = session.conversationId;
    listen();
  } catch (err) {
    bubble([text(`I can't listen: ${err.message}`)], { alert: true });
    if (session) session.stop();
  }
}

// ORBIT's own notices ("Verified: the valve is open"), for this conversation.
let stream = null;
function listen() {
  if (stream || !conversation) return;
  stream = OrbitLive.connect(`/stream?topics=assistant&conversation=${encodeURIComponent(conversation)}`, {
    onMessage(msg) {
      if (msg.type !== "NOTICE") return;
      avatar.gesture(msg.data.gesture);
      if (session) session.inform(msg.data.speech || msg.data.reply);
      else bubble([text(msg.data.reply)], { alert: msg.data.gesture === "ALERT" });
    },
  });
}

$("stage").addEventListener("click", toggle);
$("hide").addEventListener("click", (e) => { e.stopPropagation(); tell({ type: "hide" }); });
$("hide").hidden = !native; // only inside Orbi.app
window.orbiToggle = toggle; // the menu-bar "Talk" item

(async function init() {
  try {
    const st = await api("GET", "/voice/status");
    engine = st.engines.groq ? "groq" : st.engines.gemini ? "gemini" : null;
    state("off", "click me to talk");
    avatar.gesture("WAVE");
    bubble([text("Hi! I'm Orbi. Click me and just talk.")]);
  } catch {
    state("off", "ORBIT isn't running");
    bubble([text("ORBIT isn't running yet. Starting it…")], { alert: true });
    tell({ type: "server-down" });
    setTimeout(() => location.reload(), 4000);
  }
})();

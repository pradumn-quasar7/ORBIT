// ORBIT assistant page (Phase 16): chat + voice + 3D avatar. All text via textContent.
const $ = (id) => document.getElementById(id);
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};
const SUGGESTIONS = [
  "Where is the microscope?", "What changed?", "Continue.", "I put the notebook on bench 4",
  "Open the valve", "What should I check?", "What did you do for me?",
];

let avatar = null;
let conversation = null;
let busy = false;
let countdown = null;

// ------------------------------------------------------------------ avatar
const noAvatar = { setState() {}, setAlert() {}, gesture() {}, mouth() {} };
async function loadAvatar() {
  try {
    const { createAvatar } = await import("./avatar.js");
    avatar = createAvatar($("avatar"));
  } catch (err) {
    avatar = noAvatar;
    $("avatar-fallback").hidden = false;
  }
  setState("idle");
}

function setState(s) {
  avatar.setState(s);
  $("state-label").textContent = { idle: "ready", listening: "listening…", thinking: "thinking…", speaking: "speaking" }[s] || s;
}

// ------------------------------------------------------------------- voice
const synth = "speechSynthesis" in window ? window.speechSynthesis : null;
function speak(text) {
  return new Promise((resolve) => {
    if (!synth || !$("speak").checked || !text) {
      setState("speaking");
      setTimeout(() => { setState("idle"); resolve(); }, Math.min(4000, 400 + text.length * 35));
      return;
    }
    synth.cancel();
    const u = new SpeechSynthesisUtterance(text);
    const voice = synth.getVoices().find((v) => /en[-_](IN|GB|US)/i.test(v.lang) && /natural|google|samantha|daniel|rishi|veena/i.test(v.name))
      || synth.getVoices().find((v) => /^en/i.test(v.lang));
    if (voice) u.voice = voice;
    u.rate = 1.03;
    u.onstart = () => setState("speaking");
    u.onboundary = (e) => {
      const word = text.slice(e.charIndex, e.charIndex + (e.charLength || 6));
      avatar.mouth(Math.min(1, 0.35 + (word.match(/[aeiouAEIOU]/g) || []).length * 0.18));
    };
    const done = () => { setState("idle"); resolve(); };
    u.onend = done;
    u.onerror = done;
    synth.speak(u);
  });
}

// Voice input: browser speech service, or Whisper on this device (voice.js).
let voice = null;
let errorShown = false;
function voiceStatus(text, kind) {
  const h = $("voice-hint");
  h.textContent = text;
  h.className = `voice-status ${kind || ""}`;
}

async function setupMic() {
  const { Voice, inClaudeApp, explain } = await import("./voice.js");
  const select = $("engine");
  if (!Voice.browserAvailable) select.querySelector('option[value="browser"]').disabled = true;
  const saved = store.get("orbit.voice");
  voice = new Voice({
    onPartial(text) { $("text").value = text; },
    onFinal(text) { $("text").value = text; send(text); },
    onState(state, engine) {
      $("mic").setAttribute("aria-pressed", String(state === "listening"));
      $("mic").textContent = state === "listening" ? "■ Stop" : state === "transcribing" ? "… transcribing" : state === "loading" ? "… loading" : "🎙 Talk";
      $("mic").disabled = state === "transcribing";
      if (state === "listening") {
        errorShown = false;
        setState("listening");
        voiceStatus(engine === "local" ? "Listening (on this device)… speak, then pause." : "Listening… speak, then pause.", "ok");
      } else if (state === "transcribing") {
        setState("thinking");
        voiceStatus("Transcribing on this device…", "ok");
      } else {
        if (!busy) setState("idle");
        if (!errorShown) voiceStatus("Ready. Press Talk to speak again.", "");
      }
    },
    onError(code, message) {
      errorShown = true;
      voiceStatus(message, "error");
      if (inClaudeApp) $("open-browser").hidden = false;
    },
    onInfo(text) { voiceStatus(text, "info"); },
    onLevel(level) { $("level").style.transform = `scaleX(${level.toFixed(3)})`; },
  });
  if (saved && saved !== "live" && (saved !== "browser" || Voice.browserAvailable)) voice.engine = saved;
  select.value = voice.engine;
  // Realtime conversation on a hosted service, when ORBIT has a key (Phase 19):
  // Groq (free plan) first, else Gemini Live.
  const { voiceStatus: hostedStatus } = await import("./conversation.js");
  const st = await hostedStatus();
  liveEngine = st && st.engines.groq ? "groq" : st && st.engines.gemini ? "gemini" : null;
  liveAvailable = !!liveEngine;
  const liveOption = select.querySelector('option[value="live"]');
  liveOption.disabled = !liveAvailable;
  liveOption.textContent = liveEngine === "groq" ? "Realtime conversation (Groq)" : liveEngine === "gemini" ? "Gemini Live (realtime)"
    : "Realtime (add a Groq or Gemini key to .env)";
  if (liveAvailable && (!saved || saved === "live")) select.value = "live";
  select.addEventListener("change", () => {
    store.set("orbit.voice", select.value);
    if (select.value !== "live") voice.engine = select.value;
  });
  $("mic").addEventListener("click", () => {
    if (synth) synth.cancel();
    if (select.value === "live") toggleLive(); else voice.toggle();
  });
  $("copy-link").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(location.href); $("copy-link").textContent = "Copied"; }
    catch { $("copy-link").textContent = "Select the link and copy it"; }
  });

  if (inClaudeApp) {
    voiceStatus(explain("not-allowed"), "error");
    $("open-browser").hidden = false;
    return;
  }
  // Tell the user up front if the microphone is already blocked for this site.
  try {
    const perm = await navigator.permissions.query({ name: "microphone" });
    const show = () => {
      if (perm.state === "denied") voiceStatus(explain("not-allowed"), "error");
      else voiceStatus(perm.state === "granted" ? "Microphone ready. Press Talk and speak." : "Press Talk — your browser will ask to use the microphone.", "");
    };
    show();
    perm.onchange = show;
  } catch {
    voiceStatus("Press Talk and speak.", "");
  }
}

// ------------------------------------------------------------------- live
let liveAvailable = false;
let liveEngine = null; // "groq" | "gemini"
let liveSession = null;
let liveUser = null, liveOrbi = null; // the chat bubbles being filled as words arrive

async function toggleLive() {
  if (liveSession) { liveSession.stop(); return; }
  const Engine = liveEngine === "groq" ? (await import("./conversation.js")).HostedConversation : (await import("./live.js")).LiveSession;
  if (!conversation) await startConversation();
  liveSession = new Engine({
    device: "mac",
    userId: $("user").value.trim() || "operator",
    conversationId: conversation.id,
    on: {
      state(s) {
        if (s === "connecting") { $("mic").textContent = "… connecting"; voiceStatus("Starting the conversation…", "info"); }
        if (s === "listening") { $("mic").textContent = "■ Stop"; $("mic").setAttribute("aria-pressed", "true"); setState("listening"); voiceStatus("Just talk, then pause. You can interrupt Orbi any time.", "ok"); }
        if (s === "thinking") setState("thinking");
        if (s === "speaking") setState("speaking");
        if (s === "closed") {
          liveSession = null; liveUser = liveOrbi = null;
          $("mic").textContent = "🎙 Talk"; $("mic").setAttribute("aria-pressed", "false"); setState("idle");
          $("level").style.transform = "scaleX(0)";
        }
      },
      userText(text, final) {
        if (!liveUser) { liveUser = el("li", "msg user", ""); $("log").append(liveUser); liveOrbi = null; }
        liveUser.textContent = text;
        liveUser.scrollIntoView({ block: "end" });
        if (final) liveUser = null;
      },
      orbiText(text, final) {
        if (!liveOrbi) { liveOrbi = el("li", "msg bot", ""); liveOrbi.append(el("div", "say", "")); $("log").append(liveOrbi); liveUser = null; }
        liveOrbi.querySelector(".say").textContent = text;
        liveOrbi.scrollIntoView({ block: "end" });
        if (final) liveOrbi = null;
      },
      tool(name, args, result) {
        const line = el("li", "msg bot tool" + (result.ok ? "" : " alert"),
          result.ok ? `✓ ${result.result || result.answer || name}` : `✗ ${result.error}`);
        $("log").append(line);
        if (result.waiting_for_user_consent) { avatar.gesture("ASK"); }
        if (result.song_url && liveEngine === "gemini") new Audio(result.song_url).play().catch(() => {}); // Groq's engine plays it itself
        if (result.lyrics) avatar.gesture("WAVE");
        line.scrollIntoView({ block: "end" });
      },
      level(v) { if (v > 0.02) avatar.mouth(v); },
      micLevel(v) { $("level").style.transform = `scaleX(${v.toFixed(3)})`; },
      error(message) { voiceStatus(message, "error"); },
    },
  });
  try {
    await liveSession.start();
    if (liveSession && liveSession.conversationId !== conversation.id) { conversation = { id: liveSession.conversationId }; listen(); }
  } catch (err) {
    voiceStatus(`Gemini Live: ${err.message}`, "error");
    if (liveSession) liveSession.stop();
  }
}

// --------------------------------------------------------------------- api
async function api(method, path, body) {
  const res = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
  return data;
}

async function startConversation() {
  const user = $("user").value.trim() || "operator";
  store.set("orbit.user", user);
  conversation = await api("POST", "/assistant/conversations", { user_id: user });
  $("log").replaceChildren();
  listen();
}

// ---------------------------------------------------------------- realtime
// Orbi speaks up unasked when the world changes (Phase 17): the server pushes notices
// for this conversation only.
let noticeStream = null;
let speech = Promise.resolve();
function listen() {
  if (noticeStream) noticeStream.close();
  noticeStream = OrbitLive.connect(`/stream?topics=assistant&conversation=${encodeURIComponent(conversation.id)}`, {
    onMessage(msg) { if (msg.type === "NOTICE") addNotice(msg.data); },
    onStatus(state) { OrbitLive.pill($("live"), state); },
  });
}

function addNotice(notice) {
  const li = el("li", "msg bot notice" + (notice.gesture === "ALERT" ? " alert" : ""));
  li.append(el("div", "notice-label", "Orbi noticed"), el("div", "", notice.reply));
  if (notice.action) {
    const line = el("div", "meta-line");
    line.append(el("span", `badge ${notice.action.status}`, `action: ${notice.action.status.toLowerCase().replace(/_/g, " ")}`));
    li.append(line);
  }
  $("log").append(li);
  li.scrollIntoView({ block: "end", behavior: "smooth" });
  avatar.setAlert(notice.gesture === "ALERT");
  avatar.gesture(notice.gesture);
  // Queue behind whatever Orbi is saying; a new user message still cancels speech.
  if (liveSession) liveSession.inform(notice.speech || notice.reply);
  else speech = speech.then(() => speak(notice.speech));
}

// -------------------------------------------------------------------- chat
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function addUser(text) {
  const li = el("li", "msg user", text);
  $("log").append(li);
  li.scrollIntoView({ block: "end", behavior: "smooth" });
}

function addBot(turn) {
  const li = el("li", "msg bot" + (turn.gesture === "ALERT" ? " alert" : ""));
  li.append(el("div", "", turn.reply));

  const claims = (turn.response && turn.response.claims) || [];
  const statuses = [...new Set(claims.map((c) => c.status))];
  const line = el("div", "meta-line");
  statuses.forEach((s) => line.append(el("span", `badge ${s}`, s)));
  if (turn.action) line.append(el("span", `badge ${turn.action.status}`, `action: ${turn.action.status.toLowerCase().replace(/_/g, " ")}`));
  if (turn.command.parser && turn.command.parser.startsWith("anthropic")) line.append(el("span", "badge UNKNOWN", "parsed by Claude"));
  if (line.children.length) li.append(line);

  if (turn.done.length) {
    const ul = el("ul", "did");
    turn.done.forEach((d) => ul.append(el("li", "", d.summary)));
    li.append(ul);
  }
  if (turn.observation_requests.length) {
    const look = el("div", "look");
    look.append(el("strong", "", "To settle it: "), document.createTextNode(turn.observation_requests[0].instruction));
    li.append(look);
  }
  if (turn.pending) li.append(confirmCard(turn.pending));
  $("log").append(li);
  li.scrollIntoView({ block: "end", behavior: "smooth" });
}

function confirmCard(pending) {
  const box = el("div", "confirm");
  box.append(el("div", "", pending.summary));
  const row = el("div", "row");
  const yes = el("button", "primary", "Yes, authorize");
  const no = el("button", "", "No");
  const count = el("span", "count");
  yes.type = no.type = "button";
  yes.addEventListener("click", () => send("yes"));
  no.addEventListener("click", () => send("no"));
  row.append(yes, no, count);
  box.append(row);
  clearInterval(countdown);
  const expires = new Date(pending.expires_at).getTime();
  const serverSkew = Date.now() - new Date(pending.created_at).getTime();
  const tick = () => {
    const left = Math.round((expires - (Date.now() - serverSkew)) / 1000);
    if (left <= 0) {
      count.textContent = "expired";
      yes.disabled = no.disabled = true;
      clearInterval(countdown);
    } else count.textContent = `${left}s`;
  };
  tick();
  countdown = setInterval(tick, 1000);
  return box;
}

async function send(text) {
  text = (text || "").trim();
  if (!text || busy) return;
  busy = true;
  $("text").value = "";
  if (synth) synth.cancel();
  addUser(text);
  setState("thinking");
  avatar.setAlert(false);
  const typing = el("li", "msg bot typing", "…");
  $("log").append(typing);
  try {
    if (!conversation) await startConversation();
    let turn;
    try {
      turn = await api("POST", `/assistant/conversations/${encodeURIComponent(conversation.id)}/messages`, { text });
    } catch (err) {
      if (!/not found/i.test(err.message)) throw err;
      await startConversation(); // server restarted: conversations are process-local
      addUser(text);
      turn = await api("POST", `/assistant/conversations/${encodeURIComponent(conversation.id)}/messages`, { text });
    }
    typing.remove();
    clearInterval(countdown);
    document.querySelectorAll("#log .confirm").forEach((c) => c.remove()); // only the latest turn offers a decision
    addBot(turn);
    avatar.setAlert(turn.gesture === "ALERT");
    avatar.gesture(turn.gesture);
    speech = speak(turn.speech); // the user may answer (or interrupt) while Orbi is still talking
  } catch (err) {
    typing.remove();
    const li = el("li", "msg bot alert", `Something went wrong: ${err.message}`);
    $("log").append(li);
    setState("idle");
  } finally {
    busy = false;
  }
}

// -------------------------------------------------------------------- init
async function init() {
  const saved = store.get("orbit.user");
  if (saved) $("user").value = saved;
  SUGGESTIONS.forEach((s) => {
    const b = el("button", "", s);
    b.type = "button";
    b.addEventListener("click", () => send(s));
    $("chips").append(b);
  });
  $("form").addEventListener("submit", (e) => { e.preventDefault(); send($("text").value); });
  $("user").addEventListener("change", () => { conversation = null; $("log").replaceChildren(); });
  setupMic();
  await loadAvatar();
  try {
    const info = await api("GET", "/assistant");
    $("parser").textContent = info.llm ? `language: ${info.parser}` : "language: on-device rules";
    await startConversation();
    const hello = await api("POST", `/assistant/conversations/${encodeURIComponent(conversation.id)}/messages`, { text: "hello" });
    addBot(hello);
    avatar.gesture("WAVE");
  } catch (err) {
    $("log").append(el("li", "msg bot alert", `Cannot reach ORBIT: ${err.message}`));
  }
}

init();

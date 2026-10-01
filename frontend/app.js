// ORBIT inspection dashboard — plain JS, no build step. All text is inserted with
// textContent so stored world-state strings can never inject markup.
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") node.className = v;
      else if (k === "onclick") node.addEventListener("click", v);
      else node.setAttribute(k, v);
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  const badge = (status) => el("span", { class: `badge ${status || "UNKNOWN"}` }, status || "UNKNOWN");
  const when = (iso) => (iso ? new Date(iso).toISOString().slice(0, 16).replace("T", " ") + " UTC" : "—");
  const show = (v) => (v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));

  function fillList(target, items, render, emptyText) {
    target.replaceChildren(...(items.length ? items.map(render) : [el("li", { class: "empty" }, emptyText)]));
  }

  function renderSummary(s) {
    $("as-of").textContent = `as of ${when(s.as_of)}`;
    $("baseline").textContent = s.baseline ? `since ${when(s.baseline)}` : "";
    $("counts").replaceChildren(
      ...[
        ["entities", "tracked entities"],
        ["uncertain_claims", "claims needing a fresh look"],
        ["open_conflicts", "open conflicts"],
        ["open_tasks", "open tasks"],
      ].map(([k, label]) => el("div", { class: "count" }, el("b", {}, s.counts[k] ?? 0), el("span", {}, label)))
    );

    $("world").replaceChildren(
      ...(s.entities.length
        ? s.entities.map((e) =>
            el(
              "tr",
              {},
              el("td", {}, el("div", {}, e.name), e.name !== e.entity_id ? el("div", { class: "mono hint" }, e.entity_id) : null,
                e.identity_status !== "ESTABLISHED" ? badge(e.identity_status) : null),
              el("td", {}, e.type),
              el("td", { class: "mono" }, show(e.location), e.location_note ? el("div", { class: "hint" }, e.location_note) : null),
              el("td", {}, badge(e.location_status), " ", e.freshness && e.freshness !== "FRESH" ? badge(e.freshness) : null),
              el("td", { class: "mono" }, when(e.last_supported_at)),
              el("td", {}, Object.entries(e.attributes).map(([a, v]) =>
                el("span", { class: "attr" }, `${a}=`, el("span", { class: "mono" }, show(v.value)), " ", badge(v.status))))
            )
          )
        : [el("tr", {}, el("td", { colspan: "6", class: "empty" }, "No entities yet. Run scripts/seed_demo.py."))])
    );

    fillList($("changes"), s.changes, (c) => el("li", {}, c), "No evidence-supported changes.");
    fillList(
      $("observations"),
      s.requested_observations,
      (o) => el("li", {}, o.instruction, " ", el("span", { class: "hint mono" }, `score ${o.score.toFixed(2)} · ${o.resolves.join(", ")}`)),
      "Nothing to refresh — current claims are supported."
    );
    fillList(
      $("conflicts"),
      s.conflicts,
      (c) => el("li", {}, el("b", {}, `${c.entity_id}.${c.attribute}: `),
        c.sides.map((side, i) => [i ? " vs " : "", el("span", { class: "mono" }, show(side.value)), ` (${side.sources.join(", ")})`])),
      "No open conflicts."
    );

    $("tasks").replaceChildren(
      ...(s.tasks.length
        ? s.tasks.map((t) =>
            el("div", { class: "task" },
              el("h3", {}, t.goal, " ", badge(t.status)),
              t.steps.map((st) =>
                el("div", { class: "step" },
                  el("span", { class: "mono" }, `${st.step_order}.`),
                  el("span", {}, st.description),
                  badge(st.status),
                  st.status === "COMPLETED" ? badge(st.completion_status) : null,
                  st.blocked_reason ? el("span", { class: "why" }, st.blocked_reason) : null)
              )))
        : [el("p", { class: "empty" }, "No tasks.")])
    );
  }

  async function load() {
    try {
      const res = await fetch("/inspect/summary");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      renderSummary(await res.json());
    } catch (err) {
      $("as-of").textContent = `could not load: ${err.message}`;
    }
  }

  function renderAnswer(r) {
    const box = $("answer");
    box.hidden = false;
    box.className = `answer${r.abstained ? " abstained" : ""}`;
    const parts = [
      el("div", { class: "verdict" }, r.abstained ? "Abstained — evidence insufficient" : "Answer", ` · ${r.intent.kind}`),
      el("p", {}, r.answer || r.summary),
    ];
    if (r.answer && r.summary !== r.answer) parts.push(el("p", { class: "hint" }, r.summary.replace(r.answer, "").trim()));
    if (r.requested_observation) parts.push(el("p", { class: "request" }, "Requested observation: ", r.requested_observation.instruction));
    if (r.conflicts.length)
      parts.push(el("ul", {}, r.conflicts.map((c) => el("li", {}, el("span", { class: "mono" }, show(c.value)), ` — ${c.sources.join(", ")} `, badge(c.status)))));
    if (r.claims.length)
      parts.push(el("ul", {}, r.claims.slice(0, 12).map((c) =>
        el("li", {}, badge(c.status), " ", c.claim, " ", el("span", { class: "hint mono" }, c.evidence_refs.length ? `evidence: ${c.evidence_refs.slice(0, 2).join(", ")}` : "no evidence")))));
    box.replaceChildren(...parts);
  }

  async function ask(q) {
    if (!q.trim()) return;
    const res = await fetch("/queries", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: q }) });
    renderAnswer(await res.json());
    load();
  }

  $("ask").addEventListener("submit", (e) => { e.preventDefault(); ask($("q").value); });
  $("refresh").addEventListener("click", load);
  $("examples").replaceChildren(
    ...["What changed?", "Continue.", "Where is M17?", "What is the configuration of M17?", "Where is the bottle?"].map((q) =>
      el("button", { type: "button", onclick: () => { $("q").value = q; ask(q); } }, q))
  );
  load();
  setInterval(load, 15000);
})();

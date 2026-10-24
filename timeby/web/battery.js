// Energy battery agent: a row at the top of the Agents list (same pattern as the other agents) and a battery pill next to
// "Log activity". Derived — it reads sleep and the other agents' results; the owner only says how they feel.
// Reuses v3.html globals (S, render, renderAgents, post, toggle, logControl, showToast, esc, $, ltab, renderChrome, openAgents).
(() => {
  const FEEL = [["well", "Normal"], ["tired", "Tired"], ["unwell", "Unwell"]];
  const FEEL_SCOPE = [["today", "Today"], ["open", "Until I feel better"]];
  const TONE = { charged: "good", steady: "", low: "mid", drained: "low" };

  document.head.insertAdjacentHTML("beforeend", `<style>
    .batt-pill { display: inline-flex; align-items: center; gap: 8px; height: 36px; padding: 0 12px; border: 0; border-radius: 10px;
      background: var(--surface); box-shadow: var(--shadow); font-weight: 700; font-size: 14px; color: var(--ink); }
    .batt-pill[hidden] { display: none; }
    .viewbar .right { display: flex; gap: 10px; align-items: center; }
    .batt-icon { position: relative; width: 26px; height: 13px; border: 2px solid currentColor; border-radius: 3px; color: var(--muted); }
    .batt-icon::after { content: ""; position: absolute; right: -5px; top: 2px; width: 3px; height: 5px; border-radius: 0 2px 2px 0; background: currentColor; }
    .batt-icon i { position: absolute; left: 1px; top: 1px; bottom: 1px; border-radius: 1px; background: var(--accent); }
    .batt-icon.mid i { background: var(--warn); } .batt-icon.low i { background: var(--bad); }
    .batt .gauge { position: relative; height: 14px; border-radius: 7px; background: var(--surface-2); overflow: hidden; margin: 4px 0; }
    .batt .gauge .fill { position: absolute; inset: 0 auto 0 0; background: var(--accent); border-radius: 7px; transition: width .5s; }
    .batt .gauge .fill.mid { background: var(--warn); } .batt .gauge .fill.low { background: var(--bad); }
    .batt .gauge .cap { position: absolute; top: 0; bottom: 0; border-left: 2px dashed var(--muted); }
    .batt .gauge .lost { position: absolute; top: 0; bottom: 0; right: 0; background: repeating-linear-gradient(135deg, transparent 0 4px, var(--line) 4px 6px); }
    .batt .big { font-size: 26px; font-weight: 800; line-height: 1; }
    .batt .line { font-size: 12px; color: var(--muted); }
    .batt .advice { font-size: 13px; margin: 6px 0 2px; }
    .batt .factors { list-style: none; margin: 2px 0 0; padding: 0; display: grid; gap: 3px; font-size: 13px; }
    .batt .factors li { display: grid; grid-template-columns: 40px 1fr auto; gap: 8px; align-items: baseline; }
    .batt .factors .t { color: var(--faint); font-size: 12px; font-variant-numeric: tabular-nums; }
    .batt .factors .d { font-weight: 700; font-variant-numeric: tabular-nums; }
    .batt .factors .d.plus { color: var(--accent); } .batt .factors .d.minus { color: var(--bad); } .batt .factors .d.zero { color: var(--faint); }
    .batt .feel { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 4px; }
    .batt .estimate { font-size: 11.5px; color: var(--faint); margin-top: 6px; }
  </style>`);

  // ---------- the pill next to "Log activity" ----------
  $("log-btn").closest(".log-wrap").insertAdjacentHTML("beforebegin",
    `<div class="right" id="viewbar-right"></div>`);
  const right = $("viewbar-right");
  right.append($("log-btn").closest(".log-wrap"));
  right.insertAdjacentHTML("afterbegin", `<button class="batt-pill" id="batt-pill" title="Energy battery — open details" hidden></button>`);
  $("batt-pill").onclick = () => {
    ltab = "agents";
    openAgents.add("battery");
    renderChrome();
    renderAgents();
    document.querySelector("[data-batt]")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };

  const tone = (b) => TONE[b.state] ?? "";
  const icon = (level, t) => `<span class="batt-icon ${t}"><i style="width:calc(${level}% - 2px)"></i></span>`;

  function renderPill() {
    const b = S.battery, pill = $("batt-pill");
    pill.hidden = !b;
    if (!b) return;
    pill.innerHTML = b.asleep ? `${icon(b.capacity, "")}<span>up to ${b.capacity}%</span>` : `${icon(b.level, tone(b))}<span>${b.level}%</span>`;
  }

  // ---------- the agent row ----------
  function cardHtml() {
    const b = S.battery, open = openAgents.has("battery");
    const badge = !b ? "" : `<span class="mini ${b.asleep ? "" : tone(b) || ""}" title="Energy now">${b.asleep ? `≤${b.capacity}` : b.level}%</span>`;
    return `<div class="agent ${b ? "" : "off"} ${open ? "open" : ""}" data-batt>
      <div class="agent-head">
        <button class="fold-agent" data-batt-fold aria-expanded="${open}" title="${open ? "Hide" : "Show"} details">
          <span class="chev">›</span><span><b>Energy battery</b><span class="dom">${b ? "Reads your sleep and the other agents" : "paused"}</span></span></button>
        ${badge}${toggle("Energy battery", Boolean(b), "data-batt-on")}</div>
      ${open ? `<div class="insight batt">${b ? bodyHtml(b) : `<p class="paused">Paused — switch it on to see your energy.</p>`}</div>` : ""}
    </div>`;
  }

  function bodyHtml(b) {
    const level = b.asleep ? b.capacity : b.level, t = b.asleep ? "" : tone(b);
    const head = b.asleep ? `<div class="big">≤${b.capacity}%</div><div class="line">Today's ceiling — the day starts when you wake</div>`
      : `<div class="big">${b.level}% <span class="chip-s ${t === "low" ? "bad" : t === "mid" ? "late" : t === "good" ? "good" : ""}">${b.label}</span></div>
         <div class="line">Up to ${b.capacity}% today · about ${b.at_bedtime}% by bedtime ${b.bedtime}</div>`;
    const today = b.asleep ? "" : `<div class="topic">Today</div><ul class="factors">
        ${factor({ at: b.wake, label: "Up · the day's natural drain so far", delta: b.drain, why: "Being awake uses energy — about 80% of the ceiling by bedtime." })}
        ${b.events.map((f) => factor(f)).join("")}</ul>`;
    return `${head}
      <div class="gauge" aria-label="${level}% of 100"><div class="lost" style="width:${100 - b.capacity}%"></div>
        <div class="fill ${t}" style="width:${level}%"></div><div class="cap" style="left:${b.capacity}%"></div></div>
      <div class="advice">${esc(b.advice)}</div>
      <div class="topic">Today's ceiling · ${b.capacity}%</div>
      <ul class="factors">${b.capacity_factors.map((f) => factor(f)).join("")}</ul>
      ${today}
      <div class="topic">How do you feel?</div>
      <div class="feel"><div class="ctx-scope">${FEEL.map(([v, l]) => `<button type="button" data-feel="${v}" class="${v === b.condition.state ? "on" : ""}">${l}</button>`).join("")}</div>
        ${b.condition.state !== "well" ? `<div class="ctx-scope">${FEEL_SCOPE.map(([v, l]) =>
          `<button type="button" data-feel-scope="${v}" class="${v === b.condition.scope ? "on" : ""}">${l}</button>`).join("")}</div>` : ""}</div>
      <div class="estimate">An estimate from your sleep and today's records, not a measurement — a fitness tracker can measure it later.</div>`;
  }

  function factor(f) {
    const cls = f.delta > 0 ? "plus" : f.delta < 0 ? "minus" : "zero";
    return `<li title="${esc(f.why || "")}"><span class="t">${esc(f.at || "")}</span><span>${esc(f.label)}</span>
      <span class="d ${cls}">${f.delta > 0 ? "+" : ""}${f.delta}</span></li>`;
  }

  function bind() {
    const card = document.querySelector("[data-batt]");
    card.querySelector("[data-batt-fold]").onclick = () => {
      openAgents.has("battery") ? openAgents.delete("battery") : openAgents.add("battery");
      renderAgents();
    };
    card.querySelector("input[data-batt-on]").onchange = (e) => change({ on: e.target.checked }, e.target.checked ? "switched on" : "switched off");
    card.querySelectorAll("[data-feel]").forEach((b) => (b.onclick = () => change(
      { condition: b.dataset.feel, scope: S.battery.condition.scope || "today" }, `feeling ${b.textContent.toLowerCase()}`)));
    card.querySelectorAll("[data-feel-scope]").forEach((b) => (b.onclick = () => change(
      { condition: S.battery.condition.state, scope: b.dataset.feelScope }, b.textContent.toLowerCase())));
  }

  async function change(body, what) {
    try {
      const data = await post("battery", body);
      S = data.state;
      render();
      logControl("Energy battery", what);
    } catch (err) {
      showToast({ kind: "info", at: S.clock, text: err.message });
    }
  }

  // ---------- wiring ----------
  const baseRenderAgents = renderAgents;
  renderAgents = function () {
    baseRenderAgents();
    if (!S || document.querySelector("[data-batt]")) return;  // already there (the base render was held back while typing)
    $("agents").insertAdjacentHTML("afterbegin", cardHtml());
    bind();
  };
  const baseRender = render;
  render = function () { baseRender(); if (S) renderPill(); };
  if (S) render();
})();

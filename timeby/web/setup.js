// Personal setup for v3: "Your day" (anchor times + which agents are on) and the per-agent ⚙︎ panel.
// Loaded after v3.html's script, so it reuses its globals (S, post, render, renderAgents, showToast, logControl, $, esc).
(() => {
  const DAY_FIELDS = [["wake", "Wake-up"], ["breakfast", "Breakfast"], ["lunch", "Lunch"], ["dinner", "Dinner"], ["sleep", "Lights out"]];
  const WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const SEEN_KEY = "timeby.daySetupSeen";

  const CSS = `
.su-open { width: 100%; margin: 0 0 12px; }
dialog.su { border: 0; max-height: calc(100vh - 32px); border-radius: 18px; padding: 0; width: min(440px, calc(100vw - 32px)); background: var(--surface); color: var(--ink); box-shadow: 0 20px 60px rgba(0,0,0,.25); }
dialog.su::backdrop { background: rgba(0,0,0,.35); }
.su form { display: grid; gap: 10px; padding: 20px 22px; }
.su h2 { margin: 0; font-size: 18px; }
.su .su-sub { margin: -8px 0 0; color: var(--muted); font-size: 13px; }
.su h3 { margin: 4px 0 0; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
.su .su-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 14px; }
.su input[type=time] { font: inherit; font-size: 14px; color: var(--ink); background: var(--surface-2); border: 1px solid var(--line); border-radius: 8px; padding: 6px 8px; }
.su .su-goal { display: grid; gap: 6px; padding: 8px 0; border-top: 1px solid var(--line); }
.su .su-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.su .su-chips button { border: 1px solid var(--line); background: var(--surface-2); color: var(--muted); border-radius: 999px; padding: 3px 10px; font-size: 12px; }
.su .su-chips button[aria-pressed=true] { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.su .su-msg { border-radius: 10px; padding: 8px 10px; font-size: 13px; }
.su .su-msg.err { background: var(--bad-soft); color: var(--bad); }
.su .su-msg.warn { background: var(--warn-soft); color: var(--warn); }
.su .su-actions { display: flex; justify-content: flex-end; gap: 8px; }`;

  const dialog = document.createElement("dialog");
  dialog.className = "su";
  dialog.setAttribute("aria-labelledby", "su-title");

  // ---------- generic sheet ----------
  function openSheet(title, subtitle, body, onSave) {
    dialog.innerHTML = `<form method="dialog" novalidate>
      <h2 id="su-title">${esc(title)}</h2><p class="su-sub">${esc(subtitle)}</p>${body}
      <div class="su-msg err" role="alert" hidden></div>
      <div class="su-actions"><button type="button" class="btn secondary" data-cancel>Cancel</button>
        <button class="btn primary">Save</button></div></form>`;
    const form = dialog.querySelector("form");
    form.querySelector("[data-cancel]").onclick = () => dialog.close();
    form.querySelectorAll(".su-chips button").forEach((b) => (b.onclick = () => b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") !== "true")));
    form.onsubmit = async (e) => {
      e.preventDefault();
      try {
        await onSave(form);
        dialog.close();
      } catch (err) {
        showError(form, err.message);
      }
    };
    dialog.showModal();
  }

  function showError(form, text) {
    const box = form.querySelector(".su-msg.err");
    box.textContent = text;
    box.hidden = false;
  }

  // Runs ops in order (the ones that can be rejected first), then shows the final state.
  async function apply(steps) {
    let data = null;
    inflight = true;  // keeps Play and live sync from interleaving with the save
    try {
      for (const [op, body] of steps) data = await post(op, body);
    } finally {
      inflight = false;
      if (data) { S = data.state; render(); }
    }
  }

  const switchRow = (label, on, attrs) => `<div class="su-row"><span>${label}</span>${toggle(label, on, attrs, true)}</div>`;
  const warnings = (list) => list.map((w) => `<div class="su-msg warn">${esc(w)}</div>`).join("");

  // ---------- "Your day" ----------
  function openDaySetup() {
    const times = DAY_FIELDS.map(([id, label]) =>
      `<label class="su-row"><span>${label}</span><input type="time" name="${id}" value="${S.profile.anchors[id]}" required></label>`).join("");
    const agents = S.agents.map((a) => switchRow(esc(a.name), a.enabled, `data-agent="${a.id}"`)).join("");
    openSheet("Your day", "Your agents plan every goal, reminder and limit around these times.",
      `${times}${warnings(S.profile.warnings)}<h3>Agents</h3>${agents}`, saveDay);
  }

  async function saveDay(form) {
    const anchors = Object.fromEntries(DAY_FIELDS.map(([id]) => [id, form.elements[id].value]));
    const toggles = changedSwitches(form, "[data-agent]", (el) => S.agents.find((a) => a.id === el.dataset.agent).enabled)
      .map((el) => ["toggle", { contract_id: el.dataset.agent, rule_id: null, on: el.checked }]);
    await apply([["profile", { anchors }], ...toggles]);
    logControl("Your day updated", DAY_FIELDS.map(([id, label]) => `${label} ${anchors[id]}`).join(", "));
    remember();
    S.profile.warnings.forEach((w) => showToast({ kind: "info", at: S.clock, text: w }));
  }

  // ---------- per-agent ⚙︎ ----------
  function openAgentSetup(agentId) {
    const agent = S.agents.find((a) => a.id === agentId);
    const goals = agent.goals.map((g) => `<div class="su-goal" data-goal="${g.id}">
        ${switchRow(`${ICONS[g.action] || "•"} ${esc(g.label)}`, g.enabled, `data-goal-on="${g.id}"`)}
        ${g.weekly ? dayChips(g) : ""}</div>`).join("");
    openSheet(`${agent.name} setup`, "Switch goals on or off and pick the days for weekly goals.", goals,
      (form) => saveAgent(agent, form));
  }

  function dayChips(goal) {
    return `<div class="su-chips" role="group" aria-label="${esc(goal.label)} days">${WEEK.map((d) =>
      `<button type="button" data-day="${d}" aria-pressed="${goal.days.includes(d)}">${DAY_SHORT[d]}</button>`).join("")}</div>`;
  }

  async function saveAgent(agent, form) {
    const dayOps = agent.goals.filter((g) => g.weekly).flatMap((g) => {
      const days = [...form.querySelectorAll(`[data-goal="${g.id}"] [aria-pressed=true]`)].map((b) => b.dataset.day);
      if (!days.length) throw new Error(`${g.label}: pick at least one day, or switch the goal off.`);
      return days.join() === g.days.join() ? [] : [["days", { contract_id: agent.id, rule_id: g.id, days }]];
    });
    const toggles = changedSwitches(form, "[data-goal-on]", (el) => agent.goals.find((g) => g.id === el.dataset.goalOn).enabled)
      .map((el) => ["toggle", { contract_id: agent.id, rule_id: el.dataset.goalOn, on: el.checked }]);
    await apply([...dayOps, ...toggles]);
    if (dayOps.length || toggles.length) logControl("Agent set up", agent.name);
  }

  function changedSwitches(form, selector, wasOn) {
    return [...form.querySelectorAll(selector)].filter((el) => el.checked !== wasOn(el));
  }

  // ---------- wiring into v3 ----------
  function bindGears() {
    $("agents").querySelectorAll("[data-setup]").forEach((b) => {
      b.title = b.getAttribute("aria-label");
      b.onclick = () => openAgentSetup(b.dataset.setup);
    });
  }

  function remember() {
    try { localStorage.setItem(SEEN_KEY, "1"); } catch { /* storage blocked: we just ask again next time */ }
  }

  function seenBefore() {
    try { return localStorage.getItem(SEEN_KEY) === "1"; } catch { return false; }
  }

  function whenStateLoaded(fn) {
    const wait = setInterval(() => { if (S) { clearInterval(wait); fn(); } }, 100);
  }

  const baseRenderAgents = renderAgents;
  renderAgents = function () { baseRenderAgents(); bindGears(); };

  document.head.insertAdjacentHTML("beforeend", `<style>${CSS}</style>`);
  document.body.append(dialog);
  $("lpane-agents").insertAdjacentHTML("afterbegin", `<button type="button" class="btn secondary su-open" id="su-open">🕑 Your day</button>`);
  $("su-open").onclick = () => S && openDaySetup();
  whenStateLoaded(() => {
    bindGears();
    if (LIVE && !seenBefore()) { remember(); openDaySetup(); }
  });
})();

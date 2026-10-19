// Agent setup (⚙︎): the day times this agent plans around, its goals (on/off, weekdays) and — in live mode — the
// owner's context for Claude's recommendations. Loaded after v3.html's script; reuses its globals
// (S, LIVE, post, render, renderAgents, toggle, showToast, logControl, ICONS, DAY_SHORT, $, esc).
(() => {
  const WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

  const CSS = `
dialog.su { border: 0; max-height: calc(100vh - 32px); border-radius: 18px; padding: 0; width: min(460px, calc(100vw - 32px)); background: var(--surface); color: var(--ink); box-shadow: 0 20px 60px rgba(0,0,0,.25); }
dialog.su::backdrop { background: rgba(0,0,0,.35); }
.su form { display: grid; gap: 10px; padding: 20px 22px; }
.su h2 { margin: 0; font-size: 18px; }
.su .su-sub { margin: -8px 0 0; color: var(--muted); font-size: 13px; }
.su h3 { margin: 6px 0 0; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
.su .su-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 14px; }
.su .su-note { font-size: 12px; color: var(--faint); margin-top: -6px; }
.su input.tm { width: 76px; text-align: center;  font: inherit; font-size: 14px; color: var(--ink); background: var(--surface-2); border: 1px solid var(--line); border-radius: 8px; padding: 6px 8px; }
.su textarea { font: inherit; font-size: 14px; color: var(--ink); background: var(--surface-2); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; min-height: 70px; resize: vertical; }
.su .su-topic { font-size: 11px; font-weight: 700; color: var(--faint); text-transform: uppercase; letter-spacing: .06em; padding-top: 6px; }
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

  // ---------- dialog ----------
  function openAgentSetup(agentId) {
    const agent = S.agents.find((a) => a.id === agentId);
    dialog.innerHTML = `<form method="dialog" novalidate>
      <h2 id="su-title">${esc(agent.name)}</h2>
      <p class="su-sub">${esc(agent.goal)}</p>
      ${timesSection(agent)}${goalsSection(agent)}${LIVE ? contextSection(agent) : ""}
      <div class="su-msg err" role="alert" hidden></div>
      <div class="su-actions"><button type="button" class="btn secondary" data-cancel>Cancel</button>
        <button class="btn primary">Save</button></div></form>`;
    const form = dialog.querySelector("form");
    form.querySelector("[data-cancel]").onclick = () => dialog.close();
    form.querySelectorAll(".su-chips button").forEach((b) => (b.onclick = () => b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") !== "true")));
    form.onsubmit = async (e) => {
      e.preventDefault();
      try {
        await saveAgent(agent, form);
        dialog.close();
      } catch (err) {
        showError(form, err.message);
      }
    };
    dialog.showModal();
  }

  function timesSection(agent) {
    if (!agent.anchors.length) return "";
    const rows = agent.anchors.map((t) => `<label class="su-row"><span>${esc(t.label)}</span>
        <input class="tm num" name="${t.id}" value="${t.time}" required inputmode="numeric" autocomplete="off" placeholder="HH:MM" maxlength="8"></label>
      ${t.shared.length ? `<div class="su-note">Also used by ${esc(t.shared.join(", "))}.</div>` : ""}`).join("");
    const warn = S.profile.warnings.map((w) => `<div class="su-msg warn">${esc(w)}</div>`).join("");
    return `<h3>Your times</h3>${rows}${warn}`;
  }

  function goalsSection(agent) {
    let topic = null;
    const goals = agent.goals.map((g) => {
      const head = g.topic && g.topic !== topic ? `<div class="su-topic">${esc(g.topic)}</div>` : "";
      topic = g.topic || topic;
      return `${head}<div class="su-goal" data-goal="${g.id}">
        <div class="su-row"><span>${ICONS[g.action] || "•"} ${esc(g.label)}</span>${toggle(g.label, g.enabled, `data-goal-on="${g.id}"`, true)}</div>
        ${g.weekly ? dayChips(g) : ""}</div>`;
    }).join("");
    return `<h3>Goals</h3>${goals}`;
  }

  function contextSection(agent) {
    return `<h3>Context for Claude</h3>
      <textarea name="context" placeholder="${esc(agent.context_hint)}">${esc(agent.context)}</textarea>
      <div class="su-note">Used only for this agent's recommendations from Claude. Your goals and their science stay the same.</div>`;
  }

  function dayChips(goal) {
    return `<div class="su-chips" role="group" aria-label="${esc(goal.label)} days">${WEEK.map((d) =>
      `<button type="button" data-day="${d}" aria-pressed="${goal.days.includes(d)}">${DAY_SHORT[d]}</button>`).join("")}</div>`;
  }

  function showError(form, text) {
    const box = form.querySelector(".su-msg.err");
    box.textContent = text;
    box.hidden = false;
  }

  // ---------- save: the ops that can be rejected go first, then the final state is shown once ----------
  async function saveAgent(agent, form) {
    const steps = [...timeSteps(agent, form), ...daySteps(agent, form), ...toggleSteps(agent, form), ...contextSteps(agent, form)];
    if (!steps.length) return;
    let data = null;
    inflight = true;  // keeps Play and live sync from interleaving with the save
    try {
      for (const [op, body] of steps) data = await post(op, body);
    } finally {
      inflight = false;
      if (data) { S = data.state; render(); }
    }
    logControl("Agent set up", agent.name);
    S.profile.warnings.forEach((w) => showToast({ kind: "info", at: S.clock, text: w }));
  }

  function timeSteps(agent, form) {
    const typed = (t) => parseClock(form.elements[t.id].value);
    const bad = agent.anchors.find((t) => !typed(t));
    if (bad) throw new Error(`${bad.label}: ${TIME_HINT}`);
    const changed = agent.anchors.filter((t) => typed(t) !== t.time);
    if (!changed.length) return [];
    const anchors = { ...S.profile.anchors, ...Object.fromEntries(changed.map((t) => [t.id, typed(t)])) };
    return [["profile", { anchors }]];
  }

  function daySteps(agent, form) {
    return agent.goals.filter((g) => g.weekly).flatMap((g) => {
      const days = [...form.querySelectorAll(`[data-goal="${g.id}"] [aria-pressed=true]`)].map((b) => b.dataset.day);
      if (!days.length) throw new Error(`${g.label}: pick at least one day, or switch the goal off.`);
      return days.join() === g.days.join() ? [] : [["days", { contract_id: agent.id, rule_id: g.id, days }]];
    });
  }

  function toggleSteps(agent, form) {
    return [...form.querySelectorAll("[data-goal-on]")]
      .filter((el) => el.checked !== agent.goals.find((g) => g.id === el.dataset.goalOn).enabled)
      .map((el) => ["toggle", { contract_id: agent.id, rule_id: el.dataset.goalOn, on: el.checked }]);
  }

  function contextSteps(agent, form) {
    const text = form.elements.context?.value.trim();
    return text === undefined || text === agent.context ? [] : [["context", { contract_id: agent.id, text }]];
  }

  // ---------- wiring into v3 ----------
  function bindGears() {
    $("agents").querySelectorAll("[data-setup]").forEach((b) => (b.onclick = () => openAgentSetup(b.dataset.setup)));
  }

  const baseRenderAgents = renderAgents;
  renderAgents = function () { baseRenderAgents(); bindGears(); };

  document.head.insertAdjacentHTML("beforeend", `<style>${CSS}</style>`);
  document.body.append(dialog);
  if (S) renderAgents();
})();

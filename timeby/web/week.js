// Weekly view: Monday–Sunday as a horizontal timeline per goal, weekly progress and what needs the owner today.
// Replaces the Day view in the main column when "Week" is selected. Reuses v3.html globals
// (S, view, setView, render, applyChange, logControl, ICONS, esc, $).
// Days ahead (and today, while still upcoming) can be taken off the plan — e.g. no padel this week while travelling.
(() => {
  const MARK = { done: "✓", late: "◐", missed: "✕", open: "", planned: "", untracked: "·", skipped: "" };
  const CHANGEABLE = new Set(["open", "planned", "skipped"]);
  const STATE = { met: ["Met", "good"], on_track: ["On track", ""], at_risk: ["At risk", "late"], critical: ["Critical", "bad"],
    out_of_reach: ["Out of reach", "bad"], skipped: ["Skipped", ""], none: ["—", ""] };

  document.head.insertAdjacentHTML("beforeend", `<style>
    .wk-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; margin: 4px 4px 12px; }
    .wk-head b { font-size: 18px; }
    .wk-head .range { color: var(--muted); font-size: 13px; margin-left: 8px; }
    .wk-legend { display: flex; gap: 10px; font-size: 12px; color: var(--muted); flex-wrap: wrap; }
    .wk-legend span { display: inline-flex; align-items: center; gap: 4px; }
    .wk-focus { background: var(--surface); border-radius: 16px; padding: 14px 16px; box-shadow: var(--shadow); margin-bottom: 14px; }
    .wk-focus h3 { margin: 0 0 8px; font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }
    .wk-focus .item { display: grid; grid-template-columns: 26px 1fr auto; gap: 2px 10px; align-items: center; padding: 6px 0; border-top: 1px solid var(--line); }
    .wk-focus .item:first-of-type { border-top: 0; }
    .wk-focus .note { grid-column: 2 / -1; font-size: 13px; color: var(--muted); }
    .wk-focus .wk-agent { font-size: 12px; color: var(--faint); }
    .wk-focus .calm { box-shadow: none; padding: 0; font-size: 14px; color: var(--muted); }
    .wk-wrap { background: var(--surface); border-radius: 16px; box-shadow: var(--shadow); overflow-x: auto; }
    .wk-grid { display: grid; grid-template-columns: minmax(130px, 1.6fr) repeat(7, minmax(40px, 1fr)) minmax(96px, 1.1fr); min-width: 640px; }
    .wk-grid > div { padding: 7px 6px; border-top: 1px solid var(--line); display: flex; align-items: center; min-height: 40px; }
    .wk-grid .hd { border-top: 0; font-size: 12px; color: var(--muted); justify-content: center; flex-direction: column; gap: 2px; min-height: 52px; }
    .wk-grid .hd b { font-size: 15px; color: var(--ink); }
    .wk-grid .hd.today { color: var(--accent); }
    .wk-grid .hd.today b { color: var(--accent); }
    .wk-grid .hd .now { font: 700 11px ui-monospace, Menlo, monospace; color: var(--accent-ink); background: var(--accent); border-radius: 999px; padding: 1px 7px; }
    .wk-grid .tcol { background: color-mix(in srgb, var(--accent) 7%, transparent); }
    .wk-grid .agent-row { grid-column: 1 / -1; background: var(--surface-2); font-size: 12px; font-weight: 700; text-transform: uppercase;
      letter-spacing: .05em; color: var(--muted); justify-content: space-between; min-height: 32px; padding: 6px 12px; }
    .wk-grid .label { padding-left: 12px; font-size: 14px; gap: 6px; }
    .wk-grid .cell { justify-content: center; }
    .wk-grid .prog { justify-content: flex-end; gap: 6px; padding-right: 12px; font-size: 13px; }
    .wk-dot { width: 26px; height: 26px; border-radius: 50%; display: grid; place-items: center; font-size: 12px; font-weight: 700; }
    .wk-dot.done { background: var(--accent); color: var(--accent-ink); }
    .wk-dot.late { background: var(--warn-soft); color: var(--warn); }
    .wk-dot.missed { background: var(--bad-soft); color: var(--bad); }
    .wk-dot.open { border: 2px solid var(--accent); animation: pulse 2s infinite; }
    .wk-dot.planned { border: 1.5px dashed var(--line); }
    .wk-dot.need { border-color: var(--bad); border-style: solid; }
    .wk-dot.open.need { border-color: var(--bad); }
    .wk-dot.none { width: 8px; height: 2px; border-radius: 1px; background: var(--line); }
    .wk-dot.untracked { color: var(--faint); }
    .wk-dot.skipped { border: 1.5px dashed var(--line); opacity: .8;
      background: linear-gradient(135deg, transparent 46%, var(--faint) 46%, var(--faint) 54%, transparent 54%); }
    .wk-grid .cell.can { cursor: pointer; }
    .wk-grid .cell.can:hover .wk-dot, .wk-grid .cell.picked .wk-dot { box-shadow: 0 0 0 3px var(--accent-soft); }
    .wk-pop { position: absolute; z-index: 20; background: var(--surface); border-radius: 12px; padding: 6px; display: grid; gap: 2px;
      box-shadow: 0 12px 32px rgba(0,0,0,.16); min-width: 220px; }
    .wk-pop[hidden] { display: none; }
    .wk-pop .t { font-size: 12px; color: var(--muted); padding: 6px 10px 4px; }
    .wk-pop button { border: 0; background: none; text-align: left; padding: 8px 10px; border-radius: 8px; font-size: 14px; color: var(--ink); }
    .wk-pop button:hover { background: var(--surface-2); }
    .wk-tip { margin: -6px 4px 12px; font-size: 12.5px; color: var(--faint); }
    .chip-s { font-size: 11px; border-radius: 999px; padding: 1px 8px; background: var(--surface-2); color: var(--muted); white-space: nowrap; }
    .chip-s.good { background: var(--accent-soft); color: var(--accent); } .chip-s.late { background: var(--warn-soft); color: var(--warn); }
    .chip-s.bad { background: var(--bad-soft); color: var(--bad); }
    .prog .frac { font-variant-numeric: tabular-nums; font-weight: 600; }
  </style>`);

  function renderWeek() {
    if (!S?.week) return;
    const w = S.week, todayIndex = w.days.findIndex((d) => d.today);
    $("week").innerHTML = `
      <div class="wk-head"><div><b>This week</b><span class="range">${esc(w.range)}</span></div>
        <div class="wk-legend"><span><i class="wk-dot done">✓</i>on time</span><span><i class="wk-dot late">◐</i>late</span>
          <span><i class="wk-dot missed">✕</i>missed</span>${S.today?.tracking ? `<span><i class="wk-dot untracked">·</i>no record</span>` : ""}<span><i class="wk-dot planned"></i>planned</span><span><i class="wk-dot planned need"></i>needed</span><span><i class="wk-dot skipped"></i>skipped</span></div></div>
      <p class="wk-tip">Can't make it some day — travelling, ill? Click a planned day to take it off the plan.</p>
      ${focusHtml(w.focus)}
      <div class="wk-wrap"><div class="wk-grid">${headerHtml(w.days)}${w.agents.map((a) => agentRows(a, todayIndex)).join("")}</div></div>`;
  }

  function focusHtml(focus) {
    const items = focus.map((g) => { const [word, tone] = STATE[g.state];
      return `<div class="item"><span>${ICONS[g.action] || "•"}</span><span><b>${esc(g.label)}</b> <span class="wk-agent">${esc(g.agent)}</span></span>
        <span class="chip-s ${tone}">${word}</span><span class="note">${esc(g.note)}</span></div>`; }).join("");
    return `<div class="wk-focus"><h3>Needs you today</h3>${items || `<div class="calm">Nothing is at risk this week — keep the rhythm.</div>`}</div>`;
  }

  function headerHtml(days) {
    return `<div class="hd"></div>${days.map((d) => `<div class="hd ${d.today ? "today tcol" : ""}">${d.label}
      ${d.today ? `<span class="now num">${S.clock}</span>` : `<b>${d.num}</b>`}</div>`).join("")}<div class="hd">This week</div>`;
  }

  function agentRows(a, todayIndex) {
    return `<div class="agent-row"><span>${esc(a.name)}</span><span>${a.done}/${a.judged} on time</span></div>` +
      a.goals.filter((g) => g.planned).map((g) => goalRow(g, todayIndex)).join("");
  }

  function goalRow(g, todayIndex) {
    const needed = g.state === "critical";  // every remaining chance is needed: mark them
    const cells = g.cells.map((c, i) => `<div class="cell ${i === todayIndex ? "tcol" : ""} ${changeable(c, i, todayIndex) ? "can" : ""}"
      data-cid="${g.contract_id}" data-rid="${g.id}" data-i="${i}">
      <i class="wk-dot ${c || "none"} ${needed && (c === "open" || c === "planned") ? "need" : ""}" title="${c || "not planned"}">${MARK[c] ?? ""}</i></div>`).join("");
    const [word, tone] = STATE[g.state];
    return `<div class="label" title="${esc(g.note)}">${ICONS[g.action] || "•"} ${esc(g.label)}</div>${cells}
      <div class="prog" title="${esc(g.note)} Target: ${g.target} a week. ${esc(g.why)}"><span class="frac">${g.done}/${g.judged}</span><span class="chip-s ${tone}">${word}</span></div>`;
  }

  const changeable = (cell, i, todayIndex) => i >= todayIndex && CHANGEABLE.has(cell);

  // ---------- take a day off the plan, or put it back ----------
  const pop = document.createElement("div");
  pop.className = "wk-pop";
  pop.hidden = true;
  document.body.append(pop);
  const closePop = () => { pop.hidden = true; $("week").querySelector(".picked")?.classList.remove("picked"); };
  document.addEventListener("click", (e) => { if (!pop.contains(e.target)) closePop(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });

  function findGoal(cid, rid) {
    return S.week.agents.find((a) => a.id === cid).goals.find((g) => g.id === rid);
  }

  function openPop(cellEl) {
    const { cid, rid } = cellEl.dataset, i = Number(cellEl.dataset.i);
    const g = findGoal(cid, rid), days = S.week.days;
    const dayName = (k) => (days[k].today ? "today" : days[k].label);
    const ahead = g.cells.map((c, k) => k).filter((k) => k >= i && (g.cells[k] === "open" || g.cells[k] === "planned"));
    const options = g.cells[i] === "skipped"
      ? [[`Put back on ${dayName(i)}`, () => change("restore", g, [i])]]
      : [[`Skip ${dayName(i)}`, () => change("skip", g, [i])],
         ...(ahead.length > 1 ? [[`Skip the rest of this week (${ahead.map(dayName).join(", ")})`, () => change("skip", g, ahead)]] : [])];
    pop.innerHTML = `<div class="t">${ICONS[g.action] || "•"} ${esc(g.label)}</div>` + options.map(([label], k) => `<button data-k="${k}">${esc(label)}</button>`).join("");
    pop.querySelectorAll("button").forEach((b) => (b.onclick = () => { closePop(); options[Number(b.dataset.k)][1](); }));
    closePop();
    cellEl.classList.add("picked");
    pop.hidden = false;
    const box = cellEl.getBoundingClientRect(), width = pop.offsetWidth;
    pop.style.left = `${scrollX + Math.max(8, Math.min(box.left + box.width / 2 - width / 2, innerWidth - width - 8))}px`;
    pop.style.top = `${scrollY + box.bottom + 6}px`;
  }

  async function change(action, g, indexes) {
    const days = S.week.days;
    for (const k of indexes) {
      const ok = await applyChange("plan", { contract_id: g.contract_id, rule_id: g.id, action, day: days[k].date });
      if (!ok) return;
    }
    const when = indexes.map((k) => (days[k].today ? "today" : days[k].label)).join(", ");
    logControl(action === "skip" ? "Taken off the plan" : "Back on the plan", `${g.label} · ${when}`);
  }

  $("week").addEventListener("click", (e) => {
    const cellEl = e.target.closest(".cell.can");
    if (!cellEl) return;
    e.stopPropagation();
    if (cellEl.classList.contains("picked") && !pop.hidden) return closePop();
    openPop(cellEl);
  });

  const baseRender = render;
  render = function () { baseRender(); if (view === "week") renderWeek(); };
  window.renderWeek = renderWeek;
  if (S) render();
})();

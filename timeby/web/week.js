// Weekly view: Monday–Sunday as a horizontal timeline per goal, weekly progress and what needs the owner today.
// Replaces the Day view in the main column when "Week" is selected. Reuses v3.html globals
// (S, view, setView, render, ICONS, esc, $).
(() => {
  const MARK = { done: "✓", late: "◐", missed: "✕", open: "", planned: "" };
  const STATE = { met: ["Met", "good"], on_track: ["On track", ""], at_risk: ["At risk", "late"], critical: ["Critical", "bad"],
    out_of_reach: ["Out of reach", "bad"], none: ["—", ""] };

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
          <span><i class="wk-dot missed">✕</i>missed</span><span><i class="wk-dot planned"></i>planned</span><span><i class="wk-dot planned need"></i>needed</span></div></div>
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
    const cells = g.cells.map((c, i) => `<div class="cell ${i === todayIndex ? "tcol" : ""}">
      <i class="wk-dot ${c || "none"} ${needed && (c === "open" || c === "planned") ? "need" : ""}" title="${c || "not planned"}">${MARK[c] ?? ""}</i></div>`).join("");
    const [word, tone] = STATE[g.state];
    return `<div class="label" title="${esc(g.note)}">${ICONS[g.action] || "•"} ${esc(g.label)}</div>${cells}
      <div class="prog" title="${esc(g.note)} Target: ${g.target} a week. ${esc(g.why)}"><span class="frac">${g.done}/${g.judged}</span><span class="chip-s ${tone}">${word}</span></div>`;
  }

  const baseRender = render;
  render = function () { baseRender(); if (view === "week") renderWeek(); };
  window.renderWeek = renderWeek;
  if (S) render();
})();

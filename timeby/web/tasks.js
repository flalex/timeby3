// Tasks tab (next to Timeline): the backlog — books, courses, side projects — with progress in their own unit and a
// weekly rhythm. Reuses v3.html globals (S, render, post, applyChange, showError, logControl, esc, $).
(() => {
  const STATE = { behind: ["Behind", "bad"], today: ["Needs today", "late"], on_track: ["On track", ""], met: ["Rhythm kept", "good"],
    done: ["Finished", "good"], overdue: ["Overdue", "bad"], due_today: ["Due today", "late"], due_soon: ["Due soon", "late"],
    open: ["To do", ""] };
  const URGENT = new Set(["today", "behind", "overdue", "due_today"]);
  const EXAMPLE = { ongoing: "e.g. Atomic Habits — James Clear", once: "e.g. Order robot at TaoBao" };
  const UNITS = ["pages", "lessons", "chapters", "hours", ""];
  let tab = "timeline", openTask = null, adding = false;

  document.head.insertAdjacentHTML("beforeend", `<style>
    .sec-tabs { display: flex; gap: 2px; margin: 0 -8px; }
    .sec-tabs button { border: 0; background: none; padding: 5px 8px; border-radius: 8px; font: inherit; color: var(--muted);
      text-transform: uppercase; letter-spacing: .06em; }
    .sec-tabs button.on { background: var(--surface); color: var(--ink); box-shadow: var(--shadow); }
    .sec-tabs .count { font: 700 11px ui-monospace, Menlo, monospace; margin-left: 4px; padding: 0 6px; border-radius: 999px;
      background: var(--warn-soft); color: var(--warn); letter-spacing: 0; }
    .tasks { background: var(--surface); border-radius: 16px; padding: 4px 18px; box-shadow: var(--shadow); }
    .task { border-top: 1px solid var(--line); padding: 12px 0; }
    .task:first-child { border-top: 0; }
    .task-head { display: grid; grid-template-columns: 1fr auto auto; gap: 4px 12px; align-items: center; cursor: pointer; }
    .task-head b { font-size: 15px; }
    .task.finished .task-head b { color: var(--muted); text-decoration: line-through; }
    .task .sub { grid-column: 1 / -1; font-size: 13px; color: var(--muted); }
    .task .bar { grid-column: 1 / -1; height: 6px; border-radius: 3px; background: var(--surface-2); overflow: hidden; }
    .task .bar div { height: 100%; background: var(--accent); border-radius: 3px; transition: width .4s; }
    .task .add-session { border: 0; border-radius: 10px; padding: 6px 12px; font-weight: 600; font-size: 13px;
      background: var(--accent-soft); color: var(--accent); white-space: nowrap; }
    .task .add-session.did { background: var(--surface-2); color: var(--muted); }
    .task-form { display: grid; gap: 10px; font-size: 13px; }
    .task-body { padding: 10px 0 2px; }
    .task-form .fields { display: flex; flex-wrap: wrap; gap: 10px 14px; align-items: center; color: var(--muted); }
    .task-form input, .task-form select { font: inherit; font-size: 14px; color: var(--ink); background: var(--surface-2);
      border: 1px solid var(--line); border-radius: 8px; padding: 6px 8px; }
    .task-form input[type=number] { width: 76px; }
    .task-form input.title { flex: 1 1 100%; }
    .task-form .acts { display: flex; flex-wrap: wrap; gap: 8px; }
    .task-form .btn { padding: 7px 12px; font-size: 13px; }
    .task-form .hint { color: var(--faint); font-size: 12px; }
    .kind-form { display: grid; gap: 10px; }
    .kind-form .seg2 { display: inline-flex; background: var(--surface-2); border-radius: 10px; padding: 3px; width: max-content; }
    .kind-form .seg2 button { border: 0; background: none; border-radius: 8px; padding: 5px 12px; font-size: 13px; font-weight: 600; color: var(--muted); }
    .kind-form .seg2 button.on { background: var(--surface); color: var(--ink); box-shadow: var(--shadow); }
    .kind-form[data-kind="once"] .ongoing-only, .kind-form[data-kind="ongoing"] .once-only { display: none; }
    .task-form select { padding: 6px 4px; }
    .tasks .add-row { border-top: 1px solid var(--line); padding: 12px 0; }
    .tasks .add-row > button { justify-self: start; border: 0; background: none; color: var(--accent); font-weight: 600; font-size: 14px; padding: 0; }
    .tasks .empty { padding: 16px 0 4px; color: var(--muted); font-size: 14px; }
  </style>`);

  // ---------- tabs ----------
  function setTab(next) {
    tab = next;
    render();
  }

  function renderTabs() {
    const needing = (S.tasks || []).filter((t) => URGENT.has(t.state)).length;
    $("dtabs").querySelectorAll("button").forEach((b) => {
      b.classList.toggle("on", b.dataset.dtab === tab);
      b.setAttribute("aria-selected", b.dataset.dtab === tab);
    });
    $("dtabs").querySelector('[data-dtab="tasks"]').innerHTML = `Tasks${needing ? `<span class="count" title="Need you today">${needing}</span>` : ""}`;
    $("timeline").hidden = tab !== "timeline";
    $("tasks").hidden = tab !== "tasks";
    $("fold-slot").hidden = tab !== "timeline";
  }

  // ---------- list ----------
  function renderTasks() {
    const rows = (S.tasks || []).map(taskHtml).join("");
    $("tasks").innerHTML = `<div class="tasks">${rows || `<div class="empty">Books to read, a course, one-off errands — add what you want to get through: ongoing things with a weekly rhythm, or one-time tasks with a deadline.</div>`}
      <div class="add-row task-form">${adding ? formHtml() : `<button id="task-new">+ Add task</button>`}</div></div>`;
    bind();
  }

  function taskHtml(t) {
    const [word, tone] = STATE[t.state];
    const once = t.kind === "once";
    const amount = t.total ? `${t.progress} / ${t.total} ${t.unit}` : t.unit ? `${t.progress} ${t.unit}` : "";
    const due = t.deadline ? `due ${t.due}` : "";
    const sub = once ? [t.estimate ? `takes ~${fmtDur(t.estimate)}` : "", due || "no deadline"]
      : [amount, `${t.sessions} of ${t.per_week} sessions this week`, `last: ${t.last}`, due];
    const open = openTask === t.id;
    const action = t.done ? "<span></span>" : once ? `<button class="add-session" data-finish="${t.id}">✓ Done</button>`
      : `<button class="add-session ${t.today ? "did" : ""}" data-session="${t.id}">${t.today ? "+ Another" : "+ Session"}</button>`;
    return `<div class="task ${t.done ? "finished" : ""}">
      <div class="task-head" data-task="${t.id}" title="Click for details and editing">
        <b>${esc(t.title)}</b><span class="chip-s ${tone}" title="${esc(t.note)}">${word}</span>
        ${action}
        <span class="sub">${esc(sub.filter(Boolean).join(" · "))}</span>
        ${t.total ? `<div class="bar" aria-label="${t.percent}%"><div style="width:${Math.min(100, t.percent)}%"></div></div>` : ""}
      </div>${open ? bodyHtml(t) : ""}</div>`;
  }

  function bodyHtml(t) {
    const log = t.done || t.kind === "once" ? "" : `<div class="fields"><label>${t.unit ? `${esc(cap(t.unit))} done today` : "Session today"}
        ${t.unit ? `<input type="number" id="task-amount" min="0" placeholder="e.g. 25">` : ""}</label>
        <button class="btn primary" id="task-log">Log session</button></div>`;
    return `<div class="task-body task-form">
      <div class="hint">${esc(t.note)}${t.pace ? ` ${esc(t.pace)}.` : ""}</div>
      ${log}
      <div class="ed-err" id="task-err" hidden></div>
      ${formHtml(t)}
      </div>`;
  }

  /** The same fields add a task and edit one; the kind switch shows only what that kind needs. */
  function formHtml(t = null) {
    const kind = t?.kind || "ongoing", unit = t ? t.unit : "pages";
    const hours = t?.estimate && t.estimate % 60 === 0;
    return `<div class="kind-form" id="task-form" data-kind="${kind}">
      <div class="seg2" role="radiogroup" aria-label="Kind of task">
        <button type="button" data-kind="ongoing" class="${kind === "ongoing" ? "on" : ""}">Ongoing</button>
        <button type="button" data-kind="once" class="${kind === "once" ? "on" : ""}">One-time</button></div>
      <div class="fields">
        <input class="title" id="task-title" placeholder="${EXAMPLE[kind]}" value="${esc(t?.title || "")}" aria-label="Task">
        <label class="ongoing-only">Track <select id="task-unit">${UNITS.map((u) => `<option value="${u}" ${u === unit ? "selected" : ""}>${u || "sessions only"}</option>`).join("")}</select></label>
        <label class="ongoing-only">Total <input type="number" id="task-total" min="1" value="${t?.total ?? ""}" placeholder="—"></label>
        <label class="ongoing-only"><input type="number" id="task-week" min="1" max="7" value="${t?.per_week ?? 3}"> × a week</label>
        <label class="once-only">Takes <input type="number" id="task-est" min="1" step="any" value="${t?.estimate ? (hours ? t.estimate / 60 : t.estimate) : ""}" placeholder="—">
          <select id="task-est-unit"><option value="min" ${hours ? "" : "selected"}>min</option><option value="h" ${hours ? "selected" : ""}>h</option></select></label>
        <label>Deadline <input type="date" id="task-deadline" value="${t?.deadline || ""}"></label></div></div>
      ${t ? "" : `<div class="ed-err" id="task-err" hidden></div>`}
      <div class="acts">${t ? `<button class="btn secondary" id="task-save">Save changes</button>
          ${t.sessions || t.progress ? `<button class="btn secondary" id="task-undo">Undo last session</button>` : ""}
          <button class="btn secondary" id="task-done">${t.done ? "Reopen" : "Mark finished"}</button>
          <button class="btn secondary" id="task-delete">Delete</button>`
        : `<button class="btn primary" id="task-add">Add task</button><button class="btn secondary" id="task-cancel">Cancel</button>`}</div>`;
  }

  // ---------- actions ----------
  function bind() {
    const root = $("tasks");
    root.querySelectorAll("[data-task]").forEach((el) => (el.onclick = (e) => {
      if (e.target.closest("[data-session], [data-finish]")) return;
      openTask = openTask === el.dataset.task ? null : el.dataset.task;
      adding = false;  // one form at a time
      renderTasks();
    }));
    root.querySelectorAll("[data-session]").forEach((b) => (b.onclick = () => quickSession(b.dataset.session)));
    root.querySelectorAll("[data-finish]").forEach((b) => (b.onclick = () => change({ action: "done", id: b.dataset.finish, done: true }, "Task done")));
    root.querySelectorAll("#task-form [data-kind]").forEach((b) => (b.onclick = () => {
      $("task-form").dataset.kind = b.dataset.kind;
      $("task-title").placeholder = EXAMPLE[b.dataset.kind];
      $("task-form").querySelectorAll("[data-kind]").forEach((x) => x.classList.toggle("on", x === b));
    }));
    $("task-new")?.addEventListener("click", () => { adding = true; openTask = null; renderTasks(); $("task-title").focus(); });
    $("task-cancel")?.addEventListener("click", () => { adding = false; renderTasks(); });
    $("task-add")?.addEventListener("click", () => change({ action: "add", ...fields() }, "Task added", () => (adding = false)));
    if (!openTask) return;
    const id = openTask, t = S.tasks.find((x) => x.id === id);
    $("task-log")?.addEventListener("click", () => change({ action: "log", id, amount: $("task-amount")?.value || "" }, "Session logged"));
    $("task-save")?.addEventListener("click", () => change({ action: "edit", id, ...fields() }, "Task edited"));
    $("task-undo")?.addEventListener("click", () => change({ action: "undo", id }, "Session undone"));
    $("task-done")?.addEventListener("click", () => change({ action: "done", id, done: !t.done }, t.done ? "Task reopened" : "Task finished"));
    $("task-delete")?.addEventListener("click", () => change({ action: "delete", id }, "Task deleted", () => (openTask = null)));
  }

  /** "+ Session": with a unit, open the task to type how much; without one, a session is all there is to log. */
  function quickSession(id) {
    const t = S.tasks.find((x) => x.id === id);
    if (!t.unit) return change({ action: "log", id }, "Session logged");
    openTask = id;
    adding = false;
    renderTasks();
    $("task-amount")?.focus();
  }

  function fields() {
    const est = parseFloat($("task-est").value);
    return { kind: $("task-form").dataset.kind, title: $("task-title").value, unit: $("task-unit").value, total: $("task-total").value,
      per_week: $("task-week").value, deadline: $("task-deadline").value,
      estimate: est > 0 ? String(Math.round(est * ($("task-est-unit").value === "h" ? 60 : 1))) : "" };
  }

  async function change(body, what, after = () => {}) {
    if (await applyChange("task", body, "task-err")) {
      after();
      logControl(what, (S.tasks.find((t) => t.id === body.id) || {}).title || body.title || "");
      renderTasks();
    }
  }

  const cap = (s) => s[0].toUpperCase() + s.slice(1);

  // ---------- wiring ----------
  $("dtabs").querySelectorAll("button").forEach((b) => (b.onclick = () => setTab(b.dataset.dtab)));
  const baseRender = render;
  /** Live sync re-renders every 30 s — never under the owner's fingers while they type into a task. */
  const typing = () => $("tasks").contains(document.activeElement) && /^(INPUT|SELECT)$/.test(document.activeElement.tagName);
  render = function () { baseRender(); if (!S) return; renderTabs(); if (tab === "tasks" && !typing()) renderTasks(); };
  if (S) render();
})();

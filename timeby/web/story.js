// Story mode: a scripted walkthrough of one day on the real engine, driven through the v3 page's own API helpers.
// Relies on v3.html globals: LIVE, S, api, restart, setPlaying, setTab, renderAgents, renderToday, openAgents,
// logControl, tomorrowOpen, $, esc.
(() => {
  if (LIVE) return;

  const SCENARIO = "story";
  const DWELL_MS = 10000;  // reading time per chapter before auto-advance
  const BEAT_MS = 1800;    // pause inside a chapter so the viewer sees the "before" state
  const BEAT = "beat";

  const jumpTo = (at) => () => api("jump", { at });
  const showTab = (t) => () => setTab(t);

  async function doneBoth() {
    const hero = S.today.now.hero;
    await api("reply", { op: "done", contract_id: hero.contract_id, rule_id: hero.rule_id });
    await api("log", { action: hero.pair.action });
  }

  const FORGOTTEN_WALK = { contract_id: "cortisol_rhythm", rule_id: "morning_movement", happened: true, at: "10:30",
    duration: "20", comment: "Walked to the office — forgot to log it." };

  async function fixForgottenWalk() {
    await api("edit", FORGOTTEN_WALK);
    logControl("Edited by owner", `Morning movement · done ${FORGOTTEN_WALK.at}`);
  }

  function showAgentGoals() {
    openAgents.add("cortisol_rhythm");
    renderAgents();
  }

  function openTomorrow() {
    tomorrowOpen = true;
    renderToday();
  }

  const CHAPTERS = [
    { at: "06:30", title: "Meet your agents", focus: ["#agents", "#pane-insights"],
      text: "Each agent runs a contract: goals, time windows and the studies behind every rule.",
      point: "Every recommendation comes from a science-based contract — nothing is improvised.",
      steps: [startStory, showAgentGoals, showTab("insights")] },
    { at: "08:25", title: "Overslept", focus: ["#assistant"],
      text: "One alarm at 07:00, then silence while asleep. The wearable reports waking at 08:25 and the agent sends one recovery plan.",
      point: "Proactive and calm: one plan instead of a wall of red.",
      steps: [showTab("assistant"), jumpTo("08:25")] },
    { at: "08:30", title: "Do both", focus: ["#now-card"],
      text: "Daylight and breakfast are both due, so the Now card suggests doing them together — and the owner taps “Done both”.",
      point: "Productivity: compress, don't drop.",
      steps: [jumpTo("08:30"), BEAT, doneBoth] },
    { at: "17:45", title: "Caught by data", focus: ["#assistant", "#feed"],
      text: "A café card payment at 17:45 is read as coffee. It breaks the caffeine cut-off, and the agent says why.",
      point: "Data sources act for the owner — nothing to type.",
      steps: [jumpTo("17:45")] },
    { at: "18:00", title: "Fix the record", focus: ["#timeline", "#pane-insights"],
      text: "The morning walk shows as missed — the owner did walk, just forgot to log it. One edit and the score updates.",
      point: "The owner stays in control; scores stay honest.",
      steps: [jumpTo("18:00"), showTab("insights"), BEAT, fixForgottenWalk] },
    { at: "23:10", title: "Day closed", focus: ["#tomorrow", "#assistant"],
      text: "The wearable reports lights out. The agent sends the recap and plans tomorrow's wake-up time, with the reason.",
      point: "Closes the loop and plans tomorrow.",
      steps: [showTab("assistant"), jumpTo("23:10"), openTomorrow] },
  ];

  async function startStory() {
    $("scenario").value = SCENARIO;
    await restart();
  }

  // ---------- playback ----------
  let chapter = -1, completed = -1, autoplay = true, run = 0, running = Promise.resolve(), advanceTimer = null;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function playSteps(steps, token, fast) {
    for (const step of steps) {
      if (token !== run) return;
      if (step === BEAT) { if (!fast) await sleep(BEAT_MS); } else await step();
    }
  }

  /** Forward by one plays only the next chapter; any other move replays the day from a clean reset. */
  async function go(target) {
    if (target < 0 || target >= CHAPTERS.length) return;
    const token = ++run;
    clearTimeout(advanceTimer);
    await running;  // let the previous run stop at its next step, so API calls never interleave
    chapter = target;
    renderCaption(true);
    const replay = target === completed + 1 ? [] : CHAPTERS.slice(0, target);
    completed = -1;  // until this run finishes, the server state matches no chapter
    running = (async () => {
      try {
        for (const c of replay) await playSteps(c.steps, token, true);
        await playSteps(CHAPTERS[target].steps, token, false);
      } catch { autoplay = false; }  // the page already shows the server error
    })();
    await running;
    if (token !== run) return;
    completed = target;
    renderCaption(false);
    scheduleAdvance();
  }

  function scheduleAdvance() {
    clearTimeout(advanceTimer);
    if (chapter === CHAPTERS.length - 1) autoplay = false;  // the end: the play button becomes "replay"
    if (autoplay) advanceTimer = setTimeout(() => go(chapter + 1), DWELL_MS);
  }

  function togglePause() {
    autoplay = !autoplay;
    if (autoplay && chapter === CHAPTERS.length - 1) return go(0);  // replay from the start
    renderCaption(false);
    scheduleAdvance();
  }

  function start() {
    setPlaying(false);
    autoplay = true;
    chapter = completed = -1;
    document.body.append(card);
    go(0);
  }

  function exit() {
    run++;
    clearTimeout(advanceTimer);
    chapter = completed = -1;
    clearFocus();
    card.remove();
  }

  // ---------- caption card & highlight ----------
  const card = document.createElement("div");
  card.className = "story-card";
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-label", "Story mode");

  function renderCaption(loading) {
    const c = CHAPTERS[chapter], last = chapter === CHAPTERS.length - 1;
    const dots = CHAPTERS.map((x, i) => `<button class="story-dot ${i === chapter ? "on" : ""}" data-go="${i}"
      title="${x.at} · ${esc(x.title)}" aria-label="Chapter ${i + 1}"></button>`).join("");
    card.innerHTML = `<div class="story-top"><span class="num">Chapter ${chapter + 1} of ${CHAPTERS.length} · ${c.at}</span>
        <button class="story-x" data-act="exit" title="Exit story (Esc)" aria-label="Exit story">✕</button></div>
      <div class="story-title">${esc(c.title)}</div>
      <div class="story-text">${esc(c.text)}</div>
      <div class="story-point">${esc(c.point)}</div>
      <div class="story-foot"><div class="story-dots">${dots}</div>
        <div class="story-ctrl">
          <button data-act="prev" title="Previous chapter (←)" ${chapter ? "" : "disabled"}>←</button>
          <button data-act="pause" title="${autoplay ? "Pause" : "Play"}">${loading ? "…" : autoplay ? "⏸" : last ? "↺" : "▶"}</button>
          <button data-act="next" title="Next chapter (→)" ${last ? "disabled" : ""}>→</button></div></div>`;
    if (!loading) setFocus(c.focus);
  }

  function clearFocus() {
    document.querySelectorAll(".story-focus").forEach((el) => el.classList.remove("story-focus"));
  }

  function setFocus(selectors) {
    clearFocus();
    const targets = selectors.map((s) => document.querySelector(s)).filter(Boolean);
    targets.forEach((el) => el.classList.add("story-focus"));
    targets[0]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  const ACTIONS = { exit, prev: () => go(chapter - 1), next: () => go(chapter + 1), pause: togglePause };
  card.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.go) return go(Number(b.dataset.go));
    ACTIONS[b.dataset.act]?.();
  });

  const KEYS = { Escape: exit, ArrowLeft: ACTIONS.prev, ArrowRight: ACTIONS.next };
  document.addEventListener("keydown", (e) => {
    if (chapter < 0 || !KEYS[e.key] || e.target.closest?.("input, textarea, select")) return;
    e.preventDefault();
    KEYS[e.key]();
  });

  document.head.insertAdjacentHTML("beforeend", `<style>
.story-focus { box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 60%, transparent), 0 0 0 9px var(--accent-soft) !important;
  border-radius: 14px; transition: box-shadow .3s; }
.story-card { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); z-index: 30; width: min(560px, calc(100vw - 32px));
  background: var(--surface); color: var(--ink); border: 1px solid var(--line); border-radius: 16px; padding: 16px 18px;
  box-shadow: 0 10px 40px rgba(0,0,0,.18); }
.story-top { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: var(--muted); }
.story-title { font-size: 19px; font-weight: 700; margin: 4px 0 6px; }
.story-text { font-size: 14px; }
.story-point { font-size: 14px; margin-top: 6px; color: var(--accent); font-weight: 600; }
.story-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 12px; }
.story-dots { display: flex; gap: 6px; }
.story-dot { width: 9px; height: 9px; padding: 0; border-radius: 50%; border: 0; background: var(--line); }
.story-dot.on { background: var(--accent); }
.story-ctrl { display: flex; gap: 6px; }
.story-ctrl button, .story-x { background: var(--surface-2); border: 1px solid var(--line); border-radius: 8px; padding: 4px 12px; min-width: 40px; }
.story-ctrl button:disabled { opacity: .4; cursor: default; }
.story-x { min-width: 0; padding: 2px 8px; }
</style>`);

  $("play").insertAdjacentHTML("beforebegin", `<button data-demo id="story" title="Guided 3-minute demo of one day">▶ Story</button>`);
  $("story").onclick = start;
})();

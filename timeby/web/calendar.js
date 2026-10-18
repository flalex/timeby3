// Calendar connector for v3: meetings in the timeline, and feed / busy-time actions on the Calendar card.
// Plugs into v3.html globals (S, api, render, renderConnectors, withBusy, dayMin, showToast, esc, $).
(() => {
  const FEED_PATH = LIVE ? "/calendar/live.ics" : "/calendar.ics";
  const feedUrl = () => `${location.origin}${FEED_PATH}`;
  let subscribeOpen = false;

  document.head.insertAdjacentHTML("beforeend", `<style>
    .row.meeting { background: var(--surface-2); margin: 0 -18px; padding: 0 18px; }
    .row.meeting .name, .row.meeting .meta { color: var(--muted); }
    .row.meeting.past { opacity: .55; min-height: 42px; }
    .mark.busy { background: var(--line); font-size: 13px; }
    .conn .cal-acts { grid-column: 2; display: flex; flex-wrap: wrap; gap: 6px; }
    .conn .cal-acts .btn { grid-column: auto; margin-top: 4px; text-decoration: none; }
    .conn .cal-sub { grid-column: 2; display: flex; gap: 6px; margin-top: 4px; }
    .conn .cal-sub input { flex: 1; min-width: 0; font: inherit; font-size: 12px; padding: 5px 8px; border: 1px solid var(--line);
      border-radius: 8px; background: var(--surface-2); color: var(--ink); }
    .conn .cal-note { grid-column: 2; font-size: 12px; color: var(--muted); }
  </style>`);

  // ---------- timeline: meetings as grey rows, merged by time into each part of the day ----------
  // `keyOf` gives each timeline item's position in minutes of the logical day, so meetings slot in by time.
  withBusy = (items, toRows, phase, keyOf = (item) => dayMin(item.at)) => {
    const phases = phase === "upcoming" ? ["now", "later"] : [phase];
    const meetings = (S.busy || []).filter((m) => phases.includes(m.phase));
    const rows = [];
    let next = 0;
    for (const item of items) {
      while (next < meetings.length && dayMin(meetings[next].start) <= keyOf(item)) rows.push(meetingRow(meetings[next++]));
      rows.push(...toRows(item));
    }
    return rows.concat(meetings.slice(next).map(meetingRow));
  };

  function meetingRow(m) {
    const meta = m.phase === "now" ? `reminders wait until ${m.end}` : `${m.start}–${m.end}`;
    return `<div class="row meeting ${m.phase === "past" ? "past" : ""}"><span class="t num">${m.start}</span>
      <span class="mark busy">📅</span><span class="name"><span>Meeting · ${esc(m.summary)}</span></span>
      <span class="meta">${meta}</span></div>`;
  }

  // ---------- Connectors tab: real actions on the Calendar card ----------
  const baseRenderConnectors = renderConnectors;
  renderConnectors = () => {
    baseRenderConnectors();
    const button = $("connectors").querySelector('[data-connect="calendar"]');
    if (!button) return;
    button.insertAdjacentHTML("afterend", calendarActions());
    button.remove();
    bindCalendarActions();
  };

  function calendarActions() {
    const meetings = (S.busy || []).length;
    const note = meetings ? `<div class="cal-note">${meetings} meeting${meetings > 1 ? "s" : ""} today · reminders wait until they end</div>` : "";
    const subscribe = subscribeOpen ? `<div class="cal-sub"><input id="cal-url" readonly value="${esc(feedUrl())}">
      <button class="btn secondary" id="cal-copy">Copy</button></div>
      <div class="cal-note">Add it in Google / Apple / Outlook Calendar as a subscribed calendar ("From URL").</div>` : "";
    return `${note}<div class="cal-acts">
        <button class="btn secondary" id="cal-subscribe">Subscribe</button>
        <a class="btn secondary" id="cal-download" href="${FEED_PATH}" download="timeby.ics">Download .ics</a>
        ${meetings ? `<button class="btn secondary" id="cal-clear">Clear meetings</button>`
          : `<button class="btn secondary" id="cal-sample">Load sample meetings</button>`}
        <label class="btn secondary" title="Import meetings from an .ics file">Upload .ics
          <input type="file" id="cal-file" accept=".ics,text/calendar" hidden></label>
      </div>${subscribe}`;
  }

  function bindCalendarActions() {
    $("cal-subscribe").onclick = () => { subscribeOpen = !subscribeOpen; renderConnectors(); };
    $("cal-download").onclick = () => setTimeout(() => api("state"), 500);  // the server marks the feed as opened
    $("cal-copy")?.addEventListener("click", copyFeedUrl);
    $("cal-sample")?.addEventListener("click", () => loadMeetings({ sample: true }));
    $("cal-clear")?.addEventListener("click", () => loadMeetings({ clear: true }));
    $("cal-file").onchange = async (e) => {
      const file = e.target.files[0];
      if (file) loadMeetings({ ics: await file.text() });
    };
  }

  async function copyFeedUrl() {
    try {
      await navigator.clipboard.writeText(feedUrl());
    } catch {
      $("cal-url").select();
      document.execCommand("copy");
    }
    notify("Feed URL copied — paste it into your calendar app.");
  }

  async function loadMeetings(body) {
    await api("busy", body);
    const n = S.busy.length;
    notify(body.clear ? "Meetings cleared." : n ? `${n} meeting${n > 1 ? "s" : ""} loaded — no reminders during them.`
      : "No timed meetings for today in that file.");
  }

  const notify = (text) => showToast({ kind: "info", at: S.clock, text });

  if (S) render();  // the first render may have happened before this file loaded
})();

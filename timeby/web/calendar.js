// Calendar connector for v3: meetings in the timeline, and feed / busy-time actions on the Calendar card.
// Plugs into v3.html globals (S, api, render, CONNECTOR_SETUP, refreshConnectorDialog, withBusy, dayMin, showToast, esc, $).
(() => {
  const FEED_PATH = LIVE ? "/calendar/live.ics" : "/calendar.ics";
  const feedUrl = () => `${location.origin}${FEED_PATH}`;
  let subscribeOpen = false;

  document.head.insertAdjacentHTML("beforeend", `<style>
    .row.meeting { background: var(--surface-2); margin: 0 -18px; padding: 0 18px; }
    .row.meeting .name, .row.meeting .meta { color: var(--muted); }
    .row.meeting.past { opacity: .55; min-height: 42px; }
    .mark.busy { background: var(--line); font-size: 13px; }
    .cal-acts { display: flex; flex-wrap: wrap; gap: 6px; }
    .cal-acts .btn { padding: 7px 12px; font-size: 13px; text-decoration: none; cursor: pointer; }
    .cal-sub { display: flex; gap: 6px; margin-top: 8px; }
    .cal-sub input { flex: 1; min-width: 0; font: inherit; font-size: 13px; padding: 6px 8px; border: 1px solid var(--line);
      border-radius: 8px; background: var(--surface-2); color: var(--ink); }
    .cal-note { font-size: 12px; color: var(--muted); margin-top: 6px; }
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

  // ---------- Connectors: the Calendar setup (⚙︎) ----------
  const meetingsNote = () => {
    const n = (S.busy || []).length;
    return n ? `${n} meeting${n > 1 ? "s" : ""} today · reminders wait until they end` : "No meetings today.";
  };
  CONNECTOR_SETUP.calendar = { html: calendarActions, bind: bindCalendarActions, summary: meetingsNote };

  function calendarActions() {
    const meetings = (S.busy || []).length;
    const subscribe = subscribeOpen ? `<div class="cal-sub"><input id="cal-url" readonly value="${esc(feedUrl())}">
      <button type="button" class="btn secondary" id="cal-copy">Copy</button></div>
      <div class="cal-note">Add it in Google / Apple / Outlook Calendar as a subscribed calendar ("From URL").</div>` : "";
    return `<div class="cal-acts">
        <button type="button" class="btn secondary" id="cal-subscribe">Subscribe</button>
        <a class="btn secondary" id="cal-download" href="${FEED_PATH}" download="timeby.ics">Download .ics</a>
        ${meetings ? `<button type="button" class="btn secondary" id="cal-clear">Clear meetings</button>`
          : `<button type="button" class="btn secondary" id="cal-sample">Load sample meetings</button>`}
        <label class="btn secondary" title="Import meetings from an .ics file">Upload .ics
          <input type="file" id="cal-file" accept=".ics,text/calendar" hidden></label>
      </div><div class="cal-note">${meetingsNote()}</div>${subscribe}`;
  }

  function bindCalendarActions() {
    $("cal-subscribe").onclick = () => { subscribeOpen = !subscribeOpen; refreshConnectorDialog(); };
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
    refreshConnectorDialog();
    const n = S.busy.length;
    notify(body.clear ? "Meetings cleared." : n ? `${n} meeting${n > 1 ? "s" : ""} loaded — no reminders during them.`
      : "No timed meetings for today in that file.");
  }

  const notify = (text) => showToast({ kind: "info", at: S.clock, text });

  if (S) render();  // the first render may have happened before this file loaded
})();

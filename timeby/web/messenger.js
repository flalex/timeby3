// Messenger connector: Telegram setup in its ⚙︎ dialog — paste the bot token, press Start, choose what to receive.
// Reuses v3.html globals (S, LIVE, post, render, CONNECTOR_SETUP, refreshConnectorDialog, showToast, logControl, esc, $).
(() => {
  const DELIVERY = [["important", "Important only — plans, warnings, evening recap"], ["all", "Everything — including each reminder"],
    ["off", "Paused"]];

  document.head.insertAdjacentHTML("beforeend", `<style>
    .tg { display: grid; gap: 8px; }
    .tg ol { margin: 0; padding-left: 18px; font-size: 13px; color: var(--muted); }
    .tg .row2 { display: flex; gap: 6px; }
    .tg input, .tg select { flex: 1; min-width: 0; font: inherit; font-size: 13px; padding: 7px 9px; border: 1px solid var(--line);
      border-radius: 8px; background: var(--surface-2); color: var(--ink); }
    .tg .btn { padding: 7px 12px; font-size: 13px; white-space: nowrap; }
    .tg .ok { font-size: 13px; color: var(--accent); }
    .tg .err { font-size: 13px; color: var(--bad); }
    .tg .note { font-size: 12px; color: var(--faint); }
  </style>`);

  let error = "";  // last setup error, shown in the dialog

  const telegram = () => S.connectors.find((c) => c.id === "telegram").telegram;
  CONNECTOR_SETUP.telegram = {
    html: () => `<div class="tg">${setupHtml(telegram())}</div>`,
    bind,
    summary: () => (telegram().ready ? `Sending to ${esc(telegram().chat_name)} · ${telegram().delivery}` : "Not set up yet."),
  };

  function setupHtml(tg) {
    const problem = error || tg.last_error ? `<div class="err">${esc(error || tg.last_error)}</div>` : "";
    if (!tg.connected) {
      return `<ol><li>In Telegram, open <b>@BotFather</b> → <b>/newbot</b> and follow the steps.</li><li>Paste the token it gives you:</li></ol>
        <div class="row2"><input id="tg-token" placeholder="123456:ABC-..." autocomplete="off"><button type="button" class="btn primary" data-tg="connect">Connect</button></div>${problem}`;
    }
    if (!tg.ready) {
      return `<div class="ok">Bot @${esc(tg.bot)} connected.</div>
        <ol start="3"><li>Open <b>@${esc(tg.bot)}</b> in Telegram and press <b>Start</b>.</li></ol>
        <div class="row2"><button type="button" class="btn primary" data-tg="find_chat">Find my chat</button><button type="button" class="btn secondary" data-tg="disconnect">Disconnect</button></div>${problem}`;
    }
    const options = DELIVERY.map(([v, label]) => `<option value="${v}" ${v === tg.delivery ? "selected" : ""}>${label}</option>`).join("");
    const demo = LIVE ? "" : `<div class="note">Demo: the simulated day isn't sent to your phone — use “Send test message”, or /live for real days.</div>`;
    return `<div class="ok">Sending to ${esc(tg.chat_name)} via @${esc(tg.bot)}.</div>
      <div class="row2"><select id="tg-delivery" aria-label="What to send">${options}</select></div>
      <div class="row2"><button type="button" class="btn secondary" data-tg="test">Send test message</button><button type="button" class="btn secondary" data-tg="disconnect">Disconnect</button></div>
      ${demo}${problem}`;
  }

  function bind() {
    document.querySelectorAll("[data-tg]").forEach((b) => (b.onclick = () => run(b.dataset.tg)));
    const delivery = $("tg-delivery");
    if (delivery) delivery.onchange = () => run("delivery", { delivery: delivery.value });
  }

  async function run(action, extra = {}) {
    const body = { action, ...extra, ...(action === "connect" ? { token: $("tg-token").value } : {}) };
    try {
      const data = await post("telegram", body);
      error = "";
      S = data.state;
      render();
      refreshConnectorDialog();
      if (action === "test") showToast({ kind: "info", at: S.clock, text: "Test message sent to Telegram." });
      if (action !== "test") logControl("Telegram", action.replace("_", " "));
    } catch (err) {
      error = err.message;
      refreshConnectorDialog();
    }
  }
})();

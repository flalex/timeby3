// Messenger connector: Telegram setup inside its card — paste the bot token, press Start, choose what to receive.
// Reuses v3.html globals (S, LIVE, post, render, renderConnectors, showToast, logControl, esc, $).
(() => {
  const DELIVERY = [["important", "Important only — plans, warnings, evening recap"], ["all", "Everything — including each reminder"],
    ["off", "Paused"]];

  document.head.insertAdjacentHTML("beforeend", `<style>
    .conn .tg { grid-column: 2; display: grid; gap: 6px; margin-top: 4px; }
    .conn .tg ol { margin: 0; padding-left: 18px; font-size: 12px; color: var(--muted); }
    .conn .tg .row2 { display: flex; gap: 6px; }
    .conn .tg input, .conn .tg select { flex: 1; min-width: 0; font: inherit; font-size: 12px; padding: 6px 8px; border: 1px solid var(--line);
      border-radius: 8px; background: var(--surface-2); color: var(--ink); }
    .conn .tg .btn { padding: 6px 10px; font-size: 12px; white-space: nowrap; }
    .conn .tg .ok { font-size: 12px; color: var(--accent); }
    .conn .tg .err { font-size: 12px; color: var(--bad); }
    .conn .tg .note { font-size: 12px; color: var(--faint); }
  </style>`);

  let error = "";  // last setup error, shown inside the card

  const baseRenderConnectors = renderConnectors;
  renderConnectors = () => {
    baseRenderConnectors();
    const button = $("connectors").querySelector('[data-connect="telegram"]');
    const card = S.connectors.find((c) => c.id === "telegram");
    if (!button || !card) return;
    button.insertAdjacentHTML("afterend", `<div class="tg">${setupHtml(card.telegram)}</div>`);
    button.remove();
    bind();
  };

  function setupHtml(tg) {
    const problem = error || tg.last_error ? `<div class="err">${esc(error || tg.last_error)}</div>` : "";
    if (!tg.connected) {
      return `<ol><li>In Telegram, open <b>@BotFather</b> → <b>/newbot</b> and follow the steps.</li><li>Paste the token it gives you:</li></ol>
        <div class="row2"><input id="tg-token" placeholder="123456:ABC-..." autocomplete="off"><button class="btn primary" data-tg="connect">Connect</button></div>${problem}`;
    }
    if (!tg.ready) {
      return `<div class="ok">Bot @${esc(tg.bot)} connected.</div>
        <ol start="3"><li>Open <b>@${esc(tg.bot)}</b> in Telegram and press <b>Start</b>.</li></ol>
        <div class="row2"><button class="btn primary" data-tg="find_chat">Find my chat</button><button class="btn secondary" data-tg="disconnect">Disconnect</button></div>${problem}`;
    }
    const options = DELIVERY.map(([v, label]) => `<option value="${v}" ${v === tg.delivery ? "selected" : ""}>${label}</option>`).join("");
    const demo = LIVE ? "" : `<div class="note">Demo: the simulated day isn't sent to your phone — use “Send test message”, or /live for real days.</div>`;
    return `<div class="ok">Sending to ${esc(tg.chat_name)} via @${esc(tg.bot)}.</div>
      <div class="row2"><select id="tg-delivery" aria-label="What to send">${options}</select></div>
      <div class="row2"><button class="btn secondary" data-tg="test">Send test message</button><button class="btn secondary" data-tg="disconnect">Disconnect</button></div>
      ${demo}${problem}`;
  }

  function bind() {
    $("connectors").querySelectorAll("[data-tg]").forEach((b) => (b.onclick = () => run(b.dataset.tg)));
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
      if (action === "test") showToast({ kind: "info", at: S.clock, text: "Test message sent to Telegram." });
      if (action !== "test") logControl("Telegram", action.replace("_", " "));
    } catch (err) {
      error = err.message;
      renderConnectors();
    }
  }

  if (S) renderConnectors();
})();

// Assistant settings (⚙︎ on the Assistant panel): the tone of every message (same facts, different voice) and the
// Claude API key for AI recommendations. Reuses v3.html globals (S, LIVE, api, post, render, logControl, esc, $).
(() => {
  const CSS = `
.side-head { grid-template-columns: 1fr auto; align-items: start; }
.side-head .gear { grid-row: 1 / span 2; grid-column: 2; }
dialog.tone { border: 0; border-radius: 18px; padding: 0; width: min(480px, calc(100vw - 32px)); max-height: calc(100vh - 32px);
  background: var(--surface); color: var(--ink); box-shadow: 0 20px 60px rgba(0,0,0,.25); }
dialog.tone::backdrop { background: rgba(0,0,0,.35); }
.tone form { display: grid; gap: 10px; padding: 20px 22px; }
.tone h2 { margin: 0; font-size: 18px; }
.tone .sub { margin: -6px 0 4px; color: var(--muted); font-size: 13px; }
.tone label.card { display: grid; grid-template-columns: 20px 1fr; gap: 4px 10px; padding: 12px; border: 1.5px solid var(--line); border-radius: 14px; cursor: pointer; }
.tone label.card:has(input:checked) { border-color: var(--accent); background: var(--accent-soft); }
.tone label.card input { margin: 3px 0 0; accent-color: var(--accent); }
.tone .name { font-weight: 700; }
.tone .blurb { grid-column: 2; font-size: 13px; color: var(--muted); }
.tone .sample { grid-column: 2; font-size: 13px; background: var(--surface); border-radius: 12px 12px 12px 4px; padding: 8px 10px; box-shadow: var(--shadow); }
.tone .acts { display: flex; justify-content: flex-end; gap: 8px; }
.tone h3 { margin: 8px 0 0; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
.tone .key { display: grid; gap: 6px; font-size: 13px; }
.tone .key .row2 { display: flex; gap: 6px; }
.tone .key input { flex: 1; min-width: 0; font: inherit; font-size: 14px; padding: 7px 9px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-2); color: var(--ink); }
.tone .key .ok { color: var(--accent); }
.tone .key .note { color: var(--faint); font-size: 12px; }
.tone .key .err { color: var(--bad); }`;

  const dialog = document.createElement("dialog");
  dialog.className = "tone";
  dialog.setAttribute("aria-labelledby", "tone-title");

  function open() {
    const { tone, tones } = S.assistant;
    dialog.innerHTML = `<form method="dialog">
      <h2 id="tone-title">Assistant settings</h2>
      <h3>Tone</h3>
      <p class="sub">How your assistant talks to you. The plan, the facts and the science stay exactly the same.</p>
      ${tones.map((t) => `<label class="card"><input type="radio" name="tone" value="${t.id}" ${t.id === tone ? "checked" : ""}>
        <span class="name">${esc(t.label)}</span><span class="blurb">${esc(t.blurb)}</span>
        <span class="sample">${esc(t.sample)}</span></label>`).join("")}
      <h3>Claude API key</h3><div class="key" id="key-section">${keyHtml()}</div>
      <div class="acts"><button type="button" class="btn secondary" data-cancel>Cancel</button><button class="btn primary">Save</button></div>
    </form>`;
    bindKey();
    const form = dialog.querySelector("form");
    form.querySelector("[data-cancel]").onclick = () => dialog.close();
    form.onsubmit = async (e) => {
      e.preventDefault();
      const chosen = form.elements.tone.value;
      dialog.close();
      if (chosen === tone) return;
      await api("tone", { tone: chosen });
      logControl("Assistant tone", tones.find((t) => t.id === chosen).label);
    };
    dialog.showModal();
  }

  // ---------- Claude API key: stored in the macOS Keychain, only ever shown masked ----------
  let keyError = "";

  function keyHtml() {
    const ai = S.ai;
    const sdk = ai.sdk ? "" : `<div class="note">Also install the Claude SDK once: pip install -r requirements-ai.txt</div>`;
    const where = `<div class="note">Used for “Recommendations from Claude” in live mode. Stored in your ${esc(ai.where)} — never in the project folder.</div>`;
    const err = keyError ? `<div class="err">${esc(keyError)}</div>` : "";
    if (ai.configured) {
      return `<div class="row2"><span class="ok">✓ Key saved · ${esc(ai.masked)}</span>
        <button type="button" class="btn secondary" id="key-remove">Remove</button></div>${where}${sdk}${err}`;
    }
    return `<div class="row2"><input type="password" id="key-input" placeholder="sk-ant-…" autocomplete="off" spellcheck="false">
      <button type="button" class="btn secondary" id="key-save">Save key</button></div>${where}${sdk}${err}`;
  }

  function bindKey() {
    const save = $("key-save"), remove = $("key-remove");
    if (save) save.onclick = () => changeKey({ action: "save", key: $("key-input").value });
    if (remove) remove.onclick = () => changeKey({ action: "remove" });
  }

  async function changeKey(body) {
    try {
      const data = await post("ai_key", body);
      keyError = "";
      S = data.state;
      render();
      logControl("Claude API key", body.action === "save" ? "saved" : "removed");
    } catch (err) {
      keyError = err.message;
    }
    $("key-section").innerHTML = keyHtml();
    bindKey();
  }

  document.head.insertAdjacentHTML("beforeend", `<style>${CSS}</style>`);
  document.body.append(dialog);
  document.querySelector(".side-head").insertAdjacentHTML("beforeend",
    `<button class="gear" id="assistant-setup" aria-label="Assistant settings" title="Assistant settings">⚙︎</button>`);
  $("assistant-setup").onclick = () => S && open();
})();

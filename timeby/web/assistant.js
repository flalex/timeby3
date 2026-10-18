// Assistant settings (⚙︎ on the Assistant panel): the tone of every message. Same facts, different voice.
// Reuses v3.html globals (S, api, logControl, esc, $).
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
.tone .acts { display: flex; justify-content: flex-end; gap: 8px; }`;

  const dialog = document.createElement("dialog");
  dialog.className = "tone";
  dialog.setAttribute("aria-labelledby", "tone-title");

  function open() {
    const { tone, tones } = S.assistant;
    dialog.innerHTML = `<form method="dialog">
      <h2 id="tone-title">Assistant tone</h2>
      <p class="sub">How your assistant talks to you. The plan, the facts and the science stay exactly the same.</p>
      ${tones.map((t) => `<label class="card"><input type="radio" name="tone" value="${t.id}" ${t.id === tone ? "checked" : ""}>
        <span class="name">${esc(t.label)}</span><span class="blurb">${esc(t.blurb)}</span>
        <span class="sample">${esc(t.sample)}</span></label>`).join("")}
      <div class="acts"><button type="button" class="btn secondary" data-cancel>Cancel</button><button class="btn primary">Save</button></div>
    </form>`;
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

  document.head.insertAdjacentHTML("beforeend", `<style>${CSS}</style>`);
  document.body.append(dialog);
  document.querySelector(".side-head").insertAdjacentHTML("beforeend",
    `<button class="gear" id="assistant-setup" aria-label="Assistant settings" title="Assistant settings">⚙︎</button>`);
  $("assistant-setup").onclick = () => S && open();
})();

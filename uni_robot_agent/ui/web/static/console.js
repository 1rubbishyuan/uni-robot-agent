import { $, esc, seconds, statusLabel, live } from "./common.js";
function tool(c, s) {
  const duration = c.started_at
    ? seconds((c.finished_at ?? s.finished_at ?? (live(s) ? Date.now() / 1000 : c.started_at)) - c.started_at)
    : "";
  const icon =
    { completed: "✓", failed: "×", queued: "◷", running: "▶", cancelled: "−" }[
      c.status
    ] || "↳";
  return `<details class="tool ${esc(c.status)}" data-call="${esc(c.id)}"><summary><span class="tool-icon">${icon}</span><span class="tool-name">${esc(c.name)}</span><span class="tool-state">${esc(statusLabel(c.status))}</span><span class="tool-time">${duration}</span></summary><div class="tool-body">${c.resource ? `<label>RESOURCE</label>${esc(c.resource)} · ${esc(c.execution_id || c.job_id || "")}` : ""}<label>ARGUMENTS</label><pre>${esc(c.arguments || "Not recorded")}</pre>${c.output ? `<label>RESULT</label><pre>${esc(c.output)}</pre>` : ""}${c.error ? `<pre class="error-text">${esc(c.error)}</pre>` : ""}<label>IDENTITY</label>${esc(c.id)}${!c.batch_id ? "<br>Batch association not recorded" : ""}</div></details>`;
}
export function renderConsole(s, ui) {
  const node = $("console"),
    scroll = node.scrollTop,
    nearBottom = node.scrollHeight - scroll - node.clientHeight < 80;
  const opened = new Set(
    [...node.querySelectorAll("details[open]")].map((d) => d.dataset.call),
  );
  const byId = new Map(s.calls.map((c) => [c.id, c]));
  const groups = s.batches
    .filter((b) => !ui.batchId || b.id === ui.batchId)
    .map((b) => {
      const index = s.batches.indexOf(b) + 1;
      const calls = b.call_ids.map((id) => byId.get(id)).filter(Boolean);
      return `<div class="batch-group" data-batch="${esc(b.id)}"><div class="batch-title">BATCH ${String(index).padStart(2, "0")} <span>${esc(b.id)}</span></div>${calls.length ? calls.map((c) => tool(c, s)).join("") : `<p class="batch-empty">${esc((b.names || []).join(" · ") || "No call identities recorded")}</p>`}</div>`;
    });
  if (!ui.batchId) {
    const unrelated = s.calls.filter((c) => !c.batch_id);
    if (unrelated.length)
      groups.push(
        `<div class="batch-group"><div class="batch-title">EXECUTION · UNLINKED TO BATCH</div>${unrelated.map((c) => tool(c, s)).join("")}</div>`,
      );
  }
  node.innerHTML =
    groups.join("") ||
    '<div class="empty"><span class="empty-glyph">›_</span><strong>Tool calls will appear here</strong><span>Waiting for recorded agent activity.</span></div>';
  for (const d of node.querySelectorAll("details"))
    if (opened.has(d.dataset.call)) d.open = true;
  if (ui.follow && nearBottom) node.scrollTop = node.scrollHeight;
  else node.scrollTop = scroll;
  $("call-count").textContent = s.total_calls;
  $("batch-count").textContent = s.total_batches;
  $("console-note").textContent = ui.batchId
    ? "Inspecting one batch. Return to latest to show all activity."
    : "Acknowledged = SDK returned; completed = tool execution finished.";
  const batchMarkup =
    s.batches
      .map((b, i) => {
        const calls = b.call_ids.map((id) => byId.get(id)).filter(Boolean);
        const status = calls.some((c) => c.status === "failed")
          ? "failed"
          : calls.length && calls.every((c) => c.status === "completed")
            ? "completed"
            : "";
        return `<button class="batch-button ${status} ${ui.batchId === b.id ? "selected" : ""}" data-batch="${esc(b.id)}" title="${esc(b.id)} · ${b.call_ids.length} calls" aria-pressed="${ui.batchId === b.id}">${String(i + 1).padStart(2, "0")} <span>· ${b.call_ids.length || b.names?.length || 0}</span></button>`;
      })
      .join("") || '<span class="batch-empty">No batches recorded yet</span>';
  if ($("batch-items").dataset.markup !== batchMarkup) {
    $("batch-items").innerHTML = batchMarkup;
    $("batch-items").dataset.markup = batchMarkup;
  }
  const selectedIndex = ui.batchId ? s.batches.findIndex((b) => b.id === ui.batchId) : s.batches.length - 1;
  $("batch-position").textContent = ui.batchId
    ? `Batch ${String(selectedIndex + 1).padStart(2, "0")} / ${s.total_batches}`
    : `${s.total_batches} batches · latest`;
  $("batch-position").dataset.index = selectedIndex;
  $("batch-prev").disabled = selectedIndex <= 0;
  $("batch-next").disabled = selectedIndex >= s.batches.length - 1;
  positionBatchCursor();
  if (ui.follow) $("batches").scrollLeft = $("batches").scrollWidth;
  $("messages").innerHTML =
    s.messages
      .map(
        (m) =>
          `<div class="message"><label>${esc(m.type)}</label><p>${esc(m.text)}</p></div>`,
      )
      .join("") ||
    '<div class="message"><p>No agent messages recorded.</p></div>';
  $("run-path").textContent =
    `${s.directory}\nModel: ${s.model || "not recorded"} · Integration: ${s.integration || "not recorded"}${s.failure_category ? ` · ${s.failure_category}` : ""}`;
}

export function positionBatchCursor() {
  const selected = $("batch-items").querySelector(".selected");
  const cursor = $("batch-cursor");
  cursor.classList.toggle("visible", !!selected);
  if (!selected) return;
  cursor.style.width = `${selected.offsetWidth}px`;
  cursor.style.height = `${selected.offsetHeight}px`;
  cursor.style.transform = `translateX(${selected.offsetLeft}px)`;
}

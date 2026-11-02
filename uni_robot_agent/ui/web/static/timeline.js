import { $, esc, seconds, live } from "./common.js";
export function renderTimeline(s, ui = {}) {
  $("timeline").classList.toggle("has-selection", !!ui.batchId);
  const now = s.finished_at ?? (live(s) ? Date.now() / 1000 : null),
    bars = [];
  for (const m of s.models)
    if (m.started_at)
      bars.push({
        lane: "Model requests",
        start: m.started_at,
        end: m.finished_at ?? now ?? m.started_at,
        color: "#526f99",
        label: m.id,
        failed: m.success === false,
        batch: m.id,
      });
  for (const c of s.calls) {
    const lane = c.resource || "Execution";
    if (c.queued_at)
      bars.push({
        lane,
        start: c.queued_at,
        end: c.started_at ?? c.finished_at ?? now ?? c.queued_at,
        color: "#706346",
        label: `${c.name} · queued`,
        batch: c.batch_id,
      });
    if (c.started_at)
      bars.push({
        lane,
        start: c.started_at,
        end: c.finished_at ?? now ?? c.started_at,
        color: "#48776e",
        label: c.name,
        failed: c.status === "failed",
        batch: c.batch_id,
      });
  }
  if (!bars.length) {
    $("timeline").innerHTML =
      '<div class="empty"><span>Execution intervals have not been recorded.</span></div>';
    return;
  }
  const lanes = [...new Set(bars.map((b) => b.lane))];
  const start = Math.min(...bars.map((b) => b.start)),
    end = Math.max(start + 1, ...bars.map((b) => b.end)),
    span = end - start;
  const W = 1120,
    L = 155,
    R = 20,
    H = lanes.length * 44 + 42,
    scale = (t) => L + ((t - start) / span) * (W - L - R);
  let body = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Model request and tool execution overlap"><title>Execution overlap</title><desc>Each lane shares the same wall-clock axis. Amber intervals show queue waiting.</desc><defs><pattern id="failure" width="6" height="6" patternUnits="userSpaceOnUse"><rect width="6" height="6" fill="#84595e"/><path d="M-1,1L1,-1M0,6L6,0M5,7L7,5" stroke="#c99898" stroke-width="1"/></pattern></defs>`;
  lanes.forEach((lane, i) => {
    body += `<rect x="${L}" y="${i * 44 + 7}" width="${W - L - R}" height="38" rx="3" fill="${i % 2 ? "#1b2128" : "#192027"}"/><text x="4" y="${i * 44 + 30}" font-size="11" fill="#98a6b8">${esc(lane.length > 19 ? lane.slice(0, 17) + "…" : lane)}</text>`;
  });
  for (let i = 0; i <= 5; i++) {
    const x = L + (i * (W - L - R)) / 5;
    body += `<line x1="${x}" x2="${x}" y1="6" y2="${H - 27}" stroke="#303b49" stroke-dasharray="3 4"/><text x="${x}" y="${H - 7}" text-anchor="${i === 5 ? "end" : "start"}" font-size="10" fill="#7f8c9d">${seconds((i * span) / 5)}</text>`;
  }
  // Parallel work within a resource shares the lane; vertical packing avoids hiding intervals.
  const occupied = new Map();
  bars
    .sort((a, b) => a.start - b.start)
    .forEach((b) => {
      const ends = occupied.get(b.lane) || [];
      let slot = ends.findIndex((t) => t <= b.start);
      if (slot < 0) slot = ends.length;
      ends[slot] = b.end;
      occupied.set(b.lane, ends);
      b.slot = slot;
    });
  for (const b of bars) {
    const tracks = occupied.get(b.lane).length;
    const height = Math.min(24, 30 / tracks);
    const x = scale(b.start),
      width = Math.max(3, scale(b.end) - x),
      y = lanes.indexOf(b.lane) * 44 + 11 + b.slot * height;
    body += `<rect class="interval ${ui.batchId && b.batch === ui.batchId ? "is-selected" : ""}" tabindex="0" x="${x}" y="${y}" width="${width}" height="${Math.max(2, height - 2)}" rx="2" fill="${b.failed ? "url(#failure)" : b.color}" stroke="${b.failed ? "#bd8383" : b.color}" data-batch="${esc(b.batch || "")}" aria-label="${esc(b.label)}; ${seconds(b.end - b.start)}"><title>${esc(b.label)} · ${seconds(b.end - b.start)} · ${seconds(b.start - start)}–${seconds(b.end - start)}</title></rect>`;
    if (width > 85 && height >= 18)
      body += `<text x="${x + 5}" y="${y + 14}" font-size="10" fill="#dbe5f0" pointer-events="none">${esc(b.label.slice(0, Math.floor((width - 10) / 6)))}</text>`;
  }
  $("timeline").innerHTML = body + "</svg>";
}

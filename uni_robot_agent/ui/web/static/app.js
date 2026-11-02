import {
  $,
  esc,
  clock,
  elapsed,
  statusHTML,
  statusLabel,
  live,
} from "./common.js";
import { renderMetrics, renderQueue } from "./metrics.js";
import { renderViewer, viewerReady } from "./viewer.js";
import { bounds, batchAt, advance } from "./playback.js";
import { reveal, animate, reducedMotion } from "./motion.js";
import { renderConsole, positionBatchCursor } from "./console.js";
import { renderTimeline } from "./timeline.js";
let runs = [],
  state = null,
  stream = null,
  currentId = null,
  routeVersion = 0;
let playing = false;
const ui = { follow: true, batchId: "", camera: "", frameId: null, time: null, playing: false };
function connection(text, connected = false) {
  $("connection").textContent = text;
  $("connection").classList.toggle("connected", connected);
}
async function request(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
  return r.json();
}
function draw() {
  if (!state) return;
  $("task-title").textContent = state.task;
  $("instruction").textContent = state.instruction;
  $("instruction").title = state.instruction;
  $("run-context").textContent =
    `${state.integration || "RUN"} / ${state.model || "MODEL NOT RECORDED"}`;
  $("run-status").className = `status ${state.status}`;
  $("run-status").textContent = statusLabel(state.status);
  $("run-error").hidden = !state.error;
  $("run-error").textContent = state.error || "";
  renderMetrics(state);
  renderQueue(state);
  renderViewer(state, ui);
  renderConsole(state, ui);
  renderTimeline(state, ui);
  $("history-note").textContent = state.history_limited
    ? "Recent history window shown · full history remains in trajectory.jsonl"
    : state.read_errors
      ? `${state.read_errors} malformed records skipped · inspect trajectory.jsonl`
      : "Read-only · original run artifacts remain authoritative";
}
function selectBatch(id) {
  if (!state?.batches.some((b) => b.id === id)) return;
  playing = false;
  $("play").textContent = "▶";
  ui.follow = false;
  ui.batchId = id;
  const previousIndex = Number($("batch-position").dataset.index ?? 0);
  ui.time = state.batches.find(b => b.id === id).timestamp ?? bounds(state).start;
  ui.playing = false;
  draw();
  $("console").scrollTop = 0;
  const index = state.batches.findIndex((batch) => batch.id === id);
  reveal($("console"), index < previousIndex ? -1 : 1);
  const selected = $("batch-items").querySelector(".selected");
  if (selected) $("batches").scrollTo({
    left: selected.offsetLeft - ($("batches").clientWidth - selected.offsetWidth) / 2,
    behavior: reducedMotion() ? "instant" : "smooth",
  });
}
function stepBatch(direction) {
  if (!state?.batches.length) return;
  const index = ui.batchId ? state.batches.findIndex((b) => b.id === ui.batchId) : state.batches.length - 1;
  const next = Math.max(0, Math.min(state.batches.length - 1, index + direction));
  selectBatch(state.batches[next].id);
}

function cards() {
  const query = $("search").value.toLowerCase(),
    filter = $("status-filter").value;
  const shown = runs
    .filter((r) =>
      `${r.task} ${r.model} ${r.directory}`.toLowerCase().includes(query),
    )
    .filter(
      (r) =>
        filter === "all" ||
        (filter === "success" && r.status === "success") ||
        (filter === "running" && live(r)) ||
        (filter === "failed" &&
          [
            "failed",
            "error",
            "episode_timeout",
            "deployment_failed",
            "environment_failed",
          ].includes(r.status)),
    );
  $("collection-count").textContent =
    `${runs.length} RUN${runs.length === 1 ? "" : "S"}`;
  $("cards").innerHTML =
    shown
      .map(
        (r) =>
          `<a class="run-card ${esc(r.status)}" href="#run/${r.id}" aria-label="Open ${esc(r.task)}"><div class="card-image">${r.frame ? `<img src="${esc(r.frame.url)}" loading="lazy" alt="${esc(r.task)} last observation">` : '<span class="subtle">NO RECORDED FRAME</span>'}${statusHTML(r.status)}</div><div class="card-body"><h3>${esc(r.task)}</h3><p>${esc(r.integration || "Environment not recorded")} / ${esc(r.model || "Model not recorded")}</p><p title="${esc(r.directory)}">${esc(r.name)}</p><div class="card-stats"><span>${clock(elapsed(r))}</span><span>${r.steps ?? "—"} steps</span><span>Open console ↗</span></div></div></a>`,
      )
      .join("") || '<div class="empty"><strong>No matching runs</strong></div>';
}
async function route() {
  const version = ++routeVersion;
  if (stream) stream.close();
  stream = null;
  state = null;
  const match = location.hash.match(/^#run\/([a-f0-9]+)$/);
  currentId = match?.[1] ?? (runs.length === 1 ? runs[0].id : null);
  $("detail").hidden = !currentId;
  $("overview").hidden = !!currentId;
  $("loading").hidden = true;
  if (!currentId) {
    cards();
    return;
  }
  playing = false;
  $("play").textContent = "▶";
  ui.follow = true;
  ui.batchId = "";
  ui.camera = "";
  ui.frameId = null;
  ui.time = null;
  ui.playing = false;
  $("back").hidden = runs.length === 1;
  try {
    const data = await request(`/api/runs/${currentId}`);
    if (version !== routeVersion) return;
    state = data;
    draw();
    reveal(document.querySelector(".workspace"));
    connection("Connected", true);
    stream = new EventSource(`/api/runs/${currentId}/events`);
    stream.addEventListener("state", (event) => {
      if (version !== routeVersion) return;
      state = JSON.parse(event.data);
      draw();
      connection(live(state) ? "Live connection" : "Replay ready", true);
    });
    stream.onopen = () => connection("Connected", true);
    stream.onerror = () =>
      connection(
        state && !live(state)
          ? "Run finished · reconnect to replay"
          : "Reconnecting",
      );
  } catch (e) {
    connection("Unavailable");
    $("run-error").hidden = false;
    $("run-error").textContent = e.message;
  }
}
$("search").addEventListener("input", cards);
$("status-filter").addEventListener("change", cards);
$("back").addEventListener("click", () => {
  location.hash = "";
});
$("follow").addEventListener("click", () => {
  playing = false;
  $("play").textContent = "▶";
  ui.follow = true;
  ui.batchId = "";
  ui.playing = false;
  draw();
  $("console").scrollTop = $("console").scrollHeight;
  reveal($("console"));
});
function selectCamera(camera) {
  ui.camera = camera;
  renderViewer(state, ui);
}
$("camera").addEventListener("change", () => selectCamera($("camera").value));
$("camera-tiles").addEventListener("click", event => {
  const tile = event.target.closest('[data-camera]');
  if (tile) selectCamera(tile.dataset.camera);
});
function seek(time) {
  ui.follow = false;
  ui.time = time;
  const previous = ui.batchId;
  ui.batchId = batchAt(state, time);
  renderViewer(state, ui);
  if (previous !== ui.batchId) {
    renderConsole(state, ui);
    renderTimeline(state, ui);
    $("console").scrollTop = 0;
    const selected = $("batch-items").querySelector(".selected");
    if (selected) $("batches").scrollTo({
      left: selected.offsetLeft - ($("batches").clientWidth - selected.offsetWidth) / 2,
      behavior: "instant",
    });
  }
}
$("frame-slider").addEventListener("input", () => {
  playing = ui.playing = false;
  seek(bounds(state).start + Number($("frame-slider").value));
});
$("play").addEventListener("click", () => {
  if (!state?.frames.length) return;
  playing = ui.playing = !playing;
  if (playing) {
    const { start, end } = bounds(state);
    if (ui.follow || ui.time == null || ui.time >= end) ui.time = start;
    ui.follow = false;
    seek(ui.time);
  } else renderViewer(state, ui);
});
let lastTick = performance.now();
setInterval(() => {
  const now = performance.now(), delta = Math.min(.5, (now - lastTick) / 1000);
  lastTick = now;
  if (!playing || !state || !viewerReady()) return;
  const time = advance(state, ui.time, delta * Number($("playback-speed").value), $("skip-still").checked);
  if (time >= bounds(state).end) playing = ui.playing = false;
  seek(time);
}, 100);
$("fullscreen").addEventListener("click", () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else $("viewer").requestFullscreen?.();
});
$("frame").addEventListener("error", () => {
  $("frame").hidden = true;
  $("frame-empty").hidden = false;
  $("frame-empty").querySelector("strong").textContent =
    "Recorded image unavailable";
  $("frame-empty").querySelector("span:last-child").textContent =
    "The file may be missing or the monitor has stopped.";
});
$("batch-prev").addEventListener("click", () => stepBatch(-1));
$("batch-next").addEventListener("click", () => stepBatch(1));
$("batches").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  event.preventDefault();
  stepBatch(event.key === "ArrowLeft" ? -1 : 1);
  $("batch-items").querySelector(".selected")?.focus({ preventScroll: true });
});
$("console").addEventListener("click", (event) => {
  const details = event.target.closest("summary")?.parentElement;
  if (!details) return;
  requestAnimationFrame(() => {
    if (details.open) animate(details.querySelector(".tool-body"), [
      { opacity: 0, transform: "translateY(-4px)" },
      { opacity: 1, transform: "translateY(0)" },
    ], 180);
  });
});
window.addEventListener("resize", () => positionBatchCursor());
$("batches").addEventListener("click", (e) => {
  const target = e.target.closest("[data-batch]");
  if (target) selectBatch(target.dataset.batch);
});
$("timeline").addEventListener("click", (e) => {
  const target = e.target.closest("[data-batch]");
  if (target?.dataset.batch) selectBatch(target.dataset.batch);
});
$("timeline").addEventListener("keydown", (e) => {
  if (["Enter", " "].includes(e.key) && e.target.dataset.batch) {
    e.preventDefault();
    selectBatch(e.target.dataset.batch);
  }
});
window.addEventListener("hashchange", route);
setInterval(() => {
  if (state && live(state)) {
    renderMetrics(state);
    renderQueue(state);
    renderTimeline(state, ui);
  }
}, 1000);
setInterval(async () => {
  if (!currentId) {
    try {
      const updated = await request("/api/runs");
      if (JSON.stringify(updated) !== JSON.stringify(runs)) {
        runs = updated;
        cards();
      }
      connection("Directory view", true);
    } catch {
      connection("Reconnecting");
    }
  }
}, 5000);
try {
  runs = await request("/api/runs");
  connection("Connected", true);
  await route();
} catch (e) {
  $("loading").textContent = `Could not open records: ${e.message}`;
  connection("Unavailable");
}

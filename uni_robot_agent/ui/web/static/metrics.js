import { $, esc, clock, seconds, elapsed, live } from "./common.js";
export function renderMetrics(s) {
  const time = elapsed(s),
    remaining =
      s.max_steps != null && s.steps != null
        ? Math.max(0, s.max_steps - s.steps)
        : null;
  const ratio =
    s.max_steps > 0 && s.steps != null
      ? Math.min(100, (100 * s.steps) / s.max_steps)
      : null;
  const incomplete = s.models.filter((x) => x.started_at && !x.finished_at).length;
  const values = [
    [
      "Elapsed",
      clock(time),
      s.episode_timeout_s
        ? `${seconds(Math.max(0, s.episode_timeout_s - (time || 0)))} time budget left`
        : "No time budget reported",
    ],
    [
      "Environment steps",
      s.steps ?? "—",
      s.max_steps != null
        ? `of ${s.max_steps.toLocaleString()} budget`
        : "Budget unavailable",
    ],
    ["Steps remaining", remaining ?? "—", "Environment budget, not completion"],
    [
      "Task outcome",
      s.task_success === true
        ? "Success"
        : s.task_success === false
          ? "Failed"
          : "Pending",
      s.score == null ? "Score not provided" : `Score ${s.score}`,
    ],
    [
      "Model requests",
      s.models.length || "—",
      incomplete
        ? live(s)
          ? `${incomplete} request${incomplete > 1 ? "s" : ""} in flight`
          : `${incomplete} request completion${incomplete > 1 ? "s" : ""} not recorded`
        : "Recorded gateway requests",
    ],
  ];
  if (
    !["running", "starting", "deploying", "finishing"].includes(s.status) &&
    s.task_success == null
  )
    values[3][1] = "Ungraded";
  $("metrics").innerHTML = values
    .map(
      ([label, value, sub], i) =>
        `<div class="metric"><div class="metric-label">${label}</div><div class="metric-value">${esc(typeof value === "number" ? value.toLocaleString() : value)}</div><div class="metric-sub">${esc(sub)}</div>${i === 1 && ratio != null ? `<div class="budget" role="meter" aria-label="Environment step budget used" aria-valuenow="${s.steps}" aria-valuemin="0" aria-valuemax="${s.max_steps}"><i style="width:${ratio}%"></i></div>` : ""}</div>`,
    )
    .join("");
}
export function renderQueue(s) {
  const lines = [];
  if (!live(s)) {
    $("queue").innerHTML =
      '<div class="queue-row"><span class="queue-label">QUEUE</span><span class="queue-value">No live queue · inspect recorded intervals below</span></div>';
    return;
  }
  const requests = s.models.filter((m) => m.started_at && !m.finished_at);
  if (requests.length)
    lines.push(
      `<div class="queue-row"><span class="queue-label">MODEL</span><span class="queue-value">${requests.length} in flight · ${esc(seconds(Date.now() / 1000 - requests[0].started_at))}</span></div>`,
    );
  for (const [resource, q] of Object.entries(s.queue)) {
    for (const job of q.running || [])
      lines.push(
        `<div class="queue-row"><span class="queue-label">${esc(resource)}</span><span class="queue-value">▶ ${esc(job.tool_name)} · ${esc(seconds(Date.now() / 1000 - job.started_at))}</span></div>`,
      );
    if (q.queued?.length)
      lines.push(
        `<div class="queue-row"><span class="queue-label waiting">QUEUED</span><span class="queue-value waiting">${q.queued.map((j) => esc(j.tool_name)).join(" → ")}</span></div>`,
      );
  }
  // Immediate calls bypass the scheduler but still emit execution facts.
  for (const call of s.calls.filter((c) => c.execution_status === "running" && !c.job_id))
    lines.push(
      `<div class="queue-row"><span class="queue-label">${esc(call.resource || "TOOL")}</span><span class="queue-value">▶ ${esc(call.name)} · ${esc(seconds(Date.now() / 1000 - call.started_at))}</span></div>`,
    );
  $("queue").innerHTML =
    lines.join("") ||
    '<div class="queue-row"><span class="queue-label">QUEUE</span><span class="queue-value">No active work reported</span></div>';
}

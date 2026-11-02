export const $ = (id) => document.getElementById(id);
export const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const seconds = (s) =>
  s == null || !Number.isFinite(s)
    ? "—"
    : s < 60
      ? `${Math.max(0, s).toFixed(1)}s`
      : `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
export const clock = (s) =>
  s == null
    ? "—"
    : [
        Math.floor(Math.max(0, s) / 3600),
        Math.floor(Math.max(0, s) / 60) % 60,
        Math.floor(Math.max(0, s)) % 60,
      ]
        .map((x) => String(x).padStart(2, "0"))
        .join(":");
export const live = (state) =>
  ["running", "starting", "deploying", "finishing"].includes(state.status);
export const statusLabel = (s) =>
  ({
    finished: "Finished · ungraded",
    unknown: "Unknown / legacy",
    stale: "No recent heartbeat",
    acknowledged: "Acknowledged",
    dispatching: "Dispatching",
    environment_failed: "Environment failed",
    deployment_failed: "Deployment failed",
    episode_timeout: "Timed out",
  })[s] ||
  s ||
  "unknown";
export const elapsed = (s) =>
  s.elapsed_s ??
  (s.started_at == null
    ? null
    : Math.max(
        0,
        (s.finished_at ?? (live(s) ? Date.now() / 1000 : s.started_at)) -
          s.started_at,
      ));
export const statusHTML = (s) =>
  `<span class="status ${esc(s)}">${esc(statusLabel(s))}</span>`;

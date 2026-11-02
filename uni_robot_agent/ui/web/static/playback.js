import { viewFrames } from './frames.js';

export function bounds(state) {
  const times = viewFrames(state).map(f => f.timestamp).filter(Number.isFinite);
  const starts = [state.started_at, ...times, ...state.batches.map(b => b.timestamp)].filter(Number.isFinite);
  const start = starts.length ? Math.min(...starts) : 0;
  return { start, end: Math.max(start, ...times, ...[state.finished_at].filter(Number.isFinite)) };
}

export function frameAt(frames, camera, time) {
  return frames.findLast(f => f.camera === camera && Number.isFinite(f.timestamp) && f.timestamp <= time) || null;
}

export function batchAt(state, time) {
  return state.batches.findLast(b => Number.isFinite(b.timestamp) && b.timestamp <= time)?.id || '';
}

// Still intervals may be skipped explicitly; recorded timestamps never change.
export function advance(state, time, delta, skipStill) {
  const { end } = bounds(state);
  const updates = [...viewFrames(state).map(f => f.timestamp), ...state.batches.map(b => b.timestamp), end]
    .filter(t => Number.isFinite(t) && t > time);
  const upcoming = Math.min(end, ...updates);
  // Stop at every update boundary, even if a timer tick spans several frames.
  let next = Math.min(end, upcoming, time + delta);
  if (skipStill && upcoming - time > 2) next = upcoming;
  return next;
}

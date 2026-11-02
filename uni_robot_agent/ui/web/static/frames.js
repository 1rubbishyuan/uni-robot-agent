// Display continuity is separate from exact tool-to-image association.
const cachedFrames = new WeakMap();
export function viewFrames(state) {
  if (cachedFrames.has(state)) return cachedFrames.get(state);
  const unique = new Map();
  for (const frame of state.frames) {
    const key = frame.snapshot_id
      ? `${frame.snapshot_id}:${frame.camera}:${frame.path.split("/").at(-1)}`
      : frame.id;
    const previous = unique.get(key);
    // The tool output and monitor may archive the same snapshot twice.
    if (!previous || (!previous.batch_id && frame.batch_id)) unique.set(key, frame);
  }
  const result = [...unique.values()].sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
  cachedFrames.set(state, result);
  return result;
}

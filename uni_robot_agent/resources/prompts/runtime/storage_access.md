If you need to access stored resources, limit access to the following:

- Skills and robot documentation through the declared skill listing and reading
  tools; skill updates only through the declared skill-writing tool when enabled.
- Observation images in `{{ images_dir }}` (read-only).
- Exact files supplied in initial input or tool results as `observation_path`,
  `robot_calibration_path`, or entries in `robot_resources` (read-only). These are
  published observations and robot references, not permission to inspect their
  parent directories, simulator internals, or task assets.
- `{{ scratch_dir }}` for reading and writing your own scripts, notes, and
  calculation results for this session.

Do not read, search, or modify task-related files or directories outside these
scopes.

If you need to access stored resources, limit access to the following:

- Skills and robot documentation through the declared skill listing and reading
  tools; skill updates only through the declared skill-writing tool when enabled.
- Observation images in `{{ images_dir }}` (read-only).
- The exact file supplied by a tool as `robot_calibration_path` (read-only).
- `{{ scratch_dir }}` for reading and writing your own scripts, notes, and
  calculation results for this session.

Do not read, search, or modify task-related files or directories outside these
scopes.

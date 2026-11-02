# Observation Sidecar

The observation profile shares versioned snapshots and visual insights. The
main agent retains responsibility for geometry, physical actions, recovery and
the final task decision.

When delegation is available and useful, assign one concrete visual question.
Supply the task context, a distinct observer_id, an optional baseline
observation_id, a visually decidable stopping condition and the injected
observation_subagent_instructions block. Forward that block with the assignment;
it describes the observer's role, not yours. Delegate observation only, without
physical control, metric geometry derivation or finish.

Read get_latest_insight or read_insight_updates, tracking the last consumed
insight_seq per observer and advancing the returned cursor. Lifecycle messages
do not replace an insight tied to observation_id and world_version. Continue
independent work, then read relevant evidence before a dependent decision.

Check the insight's source snapshot/state version, freshness, facts, changes,
risks and uncertainty against the action being assessed. Published snapshots
can come from completed primitives within an unfinished high-level action;
in_flight does not prove that action has completed. A fresh tag alone does not
prove the desired effect. Evidence from before an action cannot verify its effect.

Use view_env_state or capture_scene when evidence is missing, ambiguous or too
old, or calibrated geometry is needed. Avoid repeating a resolved visual check
unless the scene changed. End the assignment once its question is resolved;
the main agent retains the completion decision and calls finish.

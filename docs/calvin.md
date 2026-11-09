# CALVIN

`--integration calvin` connects shared Direct gripper tools to the official
[CALVIN](https://github.com/mees/calvin) PyBullet environment and task predicates.
It provides **one task per episode**, with the native Panda longer fingers,
static and wrist RGB cameras, optical calibration and optional depth.

## Prepare a native service

Use a separate Linux/Python 3.11 native runtime with PyBullet, Gym, Hydra,
OmegaConf and the other upstream `calvin_env` dependencies. Install the matching
`uni-robot-agent-runtime[benchmarks]` wheel. The model interpreter does not need
these simulator dependencies. Use these paired revisions; the environment pin
is the benchmark repository's submodule revision:

```bash
git clone https://github.com/mees/calvin.git /persistent/calvin
git -C /persistent/calvin checkout fa03f01f19c65920e18cf37398a9ce859274af76
git clone https://github.com/mees/calvin_env.git /persistent/calvin_env
git -C /persistent/calvin_env checkout 1431a46bd36bde5903fb6345e68b5ccc30def666
```

The native Git checkout supplies scene and robot assets. EGL is enabled by
default; `--no-egl` selects PyBullet's CPU renderer. No tactile cameras or
policy-model dependencies are required by this binding.

For an integration probe using the native scene D reset:

```bash
python -m uni_robot_agent.integrations.calvin.server \
  --native-root /persistent/calvin_env --benchmark-root /persistent/calvin \
  --scene D --scene-reset --task open_drawer --seed 0 \
  --run-id calvin-drawer-0 --output-dir /persistent/runs/calvin-drawer-0 \
  --no-depth --port 18786
```

For a specified reset, replace `--scene-reset` with `--initial-state STATE.npz`.
The file must contain finite `robot_obs[15]` and `scene_obs[24]` arrays in native
CALVIN order; pickle loading is disabled. Its SHA-256, scene, seed, revisions
and action budget are recorded. These reset arrays and native scene information
are never supplied to the planner. Choose a task whose precondition holds in
that reset; the service does not repair or synthesize benchmark initial states.

Connect a planner with `--integration calvin --env-url http://127.0.0.1:18786
--action-profile direct`. Each native service owns one episode and requires a
new output directory. Automatic setup/deploy recipes and Hybrid actors are not
included.

## Control and evaluation

- Public poses refer to the native Panda TCP in world metres / `wxyz`.
  The adapter converts to native absolute pose / `xyzw`; PyBullet's native IK
  and motor control execute the action. This differs from normalized relative
  action policies and must be declared in comparisons.
- Native gripper control is binary: requested opening below 0.5 closes;
  otherwise it opens. Reported opening comes from measured finger joints.
- One primitive executes one native `env.step`, including its configured physics
  repeats, at nominal 30 Hz. The default budget is 360 actions; explicit
  `--max-steps` overrides are recorded.
- The native task oracle compares current information with the episode's reset
  information after each action. Success is latched and ends the episode.
  Agent `finish` cannot override it. Repeated captures do not render, step physics
  or query the oracle; they reuse the most recent native frame and calibration.
- The static OpenGL and moving wrist views are converted to optical coordinates
  consistently. RGB and optional metric depth retain the native image layout.
- Instructions come from the pinned official validation annotations. Object
  state, scene state, rewards and task-verifier diagnostics remain service-private.

This binding does **not** implement CALVIN's 1,000 five-subtask sequences, state
continuation between subtasks, sequence sampling or average-chain-length metric.
A default scene reset is an integration probe, not an official evaluation reset.
Single-task results must not be reported as the standard CALVIN chain score.

## Native validation

A Linux/RTX 5090 EGL probe used scene D's default reset, `open_drawer`, seed 0
and a 20-action budget. Twenty HTTP pose/gripper actions produced 24.9 mm maximum
TCP displacement. Both camera images were inspected; snapshot publication,
budget termination and cleanup passed. The native task verdict was false:
this was a lift-and-return motion probe, not an attempted drawer solution.

The tested isolated runtime used Python 3.11, PyBullet 3.2.7, Gym 0.23.1 and
NumPy 1.26.4. Install upstream dependencies including `rich` (imported by the
pinned environment), Hydra/colorlog and numpy-quaternion.

A subsequent Qwen3.8-Flash Direct episode completed `open_drawer` from the same
scene D reset with native `task_success=true` after 78 actions (412 seconds).
It used RGB, calibration, SAM and runtime skills. The final image showed the
drawer open; the planner exited normally with a complete training trace and no
reported storage errors. This validates one single-task execution path, not
the official chain score. Explicit reset files and other scenes/tasks remain
outside the completed native model validation.

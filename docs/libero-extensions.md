# LIBERO-Plus

Stock LIBERO remains available through `--integration robouse`. The separate
`libero_plus` integration uses the official LIBERO-Plus task catalog, language,
initial states, corrupted observations and success predicates with the shared
Direct gripper tools. It does not change existing RoboUse tasks.

## Prepare a native runtime

Use an isolated Linux service environment with Python 3.11+, `robosuite==1.4.0`,
the `benchmarks` extra and the matching uni-robot-agent runtime distribution. Follow the
[upstream installation and asset instructions](https://github.com/sylvestf/LIBERO-plus)
for its other dependencies, including its image-corruption libraries. Do not
install stock LIBERO and its forks into the same interpreter.

```bash
git clone https://github.com/sylvestf/LIBERO-plus.git /persistent/LIBERO-plus
git -C /persistent/LIBERO-plus checkout 4976dc30028e805ff8094b55501d532c48fec182
```

Prepare the official assets in that checkout. The server checks its pinned
revision and rejects tracked modifications. Native code and assets are external,
not included in the harness wheel. Native import, asset and rendering failures
are setup failures, not task failures. Automatic `setup` / `--deploy local`
recipes are not provided for this integration.

## Start one episode

In the native service interpreter:

```bash
python -m uni_robot_agent.integrations.libero_plus.server \
  --native-root /persistent/LIBERO-plus --suite libero_spatial --task 0 \
  --init-state-index 0 --seed 0 --run-id plus-spatial-0 \
  --output-dir /persistent/runs/plus-spatial-0 --no-depth --port 18781
```

In the planner interpreter:

```bash
uni-robot-agent run --integration libero_plus --env-url http://127.0.0.1:18781 \
  --model MODEL --action-profile direct --no-sam3
```

`--task` on the service accepts an official task name or zero-based index.
The planner's optional `--task` checks the resolved **task name**, not that index.
Every service owns exactly one episode; start a new service for another trial.
The output directory must be new. Service creation, physics, images and teardown
run on a single owner thread.

## Evaluation semantics

- Select an explicit official initial-state index; invalid indices fail instead
  of wrapping. The simulator random seed is a separate field.
- Default settling is ten open-gripper no-op steps. Recorded policy budgets are
  220 / 280 / 300 / 520 for spatial / object / goal / long (`libero_10`). Override
  `--max-steps` and `--settle-steps` explicitly when reproducing another protocol.
- Upstream recommends **one trial per Plus task**, since perturbation variants
  already have separate task IDs. This service is an episode adapter, not a claim
  to have run the complete benchmark or reproduced a VLA leaderboard score.
- Capture reuses the native wrapper's last observation, including sensor noise.
  It never renders an uncorrupted replacement or advances physics. RGB and depth
  use the same vertical convention. Zoom/glass-distorted agent-view images have
  no advertised calibration or paired depth; wrist-camera capabilities remain.
- Only robot state and configured images are exposed. Object poses, native
  rewards and verifier diagnostics are not model-visible. `finish` cannot turn
  an unsuccessful trajectory into a success.
- Direct tool trajectories, camera/depth access, action budgets and training
  access must be reported when comparing against policy-learning baselines.

The native source retains its upstream license; no simulator code or assets are
vendored by this integration.

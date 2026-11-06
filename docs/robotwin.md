# RoboTwin 2.0

`--integration robotwin` connects the shared Direct gripper tools to a separately
prepared RoboTwin environment service. The initial binding supports the official
`aloha_agilex` dual-arm parallel-gripper embodiment and `demo_clean` /
`demo_randomized` task configurations. Hybrid actors, other embodiments and
automatic `setup` / `--deploy local` recipes are not included.

## Prepare and run

Follow the [RoboTwin installation and asset instructions](https://github.com/RoboTwin-Platform/RoboTwin)
in an isolated Linux native-service environment. Install the matching
`uni-robot-agent-runtime[benchmarks]` wheel there. Native SAPIEN, motion planners,
robot assets and task assets remain external and retain their upstream licenses.
Use this pinned, unmodified source checkout:

```bash
git clone https://github.com/RoboTwin-Platform/RoboTwin.git /persistent/RoboTwin
git -C /persistent/RoboTwin checkout ea8b21121ebb3cd201ff5b3fe361944ac94eda3f
python -m uni_robot_agent.integrations.robotwin.server \
  --native-root /persistent/RoboTwin --task beat_block_hammer \
  --task-config demo_clean --seed 0 --run-id robotwin-hammer-0 \
  --output-dir /persistent/runs/robotwin-hammer-0 --no-depth --port 18783
```

The output directory must be new. The service checks the upstream expert's seed
admission, then resets the same seed for the agent. A rejected seed is a setup
exclusion, not an agent failure. Instruction selection uses upstream seen/unseen
configuration; `--instruction-type` can override it. Upstream may fall back to the
task name when description generation fails. Metadata records the selected
configuration, seed, native revision, budget and evaluation profile.

Connect from the planner interpreter:

```bash
uni-robot-agent run --integration robotwin --env-url http://127.0.0.1:18783 \
  --model MODEL --action-profile direct --no-sam3
```

Each service owns one episode. Native setup, stepping, observations and cleanup
run on one owner thread. Launch another service for each new trial.

## Control and evaluation

- Public poses are world-frame TCP centres, with `wxyz` quaternions. The adapter
  converts the 12 cm local-X offset to the pinned native EE reference. Every
  primitive action supplies both arms, including a held arm.
- Gripper openings are normalized from 0 (closed) to 1 (open). State reads use
  articulation joint positions; requested drive targets are reported separately.
- One service step invokes one native `take_action(..., action_type="ee")`.
  That action executes a planned trajectory with variable physics duration.
  The advertised 30 Hz is the upstream nominal policy rate: tool hold durations
  and timing limits use nominal action counts, not measured simulation seconds.
- Use the native per-task action limit, or explicitly record `--max-steps`.
  Step counts mean native policy actions, not individual physics steps. Direct
  tool trajectories and this timing differ from VLA action-stream evaluations;
  report them when comparing scores.
- Head and both wrist RGB cameras are available. `--depth` opts into metric depth.
  Calibration maps optical coordinates into world coordinates. Capture reuses
  cached observations without another physics step or render. Object state,
  rewards, expert trajectories and success diagnostics are not model inputs.
- Success comes from the native evaluator. Agent `finish` status does not
  override it. Missing assets, rendering failures and invalid task configuration
  are setup errors, not benchmark failures.

This is an episode integration, not a full benchmark runner or a reproduced
leaderboard result. Native rendering and complete task coverage require the
upstream runtime and assets; CPU contract tests do not establish those results.

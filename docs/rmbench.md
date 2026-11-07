# RMBench

`--integration rmbench` binds the official memory-dependent manipulation tasks
of [RMBench](https://github.com/RoboTwin-Platform/RMBench) to the shared Direct
gripper tools. The initial binding supports `demo_clean` and the dual-arm
`aloha-agilex` embodiment. It reuses RoboTwin's TCP conversion and measured
joint/gripper state; the native service must run in its own process and checkout.

## Native setup

Prepare upstream SAPIEN, CuRobo and embodiment/object assets in an isolated Linux
runtime, and install the matching `uni-robot-agent-runtime[benchmarks]` wheel.
RMBench uses its own assets from `TianxingChen/RMBench` on Hugging Face; RoboTwin
object assets alone are insufficient. Run the upstream embodiment-path setup.

```bash
git clone https://github.com/RoboTwin-Platform/RMBench.git /persistent/RMBench
git -C /persistent/RMBench checkout 7d1cdfc7661c273a7a5c5f6126e2d1a7f0a3f4b7
python -m uni_robot_agent.integrations.rmbench.server \
  --native-root /persistent/RMBench --task press_button --seed 0 \
  --run-id rmbench-buttons-0 --output-dir /persistent/runs/rmbench-buttons-0 \
  --no-depth --port 18785
```

Connect a planner with `--integration rmbench --env-url http://127.0.0.1:18785
--action-profile direct`. Each service owns one episode and requires a new output
directory. Automatic setup/deploy recipes and Hybrid actors are not provided.

## Memory and evaluation boundaries

- The official expert admits the selected seed before the environment resets for
  the agent. Rejected seeds are setup exclusions; no automatic seed replacement
  is hidden in the adapter. Expert trajectories and hidden task state are private.
- Native instruction selection uses the seen/unseen corpus. The upstream fallback
  to the task name remains possible and the selected instruction is recorded.
- Head, both wrist cameras and the configured third view are exposed with native
  RGB and optical calibration. Depth is off unless `--depth` is supplied.
- Captures use cached observations and do not advance physics, task predicates
  or memory phases. Native `check_success` is stateful for some tasks and runs
  only where upstream calls it during native actions and expert admission.
- One primitive is one native EE policy action with variable physics duration.
  Memory phases can advance within that action. The advertised 30 Hz is nominal;
  it does not make this a reproduction of a fixed-rate VLA evaluation.
- Task action limits come from upstream. A missing limit requires explicit
  `--max-steps`; overrides belong in reported evaluation settings.
- Native success is recorded independently of agent `finish`. This is an episode
  integration, not a complete benchmark runner or a leaderboard claim.

## Native validation

A Linux/RTX 5090 probe ran `press_button`, `demo_clean`, seed 0. The upstream
expert admitted the seed; twenty HTTP EE actions then moved both TCPs by about
25.0 mm. All four camera snapshots, action-budget termination and teardown
passed. Head and third-view images were visually inspected. Native task success
was false; this bounded movement probe did not solve the button task.

The runtime reused SAPIEN 3.0.0b1 and CuRobo 0.7.8 with CUDA 12.8 support.
Besides task objects and the embodiment, upstream imports require
`assets/objects/objaverse/list.json` and `assets/objects/same.json`, even for a
clean scene. Only the tested task's assets were prepared. OIDN invalid-handle
errors left visible ray-tracing noise, as in the RoboTwin probe; rendering
quality needs validation before score comparisons. No full model episode or
complete memory-task suite result is claimed.

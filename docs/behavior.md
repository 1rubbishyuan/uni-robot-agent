# BEHAVIOR R1Pro Hybrid

`--integration behavior` connects a prepared BEHAVIOR mobile manipulation episode
to the shared Hybrid tools. The R1Pro base, torso, two arms and grippers execute
together. The integration selects scene, skill, actor and episode tools; Direct
gripper tools are not exposed.

One `behavior_r1pro_joint` binding serves tasks with the same native R1Pro
observation/controller profile. New tasks select their native task/scene/instance
and suitable weights, rather than adding another binding. This does not imply
that a single checkpoint has learned every BEHAVIOR task. The official example
Pi0.5 checkpoint currently targets `turning_on_radio`.

## Prepare the simulator

Install [BEHAVIOR-1K and its assets](https://behavior.stanford.edu/challenge/baselines)
in a separate native environment. This integration pins source revision
`fe66cc74538f7eba3c57255da0ee88e8ce113c87` (`v3.9.4`) and Isaac Sim 5.1.0.
Keep the native checkout clean. Install the matching harness runtime wheel into
that interpreter, then adopt it without modifying the installation:

```bash
uni-robot-agent setup --integration behavior \
  --runtime-python /path/to/behavior/bin/python \
  --native-root /path/to/BEHAVIOR-1K

uni-robot-agent deploy env --integration behavior --task turning_on_radio \
  --run-id behavior-radio-0 --seed 0 --no-depth
```

Native installation owns Isaac Sim, BDDL/JoyLo dependencies, simulation assets,
task instances and human-demo summary metadata. The harness does not install these
through its ordinary Python venv setup. Full demonstration videos are unnecessary
for checkpoint inference.

Select a task and an instance through native evaluation metadata. Deployment uses
public test index 0 by default. Use `--runtime-env BEHAVIOR_INSTANCE_INDEX=1` to
select another public instance, and `--runtime-env BEHAVIOR_EVALUATION_MODE=train`
when explicitly evaluating training instances. Public and hidden test indices are
0..19; train values are native instance IDs. The environment prepares once before
clients connect; connecting an agent never resets a running episode.

## Prepare the actor

Download and extract the [official Pi0.5 checkpoint](https://behavior.stanford.edu/challenge/baselines#provided-checkpoints).
The directory must contain `params/` and the original normalization assets.
The provided recipe expects `assets/turning_on_radio/norm_stats.json`; do not
rename or replace normalization data to make a different checkpoint fit.
For a different asset ID, use an explicit OpenPI configuration with that ID and
its verified source/checkpoint identity.

```bash
uni-robot-agent setup --service actor --profile behavior_r1pro_pi05 \
  --checkpoint /path/to/extracted/checkpoint
uni-robot-agent deploy actor --profile behavior_r1pro_pi05
```

This uses the existing OpenPI/JAX backend and a separate pinned source/runtime.
It does not launch the upstream B1K policy wrapper or a second action queue.
For a direct service launch, adapt
[`openpi_behavior_r1pro.toml`](../examples/services/openpi_behavior_r1pro.toml).
The setup verification reports the checkpoint identity needed by the agent.

Run [`behavior_hybrid.sh`](../examples/behavior_hybrid.sh) against both prepared
services. The example defaults to Qwen `qwen3.8-flash` and disables the optional reviewer. Remove `--no-vla-review`
when enabling it and configure its provider as described in [running](running.md).
The agent still decides when to invoke `actor_act` in either case.

## Observation and action conventions

| Boundary | Meaning |
|---|---|
| Captured observations | Native 61D robot proprioception and head/left-wrist/right-wrist RGB |
| OpenPI input transform | Extracts the original 23D model state; gripper state is physical finger position |
| OpenPI output | 32 samples of 23 values; native output transforms already restore joint deltas |
| Base action | Normalized model command maps to local x/y velocity up to 0.75 m/s and yaw up to 1 rad/s |
| Torso and arms | Absolute named joint positions in radians |
| Gripper action | Native normalized smooth-position command maps to symmetric finger positions in metres |
| Execution | One atomic physical command advances all components by 1/30 second |

The binding clips to the declared native controller limits and records both raw
and scoped predictions. It never adds the observation to already absolute model
outputs. The default prefix executes 16 of the 32 predictions, then captures again.
Only `active_groups=["whole_body"]` and empty constraints are supported. Reviewer
choices are a bounded student prefix or stop; no direct joint correction is advertised.

Simulation advances synchronously during acknowledged native steps. It does not
continue moving during planner/model inference. The native evaluator's task
preparation, assisted grasping and metrics are retained. Success, predicate scores
and privileged scene state remain outside model-visible observations and feedback.
Final artifacts contain native task outcomes and evaluation metadata.
Non-finite native metrics (for example, normalized distance before movement) use
JSON strings `"Infinity"`, `"-Infinity"` or `"NaN"`; finite metrics and task success
retain their native values.

## Validation status

CPU checks cover profile and recipe agreement, native action roundtrips, units,
clipping, atomic step receipts, cancellation and result isolation. Native runtime
acceptance and learned-policy rollouts require the separately prepared simulator,
assets and checkpoint; they have not been established by these CPU checks.
No benchmark success rate is claimed. The synchronous Hybrid run is not declared
timing-equivalent to the official standalone policy evaluation.

See [SOURCE.md](../uni_robot_agent/integrations/behavior/SOURCE.md) for pins and
upstream attribution, and [Hybrid](hybrid.md) for the shared collaboration contract.

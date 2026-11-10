# Native sources and compatibility

The integration adapts [NaVILA-Bench](https://github.com/yang-zj1026/NaVILA-Bench)
revision `e9d2db12ce5788c0f987d734c0094100b6bc0d3a`, using its Go2 vision locomotion
checkpoint `logs/rsl_rl/go2_vision/2024-09-25_23-22-02/model_26499.pt` and adjacent
`params/agent.yaml`. Native source, robot assets, scenes and weights are external;
retain their respective upstream licenses when preparing a runtime.

The simulator boundary keeps two explicit profiles:

| Profile | IsaacLab source and revision | Native interpreter |
|---|---|---|
| `isaac41` (default) | [Original project fork](https://github.com/yang-zj1026/IsaacLab/tree/4d558ec83878c4892a46591c85ba91ac9d3c1834), `4d558ec83878c4892a46591c85ba91ac9d3c1834` | Isaac Sim 4.1.0 / Python 3.10 |
| `isaac51` | [Compatible fork](https://github.com/yuechen0614/IsaacLab/tree/afca7b09d60d8beb9c1cb28b43066499940b969b), `afca7b09d60d8beb9c1cb28b43066499940b969b` | Isaac Sim 5.1.0.0 / Python 3.11 |

The harness process uses Python 3.11 or newer. A private worker isolates the
native runtime and imports no harness package. Default `isaac41` preflight rejects
Blackwell GPUs before simulator launch. `isaac51` requires explicit preparation
and selection; it does not relax the default profile's version or hardware checks.

Scene/task assets come from [Zhaojing/VLN-CE-Isaac](https://huggingface.co/datasets/Zhaojing/VLN-CE-Isaac)
revision `bb580b5f25ab6c890ef334bd6546a9934376f668`. Prepare
`vln_ce_isaac_v1.json.gz` and `matterport_usd/<scene>/<scene>.usd` in the upstream
extension's `assets/` directory, or pass their common directory as `--assets-dir`
to the Isaac 5.1 preparation command. This integration does not download or
redistribute these assets implicitly.

The migration retains the original [Isaac 4.1 Go2 USD](https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/4.1/Isaac/IsaacLab/Robots/Unitree/Go2/go2.usd)
and its sibling [Props/instanceable_meshes.usd](https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/4.1/Isaac/IsaacLab/Robots/Unitree/Go2/Props/instanceable_meshes.usd).
Pass the local `go2.usd` path to `--go2-usd`, preserving the `Props/` directory.
The required Go2 USD SHA256 is
`ba171c972b987d8c8fb7157ccad2ba9c0c1fed105755d2d8af46bef96cc11c6d`.

[The preparation helper](environment/isaac51.py) generates a separate source tree
and `navila-isaac51.json`; it never edits the pinned checkout. Its manifest
validates source and generated-code digests, the migration implementation,
dataset digest and location, and Go2 asset digests. Missing datasets or scene
directories fail preparation; changed inputs fail loading. The worker verifies
the selected Isaac version, IsaacLab revision and native module import paths.
See [the preparation and launch commands](../../../docs/navila.md).

The migration updates native package interfaces and preserves the scene's
original coordinates through raw USD references. A dedicated collision mesh
retains every original vertex and face while separating uniform physical
material from visual material subsets, avoiding an undefined PhysX contact-face
lookup. Visual assets, Go2 spawn coordinates, locomotion weights, observation
history and LiDAR remain in use. The newer physics runtime is an explicit
compatibility profile; this does not establish official score equivalence.

The independent model bridge targets [NaVILA](https://github.com/AnjieCheng/NaVILA)
revision `76b98f233dd0fff05dfcd69435eec6740febff9d` and
[a8cheng/navila-llama3-8b-8f](https://huggingface.co/a8cheng/navila-llama3-8b-8f)
revision `b2294e96581454468d6b94f38201f4f965ef48b7`. It speaks the benchmark's
`scripts/vlm_server.py` protocol and never imports its Torch/Pydantic 1 runtime.
That protocol lacks model identity attestation; the operator declares the
checkpoint digest and dedicates the upstream server to one bridge session.

The original model environment uses Torch 2.3. A separate Blackwell runtime
using Torch 2.7.0+cu128 and FlashAttention 2.8.3 has passed pinned-weight loading
and native server inference. This is a model-runtime check; no Hybrid task success
or benchmark score follows from it. Keep model and simulator dependencies in
separate environments.

The native reset retains the 100-step zero-command Go2 warmup. The environment
then records real front-camera frames at ticks 0, 1, 26, 51, ... (20 ms control
ticks). Model preprocessing retains the upstream uniform whole-episode eight
frame selection, front black padding and JPEG quality 75. Invalid textual
predictions raise errors rather than inheriting upstream's forward fallback.
The strict decoder supports the observed `The next action is ...` wrapper while
requiring exactly one complete command. Decode failures retain the original model
text and sampled-frame provenance in HTTP error details and correlated service logs.

The adapter prevents IsaacLab's automatic reset from overwriting terminal
observations. It uses an explicit 2,500-step evaluation budget. Actor stop yields
to the planner; planner finish makes a fresh navigation STOP declaration and
updates private native metrics. Cleanup, agent errors and timeouts do not declare
navigation completion. This differs from the standalone evaluator's
extra zero-velocity steps and loop fencepost behavior; report Hybrid separately
from a native route-only baseline.

Optional direct-server `--record-native-frames` recording retains every control
tick in `native/native-frames/index.jsonl`, with timestamps, PNG digests and
native request IDs. Execution receipts publish `native_tick_start` and
`native_tick_end` for the acknowledged motion span. These dense frame records
preserve their own provenance alongside the service's snapshot endpoints; they
do not change the model's historical sampling phase or navigation STOP semantics.

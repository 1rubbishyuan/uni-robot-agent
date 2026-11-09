# Native sources and compatibility

The integration adapts [NaVILA-Bench](https://github.com/yang-zj1026/NaVILA-Bench)
revision `e9d2db12ce5788c0f987d734c0094100b6bc0d3a`, using its Go2 vision locomotion
checkpoint `logs/rsl_rl/go2_vision/2024-09-25_23-22-02/model_26499.pt` and adjacent
`params/agent.yaml`. Native source, robot assets, scenes and weights are external;
retain their respective upstream licenses when preparing a runtime.

The simulator boundary pins the project's [IsaacLab fork](https://github.com/yang-zj1026/IsaacLab)
revision `4d558ec83878c4892a46591c85ba91ac9d3c1834` and Isaac Sim 4.1.0/Python 3.10.
The harness process uses Python 3.11 or newer. A private worker isolates these
runtimes and imports no harness package in the native interpreter.

Scene/task assets come from [Zhaojing/VLN-CE-Isaac](https://huggingface.co/datasets/Zhaojing/VLN-CE-Isaac)
revision `bb580b5f25ab6c890ef334bd6546a9934376f668`. Prepare the dataset and
Matterport USD directories in the upstream extension's `assets/` directory.
This integration does not download or redistribute these assets implicitly.

The independent model bridge targets [NaVILA](https://github.com/AnjieCheng/NaVILA)
revision `76b98f233dd0fff05dfcd69435eec6740febff9d` and
[a8cheng/navila-llama3-8b-8f](https://huggingface.co/a8cheng/navila-llama3-8b-8f)
revision `b2294e96581454468d6b94f38201f4f965ef48b7`. It speaks the benchmark's
`scripts/vlm_server.py` protocol and never imports its Torch/Pydantic 1 runtime.
That protocol lacks model identity attestation; the operator declares the
checkpoint digest and dedicates the upstream server to one bridge session.

The native reset retains the 100-step zero-command Go2 warmup. The environment
then records real front-camera frames at ticks 0, 1, 26, 51, ... (20 ms control
ticks). Model preprocessing retains the upstream uniform whole-episode eight
frame selection, front black padding and JPEG quality 75. Invalid textual
predictions raise errors rather than inheriting upstream's forward fallback.

The adapter prevents IsaacLab's automatic reset from overwriting terminal
observations. It uses an explicit 2,500-step evaluation budget. Actor stop yields
to the planner; planner finish makes a fresh navigation STOP declaration and
updates private native metrics. Cleanup, agent errors and timeouts do not declare
navigation completion. This differs from the standalone evaluator's
extra zero-velocity steps and loop fencepost behavior; report Hybrid separately
from a native route-only baseline.

Isaac Sim 4.1 does not support Blackwell GPUs. Preflight rejects that combination
before simulator launch. A newer Isaac migration is not included or claimed.

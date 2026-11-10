# BEHAVIOR R1Pro sources

The integration uses [BEHAVIOR-1K](https://github.com/StanfordVL/BEHAVIOR-1K)
revision `fe66cc74538f7eba3c57255da0ee88e8ce113c87` (`v3.9.4`) and its R1Pro
evaluation configuration. Native source and assets remain external; upstream
licenses and dataset terms apply to those resources.

The compatibility boundary reuses the native evaluator's environment creation,
instance restoration, settling, camera wrapper and metric updates. It overrides
policy creation and never calls the native policy or rollout loop. The harness
owns prediction, bounded prefix execution, receipts and artifacts.

The configuration retains 30 Hz action/rendering, 120 Hz physics, three 224x224
RGB cameras, local planar base velocities, absolute torso/arm positions,
symmetric smooth-position grippers, and assisted grasping. Camera calibration
is not published by this integration. Public test instance indices resolve to
the native held-out instance IDs; the native task timeout is retained.

The accompanying actor recipe uses [the BEHAVIOR OpenPI fork](https://github.com/wensi-ai/openpi)
revision `0cc8e355f7bac0976db1cc3139b1ff0379feea60`. The Python source digest is
`5338398f477171f296814d7705a02338ff033a57aff307251eaf80f42782a287`.
The recipe also verifies the pinned `uv.lock`. Original normalization, tokenization,
diffusion sampling and output delta restoration run in the model runtime.

The [official baseline page](https://behavior.stanford.edu/challenge/baselines)
links the released `turning_on_radio` checkpoint. Download it separately and
supply its extracted params/assets directory. This recipe expects
`assets/turning_on_radio/norm_stats.json`; check the actual checkpoint layout.
Other checkpoint asset IDs require an explicit matching OpenPI service configuration.

The native simulator/assets and checkpoint were not executed during the CPU
integration checks. The source API was inspected at the pinned revisions.
Hybrid timing includes agent reasoning and differs from a standalone actor
baseline; a CPU fixture pass is not a benchmark score or a native rollout.

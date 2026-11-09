# IsaacLab Arena integration sources

This adapter calls the upstream simulator and controllers; their implementations,
assets and weights are not copied into this repository.

- Arena: https://github.com/isaac-sim/IsaacLab-Arena at
  `8fbe412ea1d4468bf5e4b285a092e0eca2a8f1fa` (Apache-2.0).
- IsaacLab submodule: `e3f7d9ff1f5d7f8280de3ece9000b7117f66bcf4`.
- G1 environment: `galileo_g1_locomanip_pick_and_place`, embodiment
  `g1_wbc_joint`, default Homie V2 whole-body controller.
- GR00T N1.6 runtime: https://github.com/NVIDIA/Isaac-GR00T at
  `9b37aa1ce69c73c6d165233fa88128283bba4508`, the official N1.6 branch.
  This revision supports Python 3.12. Arena's older embedded GR00T revision
  requires Python 3.10 and is not installed into the harness service.
- Matching weights: `nvidia/GN1x-Tuned-Arena-G1-Loco-Manipulation` at
  `dfe74af855007f26093f362cd2d7a2f404b64b93` (`gn1_6`). The published weights
  carry NVIDIA's noncommercial license; the recipe does not change it.

The joint-name order follows Arena's `gr00t_43dof_joint_space.yaml`. The model
observes 31 arm/hand/waist positions, head RGB and language. It predicts 50 rows
of 35 physical values: 31 joint channels, pelvis height, and planar velocity.
The default native WBC replaces waist and leg targets. The binding preserves
all raw predictions but executes 28 upper-body joint targets plus velocity and
height references. It never applies the WBC neural-input scale a second time.

Native action packing supplies 43 joint slots, three velocity values, one height
and three zero torso-RPY values. WBC runs at 50 Hz with 200 Hz physics. The native
30-second limit and native success predicate are preserved. One initial seeded
reset and `configure_episode_limit(1)` prevent starting a replacement episode.

Arena assets and Homie ONNX models remain managed by upstream's asset resolver.
A source pin alone does not pin mutable remote asset bytes; archive the resolved
asset cache and its hashes when reporting reproducible benchmark results.

This integration has CPU contract coverage. GPU simulator startup, Kit thread
ownership, rendering, WBC and checkpoint rollout acceptance require native tests
on a compatible host. Current Arena requires Isaac Sim 6.2/CUDA 13 and at least
NVIDIA driver 580.65.06; a successful metadata preflight is not GPU validation.

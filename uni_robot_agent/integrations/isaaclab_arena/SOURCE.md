# IsaacLab Arena integration sources

This adapter calls the upstream simulator and controllers; their implementations,
assets and weights are not copied into this repository.

- Arena: https://github.com/isaac-sim/IsaacLab-Arena at
  `aad4f25f44cc439c363c2b60f1fd170dfd10d2a5` (Apache-2.0), official
  `feature/arena_v0.2_on_lab_2.3` branch for Isaac Sim 5.1 / Python 3.11.
- IsaacLab submodule: `5528d986d8909825a29f3c97656108abf054a261`.
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
reset followed by suppressing the native automatic reset preserves terminal
images and state without starting a replacement episode. Physics uses CPU PhysX,
matching the pinned branch's evaluation settings. Its native success predicate
is `objects_in_proximity` (box/bin x/y/z thresholds 0.260/0.130/0.150 m), not
the redesigned predicates on current Arena main.

Arena assets and Homie ONNX models remain managed by upstream's asset resolver.
A source pin alone does not pin mutable remote asset bytes; archive the resolved
asset cache and its hashes when reporting reproducible benchmark results.

This integration has CPU contract coverage. GPU simulator startup, Kit thread
ownership, rendering, WBC and checkpoint rollout acceptance require native tests
on the target host. Preflight checks actual Arena/IsaacLab import locations and
records the installed NVIDIA driver; it is not GPU validation.

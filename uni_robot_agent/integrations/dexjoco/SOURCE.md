# DexJoCo native boundary

Native source: [brave-eai/dexjoco](https://github.com/brave-eai/dexjoco), revision
`8d23b0fab23b17a58c4b55f3942e17013aaf8267`, MIT license. Native code and assets
remain in their external checkout; this package does not vendor or modify them.
The isolated simulator requires Python 3.11, MuJoCo 3.4.0, Gymnasium 1.0.0 and
NumPy 1.26.4. Setup installs the pinned native package with its dependencies.

## Admitted environment

Only `pick_bucket` is admitted. It uses the official `TaskConfig`,
`SingleArmPolicyWrapper` and `DexjocoObsAdapter`, with the `rand_obj` regime,
seeded object/table placement, fixed cameras, no dynamics randomization and the
official 1,200-step budget. Other tasks require native admission checks even when
their device layout is compatible with the same binding.

The seed is supplied before construction. Native `reset(seed=...)` does not
reseed the global NumPy/Python RNGs. The episode records an opaque SHA256
fingerprint of the reset's native state and model geometry for reproducibility;
scene coordinates are never published as agent observations.

## Control and observations

The measured TCP is the native `attachment_site`, using world metres and wxyz
quaternions. Both native flange sensors reference that site. Sixteen Allegro
joint positions are exposed in radians, ordered `ffj0..3`, `mfj0..3`,
`rfj0..3`, `thj0..3`. Only these 23 proprioceptive values and the official
`front`/`wrist` RGB images enter snapshots, actor observations and planner state.
The flattened native object's pose and table-height tail is discarded. Camera
intrinsics and measured optical-to-world extrinsics use the native MuJoCo
cameras; RGB has calibration but no simulator depth.

One `coordinated_step` provides a complete arm pose and hand target set. After
validating all devices, it executes exactly one official environment step:
the original operational-space arm torque controller and hand position
actuators run together for ten 0.002-second physics substeps. There is no
replacement IK controller. The simulated control period is 0.02 seconds;
the native implementation's 30 Hz wall-clock throttle is retained.

Native MuJoCo hand actuators clamp controls to their published ranges. The
binding makes that conversion explicit and records requested and applied
targets; the environment boundary accepts only complete in-range targets.
The service owns request identities, stale-state checks, receipts, archives and
unknown-execution handling. Native success remains in the finalized evaluation
outcome; it is not a planner feedback field. Native termination still stops
execution.

## Validation

`scripts/validation/dexjoco_native_acceptance.py` checks seeded resets, measured
state filtering, RGB publication, one synchronized control step, invalid/stale
command rejection and direct-native action parity. These checks establish
integration correctness for the recorded runtime and seed, not a benchmark
success rate or equivalence to the upstream multi-episode evaluation schedule.

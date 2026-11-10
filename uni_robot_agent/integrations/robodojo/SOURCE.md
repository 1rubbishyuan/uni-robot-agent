# RoboDojo provenance

uni-robot-agent implements a standalone RoboDojo episode adapter with Direct control,
optional Pi proposals, perception, and observation tools.

## Sources and licenses

Robot-only forward kinematics in `environment/kinematics.py` and decision
validation in `action_binding.py` adapt GPT-as-Policy commit
`8f3d362b077d8efb77e2a7274d5b2c20e2243846`; retain `UPSTREAM_LICENSE`.
The episode bootstrap, camera compatibility, and Direct controller incorporate
prior uni-robot-agent implementations. Direct control uses native cuRobo v2 IK
through `environment/ik/curobo.py`, with bounded candidate recovery; robot-only
FK and local DLS are implemented separately in `environment/kinematics.py`.
The Pi actor uses the external XPolicyLab implementation and pinned checkpoint;
it does not run the upstream autonomous policy loop or benchmark scheduler.

## Pinned runtime

- RoboDojo: `ee67a1468510da7624a089164402359f2afc72c8`.
- XPolicyLab: `432f82b1758c5b1202e42a3dfe014546dbc50871`.
- IsaacLab: `afca7b09d60d8beb9c1cb28b43066499940b969b`.
- CuRobo: `d17b54ce32cba095c0b000c4c58777075d11de0e`.
- XPolicyLab Python-source SHA256: `807d83318fe6c48da1cb4da6627e2313cf072561c756fe8a754d4285d33ac229`
  across 3536 Python files, computed by `source_hash`.

Clone the official repository independently and initialize the recorded submodules:

```bash
git clone https://github.com/RoboDojo-Benchmark/RoboDojo.git /path/to/RoboDojo
git -C /path/to/RoboDojo checkout ee67a1468510da7624a089164402359f2afc72c8
git -C /path/to/RoboDojo submodule update --init --recursive
```

Prepare the dependencies and assets required by this pinned upstream revision,
then let `uni-robot-agent setup` install the matching runtime package in its
isolated simulator environment. Do not install the planner package there.
Setup installs all six IsaacLab base extensions, matching the upstream base
installation without optional RL training frameworks. Installing only the core
and assets packages omits dependencies needed when the app loads its extensions.
The pinned upstream `scripts/install.sh -i` runs `git submodule update --remote`;
that changes the recorded dependency revisions. Keep the submodules at the
commits above when installing their packages, and verify the checkout again
after setup. Do not use a floating submodule branch for an official run.

Install IsaacLab and CuRobo from this official checkout in the simulator
interpreter. Check their resolved module paths as well as package versions: an
editable install pointing at the optimized runtime does not validate the official
dependency chain. The harness source check alone cannot identify installed
Python dependencies.
Pass `--native-root /path/to/RoboDojo` to the environment server. It verifies the
checkout revision, tracked changes and submodule revisions before importing Isaac.
The official checkout is never patched by this integration. Runtime sources,
assets and checkpoints remain external and are not packaged.

The actor separately receives `--xpolicylab-root` (or the TOML field
`xpolicylab_root`). It must identify the pinned XPolicyLab source tree; do not
infer this path from the simulator checkout or assume similarly named runtime
copies have identical sources. The actor verifies its source fingerprint before
loading the policy.

## Adaptation boundaries

`environment/adapter.py` preserves native reset, reward registration, support-arm
initialization, action stepping, and final outcome checks. The environment RPC
preserves request order and rejects replay: a lost action acknowledgement leaves
execution uncertain. `native_compat.py` and `geometry.py` isolate native and camera
compatibility. Direct motion and finger-center feedback do not infer contact
or success from controller convergence.

The main agent selects `actor_act` and its local instruction. A separate reviewer
sees that intent, the unchanged global instruction, current and comparison images,
and public feedback; it receives no native reward. The controller adapts upstream
student/edit/eef decisions, H50 previews, 25-action default chunks with a 40-action
limit, and correction bounds of 5 cm / 0.35 rad. Each fresh chunk
re-observes, re-infers, and re-judges. Stop decisions execute no actions; tracking
interruptions and partial execution end the call. Disabling the reviewer leaves
chunk budgets unchanged; set `max_chunks=1` explicitly for one chunk. Proposal
artifacts retain decisions, targets, and actual execution.
These adaptations do not claim upstream policy or success-rate equivalence.

## Shared actor

`services/actor/` uses one shared `BatchWorker` and typed HTTP protocol
`uni_robot_agent.actor.v3`. The RoboDojo OpenPI recipe emits `NumericChunk`
predictions with unchanged physical action semantics. Each explicit session owns
a seed-zero JAX noise sequence and inference index, with one request in flight. The pinned Python
policy lives in `services/actor/backends/openpi/openpi_jax.py`; it owns no network
server or separate inference queue. Batching preserves assigned noise
(50 x 32 per sample) and returns 50 x 14 actions; floating-point outputs need not
be bitwise identical to isolated inference. Images use the pinned PIL 224 x 224
resize. Failures are not retried, and incompatible actor servers are rejected.

`/healthz` performs no inference; `/info` describes the loaded model and profile.
Request receipts under `/sessions/{id}/requests/{id}` resolve response loss
without resubmitting a prediction. Runtime failures poison the session; expiry
or restart cannot silently recreate the old random sequence.
JAX compiles encountered batch shapes; GPU throughput and memory need separate
measurement. Native transform validation requires the pinned XPolicyLab source and its runtime
dependencies. Resolve pin mismatches by preparing the pinned checkout;
do not change expected digests to accept an unverified copy.

## IsaacLab build compatibility (0.1.1)

The pinned fork declares `starlette==0.49.1`, which cannot resolve alongside
Isaac Sim 5.1's `fastapi==0.115.7` (Starlette `<0.46`). Setup verifies the exact
upstream `setup.py` SHA256, copies the source tree into its own build directory, and
changes only that dependency to `0.45.3`. The locally built IsaacLab version has
a `+ura.1` suffix, and `runtime.json` records the compatibility profile. Native
Python modules, assets, RoboDojo task logic and the original Git checkout remain
unmodified. Editable installs preserve IsaacLab’s relative `apps/` and extension
`config/` paths; setup verifies those resources before publishing the runtime
manifest. The build installs declared tools explicitly and retains `pip check`.

This is an explicit build metadata patch, not an upstream release. It preserves
the pinned fork's headless camera changes; substituting another IsaacLab release
would change more native code. Clean GPU validation is required for this profile.

## Robot asset initialization

The pinned asset snapshot contains `*_tmp.yml` templates, not initialized CuRobo
configs. Setup invokes upstream `utils/update_embodiment_config_path.py` in its
private `build/Assets` overlay. Robot directories are writable there; downloaded
templates, geometry and other assets remain links to the original snapshot.
Generated YAML therefore contains paths for this runtime without modifying a
shared `--assets-dir`. Preflight checks require initialized configs for both
supported robot models before launching the simulator.

The pinned snapshot's `X5A.urdf` has SHA256
`7aaa308a1a5b6a213d914db86ba09ddd6252f25cb498ac9a148bfa6c7166a423`.
Its 287 CRLF line endings account for the entire difference from the audited LF
file (`a31a0d3a957196ea42d216036db2fffdf7678f4f36f65f90a0b69c58daa8d7d0`):
normalizing CRLF to LF produces identical bytes. Both exact digests are accepted;
finger mesh digests, joint validation and pad calibration remain unchanged. Setup
checks these asset digests before publishing a runtime manifest.

Hybrid orchestration is shared in `tools/hybrid/`; the integration binding only
converts the declared actor and native contracts. Native control and upstream
provenance remain integration-owned. See [Hybrid design](../../../docs/hybrid.md).

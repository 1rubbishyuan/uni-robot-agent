# RoboLab integration provenance

- Upstream: <https://github.com/NVlabs/RoboLab>
- Supported native revision: `ad45d4f974725d020f82c2b0d77d78533aeba2b3`, package `robolab==0.3.1`.
- Upstream license: Apache-2.0. Simulator packages and assets retain their own terms.

DROID USD kinematics, nonlinear pad-gap conversion, native drive readback and video
recording are implemented by this adapter. Native code/assets are
installed independently. The adapter reads only the robot subtree for public
calibration; task object ground truth remains private native evidence.

The current implementation uses the shared prepared-episode service, scene and
gripper providers. Robot-specific geometry and control stay within the adapter;
HTTP transport, episode state and image storage use shared services. See the
[integration guide](../../../docs/robolab.md) for supported capabilities and limits.

Use move, hold and finish to complete the instruction. move can set the gripper
opening during motion; hold advances control steps while retaining arm poses
and gripper commands. Physical calls share one exclusive episode queue; order
calls by intended submission sequence. Inspect completed feedback before
dependent actions.

Initial input and completed actions include RGB images and measured robot state.
Camera matrices belong to the named image and image_state_version, not a future
view. intrinsic_K maps optical coordinates to image pixels; T_world_from_optical
maps optical coordinates to world metres. Disabled or unsupported calibration
must not be treated as measured geometry. Depth is not provided to the agent.

Tool completion and pose arrival do not establish object attachment or task
success. Verify visible effects and all task requirements. artifact_error does
not undo acknowledged motion; do not replay a physical action to fetch an image.
An execution_uncertain result does not mean zero execution. Do not repeat an
uncertain physical request. Submit finish with the appropriate status and a
summary of the evidence; the environment owns the official outcome.

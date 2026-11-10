"""Physical G1 joint and controller conventions, shared by its binding and adapter."""
JOINT_GROUPS = {
    **{side + "_arm": [side + "_" + joint + "_joint" for joint in (
        "shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_roll", "wrist_pitch", "wrist_yaw")]
       for side in ("left", "right")},
    **{side + "_hand": [side + "_hand_" + joint + "_joint" for joint in (
        "index_0", "index_1", "middle_0", "middle_1", "thumb_0", "thumb_1", "thumb_2")]
       for side in ("left", "right")},
    "waist": ["waist_yaw_joint", "waist_roll_joint", "waist_pitch_joint"],
}
OBSERVATION_GROUPS = ("left_arm", "right_arm", "left_hand", "right_hand", "waist")
CONTROL_GROUPS = OBSERVATION_GROUPS[:-1]
CONTROL_DT = .02
IMAGE_CONVENTION = "RGB HWC; native GR00T processor owns model preprocessing"

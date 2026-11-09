"""Publish integration-owned robot references without task state or simulator access."""
from pathlib import Path
import xml.etree.ElementTree as ET


def add_robot_resources(description, *, model=None, manual=None):
    """Declare static resources; the environment service owns artifact publication."""
    metadata = description.metadata
    payloads = metadata.setdefault("artifact_payloads", {})
    resources = []
    if model is not None:
        payloads["robot_model"] = model
        resources.append({"name": "robot_model", "description": "Native robot model reference; see format and limitations in the file."})
    if "robot_calibration" in payloads:
        resources.append({"name": "robot_calibration", "description": "Robot geometry and gripper calibration; not camera intrinsics or extrinsics."})
    if manual is None:
        lines = ["# Robot reference", "", metadata.get("control_constraints", "")]
        for arm in description.arms:
            lines.extend(["", f"## {arm.name}", f"Control frame: {arm.control_frame}.", arm.grasp_site_description])
        lines.extend(["", "Tool schemas define admitted commands and units. Static robot geometry does not establish",
                      "object attachment or contact. Camera calibration is dynamic: use the matrices attached",
                      "to each observation, never substitute a robot model transform for a camera transform."])
        manual = "\n".join(lines) + "\n"
    metadata.setdefault("artifact_texts", {})["robot_manual"] = {"text": manual, "media_type": "text/markdown"}
    resources.append({"name": "robot_manual", "description": "Robot frames and control semantics."})
    metadata["robot_resources"] = resources


def urdf_kinematics(path):
    """Read only the supplied robot URDF, without resolving external mesh paths."""
    import hashlib
    raw = Path(path).read_bytes()
    robot = ET.fromstring(raw)
    if robot.tag != "robot":
        raise ValueError("Expected a robot URDF")
    joints = []
    for joint in robot.findall("joint"):
        item = dict(joint.attrib)
        for key in ("parent", "child", "origin", "axis", "limit", "mimic", "dynamics"):
            element = joint.find(key)
            if element is not None:
                item[key] = dict(element.attrib)
        joints.append(item)
    return {"format": "urdf_kinematics_v1", "robot": robot.get("name"),
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "units": "URDF: metres, radians; origins use xyz and roll-pitch-yaw.",
            "links": [link.get("name") for link in robot.findall("link")], "joints": joints,
            "limitations": "Kinematic tree only; visual/collision meshes and dynamics are not included. "
                            "Control-point offsets and runtime base placement are supplied by the integration."}


def mujoco_robot_model(model, control_bodies):
    """Extract only robot subtrees identified by integration-owned control bodies."""
    roots = set()
    for body in control_bodies:
        body = int(body)
        if body <= 0:
            raise ValueError("A robot control body must not be the world body")
        while model.body_parentid[body] != 0:
            body = int(model.body_parentid[body])
        roots.add(body)
    selected = set(roots)
    for body in range(1, model.nbody):
        if int(model.body_parentid[body]) in selected:
            selected.add(body)
    bodies, joints, sites, geometries = [], [], [], []
    for body in sorted(selected):
        bodies.append({"id": body, "name": model.body(body).name,
            "parent_id": int(model.body_parentid[body]), "position": model.body_pos[body].tolist(),
            "quaternion_wxyz": model.body_quat[body].tolist()})
    for joint in range(model.njnt):
        if int(model.jnt_bodyid[joint]) in selected:
            joints.append({"name": model.joint(joint).name, "body_id": int(model.jnt_bodyid[joint]),
                "type": ("free", "ball", "slide", "hinge")[int(model.jnt_type[joint])],
                "position": model.jnt_pos[joint].tolist(), "axis": model.jnt_axis[joint].tolist(),
                "range": model.jnt_range[joint].tolist() if model.jnt_limited[joint] else None,
                "reference_qpos": model.qpos0[int(model.jnt_qposadr[joint]):int(model.jnt_qposadr[joint]) +
                    (7 if model.jnt_type[joint] == 0 else 4 if model.jnt_type[joint] == 1 else 1)].tolist()})
    for site in range(model.nsite):
        if int(model.site_bodyid[site]) in selected:
            sites.append({"name": model.site(site).name, "body_id": int(model.site_bodyid[site]),
                "position": model.site_pos[site].tolist(), "quaternion_wxyz": model.site_quat[site].tolist()})
    for geom in range(model.ngeom):
        if int(model.geom_bodyid[geom]) in selected:
            geometries.append({"name": model.geom(geom).name, "body_id": int(model.geom_bodyid[geom]),
                "type": ("plane", "heightfield", "sphere", "capsule", "ellipsoid", "cylinder", "box", "mesh", "sdf")[int(model.geom_type[geom])], "size": model.geom_size[geom].tolist(),
                "position": model.geom_pos[geom].tolist(), "quaternion_wxyz": model.geom_quat[geom].tolist(),
                "collision_enabled": bool(model.geom_contype[geom] or model.geom_conaffinity[geom])})
    return {"format": "mujoco_robot_v1", "units": "metres, radians; transforms relative to parent body; quaternions wxyz",
            "roots": sorted(roots), "bodies": bodies, "joints": joints, "sites": sites, "geometries": geometries,
            "limitations": "Static compiled robot parameters, not live poses. Mesh vertices are omitted; "
                            "mesh geom size is a bounding box, not a collision surface. No scene objects are included."}

"""Public robot-only geometry derived from the loaded URDF and collision assets."""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation
import trimesh

from .kinematics import GRASP_SITE, GRASP_SITE_URDF, T_NATIVE_FROM_GRASP


def origin(element):
    result = np.eye(4)
    if element is not None:
        result[:3, 3] = np.fromstring(element.get("xyz", "0 0 0"), sep=" ")
        result[:3, :3] = Rotation.from_euler("xyz", np.fromstring(element.get("rpy", "0 0 0"), sep=" ")).as_matrix()
    return result


def load_robot_geometry(urdf):
    """Export convex collision proxies and exact distal pad triangles separately."""
    path = Path(urdf).resolve()
    root = ET.parse(path).getroot()
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest(),
              GRASP_SITE_URDF.name: hashlib.sha256(GRASP_SITE_URDF.read_bytes()).hexdigest()}
    geometries, pads, bounds = [], {}, {}
    for link in root.findall("link"):
        name = link.get("name")
        for collider in link.findall("collision"):
            asset = collider.find("geometry/mesh")
            if asset is None:
                raise ValueError(f"Unsupported X5 collision geometry: {name}")
            filename = asset.get("filename")
            mesh_path = (path.parent / filename).resolve()
            hashes[filename] = hashlib.sha256(mesh_path.read_bytes()).hexdigest()
            mesh = trimesh.load_mesh(mesh_path, process=True)
            mesh.apply_scale(np.fromstring(asset.get("scale", "1 1 1"), sep=" "))
            mesh.apply_transform(origin(collider.find("origin")))
            if name in {"link7", "link8"}:
                triangles = np.asarray(mesh.triangles)
                # Audited X5 distal planar pads, excluding the recessed pocket.
                selected = ((triangles[:, :, 0].min(axis=1) > .03)
                            & (np.abs(mesh.face_normals[:, 1]) > .99)
                            & (np.abs(np.abs(triangles[:, 0, 1]) - .024494417) < 1e-7))
                pad = triangles[selected]
                if not len(pad):
                    raise ValueError(f"X5 distal pad not found: {name}")
                areas = mesh.area_faces[selected]
                pads[name] = {"triangles": pad.tolist(), "area_m2": float(areas.sum()),
                              "centroid": np.average(pad.mean(axis=1), axis=0, weights=areas).tolist()}
            bounds[name] = mesh.bounds.tolist()
            hull = mesh.convex_hull
            geometries.append({"link": name, "source_asset": filename,
                               "vertices": hull.vertices.tolist(), "triangles": hull.faces.tolist()})
    joints = []
    for joint in root.findall("joint"):
        axis, limits = joint.find("axis"), joint.find("limit")
        joints.append({"name": joint.get("name"), "type": joint.get("type"),
                       "parent": joint.find("parent").get("link"), "child": joint.find("child").get("link"),
                       "T_parent_from_joint": origin(joint.find("origin")).tolist(),
                       "axis": np.fromstring(axis.get("xyz"), sep=" ").tolist() if axis is not None else None,
                       "limits": {key: float(value) for key, value in limits.attrib.items()} if limits is not None else {}})
    return {"schema_version": 1, "robot": "RoboDojo dual X5", "units": "meters/radians",
            "asset_sha256": hashes, "joints": joints, "collision_geometry": geometries,
            "collision_approximation": "Convex hull per URDF collision mesh; not a collision checking service",
            "links_without_collision_geometry": [link.get("name") for link in root.findall("link") if link.find("collision") is None],
            "grasp_site": {"name": GRASP_SITE, "parent": "link6", "T_parent_from_site": T_NATIVE_FROM_GRASP.tolist(),
                           "approach_axis": "+X", "closing_axis": "+/-Y"},
            "pads": pads, "link_local_bounds_m": bounds}

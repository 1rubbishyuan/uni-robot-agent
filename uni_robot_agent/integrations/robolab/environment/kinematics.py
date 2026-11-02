"""DROID USD kinematics and robot-only collision geometry.

Only the robot subtree is inspected; task objects stay outside public calibration.
"""
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
from .gripper import JawOpening


class MotionRejected(ValueError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def pose(position, quaternion):
    result = np.eye(4)
    result[:3, :3] = Rotation.from_quat(np.roll(quaternion, -1)).as_matrix()
    result[:3, 3] = position
    return result


def pose_fields(matrix):
    return {"position": matrix[:3, 3].tolist(),
            "quaternion": np.roll(Rotation.from_matrix(matrix[:3, :3]).as_quat(), 1).tolist()}


def feedback(target, actual):
    p = float(np.linalg.norm(target[:3, 3] - actual[:3, 3]))
    a = float(Rotation.from_matrix(target[:3, :3] @ actual[:3, :3].T).magnitude())
    return {"position_error_m": p, "orientation_error_rad": a,
            "target_reached": p <= .002 and a <= np.deg2rad(1)}


class DroidModel:
    """USD joint-frame FK, bounded IK and conservative convex self-clearance.

    Only /panda is read. The USD also contains task objects outside that subtree.
    Convex-decomposition colliders are conservatively enclosed by one hull.
    """

    def __init__(self, usd_path):
        import fcl
        import trimesh
        from pxr import Usd, UsdGeom, UsdPhysics

        self.fcl = fcl
        stage = Usd.Stage.Open(str(usd_path))
        root = stage.GetPrimAtPath("/panda")
        if not root:
            raise ValueError("Expected DROID /panda robot subtree")
        prims = list(Usd.PrimRange(root))
        bodies = {str(p.GetPath()): p for p in prims if p.HasAPI(UsdPhysics.RigidBodyAPI)}
        self.joints = []
        gripper_limits = None
        self.lower, self.upper = np.zeros(7), np.zeros(7)
        arm_names = [f"panda_joint{i}" for i in range(1, 8)]
        def local(j, suffix):
            p = j.GetAttribute("physics:localPos" + suffix).Get()
            r = j.GetAttribute("physics:localRot" + suffix).Get()
            return pose(p, [r.GetReal(), *r.GetImaginary()])
        for p in prims:
            if not p.IsA(UsdPhysics.Joint):
                continue
            a = p.GetRelationship("physics:body0").GetTargets()
            b = p.GetRelationship("physics:body1").GetTargets()
            if not a or not b:
                continue
            name = p.GetName()
            joint = {"name": name, "parent": str(a[0]), "child": str(b[0]),
                     "parent_frame": local(p, "0"), "child_frame_inverse": np.linalg.inv(local(p, "1")),
                     "axis": p.GetAttribute("physics:axis").Get() if p.IsA(UsdPhysics.RevoluteJoint) else None,
                     "mimic": None}
            for attr in p.GetAttributes():
                if "physxMimicJoint:" in attr.GetName() and attr.GetName().endswith(":gearing"):
                    joint["mimic"] = -float(attr.Get())
            if name in arm_names:
                i = arm_names.index(name)
                self.lower[i] = np.deg2rad(p.GetAttribute("physics:lowerLimit").Get())
                self.upper[i] = np.deg2rad(p.GetAttribute("physics:upperLimit").Get())
            elif name == "finger_joint":
                gripper_limits = tuple(np.deg2rad(p.GetAttribute(f"physics:{bound}Limit").Get())
                                       for bound in ("lower", "upper"))
            self.joints.append(joint)
        self.arm_names = arm_names
        self.root_body = next(path for path in bodies if path.endswith("/panda_link0"))
        self.ee_body = next(path for path in bodies if path.endswith("/Robotiq_2F_85/base_link"))
        # Rigid bodies connected by fixed/adjacent joints have designed contact.
        self.excluded = {frozenset((j["parent"], j["child"])) for j in self.joints}
        # All internal Robotiq bodies are mechanically coupled, often touching.
        # Check the gripper against the arm; exclude internal mechanism pairs.
        gripper = [b for b in bodies if "/Robotiq_2F_85/" in b]
        self.excluded.update(frozenset((a, b)) for a in gripper for b in gripper if a != b)
        # Fixed-link clusters inherit adjacency exclusions across the cluster.
        changed = True
        while changed:
            changed = False
            for j in self.joints:
                if j["axis"] is not None:
                    continue
                for a, b in list(self.excluded):
                    for x, y in ((j["parent"], j["child"]), (j["child"], j["parent"])):
                        if x in (a, b):
                            other = b if a == x else a
                            if other != y and frozenset((other, y)) not in self.excluded:
                                self.excluded.add(frozenset((other, y)))
                                changed = True
        xforms = UsdGeom.XformCache()
        self.geometries = []
        public_geometry = []
        for p in prims:
            if not p.HasAPI(UsdPhysics.CollisionAPI):
                continue
            owner = p
            while str(owner.GetPath()) not in bodies and owner != root:
                owner = owner.GetParent()
            body = str(owner.GetPath())
            if body not in bodies:
                continue
            points = []
            descendants = list(Usd.PrimRange(p, Usd.TraverseInstanceProxies())) if not p.IsA(UsdGeom.Mesh) else [p]
            for mesh in descendants:
                rel = np.asarray(xforms.ComputeRelativeTransform(mesh, owner)[0]).T
                if mesh.IsA(UsdGeom.Mesh):
                    vertices = np.asarray(UsdGeom.Mesh(mesh).GetPointsAttr().Get(), dtype=float)
                elif mesh.IsA(UsdGeom.Cylinder):
                    cylinder = UsdGeom.Cylinder(mesh)
                    vertices = trimesh.creation.cylinder(radius=cylinder.GetRadiusAttr().Get(),
                                                         height=cylinder.GetHeightAttr().Get(), sections=32).vertices
                    axis = cylinder.GetAxisAttr().Get()
                    if axis != "Z":
                        vertices = vertices @ Rotation.from_rotvec([0, np.pi/2, 0] if axis == "X" else [np.pi/2, 0, 0]).as_matrix().T
                else:
                    continue
                points.extend((vertices @ rel[:3, :3].T + rel[:3, 3]).tolist())
            if not points:
                raise ValueError(f"Unsupported robot collision shape: {p.GetPath()}")
            hull = trimesh.convex.convex_hull(np.asarray(points))
            vertices = np.asarray(hull.vertices, dtype=np.float64)
            faces = np.asarray(hull.faces, dtype=np.int32)
            model = fcl.Convex(vertices, len(faces), np.c_[np.full(len(faces), 3), faces].ravel())
            self.geometries.append((body, fcl.CollisionObject(model)))
            public_geometry.append({"body": body, "source_prim": str(p.GetPath()),
                                    "vertices": vertices.tolist(), "triangles": faces.tolist()})
        self.pairs = [(a, b) for i, a in enumerate(self.geometries) for b in self.geometries[i+1:]
                      if a[0] != b[0] and frozenset((a[0], b[0])) not in self.excluded]
        self.public = {"robot": "Franka Panda + Robotiq 2F-85 (DROID)", "units": "meters/radians",
                       "root_body": self.root_body, "flange_body": self.ee_body,
                       "joint_names": arm_names, "lower_limits": self.lower.tolist(), "upper_limits": self.upper.tolist(),
                       "joints": [{k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in j.items()} for j in self.joints],
                       "collision_geometry": public_geometry,
                       "collision_approximation": "Conservative convex hull per USD collider, including convexDecomposition",
                       "excluded_self_pairs": [sorted(pair) for pair in sorted(self.excluded, key=lambda x: sorted(x))],
                       "eef_offset_position": [0, 0, 0], "eef_offset_quaternion_wxyz": [.5, -.5, .5, -.5]}
        # Safe bound: Panda reach plus gripper length is below 2m. Every
        # collision vertex travels at most radius*sum(abs(joint angle changes)).
        self.sweep_radius = 2.0
        if gripper_limits is None:
            raise ValueError("DROID finger_joint limits are missing")
        self.gripper = JawOpening(self, gripper_limits)
        self.public["gripper"] = self.gripper.public

    def transforms(self, q, gripper=0):
        values = dict(zip(self.arm_names, q))
        transforms = {self.root_body: np.eye(4)}
        pending = list(self.joints)
        while pending:
            progressed = False
            for j in pending[:]:
                if j["parent"] not in transforms:
                    continue
                motion = np.eye(4)
                if j["axis"] is not None:
                    value = values.get(j["name"], float(gripper) * np.pi/4 * (j["mimic"] if j["mimic"] is not None else 1))
                    motion[:3, :3] = Rotation.from_rotvec(np.eye(3)["XYZ".index(j["axis"])] * value).as_matrix()
                transforms[j["child"]] = transforms[j["parent"]] @ j["parent_frame"] @ motion @ j["child_frame_inverse"]
                pending.remove(j)
                progressed = True
            if not progressed:
                raise RuntimeError("DROID joint graph is disconnected")
        return transforms

    def fk(self, q):
        return self.transforms(q)[self.ee_body]

    def ik(self, target, seed):
        target_rot = Rotation.from_matrix(target[:3, :3])
        def residual(q):
            actual = self.fk(q)
            return np.r_[actual[:3, 3] - target[:3, 3],
                         .2 * (Rotation.from_matrix(actual[:3, :3]) * target_rot.inv()).as_rotvec()]
        result = least_squares(residual, np.clip(seed, self.lower + 1e-8, self.upper - 1e-8),
                               bounds=(self.lower, self.upper), method="dogbox",
                               xtol=1e-9, ftol=1e-9, gtol=1e-10, max_nfev=150)
        error = feedback(target, self.fk(result.x))
        if error["position_error_m"] > 1e-5 or error["orientation_error_rad"] > np.deg2rad(.01):
            raise MotionRejected("DROID IK preflight failed", **error)
        return result.x

    def sweep_bound(self, qa, qb, ga, gb):
        return self.sweep_radius * (float(np.abs(qb-qa).sum()) + 6 * abs(gb-ga) * np.pi/4)

    def clearance(self, q, gripper):
        transforms = self.transforms(q, gripper)
        for body, obj in self.geometries:
            x = transforms[body]
            obj.setTransform(self.fcl.Transform(x[:3, :3], x[:3, 3]))
        minimum = float("inf")
        for (_, a), (_, b) in self.pairs:
            d = self.fcl.distance(a, b, self.fcl.DistanceRequest(enable_signed_distance=True), self.fcl.DistanceResult())
            minimum = min(minimum, d)
        return minimum

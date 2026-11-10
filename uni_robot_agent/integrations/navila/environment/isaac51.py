"""Explicit, reproducible Isaac 5.1 migration for the pinned NaVILA benchmark."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

REVISION = "e9d2db12ce5788c0f987d734c0094100b6bc0d3a"
ISAACLAB_REVISION = "afca7b09d60d8beb9c1cb28b43066499940b969b"
ISAACSIM_VERSION = "5.1.0.0"
GO2_SHA256 = "ba171c972b987d8c8fb7157ccad2ba9c0c1fed105755d2d8af46bef96cc11c6d"
MANIFEST = "navila-isaac51.json"
REPLACEMENTS = (
    ("omni.isaac.lab_tasks.utils.wrappers.rsl_rl", "isaaclab_rl.rsl_rl"),
    ("omni.isaac.lab_tasks", "isaaclab_tasks"),
    ("omni.isaac.lab_assets", "isaaclab_assets"),
    ("omni.isaac.lab", "isaaclab"),
    ("omni.isaac.core.utils", "isaacsim.core.utils"),
    ("omni.isaac.core.simulation_context", "isaacsim.core.api.simulation_context"),
    ("omni.isaac.core.objects", "isaacsim.core.api.objects"),
    ("from isaaclab.sensors.camera.utils import convert_orientation_convention",
     "from isaaclab.utils.math import convert_camera_frame_orientation_convention"),
    ("convert_orientation_convention(sensor.", "convert_camera_frame_orientation_convention(sensor."),
    ("from isaaclab.envs.base_env import BaseEnv", "from isaaclab.envs import ManagerBasedEnv as BaseEnv"),
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean_source(root, revision=REVISION):
    root = Path(root).resolve(strict=True)
    actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], text=True)
    if actual != revision or dirty:
        raise ValueError(f"Expected a clean native checkout at {revision}")
    return root


def migrated_source(path, text):
    for before, after in REPLACEMENTS:
        text = text.replace(before, after)
    if path.endswith("domains/matterport_importer.py"):
        text = text.replace("self.meshes = dict()\n        self.warp_meshes = dict()",
                            'self.terrain_prim_paths = [self.cfg.prim_path + "/Matterport"]\n        self._terrain_flat_patches = dict()')
        start = text.index("    def load_matterport_sync(self) -> None:")
        text = text[:start] + ("    def load_matterport_sync(self) -> None:\n"
                              "        from isaac51 import load_matterport\n"
                              "        load_matterport(self)\n")
    return text


def prepare(source, output, go2_usd, assets_dir=None):
    source = clean_source(source)
    output, go2_usd = Path(output).resolve(), Path(go2_usd).resolve(strict=True)
    if digest(go2_usd) != GO2_SHA256:
        raise ValueError("Isaac 5.1 migration requires the original Isaac 4.1 Go2 asset")
    dependency = go2_usd.parent / "Props/instanceable_meshes.usd"
    dependency.resolve(strict=True)
    assets = Path("isaaclab_exts/omni.isaac.vlnce/assets")
    assets_dir = Path(assets_dir or source / assets).resolve(strict=True)
    dataset = assets_dir / "vln_ce_isaac_v1.json.gz"
    if not dataset.is_file() or not (assets_dir / "matterport_usd").is_dir():
        raise ValueError("Prepare the NaVILA episode dataset and Matterport scenes before migration")
    output.mkdir(parents=True, exist_ok=False)
    tracked = set(subprocess.check_output(["git", "-C", str(source), "ls-files"], text=True).splitlines())
    files = []
    for directory in ("isaaclab_exts", "scripts/rsl_rl"):
        for original in sorted((source / directory).rglob("*")):
            relative = original.relative_to(source)
            if "__pycache__" in relative.parts or "assets" in relative.parts or original.is_dir():
                continue
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if original.suffix == ".py":
                if str(relative) not in tracked:
                    raise ValueError(f"Untracked native Python source: {relative}")
                target.write_text(migrated_source(str(relative), original.read_text()))
                files.append({"path": str(relative), "original_sha256": digest(original),
                              "prepared_sha256": digest(target)})
            else:
                target.symlink_to(original)
    (output / assets).symlink_to(assets_dir, target_is_directory=True)
    (output / "logs").symlink_to(source / "logs", target_is_directory=True)
    (output / "LICENSE").symlink_to(source / "LICENSE")
    manifest = {"profile": "isaac51", "source": str(source), "revision": REVISION,
                "isaaclab_revision": ISAACLAB_REVISION, "isaacsim": ISAACSIM_VERSION,
                "migration_sha256": digest(__file__), "assets_dir": str(assets_dir),
                "dataset_sha256": digest(dataset), "go2_usd": str(go2_usd),
                "go2_sha256": GO2_SHA256, "go2_dependency_sha256": digest(dependency), "files": files}
    (output / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    return output


def load_prepared(root):
    root = Path(root).resolve(strict=True)
    manifest = json.loads((root / MANIFEST).read_text())
    source = clean_source(manifest["source"])
    if (manifest["profile"] != "isaac51" or manifest["revision"] != REVISION
            or manifest["isaaclab_revision"] != ISAACLAB_REVISION
            or manifest["isaacsim"] != ISAACSIM_VERSION
            or manifest["migration_sha256"] != digest(__file__)):
        raise ValueError("Prepared NaVILA runtime does not match this Isaac 5.1 migration")
    for item in manifest["files"]:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Prepared native source path escapes its root")
        if (digest(source / relative) != item["original_sha256"]
                or digest(root / relative) != item["prepared_sha256"]
                or (root / relative).read_text() != migrated_source(str(relative), (source / relative).read_text())):
            raise ValueError(f"Prepared native source changed: {relative}")
    expected = {item["path"] for item in manifest["files"]}
    actual = {str(p.relative_to(root)) for p in root.rglob("*.py") if "assets" not in p.relative_to(root).parts}
    if actual != expected:
        raise ValueError("Prepared native runtime contains additional or missing Python modules")
    assets = root / "isaaclab_exts/omni.isaac.vlnce/assets"
    if (assets.resolve() != Path(manifest["assets_dir"])
            or digest(assets / "vln_ce_isaac_v1.json.gz") != manifest["dataset_sha256"]
            or not (assets / "matterport_usd").is_dir()):
        raise ValueError("Prepared native dataset or scene directory changed")
    usd = Path(manifest["go2_usd"])
    if (digest(usd) != GO2_SHA256
            or digest(usd.parent / "Props/instanceable_meshes.usd") != manifest["go2_dependency_sha256"]):
        raise ValueError("Prepared original Go2 assets changed")
    return root, manifest


def load_matterport(importer):
    # Matterport loader behavior adapted from NaVILA-Bench's BSD-3-Clause importer,
    # Copyright (c) 2024 ETH Zurich (Robotic Systems Lab), Author: Pascal Roth.
    from pxr import Usd, UsdGeom, UsdPhysics
    import isaaclab.sim as sim_utils
    from isaacsim.core.utils.stage import get_current_stage

    asset = Path(importer.cfg.obj_filepath).with_suffix(".usd")
    if not asset.is_file():
        raise FileNotFoundError(asset)
    stage = get_current_stage()
    # Legacy layer metadata says centimeters/Y-up, but its vertices and episode
    # coordinates already use meters/Z-up. A raw reference preserves those values.
    prim = stage.DefinePrim(importer.cfg.prim_path + "/Matterport", "Xform")
    if not prim.GetReferences().AddReference(str(asset)):
        raise RuntimeError("Could not reference the original Matterport scene")
    importer._xform_prim = prim
    meshes = [p for p in Usd.PrimRange(prim) if p.IsA(UsdGeom.Mesh)]
    if not meshes:
        raise ValueError("Matterport asset contains no collision mesh")
    for mesh in meshes:
        # Keep all vertices/faces and original visual materials. A separate uniform
        # collider avoids PhysX's undefined contact-face lookup in visual subsets.
        collision = UsdGeom.Mesh.Define(stage, str(mesh.GetPath()) + "_physics")
        for name in ("points", "faceVertexCounts", "faceVertexIndices", "orientation", "subdivisionScheme", "doubleSided"):
            attribute = mesh.GetAttribute(name)
            if attribute and attribute.HasAuthoredValueOpinion():
                collision.GetPrim().CreateAttribute(name, attribute.GetTypeName()).Set(attribute.Get())
        original_transform, collision_transform = UsdGeom.Xformable(mesh), UsdGeom.Xformable(collision)
        collision_transform.AddTransformOp().Set(original_transform.GetLocalTransformation())
        collision_transform.SetResetXformStack(original_transform.GetResetXformStack())
        collision.CreateVisibilityAttr("invisible")
        if mesh.HasAPI(UsdPhysics.CollisionAPI):
            raise ValueError("Unexpected authored collision on the original visual mesh")
        for name in ("points", "faceVertexCounts", "faceVertexIndices"):
            if mesh.GetAttribute(name).Get() != collision.GetPrim().GetAttribute(name).Get():
                raise ValueError("The dedicated collider changed original Matterport geometry")
        sim_utils.define_collision_properties(str(collision.GetPath()),
                                             sim_utils.CollisionPropertiesCfg(collision_enabled=True))
        UsdPhysics.MeshCollisionAPI.Apply(collision.GetPrim()).CreateApproximationAttr("none")
    material = importer.cfg.physics_material
    material_path = importer.cfg.prim_path + "/physicsMaterial"
    material.func(material_path, material)
    sim_utils.bind_physics_material(prim.GetPath(), material_path)
    if importer.cfg.groundplane:
        cfg = sim_utils.GroundPlaneCfg(physics_material=material)
        cfg.func("/World/GroundPlane", cfg).visible = False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--go2-usd", type=Path, required=True)
    parser.add_argument("--assets-dir", type=Path, help="Prepared dataset and Matterport scenes outside the clean source checkout")
    arguments = parser.parse_args()
    print(prepare(arguments.source_root, arguments.output_root, arguments.go2_usd, arguments.assets_dir))

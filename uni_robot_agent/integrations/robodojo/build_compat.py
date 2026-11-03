"""Build-only compatibility for the pinned IsaacLab fork; native sources stay intact."""
import hashlib
from pathlib import Path
import shutil


def isaaclab_source(native: Path, destination: Path) -> Path:
    source = native / 'third_party/IsaacLab'
    setup = Path('source/isaaclab/setup.py')
    original = (source / setup).read_bytes()
    # The fork requires Starlette 0.49.1, whereas Isaac Sim 5.1's FastAPI requires
    # <0.46. Keep all simulator code and assets; change only build-time metadata.
    if hashlib.sha256(original).hexdigest() != '0839e1877f8558f0f5b5371b303cfdc0f3275e36c1980b99419218581922589c':
        raise ValueError('Pinned IsaacLab build metadata changed; review compatibility before installing')
    if destination.exists():
        shutil.rmtree(destination)
    # AppLauncher resolves apps/ relative to the source tree; an ordinary wheel
    # loses this layout and the extension config directories.
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns('.git', '__pycache__', '*.egg-info', 'build'))
    patched = original.replace(b'"starlette==0.49.1"', b'"starlette==0.45.3"')
    patched = patched.replace(b'version=EXTENSION_TOML_DATA["package"]["version"],',
                              b'version=EXTENSION_TOML_DATA["package"]["version"] + "+ura.1",')
    (destination / setup).write_bytes(patched)
    return destination

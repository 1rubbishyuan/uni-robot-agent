"""Integrity checks for Gemini's entry point, split chunks and bundled assets."""
import hashlib
from pathlib import Path

BRIDGE_ID = 'uni-robot-agent-gemini-acp-call-id-v1'


def bundle_inventory(root: Path) -> dict[str, str]:
    if root.is_symlink():
        raise ValueError('Gemini bundle directory must not be a symbolic link')
    files, has_bridge = {}, False
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Gemini bundle must not contain symbolic links')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError('Gemini bundle contains a non-regular file')
        content = path.read_bytes()
        files[path.relative_to(root).as_posix()] = hashlib.sha256(content).hexdigest()
        if path.suffix in {'.js', '.mjs'} and BRIDGE_ID.encode() in content:
            has_bridge = True
    if not has_bridge:
        raise ValueError('Gemini bundle does not contain the compatibility bridge')
    return files


def verify_bundle(binary: Path, manifest: dict, version: str) -> None:
    if manifest.get('bridge') != BRIDGE_ID or manifest.get('version') != version:
        raise ValueError('Gemini bundle bridge or version differs from its manifest')
    format_version = manifest.get('format', 1)
    if format_version == 1:
        artifact = binary.read_bytes()
        valid = (BRIDGE_ID.encode() in artifact
                 and manifest.get('sha256') == hashlib.sha256(artifact).hexdigest())
    elif format_version == 2:
        valid = (not binary.is_symlink() and manifest.get('entrypoint') == binary.name
                 and binary.is_file()
                 and manifest.get('files') == bundle_inventory(binary.parent))
    else:
        raise ValueError('Unsupported Gemini bridge manifest format')
    if not valid:
        raise ValueError('Gemini bundle does not match its bridge manifest; rebuild and seal it')

"""Keep generated robot configuration separate from shared downloaded assets."""
from pathlib import Path
import shutil


def asset_overlay(source: Path, destination: Path) -> Path:
    templates = list((source / 'Robots').rglob('*_tmp.yml'))
    if not templates:
        raise ValueError(f'Missing RoboDojo robot configuration templates: {source}')
    generated = {p.with_name(p.name.replace('_tmp.yml', '.yml')) for p in templates}
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for item in source.iterdir():
        if item.name != 'Robots':
            (destination / item.name).symlink_to(item, target_is_directory=item.is_dir())
    # Only robot directories need writable overlays. Geometry and templates stay
    # linked; generated YAML must never overwrite another runtime's shared files.
    (destination / 'Robots').mkdir()
    for item in (source / 'Robots').rglob('*'):
        target = destination / item.relative_to(source)
        if item.is_dir():
            target.mkdir(exist_ok=True)
        elif item not in generated:
            target.symlink_to(item)
    return destination


def verify_robot_configs(assets: Path) -> None:
    for robot in ('x5', 'franka'):
        config = assets / 'Robots' / robot / 'curobo.yml'
        if not config.is_file() or '$ASSETS_PATH' in config.read_text() or '${ASSETS_PATH}' in config.read_text():
            raise ValueError(f'RoboDojo robot configuration is not initialized: {config}')

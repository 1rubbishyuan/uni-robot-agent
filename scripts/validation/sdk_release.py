"""Install a wheel with full dependencies and check SDK imports/configuration outside the checkout.

Usage: python scripts/validation/sdk_release.py WHEEL NEW_DIRECTORY [--installer uv]
An optional --wheelhouse supplies offline dependencies without skipping resolution.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--installer', choices=('pip', 'uv'), default='pip')
    parser.add_argument('--wheelhouse', type=Path)
    args = parser.parse_args()
    wheel, directory = args.wheel.resolve(), args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('UNI_ROBOT_AGENT_')
                   and key != 'ANTHROPIC_BASE_URL'}
    environment.update(PYTHONPATH='', PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    target = directory / 'venv'
    venv.create(target, with_pip=True)
    python = target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    def command(parts):
        subprocess.run([str(part) for part in parts], cwd=directory, env=environment, check=True)
    install = [python, '-m', 'pip', 'install'] if args.installer == 'pip' else [
        shutil.which('uv') or 'uv', 'pip', 'install', '--link-mode', 'copy', '--python', python]
    source = ['--no-index', '--find-links', args.wheelhouse.resolve()] if args.wheelhouse else []
    command([*install, *source, wheel])
    command([python, '-I', '-c', '''import pathlib,sys,uni_robot_agent
for name in uni_robot_agent.__all__:
    getattr(uni_robot_agent, name)
assert pathlib.Path(uni_robot_agent.__file__).is_relative_to(sys.prefix)
assert pathlib.Path(uni_robot_agent.__file__).with_name('py.typed').is_file()
assert not any(n in sys.modules for n in ('isaacsim', 'mujoco', 'torch'))
print('Installed SDK:', uni_robot_agent.__file__)
'''])
    executable = python.parent / ('uni-robot-agent.exe' if os.name == 'nt' else 'uni-robot-agent')
    command([executable, '--version'])
    command([executable, '--help'])
    command([python, '-m', 'uni_robot_agent', '--version'])
    for integration in ('robodojo', 'robouse'):
        command([python, '-m', 'uni_robot_agent', 'run', '--integration', integration,
                 '--model', 'validation-model', '--env-url', 'http://127.0.0.1:18760',
                 '--no-sam3', '--agent-python', python, '--validate-config'])
    command([python, '-I', '-c', """from pathlib import Path
from importlib.util import find_spec
import uni_robot_agent
from uni_robot_agent import Session, SessionConfig, SetupConfig, DeploymentConfig
from uni_robot_agent.context.prompt import get_prompt_builder
root = Path(uni_robot_agent.__file__).parent
assert callable(get_prompt_builder('robodojo'))
assert callable(get_prompt_builder('robouse'))
for resource in ('ui/web/static/index.html', 'ui/web/static/app.js',
                 'integrations/robodojo/deployment.toml',
                 'integrations/robouse/recipes/libero.toml'):
    assert (root / resource).is_file(), resource
session = Session(SessionConfig(integration='robouse',
    env_url='http://127.0.0.1:18760', no_sam3=True))
session.close()  # Validate configuration only; do not connect or finalize an episode.
SetupConfig(integration='robodojo')
DeploymentConfig(integration='robodojo', task='stack_bowls')
rgb = DeploymentConfig(integration='robodojo', task='stack_bowls',
                       depth=False, camera_calibration=False).resolve()
assert rgb.depth is False and rgb.camera_calibration is False
try:
    DeploymentConfig(integration='robodojo', task='stack_bowls',
                     camera_calibration=False).resolve()
except ValueError as error:
    assert 'requires --no-depth' in str(error)
else:
    raise AssertionError('Uncalibrated depth configuration accepted')
from uni_robot_agent.integrations.robodojo.prompt import load_task_requirements
assert load_task_requirements('make_kong') == ''
assert '2 cm' not in load_task_requirements('arrange_largest_number_random')
assert 'object itself more than 10 cm' in load_task_requirements('general_pickup')
for name in ('robodojo_internal', 'efe', 'franka_robotiq', 'franka_sharpa', '_franka', 'dexterous_hand'):
    assert find_spec(f'uni_robot_agent.integrations.{name}') is None, name
    assert not (root / 'integrations' / name).exists(), name
for name in ('gripper', 'libero', 'robodojo_internal', 'efe', 'franka_robotiq', 'franka_sharpa'):
    try:
        Session(SessionConfig(integration=name, env_url='http://127.0.0.1:18760', no_sam3=True))
    except ValueError as error:
        assert 'not available' in str(error)
    else:
        raise AssertionError(f'Unavailable integration accepted: {name}')
assert find_spec('uni_robot_agent.tools.dexterous_hand') is None
assert not (root / 'tools' / 'dexterous_hand').exists()
print('Offline SDK configuration, unavailable-entry errors and packaged resources: OK')
"""])
    command([python, '-m', 'pip', 'check'])


if __name__ == '__main__':
    main()

"""Check the native wheel with Isaac-compatible dependencies in a fresh environment."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tomllib
import venv
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--main-wheel', type=Path, help='Also check rejection of a planner wheel before setup')
    args = parser.parse_args()
    wheel, directory = args.wheel.resolve(), args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(wheel) as archive:
        recipe = tomllib.loads(archive.read('uni_robot_agent/integrations/robodojo/deployment.toml').decode())
    constraints = directory / 'constraints.txt'
    constraints.write_text('\n'.join(recipe['constraints']) + '\n')
    venv.create(directory / 'venv', with_pip=True)
    python = directory / 'venv/bin/python'

    def run(*command):
        subprocess.run([str(part) for part in command], cwd=directory, check=True)

    run(python, '-m', 'pip', 'install', '--constraint', constraints, wheel)
    run(python, '-m', 'pip', 'check')
    run(python, '-I', '-c', '''
import importlib.metadata as metadata
import importlib.util
import pathlib
import signal
import sys
import httpx
from fastapi import APIRouter
import uni_robot_agent
from uni_robot_agent.services.hosting import HttpServer, create_app
from uni_robot_agent.integrations.robodojo.environment import server
assert pathlib.Path(uni_robot_agent.__file__).is_relative_to(sys.prefix)
assert uni_robot_agent.__version__ == metadata.version('uni-robot-agent-runtime')
for name in ('openai_codex', 'claude_agent_sdk', 'mcp', 'textual'):
    assert importlib.util.find_spec(name) is None, name
for name in ('openai-codex', 'openai-codex-cli-bin', 'claude-agent-sdk', 'mcp', 'textual'):
    try:
        metadata.version(name)
    except metadata.PackageNotFoundError:
        continue
    raise AssertionError(f'Planner dependency installed in native runtime: {name}')
assert metadata.version('fastapi') == '0.115.7'
assert metadata.version('uvicorn') == '0.29.0'
assert metadata.version('pydantic') == '2.11.7'
assert metadata.version('typing-extensions') == '4.12.2'
closed = []
router = APIRouter()
@router.get('/probe')
def probe():
    return {'ok': True}
signals = {n: signal.getsignal(n) for n in (signal.SIGINT, signal.SIGTERM)}
host = HttpServer(create_app(router, on_close=lambda: closed.append(True)))
try:
    url = host.start_background()
    assert httpx.get(url + '/probe', trust_env=False).json() == {'ok': True}
finally:
    host.stop()
assert closed == [True]
assert {n: signal.getsignal(n) for n in signals} == signals
print('Native dependencies, imports, HTTP request, shutdown and signal ownership: OK')
''')
    run(python, '-I', '-c', '''
from pathlib import Path
from uni_robot_agent.integrations.robodojo.assets import asset_overlay, verify_robot_configs
source, target = Path('shared-assets').resolve(), Path('private/Assets').resolve()
for robot in ('x5', 'franka'):
    directory = source / 'Robots' / robot
    directory.mkdir(parents=True)
    (directory / 'curobo_tmp.yml').write_text('asset_root_path: ${ASSETS_PATH}/Assets\\n')
    (directory / 'curobo.yml').write_text('existing shared configuration\\n')
asset_overlay(source, target)
try:
    verify_robot_configs(target)
except ValueError:
    pass
else:
    raise AssertionError('Uninitialized robot configuration accepted')
for robot in ('x5', 'franka'):
    config = target / 'Robots' / robot / 'curobo.yml'
    assert not config.exists()
    config.write_text('asset_root_path: ' + str(target) + '\\n')
    assert (source / 'Robots' / robot / 'curobo.yml').read_text() == 'existing shared configuration\\n'
verify_robot_configs(target)
print('Robot config preflight and private writes preserve shared assets: OK')
''')
    if args.main_wheel:
        run(python, '-I', '-c', '''
from pathlib import Path
import sys
import zipfile
from uni_robot_agent import SetupConfig, setup
from uni_robot_agent.services.deployment.setup import validate_runtime_wheel
validate_runtime_wheel(Path(sys.argv[1]))
bad = Path('wrong-version.whl')
with zipfile.ZipFile(bad, 'w') as archive:
    archive.writestr('uni_robot_agent_runtime-0.0.0.dist-info/METADATA',
                     'Name: uni-robot-agent-runtime\\nVersion: 0.0.0\\n')
for wheel in (Path(sys.argv[2]), bad):
    try:
        setup(SetupConfig(integration='robodojo', wheel=wheel, cache_dir=Path('rejected-cache')))
    except ValueError as error:
        assert '--wheel requires' in str(error), error
    else:
        raise AssertionError('Wrong wheel accepted')
    assert not Path('rejected-cache').exists()
print('Planner and mismatched-version wheels rejected before installation: OK')
''', wheel, args.main_wheel.resolve())
    run(python, '-m', 'pip', 'uninstall', '-y', 'uni-robot-agent-runtime')
    run(python, '-I', '-c', "import importlib.util; assert importlib.util.find_spec('uni_robot_agent') is None")


if __name__ == '__main__':
    main()

"""Versioned runtime records; deployment never repairs an installation."""
from contextlib import contextmanager
from dataclasses import replace
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

from uni_robot_agent import __version__
from uni_robot_agent.artifacts.run import write_json_atomic
from .config import Runtime, environment_options


def cache_root(args):
    path = args.cache_dir or os.environ.get('UNI_ROBOT_AGENT_CACHE_DIR')
    return Path(path or Path.home() / '.cache' / 'uni-robot-agent').expanduser().absolute()


def runtime_root(args, recipe):
    return cache_root(args) / recipe.name / f'{__version__}-{recipe.digest[:16]}'


@contextmanager
def runtime_lock(root, timeout=14400):
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.prepare.lock').open('a') as lock:
        # One producer publishes this immutable recipe/version; processes may read the old record.
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Timed out waiting for another setup process')
                time.sleep(.1)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_runtime(args, recipe):
    root = runtime_root(args, recipe)
    path = root / 'runtime.json'
    if not path.is_file():
        raise ValueError(f'Runtime {recipe.name} is not prepared. Run the matching uni-robot-agent setup command.')
    data = json.loads(path.read_text())
    if data.pop('schema_version', None) != 1:
        raise ValueError('Unsupported runtime record version; rerun setup')
    runtime = Runtime(**{**data, 'root': Path(data['root'])})
    if runtime.recipe_digest != recipe.digest or runtime.package_version != __version__:
        raise ValueError('Runtime version mismatch; rerun setup')
    if not Path(runtime.python).is_file():
        raise ValueError('Prepared interpreter is missing; rerun setup')
    return runtime


def runtime_for(args, recipe, adapter):
    if args.runtime_python:
        python = str(args.runtime_python.expanduser().absolute())
        if not Path(python).is_file():
            raise ValueError('--runtime-python must be an existing interpreter')
        data = adapter.external_data(args)
        return Runtime(recipe.name, python, recipe.digest, __version__, runtime_root(args, recipe),
                       data, environment_options(args.runtime_env))
    runtime = read_runtime(args, recipe)
    if any(getattr(args, name, None) for name in ('native_root', 'assets_dir', 'checkpoint', 'worker_python')):
        raise ValueError('Runtime path overrides require --runtime-python; otherwise use the prepared manifest')
    expected = getattr(args, 'checkpoint_sha256', None)
    if expected and expected != runtime.data.get('checkpoint_sha256'):
        raise ValueError('Checkpoint SHA256 differs from the prepared runtime')
    return replace(runtime, environment={**runtime.environment, **environment_options(args.runtime_env)})


def check_python(python, imports=(), *, environment=None, timeout=120):
    script = ('import importlib,importlib.metadata,json,sys; '
              'from uni_robot_agent import __version__; '
              'names=json.loads(sys.argv[1]); '
              '[importlib.import_module(n) for n in names]; '
              'print(json.dumps(dict(python=sys.version.split()[0],package_version=__version__)))')
    env = {**os.environ, **(environment or {}), 'PYTHONPATH': '', 'PYTHONNOUSERSITE': '1'}
    result = subprocess.run([str(python), '-c', script, json.dumps(['uni_robot_agent.services.hosting', *imports])], env=env,
                            capture_output=True, text=True, timeout=timeout, cwd='/')
    if result.returncode:
        raise RuntimeError(f'Runtime import check failed: {result.stderr[-3000:]}')
    info = json.loads(result.stdout.strip().splitlines()[-1])
    if info['package_version'] != __version__:
        raise ValueError(f'Runtime needs uni-robot-agent=={__version__}, found {info["package_version"]}')
    return info


def publish_runtime(runtime, validation):
    write_json_atomic(runtime.root / 'validation.json', validation)
    write_json_atomic(runtime.root / 'runtime.json', runtime.record())


def query_python(python, code, *arguments, environment=None, timeout=120):
    result = subprocess.run([str(python), '-c', code, *map(str, arguments)],
        env={**os.environ, **(environment or {}), 'PYTHONPATH': '', 'PYTHONNOUSERSITE': '1'},
        capture_output=True, text=True, timeout=timeout, cwd='/')
    if result.returncode:
        raise RuntimeError(f'Runtime query failed: {result.stderr[-3000:]}')
    return json.loads(result.stdout.strip().splitlines()[-1])

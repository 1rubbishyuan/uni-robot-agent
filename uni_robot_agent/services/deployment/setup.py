"""Explicit installation only: never invoked from deploy or run."""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

from uni_robot_agent import __version__
from uni_robot_agent.artifacts.run import write_json_atomic
from .config import Runtime, SetupConfig, environment_options
from .process import OwnedProcess
from .runtime import publish_runtime, read_runtime, runtime_lock, runtime_root


class Preparation:
    def __init__(self, args, recipe, root, *, report=print):
        self.args, self.recipe, self.root = args, recipe, root
        self.report = report
        self.python = root / 'venv/bin/python'
        self.environment = {'PYTHONNOUSERSITE': '1', 'PYTHONPATH': '',
                            **environment_options(args.runtime_env)}
        self.log = root / 'setup.log'
        self.data = {}
        self.wheel = args.wheel
        self.constraints = root / 'constraints.txt'
        constraints = recipe.settings.get('constraints', [])
        if constraints:
            self.constraints.write_text('\n'.join(constraints) + '\n')
        self.deadline = time.monotonic() + args.setup_timeout_s

    def command(self, command, *, cwd=None, environment=None):
        process = OwnedProcess(command, cwd=cwd, log_path=self.log,
                               environment={**self.environment, **(environment or {})})
        process.start()
        try:
            code = process.process.wait(timeout=max(.01, self.deadline - time.monotonic()))
            if code:
                raise RuntimeError(f'Preparation command exited {code}; see {self.log}')
        finally:
            process.close()

    def step(self, name, operation):
        marker = self.root / 'steps' / (name + '.json')
        if marker.exists():
            return json.loads(marker.read_text())['result']
        if self.report is not None:
            self.report(f'Preparing {self.recipe.name}: {name}')
        value = operation()
        write_json_atomic(marker, dict(result=value))
        return value

    def create_python(self):
        def create():
            base = str(self.args.python or self.recipe.settings.get('python', sys.executable))
            if not shutil.which(base) and not Path(base).is_file():
                raise ValueError(f'Required interpreter {base} is unavailable; supply --python')
            self.command([base, '-m', 'venv', str(self.root / 'venv')])
            return str(self.python)
        self.step('venv', create)

    def pip(self, *requirements, options=()):
        constraints = ['--constraint', str(self.constraints)] if self.constraints.is_file() else []
        self.command([str(self.python), '-m', 'pip', 'install', *constraints, *options, *map(str, requirements)])

    def check_dependencies(self):
        result = subprocess.run([str(self.python), '-m', 'pip', 'check'], capture_output=True,
                                text=True, timeout=max(.01, self.deadline-time.monotonic()))
        if not result.returncode:
            return
        reported = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        allowed = self.recipe.settings.get('dependency_exceptions', [])
        if not reported or result.stderr or any(line not in allowed for line in reported):
            raise RuntimeError('Runtime dependencies are inconsistent: ' + result.stdout + result.stderr)
        self.data['dependency_exceptions'] = reported

    def install_harness(self):
        wheel = self.wheel
        requirement = str(wheel.expanduser().absolute()) if wheel else f'uni-robot-agent=={__version__}'
        if wheel and (not wheel.is_file() or wheel.suffix != '.whl'):
            raise ValueError('--wheel must be an existing wheel')
        self.pip(requirement)

    def git(self, url, revision, target, *, submodules=False):
        target = Path(target)
        if not (target / '.git').exists():
            self.command(['git', 'clone', '--no-checkout', url, str(target)])
        self.command(['git', '-C', str(target), 'fetch', 'origin', revision])
        self.command(['git', '-C', str(target), 'checkout', '--detach', revision])
        if submodules:
            self.command(['git', '-C', str(target), 'submodule', 'update', '--init', '--recursive'])
        return str(target)

    def snapshot(self, repo_id, revision, target, *, repo_type='dataset', patterns=None):
        if not revision or revision in ('main', 'master'):
            raise ValueError('Downloads require an immutable Hub revision')
        # Hub credentials are read by the library, never persisted in the runtime record.
        payload = json.dumps(dict(repo_id=repo_id, repo_type=repo_type, revision=revision,
                                  local_dir=str(target), allow_patterns=patterns))
        self.command([sys.executable, '-c',
            'import json,sys; from huggingface_hub import snapshot_download; snapshot_download(**json.loads(sys.argv[1]))',
            payload])
        return str(target)

    def download_zip(self, url, digest, target):
        archive = self.root / 'downloads' / (digest + '.zip')
        archive.parent.mkdir(exist_ok=True)
        if not archive.exists():
            partial = archive.with_suffix('.partial')
            with urllib.request.urlopen(url, timeout=120) as response, partial.open('wb') as stream:
                while chunk := response.read(8 * 1024 * 1024):
                    if time.monotonic() >= self.deadline:
                        raise TimeoutError('Setup download deadline exceeded')
                    stream.write(chunk)
            if file_hash(partial) != digest:
                raise ValueError(f'Asset checksum mismatch: {url}')
            partial.replace(archive)
        if file_hash(archive) != digest:
            raise ValueError(f'Cached asset checksum mismatch: {archive}')
        destination = Path(target).resolve()
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if not (destination / member.filename).resolve().is_relative_to(destination):
                    raise ValueError('Asset archive contains a path outside its destination')
            if bundle.testzip() is not None:
                raise ValueError('Asset ZIP CRC check failed')
            bundle.extractall(destination)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(args, adapter, *, report=print):
    recipe = adapter.recipe(args)
    root = runtime_root(args, recipe)
    with runtime_lock(root, args.setup_timeout_s):
        request = {name: str(getattr(args, name)) if getattr(args, name, None) is not None else None
                   for name in ('runtime_python', 'native_root', 'assets_dir', 'checkpoint', 'worker_python', 'python')}
        request['environment'] = environment_options(args.runtime_env)
        request['wheel_sha256'] = file_hash(args.wheel) if args.wheel else None
        request_file = root / 'prepare-request.json'
        if request_file.exists() and json.loads(request_file.read_text()) != request:
            raise ValueError('This cache entry was prepared with different inputs. Select another --cache-dir.')
        write_json_atomic(request_file, request)
        if (root / 'runtime.json').exists():
            runtime = read_runtime(args, recipe)
            expected = args.checkpoint_sha256
            if expected and expected != runtime.data.get('checkpoint_sha256'):
                raise ValueError('Checkpoint SHA256 differs from the prepared runtime')
            validation = adapter.verify(runtime)
            if report is not None:
                report(f'Runtime ready (reused): {root}')
            return runtime
        context = Preparation(args, recipe, root, report=report)
        if args.wheel:
            if args.wheel.suffix != '.whl':
                raise ValueError('--wheel must be an existing wheel')
            staged = root / 'inputs' / request['wheel_sha256'] / args.wheel.name
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.wheel, staged)
            if file_hash(staged) != request['wheel_sha256']:
                raise ValueError('Input wheel changed during preparation')
            context.wheel = staged
        started = time.time()
        try:
            if args.runtime_python:
                # Adoption records runtime metadata in the cache without installing packages.
                runtime = Runtime(recipe.name, str(args.runtime_python.expanduser().absolute()),
                    recipe.digest, __version__, root, adapter.external_data(args), context.environment)
            else:
                context.create_python()
                adapter.prepare(context)
                context.step('harness', lambda: context.install_harness())
                runtime = Runtime(recipe.name, str(context.python), recipe.digest,
                                  __version__, root, context.data, context.environment)
                context.check_dependencies()
                frozen = subprocess.check_output([str(context.python), '-m', 'pip', 'freeze', '--all'], text=True)
                (root / 'installed.txt').write_text(frozen)
            validation = adapter.verify(runtime)
            validation.update(preparation='external' if args.runtime_python else 'isolated',
                              elapsed_s=time.time()-started, gpu_verified=False)
            publish_runtime(runtime, validation)
        except BaseException as error:
            write_json_atomic(root / 'setup_failure.json', dict(error=str(error), type=type(error).__name__,
                                                              elapsed_s=time.time()-started))
            raise
        if report is not None:
            report(f'Runtime ready: {root}')
        return runtime


def setup(config: SetupConfig, *, report: Callable[[str], None] | None = None) -> Runtime:
    """Prepare a runtime without changing host signal handlers or logging settings."""
    from .config import adapter_for
    args = config.resolve()
    return prepare(args, adapter_for(args), report=report)

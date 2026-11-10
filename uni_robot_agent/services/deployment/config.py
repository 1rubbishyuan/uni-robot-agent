"""Lightweight contracts for preparing and owning local service processes."""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module, resources
import hashlib
import argparse
import math
from pathlib import Path
import tomllib
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class Recipe:
    name: str
    package: str
    resource: str
    settings: dict
    digest: str

    @classmethod
    def load(cls, package, resource):
        content = resources.files(package).joinpath(resource).read_bytes()
        settings = tomllib.loads(content.decode())
        if base := settings.get('base'):
            inherited = resources.files(package).joinpath(base).read_bytes()
            defaults = tomllib.loads(inherited.decode())
            settings = {**defaults, **settings, 'packages': defaults.get('packages', []) + settings.get('packages', [])}
            content = inherited + b'\0' + content
        return cls(settings['name'], package, resource, settings, hashlib.sha256(content).hexdigest())


@dataclass(frozen=True)
class Runtime:
    name: str
    python: str
    recipe_digest: str
    package_version: str
    root: Path
    data: dict = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)

    def record(self):
        return dict(schema_version=1, name=self.name, python=self.python,
                    recipe_digest=self.recipe_digest, package_version=self.package_version,
                    root=str(self.root), data=self.data, environment=self.environment)


@dataclass(frozen=True)
class LaunchPlan:
    command: tuple[str, ...]
    url: str
    expected: dict
    environment: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None


def adapter_for(args):
    """Resolve explicit service selection without importing native runtimes."""
    service = getattr(args, 'service', None) or 'env'
    if service == 'env':
        from uni_robot_agent.integrations import load_integration
        if not args.integration:
            raise ValueError('Environment deployment requires --integration')
        module = load_integration(args.integration).get_integration_spec().deployment_module
        if not module:
            raise ValueError(f'{args.integration} has no local deployment recipe; use --env-url')
    else:
        module = {'actor': 'uni_robot_agent.services.actor.deployment',
                  'sam3': 'uni_robot_agent.services.perception.deployment'}[service]
    return import_module(module)


def positive(value):
    number = float(value)
    if number <= 0 or not math.isfinite(number):
        raise ValueError('must be finite and positive')
    return number


def validate_deployment(args):
    service = getattr(args, 'service', None) or 'env'
    if service != 'env' and getattr(args, 'integration', None):
        raise ValueError('--integration only applies to environment services')
    if args.command == 'setup':
        if args.runtime_python and (args.wheel or args.python):
            raise ValueError('Adoption is read-only; --wheel and --python require an isolated setup')
        if not args.runtime_python and (args.native_root or args.worker_python):
            raise ValueError('--native-root and --worker-python require --runtime-python')
    if service == 'env' and getattr(args, 'checkpoint', None):
        raise ValueError('--checkpoint belongs to actor or SAM3 services')
    if getattr(args, 'assets_dir', None) and (service != 'env' or args.integration != 'robodojo'):
        raise ValueError('--assets-dir currently applies only to RoboDojo')
    if getattr(args, 'deploy_gpu', 0) < 0:
        raise ValueError('GPU index must be nonnegative')
    if service != 'env' and getattr(args, 'runtime_profile', None):
        raise ValueError('--runtime-profile only applies to environment services')
    if service == 'env' and getattr(args, 'profile', None):
        raise ValueError('--profile only applies to actor services')
    if getattr(args, 'camera_calibration', None) is not None and (
        service != 'env' or args.integration != 'robodojo'
    ):
        raise ValueError('--camera-calibration/--no-camera-calibration only apply to RoboDojo environment deployment')
    adapter = adapter_for(args)
    adapter.validate(args)
    if getattr(args, 'port', None) is not None and not 1 <= args.port <= 65535:
        raise ValueError('--port must be between 1 and 65535')
    for field_name in ('startup_timeout_s', 'shutdown_timeout_s', 'setup_timeout_s'):
        if hasattr(args, field_name):
            positive(getattr(args, field_name))
    return adapter


def add_runtime_arguments(parser):
    parser.add_argument('--cache-dir', type=Path, help='Persistent runtime cache (or UNI_ROBOT_AGENT_CACHE_DIR)')
    parser.add_argument('--runtime-python', type=Path, help='Explicit prepared interpreter; never modified by deploy')
    parser.add_argument('--native-root', type=Path)
    parser.add_argument('--assets-dir', type=Path)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--checkpoint-sha256')
    parser.add_argument('--worker-python', type=Path)
    parser.add_argument('--runtime-profile', help='Native runtime profile selected by the environment integration')
    parser.add_argument('--runtime-env', action='append', default=[], metavar='KEY=VALUE',
                        help='Explicit native runtime environment setting, persisted by setup')


def add_launch_arguments(parser, *, managed=False):
    parser.add_argument('--env-gpu' if managed else '--gpu', dest='deploy_gpu', type=int, default=0,
                        help='Index within currently visible GPUs')
    parser.add_argument('--env-port' if managed else '--port', dest='port', type=int)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--startup-timeout-s', type=float, default=900)
    parser.add_argument('--shutdown-timeout-s', type=float, default=30)
    if '--layout' not in parser._option_string_actions:
        parser.add_argument('--layout', type=Path)
    parser.add_argument('--layout-sha256')
    parser.add_argument('--task-dir', type=Path)
    if '--seed' not in parser._option_string_actions:
        parser.add_argument('--seed', type=int)
    parser.add_argument('--cameras', nargs='+')
    parser.add_argument('--depth', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--camera-calibration', action=argparse.BooleanOptionalAction, default=None,
                        help='RoboDojo environment deployment only; disabling requires --no-depth')


def environment_options(values):
    result = {}
    for value in values:
        key, separator, text = value.partition('=')
        if not separator or not key.isidentifier():
            raise ValueError('--runtime-env requires KEY=VALUE')
        if any(word in key.upper() for word in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY')):
            raise ValueError('Do not persist credentials in runtime configuration; use the process environment')
        result[key] = text
    return result




class RuntimeConfig(BaseModel):
    """Selection shared by explicit preparation and deployment."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    integration: str | None = None
    service: Literal["env", "actor", "sam3"] = "env"
    profile: str | None = None
    cache_dir: Path | None = None
    runtime_python: Path | None = None
    native_root: Path | None = None
    assets_dir: Path | None = None
    checkpoint: Path | None = None
    checkpoint_sha256: str | None = None
    worker_python: Path | None = None
    runtime_profile: str | None = None
    runtime_env: list[str] = Field(default_factory=list)

    def resolve(self):
        args = SimpleNamespace(**self.model_dump())
        args.command = "setup" if isinstance(self, SetupConfig) else "deploy"
        environment_options(args.runtime_env)
        validate_deployment(args)
        return args


class SetupConfig(RuntimeConfig):
    """Explicit installation/adoption; never run implicitly by a Session."""

    python: str | None = None
    wheel: Path | None = None
    setup_timeout_s: float = Field(default=14400, gt=0, allow_inf_nan=False)


class DeploymentConfig(RuntimeConfig):
    """Launch one prepared local service, with an explicit owned process handle."""

    task: str | None = None
    run_id: str | None = None
    output_dir: Path = Path("runs")
    deploy_gpu: int = Field(default=0, ge=0)
    port: int | None = Field(default=None, ge=1, le=65535)
    host: str = "127.0.0.1"
    startup_timeout_s: float = Field(default=900, gt=0, allow_inf_nan=False)
    shutdown_timeout_s: float = Field(default=30, gt=0, allow_inf_nan=False)
    layout: Path | None = None
    layout_sha256: str | None = None
    task_dir: Path | None = None
    seed: int | None = None
    cameras: list[str] | None = None
    depth: bool | None = None
    camera_calibration: bool | None = None

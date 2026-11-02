"""Launch prepared services with explicit ownership and bounded cleanup."""
from __future__ import annotations

import json
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import uuid4

from uni_robot_agent.artifacts.run import write_json_atomic
from .config import adapter_for, DeploymentConfig
from .process import OwnedProcess, cancellation_signals, gpu_environment, require_free_port
from .runtime import runtime_for


class Deployment:
    """An owned service handle. External URLs never create this handle."""

    def __init__(self, args, adapter, output, run_id):
        self.args, self.adapter, self.output, self.run_id = args, adapter, Path(output), run_id
        self._closed = False
        self.process = None
        self.info = None
        self.url = None
        self.record = dict(run_id=run_id, state='preparing', owned=True)

    def save(self):
        write_json_atomic(self.output / 'deployment.json', self.record)

    def start(self):
        if self._closed:
            raise RuntimeError("Deployment is closed")
        if self.info is not None:
            return self
        self.output.mkdir(parents=True, exist_ok=True)
        self.save()
        deadline = time.monotonic() + self.args.startup_timeout_s
        try:
            recipe = self.adapter.recipe(self.args)
            runtime = runtime_for(self.args, recipe, self.adapter)
            self.adapter.verify(runtime)
            plan = self.adapter.plan(self.args, runtime, self.output, self.run_id)
            self.url = plan.url
            require_free_port(plan.url)
            self.record.update(runtime=runtime.name, recipe_digest=runtime.recipe_digest,
                               runtime_manifest=str(runtime.root / 'runtime.json'), url=plan.url,
                               expected=plan.expected, state='starting')
            self.save()
            environment = {**plan.environment, **gpu_environment(self.args.deploy_gpu),
                           'PYTHONPATH': '', 'PYTHONNOUSERSITE': '1'}
            self.process = OwnedProcess(plan.command, environment=environment, cwd=plan.cwd,
                log_path=self.output / 'service.log', shutdown_timeout=self.args.shutdown_timeout_s)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Runtime validation exhausted the startup budget')
            self.process.start()
            self.info = self.process.wait_ready(plan.url, plan.expected, remaining)
            self.record.update(state='ready', pid=self.process.process.pid, identity=self.info)
            self.save()
            return self
        except BaseException as error:
            self.record.update(state='failed', error=str(error), error_type=type(error).__name__)
            try:
                self.close()
            finally:
                self.save()
            raise

    def close(self):
        if self._closed:
            return
        if self.process is not None and self.process.process is not None:
            self.process.close()
            self.record.update(closed=True, forced_cleanup=self.process.forced,
                               service_exit_code=self.process.process.returncode)
        self.save()
        self._closed = True

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()


def start_deployment(config: DeploymentConfig) -> Deployment:
    """Start a prepared local service; the caller owns close() or its context manager."""
    args = config.resolve()
    output, run_id = output_directory(args, args.service)
    return Deployment(args, adapter_for(args), output, run_id).start()


def output_directory(args, role):
    run_id = getattr(args, 'run_id', None) or f'{role}_{int(time.time())}_{uuid4().hex[:8]}'
    # An identity can be arbitrary protocol text; it must never control a filesystem path.
    output = Path(args.output_dir).expanduser().resolve() / f'{role}_{int(time.time())}_{uuid4().hex[:8]}'
    output.mkdir(parents=True, exist_ok=False)
    return output, run_id


def deploy(args, adapter):
    output, run_id = output_directory(args, args.service)
    with cancellation_signals(), Deployment(args, adapter, output, run_id) as service:
        print(json.dumps(dict(url=service.url, run_id=run_id, output_dir=str(output))), flush=True)
        return service.process.wait()


def evaluator_entry():
    """Private process entry: resolved secrets travel through stdin, never argv or disk."""
    from argparse import Namespace
    from uni_robot_agent.session import run
    from uni_robot_agent.telemetry.logging import configure_logging
    args = Namespace(**json.load(sys.stdin))
    configure_logging(args.log_level)
    run(args)


def run_local(args):
    output, run_id = output_directory(args, args.integration)
    args._session_dir = str(output)
    args.run_id = run_id
    child = None
    monitor = None
    service = Deployment(args, adapter_for(args), output, run_id)
    status = dict(state='deploying', episode_exit_code=None)
    code = 1
    with cancellation_signals():
        try:
            if getattr(args, 'monitor', False):
                from uni_robot_agent.ui.web.runtime import LiveMonitor
                monitor = LiveMonitor(args, output).start()
                monitor.update(state='deploying')
                args._monitor_parent = True
            service.start()
            args.env_url = service.url
            if monitor is not None:
                monitor.attach(service.url)
            args._episode_identity = service.info
            args._deployment = dict(manifest='deployment.json', owned=True, runtime=service.record['runtime'])
            child = OwnedProcess([sys.executable, '-c',
                'from uni_robot_agent.services.deployment.manager import evaluator_entry; evaluator_entry()'],
                shutdown_timeout=args.shutdown_timeout_s)
            # Parent survives episode timeout to release its own environment process group.
            child.start(input_data=json.dumps(vars(args), default=str).encode())
            status['state'] = 'evaluating'
            deadline = time.monotonic() + args.episode_timeout_s if args.episode_timeout_s else None
            while True:
                if service.process.process.poll() is not None:
                    status.update(state='environment_failed', error='Owned environment exited during evaluation')
                    code = 1
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    code = 124
                    status['state'] = 'episode_timeout'
                    break
                try:
                    code = child.process.wait(timeout=.1)
                    status['state'] = 'finished'
                    break
                except subprocess.TimeoutExpired:
                    continue
        except KeyboardInterrupt:
            code = 130
            status['state'] = 'cancelled'
        except (ValueError, OSError, RuntimeError, TimeoutError) as error:
            status.update(state='deployment_failed', error=str(error))
            print(f'Local deployment failed: {error}; see {output}', file=sys.stderr)
        finally:
            try:
                if child is not None:
                    child.close(sig=signal.SIGINT)
            finally:
                service.close()
                status['episode_exit_code'] = code
                write_json_atomic(output / 'supervisor.json', status)
                if monitor is not None:
                    monitor.close(status['state'], status.get('error'))
    print(f'Run artifacts: {output}', flush=True)
    return code if code >= 0 else 128 - code

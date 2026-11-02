"""Prepare and launch the pinned RoboDojo OpenPI actor."""
from pathlib import Path

from uni_robot_agent.services.deployment.config import LaunchPlan, Recipe
from uni_robot_agent.services.deployment.runtime import check_python, query_python


def recipe(args):
    return Recipe.load(__package__, 'recipes/robodojo_pi05.toml')


def validate(args):
    if args.profile != 'robodojo_pi05':
        raise ValueError('Actor deployment requires --profile robodojo_pi05')


def external_data(args):
    if not args.native_root or not args.checkpoint:
        raise ValueError('External actor runtime requires --native-root and --checkpoint')
    return dict(native_root=str(args.native_root.expanduser().resolve()),
                checkpoint=str(args.checkpoint.expanduser().resolve()),
                checkpoint_sha256=args.checkpoint_sha256)


def prepare(context):
    settings = context.recipe.settings
    source = Path(context.step('source', lambda: context.git(settings['repository'], settings['revision'],
                                                            context.root / 'source', submodules=True)))
    project = source / 'policy/Pi_05/openpi'
    context.step('uv', lambda: context.pip('uv==0.9.5'))
    context.step('openpi', lambda: context.command([str(context.python.parent / 'uv'), 'sync',
        '--frozen', '--group', 'lerobot', '--no-dev', '--python', str(context.python)], cwd=project,
        environment={'UV_PROJECT_ENVIRONMENT': str(context.python.parent.parent)}))
    context.step('pip-bootstrap', lambda: context.command([str(context.python), '-m', 'ensurepip', '--upgrade']))
    if context.args.checkpoint:
        checkpoint = context.args.checkpoint.expanduser().resolve()
    else:
        download = Path(context.step('weights', lambda: context.snapshot(settings['asset_repository'],
            settings['asset_revision'], context.root / 'weights', patterns=['ckpt/RoboDojo/Pi_05/**'])))
        candidates = [p.parent.parent.parent for p in download.rglob('assets/arx_x5_sim/norm_stats.json')
                      if (p.parent.parent.parent / 'params').is_dir()]
        if len(candidates) != 1:
            raise ValueError('Expected one official Pi0.5 checkpoint; supply --checkpoint to select it')
        checkpoint = candidates[0]
    context.data.update(native_root=str(source), checkpoint=str(checkpoint),
                        checkpoint_sha256=context.args.checkpoint_sha256)


def verify(runtime):
    info = check_python(runtime.python, ['jax'], environment=runtime.environment)
    code = '''import json,sys
from uni_robot_agent.services.actor.backends.pi05.openpi_jax import source_hash, checkpoint_identity, OPENPI_SOURCE_SHA256
assert source_hash(sys.argv[1])[0] == OPENPI_SOURCE_SHA256, 'Actor source mismatch'
from pathlib import Path
root=Path(sys.argv[1])
sys.path[:0]=[str(root/'policy/Pi_05/openpi/src'),str(root/'policy/Pi_05/openpi/packages/openpi-client/src')]
import openpi,openpi_client
print(json.dumps(checkpoint_identity(sys.argv[2])))
'''
    identity = query_python(runtime.python, code, runtime.data['native_root'], runtime.data['checkpoint'],
                            environment=runtime.environment, timeout=900)
    expected = runtime.data.get('checkpoint_sha256')
    if expected and expected != identity['checkpoint_sha256']:
        raise ValueError('Actor checkpoint checksum mismatch')
    runtime.data['checkpoint_sha256'] = identity['checkpoint_sha256']
    info['checkpoint_sha256'] = identity['checkpoint_sha256']
    return info


def plan(args, runtime, output, run_id):
    port = args.port or 18761
    data = runtime.data
    command = [runtime.python, '-m', 'uni_robot_agent.services.actor.server', '--backend', 'pi05',
        '--runtime', 'openpi_jax', '--profile', 'robodojo_pi05',
        '--model-config', 'pi05_base_aloha_full_sim_arx-x5_seed_0',
        '--xpolicylab-root', data['native_root'], '--checkpoint', data['checkpoint'],
        '--checkpoint-sha256', data['checkpoint_sha256'], '--host', args.host, '--port', str(port)]
    host = '127.0.0.1' if args.host == '0.0.0.0' else args.host
    return LaunchPlan(tuple(command), f'http://{host}:{port}',
        {'model': 'pi05', 'checkpoint_sha256': data['checkpoint_sha256']}, runtime.environment, output)

"""Explicit SAM3 resource preparation and standalone service launch."""
from pathlib import Path

from uni_robot_agent.services.deployment.config import LaunchPlan, Recipe
from uni_robot_agent.services.deployment.runtime import check_python
from uni_robot_agent.services.deployment.setup import file_hash


def recipe(args):
    return Recipe.load(__package__, 'recipes/sam3.toml')


def validate(args):
    if getattr(args, 'profile', None):
        raise ValueError('SAM3 has no actor profile')


def external_data(args):
    if not args.checkpoint:
        raise ValueError('An external SAM3 runtime requires --checkpoint')
    return dict(checkpoint=str(args.checkpoint.expanduser().resolve()),
                checkpoint_sha256=args.checkpoint_sha256)


def prepare(context):
    settings = context.recipe.settings
    context.step('torch', lambda: context.pip(*settings['torch'], options=['--index-url', settings['torch_index']]))
    context.step('dependencies', lambda: context.pip(*settings['packages']))
    if context.args.checkpoint:
        checkpoint = context.args.checkpoint.expanduser().resolve()
    else:
        weights = Path(context.step('weights', lambda: context.snapshot(settings['repository'],
            settings['revision'], context.root / 'weights', repo_type='model', patterns=['sam3.pt'])))
        checkpoint = weights / 'sam3.pt'
    context.data.update(checkpoint=str(checkpoint), checkpoint_sha256=context.args.checkpoint_sha256)


def verify(runtime):
    info = check_python(runtime.python, ['torch', 'sam3.model_builder'], environment=runtime.environment)
    checkpoint = Path(runtime.data['checkpoint'])
    if not checkpoint.is_file():
        raise ValueError('SAM3 checkpoint is missing; run setup')
    digest = file_hash(checkpoint)
    if runtime.data.get('checkpoint_sha256') and runtime.data['checkpoint_sha256'] != digest:
        raise ValueError('SAM3 checkpoint checksum mismatch')
    runtime.data['checkpoint_sha256'] = digest
    info['checkpoint_sha256'] = digest
    return info


def plan(args, runtime, output, run_id):
    port = args.port or 18762
    host = '127.0.0.1' if args.host == '0.0.0.0' else args.host
    return LaunchPlan((runtime.python, '-m', 'uni_robot_agent.services.perception.server',
        '--checkpoint', runtime.data['checkpoint'], '--host', args.host, '--port', str(port)),
        f'http://{host}:{port}', {'model': 'sam3'}, runtime.environment, output)

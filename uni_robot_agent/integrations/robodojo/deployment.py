"""Prepare the pinned official runtime and resolve one frozen RoboDojo episode."""
from pathlib import Path

from uni_robot_agent.services.deployment.config import LaunchPlan, Recipe
from uni_robot_agent.services.deployment.runtime import check_python, query_python
from uni_robot_agent.services.deployment.setup import file_hash


def recipe(args):
    return Recipe.load(__package__, 'deployment.toml')


def validate(args):
    if getattr(args, 'seed', None) not in (None, 0):
        raise ValueError('Official RoboDojo currently supports seed 0 with an explicit frozen layout')
    if args.command != 'setup' and not args.task:
        raise ValueError('Local RoboDojo deployment requires --task')
    if getattr(args, 'camera_calibration', None) is False and getattr(args, 'depth', None) is not False:
        raise ValueError('--no-camera-calibration requires --no-depth')
    if getattr(args, 'runtime_profile', None):
        raise ValueError('RoboDojo does not use --runtime-profile')


def external_data(args):
    if not args.native_root:
        raise ValueError('An external RoboDojo runtime requires --native-root')
    native = args.native_root.expanduser().resolve()
    assets = (args.assets_dir or native / 'Assets').expanduser().resolve()
    return dict(native_root=str(native), assets_dir=str(assets))


def prepare(context):
    config = context.recipe.settings
    native = Path(context.step('source', lambda: context.git(config['repository'], config['revision'],
                                                           context.root / 'source', submodules=True)))
    if context.args.assets_dir:
        assets = context.args.assets_dir.expanduser().resolve()
    else:
        download = Path(context.step('assets', lambda: context.snapshot(config['asset_repository'],
            config['asset_revision'], context.root / 'assets', patterns=['Assets/**'])))
        assets = download / 'Assets'
    link = native / 'Assets'
    if not link.exists():
        link.symlink_to(assets, target_is_directory=True)
    elif link.resolve() != assets:
        raise ValueError('Native Assets path differs from the prepared asset directory')
    context.step('torch', lambda: context.pip(*config['torch'], options=['--index-url', config['torch_index']]))
    context.step('isaacsim', lambda: context.pip('isaacsim[all,extscache]==5.1.0',
                                              options=['--extra-index-url', 'https://pypi.nvidia.com']))
    context.step('dependencies', lambda: context.pip('-r', native / 'scripts/requirements.txt', *config['packages']))
    context.step('isaaclab', lambda: context.pip(native / 'third_party/IsaacLab/source/isaaclab',
                                               native / 'third_party/IsaacLab/source/isaaclab_assets'))
    context.step('curobo', lambda: context.pip(str(native / 'third_party/curobo') + '[cu12]',
                                            options=['--no-build-isolation']))
    context.data.update(native_root=str(native), assets_dir=str(assets))
    context.environment.update(XDG_CACHE_HOME=str(context.root / 'cache'),
                               XDG_DATA_HOME=str(context.root / 'data'),
                               XDG_CONFIG_HOME=str(context.root / 'config'))


def verify(runtime):
    info = check_python(runtime.python, ['scipy', 'trimesh'], environment=runtime.environment)
    native, assets = Path(runtime.data['native_root']), Path(runtime.data['assets_dir'])
    if (native / 'Assets').resolve() != assets.resolve():
        raise ValueError('The official checkout must resolve Assets to the prepared assets')
    for directory in ('Robots', 'Object', 'Material', 'Eval_Layout'):
        if not (assets / directory).is_dir():
            raise ValueError(f'Missing RoboDojo asset directory: {assets / directory}')
    code = ('import json,sys,importlib.util; from pathlib import Path; '
            'from uni_robot_agent.integrations.robodojo.environment.native_compat import resolve_native_root; '
            'root=resolve_native_root(Path(sys.argv[1])); '
            'names=("isaacsim","isaaclab","curobo"); '
            'missing=[n for n in names if importlib.util.find_spec(n) is None]; '
            'assert not missing,missing; print(json.dumps(dict(native_root=str(root))))')
    info.update(query_python(runtime.python, code, native, environment=runtime.environment))
    return info


def plan(args, runtime, output, run_id):
    native, assets = Path(runtime.data['native_root']), Path(runtime.data['assets_dir'])
    task = args.task
    if not task or Path(task).name != task or task in ('.', '..'):
        raise ValueError('RoboDojo task must be a native task name')
    if not (native / 'task/RoboDojo/config' / (task + '.yml')).is_file():
        raise ValueError(f'Unknown RoboDojo task: {task}')
    layout = (args.layout or assets / 'Eval_Layout/RoboDojo/arx_x5/0' / f'{task}_0.json').resolve()
    if not layout.is_file():
        raise ValueError(f'Frozen layout not found: {layout}; prepare assets or pass --layout')
    digest = file_hash(layout)
    if args.layout_sha256 and digest != args.layout_sha256:
        raise ValueError('Layout SHA256 mismatch')
    port = args.port or 18760
    command = [runtime.python, '-m', 'uni_robot_agent.integrations.robodojo.environment.server',
        '--native-root', str(native), '--task', task, '--layout', str(layout), '--layout-sha256', digest,
        '--output-dir', str(output / 'environment'), '--run-id', run_id, '--host', args.host, '--port', str(port)]
    if args.depth is False:
        command.append('--no-depth')
    if getattr(args, 'camera_calibration', None) is False:
        command.append('--no-camera-calibration')
    connect_host = '127.0.0.1' if args.host == '0.0.0.0' else args.host
    return LaunchPlan(tuple(command), f'http://{connect_host}:{port}',
                      {'integration': 'robodojo', 'episode_id': run_id, 'task': task,
                       'environment.metadata.layout_sha256': digest}, runtime.environment, output)

"""Resolve RoboUse tasks into isolated backend runtimes and existing workers."""
import json
from pathlib import Path

from uni_robot_agent.services.deployment.config import LaunchPlan, Recipe
from uni_robot_agent.services.deployment.runtime import check_python, query_python, runtime_root

PROFILES = ('mujoco', 'libero', 'robocasa', 'maniskill_cpu', 'maniskill_gpu', 'genesis', 'factory')
CAMERAS = {'tabletop': ['front', 'top'], 'metaworld': ['corner', 'topview'], 'gymrobotics': ['default'],
           'kitchen': ['front'], 'libero': ['agentview', 'robot0_eye_in_hand'], 'robosuite': ['agentview'],
           'menagerie': ['workspace', 'top'], 'components': ['overview'],
           'maniskill': ['render_camera', 'base_camera'], 'robocasa': ['robot0_agentview_center']}


def profile_for(spec):
    backend = spec['backend']
    if backend == 'gpu':
        return {'maniskill': 'maniskill_gpu', 'genesis': 'genesis', 'isaaclab': 'factory'}[spec['sim']]
    return {'libero': 'libero', 'robocasa': 'robocasa', 'maniskill': 'maniskill_cpu'}.get(backend, 'mujoco')


def recipe(args):
    profile = args.runtime_profile
    if not profile and args.command != 'setup':
        matches = []
        for candidate in PROFILES:
            item = Recipe.load(__package__, f'recipes/{candidate}.toml')
            manifest = runtime_root(args, item) / 'runtime.json'
            if manifest.is_file():
                tasks = json.loads(manifest.read_text())['data'].get('tasks', {})
                if getattr(args, 'task', None) in tasks:
                    matches.append(candidate)
        if len(matches) == 1:
            profile = matches[0]
        elif len(matches) > 1:
            raise ValueError('Ambiguous task runtime; select --runtime-profile')
    profile = profile or 'mujoco'
    if profile not in PROFILES:
        raise ValueError(f'Unknown RoboUse runtime profile: {profile}')
    return Recipe.load(__package__, f'recipes/{profile}.toml')


def validate(args):
    if args.runtime_profile and args.runtime_profile not in PROFILES:
        raise ValueError(f'--runtime-profile must be one of {PROFILES}')
    if args.command != 'setup' and not (getattr(args, 'task', None) or args.task_dir):
        raise ValueError('RoboUse local deployment requires --task or --task-dir')
    if getattr(args, 'layout', None):
        raise ValueError('RoboUse initial state belongs to its task specification, not --layout')


def external_data(args):
    return dict(profile=args.runtime_profile or 'mujoco',
                worker_python=str(args.worker_python.expanduser().absolute()) if args.worker_python else None)


def prepare(context):
    settings = context.recipe.settings
    if settings.get('torch'):
        context.step('torch', lambda: context.pip(*settings['torch'], options=['--index-url', settings['torch_index']]))
    context.step('dependencies', lambda: context.pip(*settings['packages'],
        options=['--extra-index-url', 'https://pypi.nvidia.com'] if settings['profile'] == 'factory' else ()))
    profile = settings['profile']
    context.data.update(profile=profile, worker_python=str(context.python) if profile not in ('mujoco', 'libero') else None)
    context.environment.update(MUJOCO_GL='egl', PYOPENGL_PLATFORM='egl',
        ROBOUSE_CACHE_DIR=str(context.root / 'assets'), LIBERO_CONFIG_PATH=str(context.root / 'libero-config'))
    # Native packages own their asset discovery; all acquisition stays in setup.
    if profile == 'mujoco':
        context.step('robot-assets', lambda: context.command([str(context.python.parent / 'robouse'),
                     'fetch-assets', '--robot', 'franka_emika_panda', '--robot', 'aloha']))
    # Admitted ManiSkill 3.0.1 tasks use package assets; do not download unrelated datasets.
    if profile == 'robocasa':
        prepare_casa_assets(context)
    if profile == 'libero':
        prepare_libero_assets(context)


def prepare_libero_assets(context):
    settings = context.recipe.settings
    assets = context.step('libero-assets', lambda: context.snapshot(settings['asset_repository'],
        settings['asset_revision'], context.root / 'libero-assets'))
    script = """import importlib.metadata,json,os,sys
from pathlib import Path
import yaml
root=Path(importlib.metadata.distribution('hf-libero').locate_file('libero/libero'))
assets=Path(sys.argv[1])
link=root/'assets'
if not link.exists():
    link.symlink_to(assets, target_is_directory=True)
config=Path(os.environ['LIBERO_CONFIG_PATH'])
config.mkdir(parents=True,exist_ok=True)
(config/'config.yaml').write_text(yaml.safe_dump(dict(benchmark_root=str(root), bddl_files=str(root/'bddl_files'),
    init_states=str(root/'init_files'), datasets=str(root/'../datasets'), assets=str(link))))
"""
    context.step('libero-config', lambda: context.command([str(context.python), '-c', script, assets]))


def prepare_casa_assets(context):
    settings = context.recipe.settings
    # Install pinned sources into this runtime so its native asset directory is writable.
    source = Path(context.step('casa-source', lambda: context.git(settings['repository'], settings['revision'],
                                                               context.root / 'robocasa')))
    suite = context.step('suite-source', lambda: context.git(settings['suite_repository'], settings['suite_revision'],
                                                           context.root / 'robosuite'))
    context.step('casa-install', lambda: context.pip('-e', suite, '-e', source))
    for asset in settings.get('assets', []):
        context.step('asset-' + asset['name'], lambda a=asset: context.download_zip(
            a['url'], a['sha256'], source / 'robocasa/models/assets' / a.get('destination', '')))


def catalog(runtime):
    code = '''import json
from robouse.tasks import bundled_tasks_root
from uni_robot_agent.integrations.robouse.tasks import load
result={}
for path in sorted(bundled_tasks_root().rglob('task.md')):
    try:
        task, instruction, digest=load(path.parent)
        result[task.id]=dict(path=str(path.parent),spec=task.spec,digest=digest)
    except (ValueError, KeyError, ImportError):
        continue
print(json.dumps(result))
'''
    return query_python(runtime.python, code, environment=runtime.environment)


def verify(runtime):
    info = check_python(runtime.python, ['robouse', 'scipy'], environment=runtime.environment)
    profile = runtime.data['profile']
    recipe_settings = Recipe.load(__package__, f'recipes/{profile}.toml').settings
    info.update(check_python(runtime.data.get('worker_python') or runtime.python,
                             recipe_settings.get('imports', []), environment=runtime.environment))
    tasks = {key: value for key, value in catalog(runtime).items() if profile_for(value['spec']) == profile}
    runtime.data['tasks'] = tasks
    info['task_count'] = len(tasks)
    return info


def plan(args, runtime, output, run_id):
    path = args.task_dir
    if path is None:
        tasks = runtime.data.get('tasks') or catalog(runtime)
        item = tasks.get(args.task)
        if item is None:
            raise ValueError(f'Unknown bundled RoboUse task {args.task!r}; supply --task-dir for external tasks')
        path = Path(item['path'])
    path = path.expanduser().resolve()
    code = ('import json,sys; from uni_robot_agent.integrations.robouse.tasks import load; '
            'task,instruction,digest=load(sys.argv[1]); '
            'print(json.dumps(dict(id=task.id,spec=task.spec,digest=digest)))')
    task = query_python(runtime.python, code, path, environment=runtime.environment)
    spec = task['spec']
    if args.task and args.task != task['id']:
        raise ValueError('--task does not match --task-dir')
    if profile_for(spec) != runtime.data['profile']:
        raise ValueError(f'Task requires --runtime-profile {profile_for(spec)}')
    backend = spec['backend']
    cameras = args.cameras or (['scene', 'close'] if spec.get('sim') == 'isaaclab' else
                              ['scene', 'front'] if backend == 'gpu' else CAMERAS[backend])
    port = args.port or 18780
    command = [runtime.python, '-m', 'uni_robot_agent.integrations.robouse.environment.server',
        '--task-dir', str(path), '--backend', backend, '--output-dir', str(output / 'environment'),
        '--run-id', run_id, '--host', args.host, '--port', str(port), '--cameras', *cameras,
        '--depth' if args.depth else '--no-depth']
    if spec.get('sim'):
        command.extend(['--sim', spec['sim']])
    if runtime.data.get('worker_python'):
        command.extend(['--worker-python', runtime.data['worker_python']])
    if args.seed is not None:
        command.extend(['--seed', str(args.seed)])
    connect_host = '127.0.0.1' if args.host == '0.0.0.0' else args.host
    return LaunchPlan(tuple(command), f'http://{connect_host}:{port}',
        {'integration': 'robouse', 'episode_id': run_id, 'metadata.task_id': task['id'],
         'metadata.task_sha256': task['digest']}, runtime.environment, output)

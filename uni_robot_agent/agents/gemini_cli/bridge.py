"""Apply the explicit ACP compatibility patch and seal a separately built bundle.

Run in a disposable, reviewed Gemini source checkout. This command does not
download dependencies, build the CLI, or modify a globally installed runtime.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from .manifest import BRIDGE_ID, bundle_inventory

SOURCE = Path('packages/cli/src/acp/acpSession.ts')
HELPER = SOURCE.with_name('robotBridge.ts')
IMPORT = "import { callRobotTool } from './robotBridge.js';\n"
PATTERN = re.compile(r'const toolResult: ToolResult = await invocation\.execute\(\{\s*abortSignal,?\s*\}\);')
REPLACEMENT = """const toolResult: ToolResult =
        tool instanceof DiscoveredMCPTool && tool.serverName === 'ura'
          ? await callRobotTool(tool, args, callId, abortSignal)
          : await invocation.execute({ abortSignal });"""


def patch_source(source: str) -> str:
    if IMPORT in source or 'DiscoveredMCPTool' not in source or len(PATTERN.findall(source)) != 1:
        raise ValueError('Gemini ACP source does not match the reviewed compatibility seam')
    return IMPORT + PATTERN.sub(REPLACEMENT, source)


def git(checkout: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(checkout), *args], text=True).strip()


def apply(checkout: Path, revision: str) -> None:
    if not re.fullmatch(r'[0-9a-f]{40}', revision) or git(checkout, 'rev-parse', 'HEAD') != revision:
        raise ValueError('Specify the exact reviewed 40-character Gemini source revision')
    if git(checkout, 'status', '--porcelain'):
        raise ValueError('Apply the compatibility patch to a clean disposable checkout')
    path = checkout / SOURCE
    original = path.read_text()
    patched = patch_source(original)
    helper = Path(__file__).with_name('robotBridge.ts').read_bytes()
    path.write_text(patched)
    (checkout / HELPER).write_bytes(helper)
    (checkout / 'ura-bridge-source.json').write_text(json.dumps({
        'bridge': BRIDGE_ID, 'revision': revision,
        'original_sha256': hashlib.sha256(original.encode()).hexdigest(),
        'patched_sha256': hashlib.sha256(patched.encode()).hexdigest(),
        'helper_sha256': hashlib.sha256(helper).hexdigest(),
    }, indent=2) + '\n')


def seal(checkout: Path, binary: Path, version: str, output: Path) -> None:
    source = json.loads((checkout / 'ura-bridge-source.json').read_text())
    if source.get('bridge') != BRIDGE_ID or git(checkout, 'rev-parse', 'HEAD') != source['revision']:
        raise ValueError('Gemini source revision changed after patching')
    for path, key in [(SOURCE, 'patched_sha256'), (HELPER, 'helper_sha256')]:
        if hashlib.sha256((checkout / path).read_bytes()).hexdigest() != source[key]:
            raise ValueError('Gemini compatibility source changed after patching')
    root = checkout.resolve() / 'bundle'
    if binary.is_symlink() or binary.resolve().parent != root or not binary.is_file():
        raise ValueError('Seal the entry point directly inside the built bundle directory')
    if output.resolve().is_relative_to(root):
        raise ValueError('Write the bridge manifest outside the sealed bundle directory')
    files = bundle_inventory(root)
    actual = subprocess.check_output([str(binary.resolve()), '--version'], text=True, timeout=30).strip()
    if actual != version:
        raise ValueError('Built Gemini version differs from the requested version')
    output.write_text(json.dumps({**source, 'format': 2, 'version': version,
        'entrypoint': binary.name, 'files': files}, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('apply')
    prepare.add_argument('checkout', type=Path)
    prepare.add_argument('--revision', required=True)
    finish = commands.add_parser('seal')
    finish.add_argument('checkout', type=Path)
    finish.add_argument('--binary', type=Path, required=True)
    finish.add_argument('--version', required=True)
    finish.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'apply':
        apply(args.checkout, args.revision)
    else:
        seal(args.checkout, args.binary, args.version, args.output)


if __name__ == '__main__':
    main()

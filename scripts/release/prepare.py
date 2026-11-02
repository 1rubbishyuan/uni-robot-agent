"""Build and check release archives without uploading anything.

Run from a source checkout with build and twine installed. The output directory
must be new so that each manifest describes exactly one candidate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tomllib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--tag', help='Require this release tag to match the package version')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    version = project['version']
    if args.tag is not None and args.tag != f'v{version}':
        parser.error(f'Release tag must be v{version}, got {args.tag!r}')
    output = args.output_dir.resolve()
    if output.exists():
        parser.error('Output directory already exists; choose a new candidate directory')
    output.mkdir(parents=True)
    dist = output / 'dist'

    def run(*command):
        subprocess.run([str(value) for value in command], cwd=root, check=True)

    # The default build makes the wheel from the sdist, exercising both archives.
    run(sys.executable, '-m', 'build', '--outdir', dist)
    stem = project['name'].replace('-', '_')
    sdist = dist / f'{stem}-{version}.tar.gz'
    wheel = dist / f'{stem}-{version}-py3-none-any.whl'
    run(sys.executable, root / 'scripts/validation/release_contents.py',
        '--sdist', sdist, '--wheel', wheel)
    run(sys.executable, '-m', 'twine', 'check', '--strict', sdist, wheel)
    archives = [sdist, wheel]
    digests = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in archives}
    (output / 'SHA256SUMS').write_text(''.join(
        f'{digest}  dist/{name}\n' for name, digest in digests.items()))
    manifest = {
        'name': project['name'], 'version': version,
        'checks': ['sdist-to-wheel build', 'release contents', 'twine check --strict'],
        'installation_check': 'Run scripts/validation/sdk_release.py separately',
        'files': {name: {'sha256': digests[name], 'bytes': (dist / name).stat().st_size}
                  for name in digests},
        'published': False,
    }
    (output / 'release.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Release candidate prepared: {output}. Nothing was uploaded.')


if __name__ == '__main__':
    main()

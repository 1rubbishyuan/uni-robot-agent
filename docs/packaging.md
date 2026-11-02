# Packaging and installation checks

The distribution and CLI are `uni-robot-agent`; imports use `uni_robot_agent`.
The standard installation includes the pinned Codex and Claude SDKs, Textual,
prompt templates, and skills. Native simulators, checkpoints, hardware runtimes,
and deployment scripts remain separate from the wheel.

These checks cover package contents and installation. Validate native integrations
separately on their target platforms. During documentation and history cleanup,
these builds are previews; rebuild from the final public commit before publishing
as described in the [release sequence](publishing.md#first-release-sequence).

## Prepare a release candidate without publishing

With `build` and `twine` installed, run from a source checkout:

```bash
python scripts/release/prepare.py --output-dir ../uni-robot-agent-0.1.0-candidate
```

The output directory must not already exist. It receives `dist/` with the sdist
and wheel, `SHA256SUMS`, and `release.json`. The command builds the wheel from the
sdist, verifies contents, and runs `twine check --strict`. It never uploads or
creates a Git tag. Add `--tag v0.1.0` to verify the planned release tag matches
`pyproject.toml`. Complete the fresh installation checks below before release.
See [Publishing](publishing.md) for CI, account setup and the separate upload step.

## Build and validate

From the repository root, with the development extra installed:

```bash
python -m build
```

The build writes a source distribution and wheel to `dist/`. Building the wheel
from the source distribution catches missing source-package resources. Verify
content against the checkout before installing:

```bash
python scripts/validation/release_contents.py \
  --sdist dist/uni_robot_agent-0.1.0.tar.gz \
  --wheel dist/uni_robot_agent-0.1.0-py3-none-any.whl
```

The check compares public source files and packaged resources byte-for-byte,
including SDK examples, service configurations, script dependencies and guides.
Run it from the source checkout so omitted files remain detectable. Then run the
standalone installation checks below.

## Check standalone installers

Use disposable environments outside the checkout, with enough free space for the
SDKs and their dependencies. Do not duplicate simulator environments or model
directories. Substitute the absolute wheel path below:

```bash
python3 -m venv ../uni-robot-agent-pip-check
../uni-robot-agent-pip-check/bin/python -m pip install /absolute/path/to/uni_robot_agent-0.1.0-py3-none-any.whl

UV_TOOL_DIR="$PWD/../uni-robot-agent-uv-check/tools" \
UV_TOOL_BIN_DIR="$PWD/../uni-robot-agent-uv-check/bin" \
  uv tool install /absolute/path/to/uni_robot_agent-0.1.0-py3-none-any.whl
```

Outside the checkout, run each installed executable with `--version`, `--help`,
and the offline configuration command:

```bash
uni-robot-agent run --integration robodojo --model test-model --env-url http://127.0.0.1:18760 \
  --action-profile direct --no-sam3 --validate-config
```

For the disposable installs, use the full executable paths from the pip and uv
directories above. On a shared cluster, put both check environments and package
caches on its permitted persistent storage; set `TMPDIR` there before installing. Check the pip environment's `python -m uni_robot_agent
--version` as well. These commands do not contact a model or simulator. Exercise
skill listing and reading with the bundled library, then use the
[external skills setup](running.md#skills) to verify that writes affect only that
library and are visible to subsequent reads and listings.

Remove only the disposable build and installer directories created for the check
when finished. Reuse prepared test environments when disk space is limited.

## Release status

Install from source or a built wheel. Commands such as `pip install uni-robot-agent`
require a published package on your configured index. Building or installing
locally does not publish it.

### Deployment resources

The wheel and sdist include integration/service deployment TOML recipes. Native
repositories, simulator assets, actor weights and SAM3 checkpoints stay outside
the distribution in the explicit runtime cache. The main distribution provides
the `robouse.backends:uni_robot_agent_worker` entry point. Install the agent package
in every native worker interpreter; workers load it from that installation.
Pass your local build with `setup --wheel`; see [local deployment](deployment.md)
for setup and validation.

## SDK release validation

The wheel includes public Python APIs and `py.typed` alongside the CLI entry point.
For a full dependency installation and offline SDK smoke outside the checkout:

```bash
python scripts/validation/sdk_release.py /path/to/package.whl /path/to/new-pip-check
python scripts/validation/sdk_release.py /path/to/package.whl /path/to/new-uv-check --installer uv
```

Use `--wheelhouse /path/to/wheels` for offline dependency resolution. The validator
uses fresh virtual environments, checks the CLI entry point, public imports,
offline Session configuration and deployment/Web resources without connecting
to a model or environment. Validate HTTP execution and native runtimes separately.

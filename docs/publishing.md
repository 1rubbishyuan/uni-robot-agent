# Preparing and publishing a release

Distribution: `uni-robot-agent`. Import: `uni_robot_agent`. Current candidate:
`0.1.1`. Building a candidate, pushing a branch, or creating a tag does not upload
this project to a package index.

## First-release sequence

1. Finalize README, installation, SDK and deployment guides. Package-name install
   commands can be prepared before the package is uploaded; retain the source
   preview notice until the release commit is ready.
2. Review and clean the intended public Git history, then migrate the approved
   source tree to its final repository and branch. Preserve required attribution
   and licensing. Keep this work separate from publication and coordinate any
   history rewrite with collaborators.
3. Check repository URLs, README image/documentation links, and the Trusted
   Publisher binding against the final destination. Put the workflow on the
   repository's default branch and in the intended release commit.
4. Build and validate again from that final commit, create its version tag, then
   explicitly publish. Keep the released commit and tag stable afterward.
5. Verify installation from PyPI and add the release announcement or badge.

Build checks can run during steps 1 and 2, but artifacts made before the final
migration are preview artifacts. `docs/pypi.md` is embedded in package metadata:
editing it later does not update the uploaded PyPI description. Make its
substantive changes before the final build; see the
[PyPI README guide](https://packaging.python.org/en/latest/guides/making-a-pypi-friendly-readme/).

## Prepare without publishing

Use Python 3.11+ and a source checkout containing the intended release changes:

```bash
python -m pip install 'build>=1' 'twine>=6' 'tomli-w>=1'
python scripts/release/prepare.py --tag v0.1.1 \
  --output-dir ../uni-robot-agent-0.1.1-candidate
python scripts/validation/sdk_release.py \
  ../uni-robot-agent-0.1.1-candidate/dist/uni_robot_agent-0.1.1-py3-none-any.whl \
  ../uni-robot-agent-0.1.1-install-check
```

Choose new directories for each candidate/check. `prepare.py` builds independent source
distributions for `uni-robot-agent` and `uni-robot-agent-runtime`, then builds each wheel from its sdist, compares archive contents with source,
checks PyPI metadata/README rendering syntax, and writes SHA-256 checksums plus a
JSON manifest outside `dist/`. It has no upload operation. The installation check
uses a fresh environment outside the checkout and does not start a model, GPU,
robot or simulator. Native integration acceptance is a separate validation.

The GitHub workflow `.github/workflows/release.yml` runs the same build and
installation checks on pull requests and pushes to `main` or
`release/open-source`. Its installation matrix covers Linux Python 3.11/3.12,
macOS Python 3.12, pip, uv, an isolated uv CLI install, upgrade from 0.1.0,
and the native dependency/HTTP contract under Isaac-compatible constraints. Archives and the
manifest are downloadable workflow artifacts.

A manual run with `publish_to=none` also only checks and saves artifacts. No
automatic trigger has permission to upload to PyPI. A tag push does not trigger
the workflow. Inspect the actual CI results before claiming a platform passed.

## One-time account and repository setup

Use [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
so the workflow can obtain a short-lived upload credential without storing a token.
Register and verify your PyPI email, enable 2FA, and save recovery codes.

Before the first upload, open [PyPI account publishing](https://pypi.org/manage/account/publishing/)
and add a pending GitHub publisher with these exact values:

| Field | Value |
|---|---|
| PyPI project name | `uni-robot-agent` |
| Owner | `Open-RUA` |
| Repository | `uni-robot-agent` |
| Workflow filename | `release.yml` (no directory prefix) |
| Environment | `pypi` |

For 0.1.1, also register **`uni-robot-agent-runtime`** with the same owner,
repository, workflow and environment. Both projects need a Trusted Publisher.
The workflow uploads runtime archives first, then the main package. If the second
upload fails, rerun the same release tag; the runtime upload skips existing files.
Never rebuild a changed source tree under an already published version.

The repository identity must match the actual GitHub repository that runs the
workflow. Update these fields if the repository moves. A pending publisher does
not upload a package or reserve the project name. First successful publication
creates the project and converts the pending publisher to a normal publisher.

Create a GitHub environment named `pypi` in the repository settings. Limit its
deployment refs to release tags and, where available, configure required
reviewers. The workflow's upload job alone has `id-token: write`; build and
installation jobs have read-only repository permissions.

For a rehearsal, register a separate account/publisher on
[TestPyPI](https://test.pypi.org/manage/account/publishing/) using the same project,
owner, repository and workflow, but environment `testpypi`. Create a matching
GitHub environment. TestPyPI and PyPI have separate accounts and publisher
configuration. Do not upload to either service as part of preparation alone.

The workflow must exist on the GitHub repository's **default branch** before
`workflow_dispatch` can be used. It must also be present in the release tag's
commit. The default branch is `main`.

## Finalize the candidate

Before choosing to publish:

1. Review and commit the exact release changes. Resolve any unrelated working
   tree changes with their owners; never bundle them implicitly.
2. Confirm `pyproject.toml`'s version and the intended `vVERSION` tag match. Set
   the changelog release date, remove the first-publication pending notices from
   README and installation/publishing guides, and replace the source-preview
   status in README.
   Keep the source/wheel instructions for developers and unreleased builds.
3. Check the links in `docs/pypi.md` and package metadata without authentication.
   The package description uses the public documentation website and does not
   depend on private GitHub images or links. GitHub's README keeps relative
   images for repository viewers. `twine check` validates markup, not remote
   link accessibility. GitHub can remain private, but both the wheel and sdist
   published to PyPI expose their included Python source to everyone.
4. Review successful package checks and any promised native integration evidence.
   A package smoke test does not establish simulator or real-hardware success.

## Explicit publication

This section uploads publicly. Execute it only when the release is authorized.
Create `v0.1.1` on the reviewed commit and push that tag. Creating the tag alone
does not publish. For the final browser confirmation, open the repository's Actions tab, select
**Package checks and manual release**, and open **Run workflow**. Select tag
`v0.1.1` and destination `pypi`. Clicking the final **Run workflow** button starts
public publication after the checks pass; stop before that button during release
preparation.

Alternatively, from the authenticated repository context, dispatch:

```bash
# Optional rehearsal: this uploads to TestPyPI.
gh workflow run release.yml --repo Open-RUA/uni-robot-agent \
  --ref v0.1.1 -f publish_to=testpypi

# Production release: this uploads to PyPI.
gh workflow run release.yml --repo Open-RUA/uni-robot-agent \
  --ref v0.1.1 -f publish_to=pypi
```

Choose only the intended command. Each run builds and checks the selected tag,
then uploads those exact archives after all installation jobs pass. Branch refs
or a tag that does not match `pyproject.toml` fail before uploading. The uploader
downloads artifacts from the same run; it does not rebuild them or check out
source in the credential-bearing job. Complete any GitHub environment approval
configured by the project owner and verify the upload job succeeds.

Versioned filenames cannot be replaced on PyPI. Publish fixes under a new version
such as `0.1.2`; deleting a release does not make its filenames reusable.

## Verify the published package

In a new virtual environment, away from a source checkout:

```bash
python -m pip install --index-url https://pypi.org/simple uni-robot-agent==0.1.1
python -m pip check
uni-robot-agent --version
python -c 'from uni_robot_agent import Session, SessionConfig, run'
uni-robot-agent run --integration robodojo --model validation-model \
  --env-url http://127.0.0.1:18760 --no-sam3 --validate-config
```

For TestPyPI, download only this project from that index, then resolve its
dependencies from normal PyPI when installing the downloaded wheel:

```bash
python -m pip download --index-url https://test.pypi.org/simple --no-deps \
  --only-binary=:all: --dest candidate-download uni-robot-agent==0.1.1
python -m pip install --index-url https://pypi.org/simple \
  candidate-download/uni_robot_agent-0.1.1-py3-none-any.whl
```

On a prepared Linux GPU host, separately validate `setup` without `--wheel` and
a real task. That path must fetch the same published harness version into the
native runtime as `uni-robot-agent-runtime==0.1.1`. TestPyPI installation of the outer CLI alone does not redirect
setup's pip index. Simulators, assets, model credentials and GPU drivers remain
separate prerequisites; see [Deployment](deployment.md).

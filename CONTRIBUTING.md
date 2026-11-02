# Contributing

Contributions to uni-robot-agent are welcome. The distribution and CLI are
`uni-robot-agent`; Python imports use `uni_robot_agent`.

## Agent-assisted development

Start with [AGENTS.md](AGENTS.md) and the
[development boundaries and extension guide](docs/development.md). They define
directory ownership, the environment/tool/service boundary, and compatibility
checks for new robots and planners. `CLAUDE.md` imports the same instructions
for Claude Code; maintain the rules in one place. Read these before adapting a
standalone native implementation into the harness.

## Development setup

Clone the public source branch first:

```bash
git clone https://github.com/Open-RUA/uni-robot-agent.git
cd uni-robot-agent
```

Use a dedicated environment and run the following commands from the repository root.
See [Getting started](docs/getting-started.md) for platform notes and external
service requirements. Use Python 3.11+:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,robodojo]'
uni-robot-agent run --integration robodojo --model test-model \
  --env-url http://127.0.0.1:18760 --no-sam3 --validate-config
python -m build
```

Follow [packaging checks](docs/packaging.md) to verify release contents, standalone
installation and offline SDK configuration. Add focused tests with new behavior
and keep them under `tests/`. Native simulators, model inference and hardware need
separate runtime validation. A successful package build is not benchmark evidence.

For a release candidate, use the [build-only preparation command](docs/packaging.md#prepare-a-release-candidate-without-publishing).
Release maintainers should follow [the first-release sequence](docs/publishing.md#first-release-sequence):
finish documentation, finalize public history and repository layout, then build
and publish from the final commit.

## Changes and pull requests

- Keep each change focused; explain the problem, resulting behavior and validation.
- Use English for comments, docstrings, user messages and documentation.
- Keep shared tool semantics in `uni_robot_agent/tools/`, environment and embodiment
  behavior in `uni_robot_agent/integrations/`, transport in `uni_robot_agent/services/`,
  and SDK-specific behavior in `uni_robot_agent/agents/`. Follow the [architecture](docs/architecture.md) and
  [environment integration contracts](docs/integrations.md#integration-contract).
- Preserve physical-action ordering, resource ownership and no-replay guarantees.
  Keep tool completion distinct from task success and official environment outcomes.
- Treat prompts, skills and model-visible feedback as behavior changes; identify
  their experimental impact separately from documentation or formatting edits.
- Run tests relevant to the change. Check package builds when dependencies,
  metadata or packaged resources change; update the changelog for user-facing changes.
- Preserve upstream licenses and attribution when adapting external code.

## Reporting issues

Include the revision, Python/platform, installation extras, relevant configuration,
minimal reproduction and expected versus observed behavior. Remove credentials
and private data from logs; a complete experiment directory is usually unnecessary.

## License

uni-robot-agent is released under the [MIT License](LICENSE). Contributions are made
under the same license. Preserve upstream notices for adapted code, including
the [RoboDojo](uni_robot_agent/integrations/robodojo/UPSTREAM_LICENSE) notices.
External simulators, assets, and checkpoints retain their own licenses.

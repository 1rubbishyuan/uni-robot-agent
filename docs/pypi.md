# uni-robot-agent

A unified harness for robot-use agents, with Codex and Claude Code runtimes,
a Python SDK, shared robot tools, and a local monitoring dashboard.

[Documentation](https://uni-robot-agent.com/docs) · [Project website](https://uni-robot-agent.com/)

## Install

Use Python 3.11+ in a virtual environment:

```bash
pip install uni-robot-agent
# or: uv pip install uni-robot-agent
uni-robot-agent --version
```

For an isolated CLI, use `uv tool install uni-robot-agent`. For the SDK in a uv
project, use `uv add uni-robot-agent`.

## Run a task

On a Linux GPU host with Python 3.11, prepare RoboDojo once, then run a task:

```bash
uni-robot-agent setup --integration robodojo

export UNI_ROBOT_AGENT_API_KEY='YOUR_API_KEY'
export UNI_ROBOT_AGENT_PROVIDER_URL='https://provider.example/v1'
uni-robot-agent run --integration robodojo --deploy local \
  --task stack_bowls --model YOUR_MODEL_ID \
  --action-profile direct --no-sam3 --output-dir ./runs --monitor
```

Set your vision model and Responses-compatible endpoint. Open the printed
dashboard URL to follow execution, or replay results later with
`uni-robot-agent monitor --run-dir ./runs`.

## Python SDK

Connect to an already running environment service using the same credentials:

```python
from uni_robot_agent import AgentConfig, SessionConfig, run

result = run(SessionConfig(
    integration="robodojo",
    env_url="http://127.0.0.1:18760",
    no_sam3=True,
    agent=AgentConfig(
        model="YOUR_MODEL_ID",
        provider_url="https://provider.example/v1",
    ),
    output_dir="runs",
))
print(result.agent_finished, result.task_success, result.session_dir)
```

The package includes prompts, skills, deployment recipes and dashboard assets.
Simulator runtimes, assets, GPU drivers and model weights are prepared separately;
see the documentation for host requirements. macOS supports the CLI and SDK with
remote environment services and local replay. Physical-robot workflows are not
part of this initial release.

Released under the MIT License. Bundled upstream components and external assets
retain their respective licenses.

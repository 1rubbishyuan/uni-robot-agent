# uni-robot-agent documentation

uni-robot-agent is a harness for robot-use agents built around **Unified**,
**Efficient**, **Realistic** and **Modular** interaction. Agents observe, plan
and act through shared robot tools. Install its CLI
and Python SDK, prepare or connect to an environment, run the agent on a task,
and inspect the results. The command is `uni-robot-agent`; Python imports
use `uni_robot_agent`.

## Get started

Choose [local setup](deployment.md) if you need to prepare a simulator, or
[connect to an existing episode](getting-started.md#run-a-robodojo-episode) if its
service is already available. Use the [source or wheel installation](getting-started.md#install-from-source-or-a-wheel)
during the source preview; package-name installation becomes available with the
first PyPI release.

- **[Runnable examples](../examples/README.md)** — one task with RoboDojo, RoboLab
  or RoboUse, including environment preparation and offline configuration checks.
- **[Installation and first run](getting-started.md)** — install a published
  package, source checkout or wheel; validate offline, prepare a runtime and run a task.
- **[Local setup and deployment](deployment.md)** — prepare RoboDojo/RoboUse,
  start environment, actor or SAM3 independently, and understand ownership.
- **[Python SDK](sdk.md)** — custom strategies, built-in agents, tool jobs, events,
  results and explicit preparation/deployment; [examples](../examples/sdk/).

## Run and inspect

- **[CLI reference](running.md#single-episode-cli)** — provider access, context,
  scheduling, budgets and integration-specific options.
- **[Claude Code](getting-started.md#use-the-claude-agent)** — Messages provider
  configuration and backend-specific options.
- **[Interactive terminal](getting-started.md#view-one-task-in-the-terminal)** —
  follow a prepared episode in the TUI.
- **[Web monitor and directory replay](running.md#local-web-monitor)** — live
  cameras, tool batches and queues, synchronized replay and multiple-run cards.
- **[Run artifacts](getting-started.md#inspect-the-result)** — native outcomes,
  trajectories, image archives, provider traces and training exports.
- **[Scripts](running.md#scripts)** — service launchers and environment-specific
  examples for single-task runs.

## Understand and extend

- **[Architecture](architecture.md)** — the [four design principles](architecture.md#design-principles),
  execution flow, service ownership and the
  [system diagram](../assets/diagrams/uni-robot-agent-architecture.png).
- **[Realistic observation settings](integrations.md#realistic-observation-settings)** —
  the paper's input constraints, RGB-only RoboDojo configuration and optional depth capabilities.
- **[Services](services.md)** — environment, actor and perception contracts.
- **[Integrations](integrations.md)** — adapters, capabilities and extension points.
- **[RoboDojo source notes](../uni_robot_agent/integrations/robodojo/SOURCE.md)** —
  pinned runtime and upstream provenance.
- **[RoboLab](robolab.md)** — native installation, connection and validation notes.
- **[Model providers](providers.md)** — profiles and wire protocol compatibility.
- **[Development boundaries](development.md)** — ownership, extension recipes and
  validation; [coding-agent instructions](../AGENTS.md).
- **[Packaging](packaging.md)** — build and verify standalone installations.
- **[Publishing](publishing.md)** — finish documentation, finalize public history
  and migration, then build and explicitly publish from the final commit.
- **[Contributing](../CONTRIBUTING.md)** and **[Changelog](../CHANGELOG.md)**.

[Back to uni-robot-agent](../README.md)

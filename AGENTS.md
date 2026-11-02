# Development instructions

These instructions apply to agents developing this repository. They are not
robot-task instructions, runtime skills, or permission to operate hardware.
When running a robot episode, use its assembled prompt and registered tools;
do not treat this document as a request to edit code during the episode.

## Before changing code

- Check the checkout, branch, status, and relevant diff. Preserve other work;
  stage only task-owned changes when a commit is requested.
- Read [the architecture and diagram](docs/architecture.md), then
  [development boundaries](docs/development.md) for the affected layer.
- For environment work, also read [integration contracts](docs/integrations.md).
  For planner SDK work, read [agent integration](docs/agents.md).
- Identify the owner of each new behavior before implementing it. Existing
  code is evidence, not permission to copy a boundary violation.

## Architecture and extension points

All components can evolve, including existing gripper tools. Keep each behavior
in its owning layer and make cross-layer contracts explicit. The current tool
families are examples of this architecture, not a closed list of allowed tools.

| Layer | Responsibility |
|---|---|
| `cli.py`, `config.py`, `session.py` | Resolve configuration and assemble/own the run lifecycle |
| `agents/` | Adapt planner SDKs, deliver tool calls through the executor, translate SDK events and records |
| `provider/` | Connect model APIs: gateway, wire conversion, model-request retry and response delivery |
| `context/` | Assemble prompts/history and apply context policies |
| `tools/` | Define model-visible tool schemas, handlers, orchestration, feedback and resource requirements |
| `execution/` | Schedule tool work, enforce resource exclusion and manage background jobs |
| `integrations/` | Compose tool families and adapt native environments, controllers, observations and model/resource bindings |
| `services/` | Own typed service contracts, clients, hosts and environment/model service lifecycles |
| `observation/` | Coordinate observation collection, freshness and observer insights |
| `events/` | Define and distribute shared execution events |
| `artifacts/` | Store run artifacts and support persistence/export |
| `telemetry/` | Record traces, logs and diagnostics |
| `ui/` | Present state/events and route user intent through the session interfaces |
| `resources/` | Package robot-facing prompts and runtime skills |

- To change robot behavior, modify or add tools in an existing tool set, or
  create `tools/<family>/` for a new tool set. Keep reusable behavior there even
  when the first user is one environment. Avoid concrete integration imports;
  accept domain contracts and bindings instead.
- Environments provide native execution/data and compose shared providers;
  they do not define model tools or hide tool-name dispatch inside bindings.
  Reuse existing episode/service infrastructure and extend its contracts when
  needed. Services expose domain operations, not a copy of the model tool API.
- Reuse compatible observation contracts and scene tools. Extend them or add
  a tool family for a different agent capability; native sensor setup belongs
  in the adapter. Expose only capabilities the selected environment supports.
- Keep SDK-specific behavior in `agents/`, upstream wire differences in
  `provider/`, and scheduling in `execution/`. Rendering and logging consume
  shared state/events; they do not implement robot control.
- Keep native dependencies at service/startup boundaries so CLI help and offline
  configuration validation do not require robot or model runtimes.

## Compatibility and validation

- Intentional behavior changes are welcome: document the new contract, affected
  users and any migration, then update tests/docs. For a behavior-preserving
  refactor, compare tool schemas/visibility, prompts, feedback, defaults, budgets,
  ordering and outcomes. Neither existing tools nor public APIs are frozen.
- Check affected callers and unsupported-capability cases when changing shared
  contracts. Scale regression coverage to the change rather than requiring a
  full benchmark for every contribution.
- Preserve command identity, state ownership and receipt reconciliation. A lost
  physical acknowledgement does not justify replaying a motion. Distinguish
  tool completion, agent completion and environment task success.
- Use English for code comments, docstrings, model-facing text, and repository
  documentation. Prefer explicit types and small functions; avoid speculative
  compatibility layers. Follow [Contributing](CONTRIBUTING.md) for setup.
- Run relevant validation and report failures/skips honestly. Follow
  [the validation matrix](docs/development.md#validation-and-review) and add focused
  tests for new behavior. CPU checks are not evidence of real-hardware success.
- Do not put credentials, machine-specific deployment paths, or experiment
  outputs in source. Preserve upstream attribution and native resource formats.

## Code Review Rules

Flag integration-local model tools, generic `execute_tool(name, args)` service
routes, tool-family imports of native integrations, duplicated episode state,
unsupported capabilities advertised to the planner, SDK-specific decisions in
generic scheduling, and robot control implemented in UI/telemetry. Review changes
to shared contracts for affected callers and migration, not for conformity to
an immutable tool list. For migrations, account for retained, replaced and
deliberately removed features.

Maintain shared rules here. `CLAUDE.md` only imports this file. Keep detailed
extension recipes in `docs/development.md` and update them with the contracts.

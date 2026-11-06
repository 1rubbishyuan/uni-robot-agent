# Code as Policy

Select `--interaction-mode code_as_policy` for CLI run/TUI, or
`SessionConfig(..., interaction_mode="code_as_policy")` for the Python SDK.
The default remains `tool_calling`. Both Codex and Claude Code use their native
shell, file editing, and image readers. No Python kernel or new model tool is
introduced. Programs call the same registered tools, arguments, validation and
executor policies as direct tool calling.

```text
Agent -> native shell -> ordinary Python program -> Robot client
      -> existing HTTP MCP -> blocking ToolClient -> session executor -> tools
```

The session provides the task/environment prompt, a generated
`scratch/robot_api.md` containing the actual descriptions and schemas, an
absolute Python executable, scratch cwd and connection environment variables.
Skills, calibration references, observations and robot metadata still come from
the existing tools. The planner does not receive direct robot MCP tools in this
mode. Existing Bash-based geometry/calibration calculations remain possible.

```python
from uni_robot_agent.execution.code_as_policy import Robot

with Robot() as robot:
    # Choose exact names/arguments from this session's robot_api.md.
    observation = robot.tools.view_env_state()
    print(observation.data)
    for image in observation.images:
        print(image)  # The agent opens these paths with its native image reader.
```

Each call waits for the underlying job's completed feedback. `CodeResult.data`
is the original model-visible tool payload; `images` preserves image order and
bytes; `call_id` identifies the actual transport/execution events. The agent
chooses what to print and which images to open. It must inspect errors and
physical feedback before dependent actions. `ToolCallError.result` retains
failed-call feedback. An exception can follow partial execution: rerunning an
entire script is not safe recovery.

Programs use normal imports, functions, loops and files. Python variables last
only for one process; files and the robot episode persist across scripts.
The bridge serializes calls from all programs, admits no new work after cancel
or an accepted finish, and leaves interruption/draining to the original Session.
It never retries robot calls. A transport failure closes the affected client
and reports uncertainty; it does not prove the action stopped. A fresh process
is not permission to repeat an uncertain action. This is an API/lifecycle
boundary in the existing trusted local runtime, not an OS security sandbox.

A successful `finish` submission is recorded from completed tool output, not
from text or a proposed SDK tool call. Agent completion additionally requires
normal SDK termination. The environment remains the authority for official task
success. Events retain individual robot calls even when one shell invocation
runs a loop; SDK progress counts still measure native agent operations.

Compared with a persistent interpreter approach such as PyRUA-Lean, this design
keeps API discovery, programmatic composition and selective feedback, but uses
the agent's existing process/file workflow instead of introducing a persistent
Python namespace. Parallel robot programs, notebook sessions and a separate
robot-control abstraction are deliberately outside this initial implementation.
New agent adapters need only forward `CodePolicyContext` cwd/env to native
processes, revoke admission on cancellation, and use its trusted finish receipt.
They must be validated before being advertised as supported.

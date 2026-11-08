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
`scratch/robot_api.md` containing the actual descriptions, input schemas and
typed return-field documentation, an
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

### Typed feedback and explicit summaries

`CodeResult` adds optional typed access to the existing receipt. For example,
`result.feedback.arms["right"].pose_reached` accesses the same evidence as
`result.data["feedback"]["arms"]["right"]["pose_reached"]` when present.
`result.remaining_steps`, `result.state.native_steps`, `result.state.ended` and
`result.state.state_version` describe progress at that receipt. Missing, null or
incorrectly typed fields are `None`; zero and False retain their meaning. The
raw payload, including unknown or malformed fields, remains in `result.data`.
Treat receipts as read-only; modifying raw data does not update cached views.

For a Direct tool named `move`, a program can gate its next decision like this:

```python
def move_and_check(robot, targets):
    result = robot.tools.move(targets=targets, max_steps=12)
    print(result.summary())
    if result.state is not None and result.state.ended is True:
        return False
    if result.remaining_steps is not None and result.remaining_steps <= 0:
        return False
    feedback = result.feedback
    return result.call_ok is True and feedback is not None and feedback.targets_reached is True
```

The caller must honor a False return before any dependent action. `call_ok`
reports call validity; `pose_reached` reports arm arrival; `targets_reached` also
accounts for requested openings. None of these establish object attachment,
support for release, or official task success. Omit opening to preserve drive
intent; an explicit opening in `move` applies during motion, not after arrival.
Check object effects using the returned images.

`summary()` returns JSON containing common progress, Direct per-arm arrival and
errors, stopping evidence, call ID, image paths and camera order when reported.
It preserves reported False, zero and null values. It reads only this receipt:
no extra calls, automatic printing or image opening. Python variables alone are
not model context: print feedback at decision points and open relevant images
with the agent's native image reader. This does not replace a new observation
when the scene has changed.

The generated field tables derive from the same types used by the client.
They document optional fields, units and meanings without changing tool schemas.
Scene tools need not return motion feedback; Hybrid execution/assessment,
localization, calibration, skill content and full measured arm/device state
remain in `result.data`. Summary `data_fields` lists additional top-level keys
whose values require raw access. Extra nested diagnostics also remain in `data`.
Tool-calling mode continues to receive its existing JSON and images unchanged.

### Process and session lifecycle

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

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
When enabled by the tool profile, skills are available through the existing
direct listing and reading tools.
Calibration references, observations and robot metadata still come from the
existing tools through Python. Existing Bash-based geometry/calibration
calculations remain possible.

```python
from uni_robot_agent.execution.code_as_policy import Robot

with Robot() as robot:
    # Choose exact names/arguments from this session's robot_api.md.
    observation = robot.tools.view_env_state()
    print(observation)  # Full receipt, including paths for the native image reader.
```

Each call waits for completed feedback through the existing executor.

### Skills and robot operations

When registered by the selected tool profile, use the directly exposed
`list_skills` and `read_skill` tools to obtain guidance.
They use the existing schemas and return the paths or Markdown text directly.
Only these registered knowledge tools are exposed through the agent's MCP tool
list. Observations, robot actions and task completion continue through Python.
The skill tools remain callable from Python for existing programs.

### Complete feedback

Use `print(result)` after each robot call. Its JSON contains:

| Field | Content |
|---|---|
| `data` | The entire original model-visible tool payload, including nested state, diagnostics and tool-specific fields |
| `images` | Local paths preserving returned image bytes and order |
| `call_id` | The transport/execution correlation ID |

This representation performs no field selection, truncation or extra tool calls.
The original payload keeps the same numeric formatting as tool calling. Python
variables and unprinted results are not model context. Replace older
`print(result.summary())` calls with `print(result)`; `summary()` is removed.

`ToolCallError.result` retains failed-call feedback; the exception message also
contains the complete JSON representation. Catch the error in the script to save
or print its result before exiting. An exception can follow partial execution:
rerunning an entire script is not safe recovery.

### Typed access for program decisions

Typed properties are optional conveniences independent of complete output.
`result.feedback.arms["right"].pose_reached` accesses the corresponding Direct
feedback when present. `result.remaining_steps` and `result.state` expose budget
and progress. Missing, null or incorrectly typed fields are `None`; zero and
False retain their meaning. Full raw data, including additional or malformed
fields, remains in `data` and in `print(result)`.

| Operation | Relevant raw fields, when returned |
|---|---|
| Direct motion | `data["feedback"]`, state, budget and images |
| Observation/capture | `data["state"]` and snapshot/camera metadata; typed `result.state` contains only version, step count and ended status |
| Localization/calibration | Method-specific measurements, validity and provenance in `data` |
| Hybrid execution | `data["execution"]`, `data["assessment"]`, state and images |

Treat receipts as read-only; mutating raw data does not update cached typed views.
Each receipt describes its own state/snapshot. Arrival or opening feedback does
not prove object attachment, release support or official task success.

### Choose the program's decision boundary

A one-call program is valid. Use calculations, functions and loops when their
dependencies are established and continuation can be decided from available
data. The generated API includes a numeric route example that stops on a failed,
unknown or ended outcome. Keep the same physical verification requirements as
tool calling; a helper's False return must stop its caller's dependent sequence.

When the next action needs the model to interpret new images, end the current
program, inspect the images, then decide the next program. Printing a grasp
receipt and immediately transporting in the same script does not pause for that
judgment. Returning from the script leaves the robot where it stopped.

### Images and long output

Select `action_name` and `arguments` from the generated API and current evidence.
This example executes a justified action and preserves its full output before
returning for inspection:

```python
from pathlib import Path
from uni_robot_agent.execution.code_as_policy import Robot

with Robot() as robot:
    result = robot.call(action_name, **arguments)
    receipt = Path(f"receipt-{result.call_id}.json")
    receipt.write_text(str(result), encoding="utf-8")
    print(f"Full result: {receipt.resolve()}")
    print(result)
    # End this script here, before the image-dependent action.
```

The native shell tool can limit displayed output. Read any omitted content from
the saved JSON with native file tools; do not repeat the robot action to recover
its output. Saving is explicit, and separate call IDs keep receipts distinct.

Open the relevant `images` paths with the native image reader before the dependent
decision. Use `data["image_cameras"]` when reported and the `snapshot_id` to
identify the views. Reuse returned images when sufficient; request another
observation if the scene changed or evidence is missing.

```text
Completed call -> CodeResult -> print/save full receipt -> program ends
               -> native image/file reads -> next model decision
```

Image paths are not image attachments, and scripts do not echo a final expression
automatically. This mode retains native readers rather than automatically
injecting images into shell output. Tool-calling results are unchanged.

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

The examples apply the explicit output and image-dependent decision boundaries
illustrated by [PyRUA-Lean](https://github.com/DAGroup-PKU/PyRUA-Lean), and the
return-for-inspection workflow in [RAX](https://github.com/robot-agent-exploration/rax).
They use this project's existing API and execution contracts.

Compared with a persistent interpreter approach such as PyRUA-Lean, this design
keeps API discovery, programmatic composition and explicit feedback, but uses
the agent's existing process/file workflow instead of introducing a persistent
Python namespace. Parallel robot programs, notebook sessions and a separate
robot-control abstraction are deliberately outside this initial implementation.
New agent adapters need only forward `CodePolicyContext` cwd/env to native
processes, revoke admission on cancellation, and use its trusted finish receipt.
They must be validated before being advertised as supported.

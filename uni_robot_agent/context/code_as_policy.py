"""Generate program documentation from the session's actual tool declarations."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys

from uni_robot_agent.context.prompt import Prompt
from uni_robot_agent.tools.results import ArmFeedback, MotionFeedback, ResultFields, ResultState


def _field_type(spec: dict) -> str:
    if "anyOf" in spec:
        return " | ".join(_field_type(item) for item in spec["anyOf"])
    if "$ref" in spec:
        return spec["$ref"].rsplit("/", 1)[-1]
    if spec.get("type") == "array":
        return f"list[{_field_type(spec['items'])}]"
    if spec.get("type") == "object":
        item = spec.get("additionalProperties")
        return f"dict[str, {_field_type(item)}]" if isinstance(item, dict) else "dict"
    return {"null": "None", "boolean": "bool", "integer": "int", "number": "float", "string": "str"}.get(spec.get("type"), "Any")


def _result_documentation() -> str:
    sections = ["## Returned feedback", """
Use `print(result)` after each robot call. It returns the full receipt as JSON:
`{"data": <original tool payload>, "images": <ordered image paths>, "call_id": <id>}`.
No fields are selected, summarized or truncated by this representation. It does
not query fresh state, print automatically or display images. Open the returned
image paths with the native reader; use reported `data["image_cameras"]` and
`data["snapshot_id"]` to identify their views.

CodeResult also exposes the ResultFields attributes below for programmatic checks.
Missing, null or incorrectly typed fields are `None`, never invented success or
zero. These are optional projections, not a promise that every tool returns every
field. Full output always uses `data`, preserving additional and malformed values.

| Operation | Where to inspect the complete payload |
|---|---|
| Direct motion | `data["feedback"]`, returned state, budget and images; arrival does not prove object attachment. |
| Observe/capture | `data["state"]` when returned, plus snapshot/camera metadata. Typed `result.state` projects only progress, not arm/device measurements. |
| Localization/calibration | Method-specific values, validity and camera/snapshot references in `data`; follow the declared units and conventions. |
| Hybrid execution | `data["execution"]`, `data["assessment"]`, state and images when returned; do not apply a Direct arrival gate. |

When declared, use the directly exposed skill-listing and skill-reading tools
for guidance.
They return paths and Markdown text to the model. Their Python equivalents remain
available, but are not needed for ordinary skill discovery and reading.

### One call, complete feedback

For an observation tool named `view_env_state`, this is a complete program.
Use the actual registered names and arguments from this file:

```python
from uni_robot_agent.execution.code_as_policy import Robot

with Robot() as robot:
    result = robot.tools.view_env_state()
    print(result)
```

### Continue on sufficient numeric evidence

For a Direct tool named `move`, this helper follows an already-justified route.
Supply segments as lists of grounded arm-target dictionaries and a suitable
per-call step limit. Use it only when no intermediate visual judgment is needed:

```python
def follow_verified_route(robot, segments, max_steps):
    for targets in segments:
        result = robot.tools.move(targets=targets, max_steps=max_steps)
        print(result)
        if result.state is not None and result.state.ended is True:
            return False
        if result.remaining_steps is not None and result.remaining_steps <= 0:
            return False
        feedback = result.feedback
        if result.call_ok is not True or feedback is None or feedback.targets_reached is not True:
            return False
    return True
```

The caller must honor False before dependent work. True only means these numeric
checks passed, not that an object is attached, supported or the task is complete.
Omit opening to preserve drive intent; an explicit opening applies during motion.

### End execution for a visual decision

When the next action needs your interpretation of new images, execute only the
currently justified action. Select `action_name` and `arguments` from the API and
current evidence. Save the full receipt before printing so long output can be
read from scratch if the native terminal tool truncates it:

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

Open the relevant images before deciding the next program. Read any omitted text
from the saved receipt rather than repeating the action. A print or helper return
does not make a still-running caller wait for your decision. The scene persists
after script exit; observe again if it changed or required evidence is absent.
Catch ToolCallError in the script to print or save error.result before exiting.
An exception may follow partial execution; never blindly replay the script.
"""]
    for model in (ResultFields, ResultState, MotionFeedback, ArmFeedback):
        sections.append(f"### {model.__name__}\n\n| Field | Type | Meaning |\n|---|---|---|")
        for name, spec in model.model_json_schema()["properties"].items():
            kind = _field_type(spec).replace("|", "\\|")
            sections.append(f"| `{name}` | `{kind}` | {spec['description']} |")
    return "\n".join(sections)


def prepare_code_policy(schemas: list[dict], session_dir: Path, prompt: Prompt) -> Prompt:
    scratch = session_dir.resolve() / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    api = scratch / "robot_api.md"
    sections = ["# Robot program API", """
Use `from uni_robot_agent.execution.code_as_policy import Robot` and
`with Robot() as robot:`. Call `robot.tools.<exact_name>(**arguments)` or
`robot.call("exact_name", **arguments)`. Each call blocks until its tool finishes.
The returned `CodeResult` has `data` (the original model-visible tool payload),
`images` (local image paths, in returned camera order), and `call_id` (execution
correlation). Print the complete receipt with `print(result)`; use your native
image reader to inspect its images. A path string alone does not show an image.
When declared, use the directly exposed skill-listing and skill-reading tools
for guidance.
A failed call raises `ToolCallError`; inspect `error.result` for feedback.
`call_ok` establishes call validity, not physical or task success.

Use ordinary scripts, imports, functions, loops, and files. Variables do not
persist between processes. Reconnect with Robot() in each new script to use
the same episode. Calls are serialized across scripts; do not launch detached
robot-control processes. Do not retry transport errors: execution is uncertain.
Finish only through the declared finish operation after verifying the task.
""", _result_documentation()]
    for spec in schemas:
        sections.append(f"## {spec['name']}\n\n{spec.get('description', '')}\n\n"
                        f"```json\n{json.dumps(spec['input_schema'], indent=2)}\n```")
    api.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    prompt = prompt.append(system=(
        f"Read the generated API at {api} before controlling the robot. "
        f"Run your scripts with {shlex.quote(sys.executable)}. "
        f"Your working directory is {scratch}; connection settings are already in the environment."
    ), source=str(api))
    (session_dir / "effective_prompt.md").write_text(prompt.flatten(), encoding="utf-8")
    return prompt

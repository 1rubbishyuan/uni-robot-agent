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
CodeResult exposes the ResultFields attributes below, alongside raw `data`,
`images` and `call_id`. Missing, null or incorrectly typed fields are `None`,
never invented success or zero. Additional and malformed values remain in `data`.
These are optional projections, not a promise that every tool returns every field.
Scene tools may return state without motion feedback; Hybrid returns its own
execution/assessment payload. Read `result.data` for those results, calibration,
localization, skill content, full measured arm/device state and extra diagnostics.

`print(result.summary())` exposes compact feedback, stopping evidence, image paths
and additional top-level data keys to the model. It does not query fresh state,
open images, or print automatically. A stored result describes its own receipt.
Use explicit `is True` checks for confirmed arrival, not `not field` for unknowns.
For a Direct move, a dependent-action gate inside your helper can be:

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

Use the exact registered name if it differs. The caller must honor False rather
than execute the dependent action anyway. Arrival does not establish attachment
or safe support for release; inspect the returned images for object effects.
Omit opening to preserve drive intent; supplying an opening in move applies it
during motion. Handle ToolCallError by inspecting error.result, not replaying.
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
correlation). Print only useful data and image paths; use your native image
reader to inspect images. A path string alone does not show an image to you.
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

"""Generate program documentation from the session's actual tool declarations."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys

from uni_robot_agent.context.prompt import Prompt


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
"""]
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

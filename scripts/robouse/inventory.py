"""List admitted/rejected pinned task configurations without creating simulators."""
import json
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from robouse.tasks import bundled_tasks_root, load_task
from uni_robot_agent.integrations.robouse.tasks import SUPPORTED, load
from uni_robot_agent.integrations.robouse.worker_tasks import GPU_TASKS


def main():
    root = bundled_tasks_root()
    counts, rejected, tasks = Counter(), [], []
    for path in sorted(root.rglob("task.md")):
        task = load_task(path.parent)
        kind = task.spec.get("backend")
        if kind not in SUPPORTED:
            continue
        name = str(path.parent.relative_to(root))
        try:
            load(path.parent)
        except ValueError as error:
            rejected.append({"task": name, "backend": kind, "reason": str(error)})
        else:
            counts[kind] += 1
            tasks.append({"task": name, "backend": kind})
    print(json.dumps({"robouse_version": "0.4.1", "admitted_counts": dict(counts),
                      "admitted": tasks, "rejected": rejected,
                      "gpu_native_task_registry_not_bundled": GPU_TASKS}, indent=2))


if __name__ == "__main__":
    main()

"""Run a synchronous actor-only baseline through the same hybrid binding/executor.

This is not the official asynchronous DexJoCo evaluator: inference pauses physics,
unused action suffixes are discarded, and no overlapping chunks are blended.
Start fresh prepared DexJoCo and actor services separately; this client never resets
an episode. Without --max-chunks, the configured prefix covers the native budget.
An explicit shorter chunk budget is recorded as a bounded actor smoke run.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from uni_robot_agent.artifacts.run import write_json_atomic
from uni_robot_agent.artifacts.store import ArtifactStore
from uni_robot_agent.integrations.base import IntegrationContext
from uni_robot_agent.integrations.dexjoco import DexjocoOptions
from uni_robot_agent.integrations.dexjoco.session import create_session
from uni_robot_agent.integrations.hybrid import configure_hybrid


def positive(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def digest(value):
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise argparse.ArgumentTypeError("must be a lowercase SHA256 digest")
    return value


def baseline_plan(episode, *, actions_per_chunk, max_chunks, reset_sha256):
    # Episode initially mirrors record 0; inspect the live state before any inference.
    current = episode.env.device_state()
    if current.ended or current.completed_commands != 0 or current.state_version != 0:
        raise ValueError("Actor baseline requires a fresh, unended environment episode")
    metadata = episode.description.metadata
    if reset_sha256 is not None and metadata.get("reset_sha256") != reset_sha256:
        raise ValueError("Prepared environment reset fingerprint differs from the expected value")
    budget = metadata.get("max_steps")
    if type(budget) is not int or budget < 1:
        raise ValueError("Actor baseline requires an explicit positive native step budget")
    chunks = math.ceil(budget / actions_per_chunk) if max_chunks is None else max_chunks
    return {"max_chunks": chunks, "native_step_budget": budget,
        "evaluation_scope": "full_episode_budget" if chunks * actions_per_chunk >= budget else "bounded_smoke",
        "initial_state": current.model_dump(mode="json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-url", required=True)
    parser.add_argument("--action-model-url", required=True)
    parser.add_argument("--checkpoint-sha256", type=digest, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--instruction")
    parser.add_argument("--seed", type=int, help="Expected native constructor seed")
    parser.add_argument("--reset-sha256", type=digest, help="Expected fresh-reset fingerprint")
    parser.add_argument("--actions-per-chunk", type=positive, default=30)
    parser.add_argument("--max-chunks", type=positive, help="Explicit chunk cap; defaults to the full native budget")
    options = parser.parse_args()
    if options.seed is not None and options.seed < 0:
        parser.error("--seed must be nonnegative")
    output = options.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    args = SimpleNamespace(**DexjocoOptions(vla_review=False, seed=options.seed,
        action_model_checkpoint_sha256=options.checkpoint_sha256).model_dump(),
        env_url=options.env_url, action_model_url=options.action_model_url,
        instruction=options.instruction, skill_writing=False, _vla_review_api_key=None)
    artifacts = ArtifactStore(output)
    # Prepare only the native episode; the baseline calls the shared actor runner
    # directly, so no planner-facing tool modules are assembled.
    session = create_session(IntegrationContext(args, output, artifacts, None, tool_modules=()))
    try:
        session.start()
        hybrid = configure_hybrid(session)
        if options.actions_per_chunk > hybrid.binding.action_horizon:
            raise ValueError("actions-per-chunk exceeds the declared actor horizon")
        plan = baseline_plan(session.episode, actions_per_chunk=options.actions_per_chunk,
            max_chunks=options.max_chunks, reset_sha256=options.reset_sha256)
        config = {"execution_mode": "synchronous_actor_only", "reviewer_enabled": False,
            "planner_enabled": False, "actions_per_chunk": options.actions_per_chunk,
            **plan, "instruction": session.episode.instruction,
            "environment": session.episode.description.model_dump(mode="json"),
            "actor": hybrid.runner.actor.info.model_dump(mode="json"),
            "official_async_baseline": False}
        write_json_atomic(output / "baseline_config.json", config)
        summary = hybrid.runner.run(session.episode.instruction,
            active_groups=list(hybrid.binding.capabilities.groups),
            actions_per_chunk=options.actions_per_chunk, max_chunks=plan["max_chunks"])
        native_terminal = session.episode.state.ended
        outcome = session.finalize(agent_result=None, error=summary["error"])
        result = {"execution": summary, "outcome": asdict(outcome),
            "evaluation_scope": plan["evaluation_scope"], "native_terminal_reached": native_terminal}
        write_json_atomic(output / "baseline_result.json", result)
        print(json.dumps(result, indent=2, allow_nan=False))
        return int(summary["error"] is not None or not outcome.accepted
                   or (plan["evaluation_scope"] == "full_episode_budget" and not native_terminal))
    finally:
        session.close()
        artifacts.close()


if __name__ == "__main__":
    raise SystemExit(main())

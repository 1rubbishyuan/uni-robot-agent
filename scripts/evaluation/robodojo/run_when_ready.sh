#!/usr/bin/env bash
# Same CLI as run_episode.sh, plus --startup-timeout-s for endpoint readiness.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
exec "${PYTHON:-python}" - "$@" <<'PYTHON'
import argparse
import time
from uni_robot_agent.services.actor.contracts import ActorInfo
from uni_robot_agent.services.perception.contracts import PerceptionInfo
from uni_robot_agent.cli import build_parser, main
from uni_robot_agent.config import resolve_config
from uni_robot_agent.integrations.robodojo import get_integration_spec

probe = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
probe.add_argument("--startup-timeout-s", type=int, default=360)
startup, rest = probe.parse_known_args()
if startup.startup_timeout_s <= 0:
    probe.error("--startup-timeout-s must be positive")
argv = ["run", "--integration", "robodojo", *rest]
spec = get_integration_spec()
parser = build_parser(spec)
args = resolve_config(parser.parse_args(argv), parser, spec, argv)
if args.print_config or args.validate_config:
    main(argv)
    raise SystemExit(0)
if args.deploy == "local":
    main([*argv, "--startup-timeout-s", str(startup.startup_timeout_s)])
    raise SystemExit(0)
from uni_robot_agent.services.deployment.process import wait_for_service
from uni_robot_agent.services.environment.contracts import EpisodeInfo
expected = {"integration": "robodojo"}
if args.run_id:
    expected["episode_id"] = args.run_id
if args.task:
    expected["task"] = args.task
services = [(args.env_url, EpisodeInfo, expected), (args.sam3_url, PerceptionInfo, {})]
if args.action_profile == "hybrid":
    services.append((args.action_model_url, ActorInfo, {}))
deadline = time.monotonic() + startup.startup_timeout_s
for url, contract, identity in services:
    if url:
        try:
            wait_for_service(url, identity, max(.01, deadline-time.monotonic()), contract=contract)
        except TimeoutError:
            raise SystemExit(75)
main(argv)
PYTHON

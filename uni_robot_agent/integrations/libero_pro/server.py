"""Serve a pinned LIBERO-PRO task without generating or rewriting its assets."""
from uni_robot_agent.integrations.libero_plus.server import BUDGETS, parser as family_parser, prepare
from uni_robot_agent.integrations.simulation import serve_episode

REVISION = "eafdb809426b13153aa1e4c42d6601844217dfec"
SUITES = tuple(base + suffix for base in BUDGETS for suffix in ("", "_env", "_object", "_swap", "_task", "_lan"))


def parser():
    return family_parser(suites=SUITES, description=__doc__, port=18782)


def main():
    args = parser().parse_args()
    serve_episode(lambda: prepare(args, benchmark_name="libero_pro", revision=REVISION), args, "libero_pro")


if __name__ == "__main__":
    main()

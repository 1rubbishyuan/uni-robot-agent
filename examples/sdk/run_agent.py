"""Run one built-in planner; provider credentials use the standard environment variables."""
import argparse
from uni_robot_agent import AgentConfig, SessionConfig, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--integration', required=True)
    parser.add_argument('--env-url', required=True)
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    result = run(SessionConfig(integration=args.integration, env_url=args.env_url,
                              no_sam3=True, agent=AgentConfig(model=args.model)))
    print(result.task_success, result.session_dir)


if __name__ == '__main__':
    main()

"""Connect to a prepared episode and inspect it without a planner."""
import argparse
from uni_robot_agent import Session, SessionConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--integration', required=True)
    parser.add_argument('--env-url', required=True)
    args = parser.parse_args()
    with Session(SessionConfig(integration=args.integration, env_url=args.env_url,
                               no_sam3=True)) as session:
        print([tool['name'] for tool in session.tools.list()])
        print(session.tools.call('view_env_state').to_dict())
    print(session.result.record)


if __name__ == '__main__':
    main()

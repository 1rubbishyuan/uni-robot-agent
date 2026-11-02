"""Start a previously prepared local runtime and own its cleanup."""
import argparse
from uni_robot_agent import DeploymentConfig, Session, SessionConfig, deploy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--integration', required=True)
    parser.add_argument('--task', required=True)
    parser.add_argument('--gpu', type=int, default=0)
    args = parser.parse_args()
    with deploy(DeploymentConfig(integration=args.integration, task=args.task,
                                 deploy_gpu=args.gpu)) as environment:
        with Session(SessionConfig(integration=args.integration, env_url=environment.url,
                                   no_sam3=True)) as session:
            print(session.tools.call('view_env_state').to_dict())
        print(session.result.record)


if __name__ == '__main__':
    main()

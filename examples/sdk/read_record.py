"""Read existing artifacts without starting an environment or a planner."""
import argparse
from uni_robot_agent import RunRecord


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    args = parser.parse_args()
    record = RunRecord.open(args.directory)
    print(record.result)
    for event in record.events():
        print(event['type'])


if __name__ == '__main__':
    main()

"""uni-robot-agent logging; level is configured at the CLI boundary."""

import logging


def configure_logging(level: str = "INFO") -> None:
    logger = logging.getLogger("uni_robot_agent")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[%(name)s] %(levelname)s: %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    if not logging.getLogger("uni_robot_agent").handlers:
        configure_logging()
    return logging.getLogger(f"uni_robot_agent.{name}")

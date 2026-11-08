"""RoboLab identity and native outcome policy over the prepared lifecycle."""
from uni_robot_agent.integrations.base import EpisodeOutcome
from uni_robot_agent.integrations.prepared_episode import PreparedEpisodeSession
from uni_robot_agent.services.environment.client import EnvironmentRequestError, ExecutionUncertain


def validate_episode(session):
    info = session.episode.description
    if info.integration != "robolab":
        raise ValueError("RoboLab requires a prepared robolab environment")
    if info.metadata.get("instruction_type") != session.args.instruction_type:
        raise ValueError("Episode instruction type does not match the expected CLI value")


class Session(PreparedEpisodeSession):
    def finalize(self, *, agent_result, error):
        if self.result is not None:
            return self.result
        try:
            outcome = super().finalize(agent_result=agent_result, error=error)
        except (EnvironmentRequestError, ExecutionUncertain) as failure:
            # Native recording may already have flushed; never replay finalization.
            self.result = EpisodeOutcome(success=None, accepted=False, retryable=False,
                failure_category="robolab_result_unavailable",
                metadata={"execution_uncertain": failure.execution_uncertain,
                          "result_error": str(failure)})
            return self.result
        category = outcome.metadata.get("failure_category")
        uncertain = outcome.metadata.get("execution_uncertain", False)
        self.result = EpisodeOutcome(
            success=outcome.success, accepted=outcome.accepted and category != "environment_error",
            retryable=False,
            failure_category="execution_uncertain" if uncertain else category or outcome.failure_category,
            metadata=outcome.metadata,
        )
        return self.result


def create_session(context):
    from .action_binding import create_binding
    return Session(context, binding_factory=create_binding, validate_episode=validate_episode)

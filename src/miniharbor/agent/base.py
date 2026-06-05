"""The Agent interface: the policy. One job -- given the context so far, produce
the next Action.

The agent is a pure function of the TrajectoryContext the harness hands it: it
holds NO loop, NO history, and is blind to the environment. A model-backed agent
is composed of ModelClient (the bare model interface) + PromptTemplate (render
context -> messages) + Parser (model output -> Action); the scripted stub has none
of those. Either way the contract is just `act(context) -> Action`.
"""

from __future__ import annotations

import abc

from ..models import AgentResponse, TrajectoryContext


class Agent(abc.ABC):
    name: str = "agent"
    version: str = "v0"

    @abc.abstractmethod
    async def act(self, context: TrajectoryContext) -> AgentResponse:
        """Given the trajectory so far, return the next action + the model I/O the
        harness logs (message, model_input, tokens). The agent holds no history; it
        is a pure function of the context."""

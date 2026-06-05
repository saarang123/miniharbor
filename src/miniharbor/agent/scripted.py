"""A scripted agent: replays a fixed list of actions, ignoring the context.

No model, no prompt -- it exists so the Harness loop can be tested deterministically
(loop correctness separated from model behavior). When the script runs out it
submits, so the loop always terminates.
"""

from __future__ import annotations

from ..models import Action, AgentResponse, TrajectoryContext
from .base import Agent


class ScriptedAgent(Agent):
    name = "scripted"
    version = "v1"

    def __init__(self, actions: list[Action]):
        self._actions = list(actions)
        self._i = 0

    async def act(self, context: TrajectoryContext) -> AgentResponse:
        if self._i >= len(self._actions):
            return AgentResponse(action=Action(tool="submit"))   # exhausted -> submit
        action = self._actions[self._i]
        self._i += 1
        return AgentResponse(action=action)                      # no model I/O (no model)

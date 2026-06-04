"""The model-backed Agent and its three swappable parts.

  ModelClient        -- the bare model interface (OpenAI-compatible HTTP).
  DefaultPromptTemplate -- render TrajectoryContext -> messages (model-specific).
  JSONActionParser   -- model text -> a structured Action.

ModelAgent composes them: act(context) = render -> complete -> parse, with one
corrective retry if the model's reply has no parseable tool call.

Action format is text/JSON (a ```json {"tool":..., "args":...} block), not native
tool-calling: it works against any OpenAI-compatible endpoint (Ollama, vLLM, ...)
and is robust to debug with small models. A native-tool-calling Parser can be
added later behind the same seam.
"""

from __future__ import annotations

import abc
import json
import re
import time

from ..models import Action, Message, ModelResponse, TrajectoryContext
from .base import Agent


# --- model client ------------------------------------------------------

class ModelClient(abc.ABC):
    @abc.abstractmethod
    async def complete(self, messages: list[Message], **sampling) -> ModelResponse: ...


class OpenAIChatClient(ModelClient):
    """Any OpenAI-compatible /v1/chat/completions endpoint (Ollama, vLLM, etc.).
    The serving setup is out of repo scope; this just needs a base_url + model id."""

    def __init__(self, base_url: str, model: str, *, api_key: str = "none", timeout_s: float = 120):
        self._base = base_url.rstrip("/")
        self._model = model
        self._key = api_key
        self._timeout = timeout_s

    async def complete(self, messages: list[Message], **sampling) -> ModelResponse:
        import httpx  # lazy: package imports without httpx (only the real client needs it)

        payload = {"model": self._model, "messages": [m.model_dump() for m in messages], **sampling}
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base}/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._key}"},
            )
            resp.raise_for_status()
            data = resp.json()
        usage = data.get("usage", {})
        return ModelResponse(
            text=data["choices"][0]["message"].get("content") or "",
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )


class FakeModelClient(ModelClient):
    """Returns canned responses in order (for tests). Defaults to a submit once exhausted."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self._i = 0

    async def complete(self, messages: list[Message], **sampling) -> ModelResponse:
        text = self._responses[self._i] if self._i < len(self._responses) else '{"tool":"submit","args":{}}'
        self._i += 1
        return ModelResponse(text=text, tokens_in=10, tokens_out=5, latency_ms=1)


# --- prompt template ---------------------------------------------------

class DefaultPromptTemplate:
    version = "v1"

    def render(self, ctx: TrajectoryContext) -> list[Message]:
        tools_doc = "\n".join(
            f"- {s.name}({', '.join(s.parameters.get('properties', {}).keys())}): {s.description}"
            for s in ctx.tool_schemas
        )
        system = (
            "You are an autonomous agent solving a task in a Linux sandbox.\n\n"
            f"Available tools:\n{tools_doc}\n\n"
            "Each turn, respond with EXACTLY ONE tool call as a JSON object in a "
            "```json fenced block, e.g.:\n"
            '```json\n{"tool": "exec", "args": {"command": "ls"}}\n```\n'
            "Omit terminal_id to use the default persistent terminal (state persists). "
            "When the task is complete, call submit."
        )
        msgs = [Message(role="system", content=system),
                Message(role="user", content=ctx.instruction)]
        for step in ctx.history:
            msgs.append(Message(
                role="assistant",
                content=step.model_output or json.dumps({"tool": step.action.tool, "args": step.action.args}),
            ))
            msgs.append(Message(
                role="user",
                content="Observation: " + json.dumps(step.observation.result)[:4000],
            ))
        return msgs


# --- parser ------------------------------------------------------------

class JSONActionParser:
    version = "v1"
    _FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

    def parse(self, text: str) -> Action | None:
        matches = self._FENCE.findall(text)
        raw = matches[-1] if matches else self._outermost_braces(text)
        if not raw:
            return None
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return None
        tool = obj.get("tool")
        if not isinstance(tool, str):
            return None
        args = obj.get("args")
        return Action(tool=tool, args=args if isinstance(args, dict) else {}, raw=text)

    @staticmethod
    def _outermost_braces(text: str) -> str | None:
        # first '{' to last '}' -- the outermost object, tolerant of prose around it
        start, end = text.find("{"), text.rfind("}")
        return text[start:end + 1] if 0 <= start < end else None


# --- the agent ---------------------------------------------------------

class ModelAgent(Agent):
    name = "model"

    def __init__(self, client: ModelClient, *, prompt=None, parser=None, version="v1", sampling=None):
        self._client = client
        self._prompt = prompt or DefaultPromptTemplate()
        self._parser = parser or JSONActionParser()
        self.version = version
        self._sampling = sampling or {}

    async def act(self, context: TrajectoryContext) -> Action:
        messages = self._prompt.render(context)
        resp = await self._client.complete(messages, **self._sampling)
        action = self._parser.parse(resp.text)
        if action is None:
            # one corrective retry: tell the model its format was wrong
            messages = messages + [Message(
                role="user",
                content=('Your last reply had no valid tool call. Reply with exactly one '
                         '```json {"tool": ..., "args": ...} ``` block and nothing else.'),
            )]
            resp = await self._client.complete(messages, **self._sampling)
            action = self._parser.parse(resp.text)
        if action is None:
            raise ValueError("agent produced no parseable tool call after one retry")
        action.raw = resp.text
        return action

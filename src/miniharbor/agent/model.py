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
import asyncio
import json
import os
import re
import time

from ..models import Action, AgentResponse, Message, ModelResponse, TrajectoryContext
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
        self.model_id = model
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


class AnthropicClient(ModelClient):
    """Anthropic Messages API. Not OpenAI-shaped: the system prompt is a top-level
    field and `messages` carries only user/assistant turns, so we split it out."""

    def __init__(self, model: str, *, api_key: str | None = None,
                 base_url: str = "https://api.anthropic.com", max_tokens: int = 4096,
                 version: str = "2023-06-01", timeout_s: float = 120):
        self._model = model
        self.model_id = model
        self._key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._base = base_url.rstrip("/")
        self._max_tokens = max_tokens
        self._version = version
        self._timeout = timeout_s

    @staticmethod
    def _split_system(messages: list[Message]) -> tuple[str, list[dict]]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        conv = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        return system, conv

    @staticmethod
    def _parse(data: dict) -> ModelResponse:
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage", {})
        return ModelResponse(text=text,
                             tokens_in=usage.get("input_tokens", 0),
                             tokens_out=usage.get("output_tokens", 0))

    async def complete(self, messages: list[Message], **sampling) -> ModelResponse:
        import httpx

        system, conv = self._split_system(messages)
        body = {"model": self._model,
                "max_tokens": sampling.pop("max_tokens", self._max_tokens),
                "messages": conv, **sampling}
        if system:
            body["system"] = system
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base}/v1/messages", json=body,
                headers={"x-api-key": self._key, "anthropic-version": self._version},
            )
            resp.raise_for_status()
            data = resp.json()
        out = self._parse(data)
        out.latency_ms = int((time.monotonic() - t0) * 1000)
        return out


class SpindleClient(ModelClient):
    """ModelClient backed by an async generative-job fabric: submit POST /jobs,
    poll GET /jobs/{id} until terminal, read the text out of `output`.

    Requires a text-generation job type to exist on the fabric; `job_type` and
    `config_id` select it, `output_key` is where the worker puts the generated text.
    base_url / auth are supplied at init -- nothing about the backend is hardcoded.
    """

    _TERMINAL = {"succeeded", "failed", "canceled", "dead_lettered"}

    def __init__(self, base_url: str, job_type: str, config_id: str, *,
                 auth_token: str | None = None, output_key: str = "text",
                 poll_interval_s: float = 1.0, timeout_s: float = 300):
        self._base = base_url.rstrip("/")
        self._type = job_type
        self._config_id = config_id
        self.model_id = f"spindle:{job_type}/{config_id}"
        self._auth = auth_token
        self._output_key = output_key
        self._poll = poll_interval_s
        self._timeout = timeout_s

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._auth}"} if self._auth else {}

    @staticmethod
    def _build_input(messages: list[Message], sampling: dict) -> dict:
        return {"messages": [m.model_dump() for m in messages], **sampling}

    def _extract(self, job: dict) -> ModelResponse:
        out = job.get("output") or {}
        usage = out.get("usage", {})
        return ModelResponse(
            text=out.get(self._output_key, ""),
            tokens_in=usage.get("prompt_tokens", usage.get("input_tokens", 0)),
            tokens_out=usage.get("completion_tokens", usage.get("output_tokens", 0)),
        )

    async def complete(self, messages: list[Message], **sampling) -> ModelResponse:
        import httpx

        body = {"type": self._type, "config_id": self._config_id,
                "input": self._build_input(messages, sampling),
                "timeout_seconds": int(self._timeout)}
        t0 = time.monotonic()
        deadline = t0 + self._timeout
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
            r = await client.post(f"{self._base}/jobs", json=body)
            r.raise_for_status()
            job_id = r.json()["job_id"]
            while True:
                jr = await client.get(f"{self._base}/jobs/{job_id}")
                jr.raise_for_status()
                job = jr.json()
                if job["status"] in self._TERMINAL:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError(f"spindle job {job_id} did not finish in {self._timeout}s")
                await asyncio.sleep(self._poll)
        if job["status"] != "succeeded":
            err = job.get("error") or {}
            raise RuntimeError(f"spindle job {job['status']}: {err.get('code', '?')} {err.get('message', '')}")
        out = self._extract(job)
        out.latency_ms = int((time.monotonic() - t0) * 1000)
        return out


class FakeModelClient(ModelClient):
    """Returns canned responses in order (for tests). Defaults to a submit once exhausted."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self._i = 0
        self.model_id = "fake"

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
            "The task's files are under /workspace (use absolute paths or cd there). "
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
        self.model_id = getattr(client, "model_id", "model")

    async def act(self, context: TrajectoryContext) -> AgentResponse:
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
        return AgentResponse(
            action=action,
            message=resp.text,                 # assistant content/reasoning
            model_input=messages,              # exact messages sent (for training)
            tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out,
            latency_ms=resp.latency_ms,
        )

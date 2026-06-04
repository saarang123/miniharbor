# Agent

The policy: "given the trajectory so far, produce the next action." The agent is the unit under test (eval) or under training. It is deliberately blind to everything except the observations the harness feeds it, which is what makes a trajectory a well-defined training example and the agent swappable.

> Port. v1 adapter: a model-backed ReAct policy (ModelClient + PromptTemplate + Parser). Swap-to: wrap an external CLI agent (eval-only, agent-owned loop).

## Interface

```python
class AgentConfig(BaseModel):
    model: str                  # model identifier
    prompt_template: str        # template name + version
    parser: str                 # parser name + version
    sampling: dict = {}         # temperature, top_p, max_tokens, ...

class Action(BaseModel):
    tool: str
    args: dict
    raw: str | None = None      # the raw model text this action was parsed from

class TrajectoryContext(BaseModel):
    instruction: str
    tool_schemas: list[ToolSchema]
    history: list[Step]         # prior (action, observation) pairs
    budgets_left: Budgets

class Agent(Protocol):
    name: str
    version: str
    async def act(self, context: TrajectoryContext) -> Action: ...
```

`act` is pure with respect to the environment: it reads context and returns an action. It does not execute anything. The harness executes the action via the ToolServer.

## Internal structure (three swappable parts)

A model-backed agent is composed, not monolithic:

```
TrajectoryContext
      │
      ▼
PromptTemplate.render(context) ──► list[Message]
      │
      ▼
ModelClient.complete(messages, tool_schemas, sampling) ──► ModelResponse
      │
      ▼
Parser.parse(ModelResponse) ──► Action
```

| Part | Responsibility | Swappable for |
|---|---|---|
| `ModelClient` | call the model: messages + tool schemas → response | different model / serving backend |
| `PromptTemplate` | render context into messages (system prompt, history formatting, tool docs) | prompt iteration |
| `Parser` | turn the model response into a typed `Action` | native tool-calling vs ReAct text |

Each part is versioned; the `AgentConfig` records which versions ran so a trajectory is reproducible.

## ModelClient

```python
class ModelClient(Protocol):
    async def complete(self, messages: list[Message], tools: list[ToolSchema],
                       **sampling) -> ModelResponse: ...

class ModelResponse(BaseModel):
    text: str
    tool_calls: list[Action] = []   # populated when the backend does native tool-calling
    tokens_in: int
    tokens_out: int
    latency_ms: int
    logprobs: list[float] | None = None   # needed by GRPO; optional otherwise
```

v1 adapter: an OpenAI-compatible HTTP client (works against any server exposing that API). Swap-to: a self-hosted batched server that returns token logprobs, required once the post-training loop reaches GRPO. The agent code does not change when the ModelClient is swapped.

## Action parsing: native tool-calling vs ReAct

Two parser styles behind the same `Parser` interface:

- **Native tool-calling:** the ModelClient returns `tool_calls`; the parser passes them through. Cleanest when the model and serving backend support function-calling.
- **ReAct text:** the model emits text like `Thought: ... \n Action: bash {"command": "pytest -q"}`; the parser extracts the tool + args. Works with any model; more brittle, so the parser must handle malformed output (retry once, else emit a no-op observation and let the loop continue or count an `agent_failed`).

The choice is part of the agent config and the harness version because it changes what the model is asked to produce.

## Eval-only agent-owned adapter (later)

To benchmark a third-party CLI agent (which runs its own loop and calls tools itself), the adapter does not implement `act`. Instead the harness hands the agent a ToolServer endpoint and an environment, lets it run, and captures whatever trajectory it exposes. This path cannot be used for training your own policy — you do not own the prompt, action space, or tokenization — so it lives behind the same seam but is marked eval-only.

## Reproducibility

A trajectory pins `AgentConfig` (model + prompt_template version + parser version + sampling) and the `harness_version`. Same config + same seed should yield comparable runs; sampling temperature and any model nondeterminism are why a task is run N times and reported as a rate, not a single result.

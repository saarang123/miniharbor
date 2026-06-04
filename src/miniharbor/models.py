"""Shared data models.

These are the contracts every component agrees on. The Environment only reads a
small slice of `Task` (`image_ref`, `resources`, `network`, `workdir`); the
Verifier reads `verifier` + the test/solution refs; the Registry produces `Task`
from a `task_id`.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ExecResult(BaseModel):
    """Result of running ONE command in an environment.

    Returned for any command that actually ran -- including one that exited
    nonzero (that is a normal observation, not an error). A wall-clock timeout
    sets `timed_out=True` (exit_code is 124 by convention). A sandbox/daemon
    failure does NOT produce an ExecResult; it raises SandboxError.
    """

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    duration_ms: int


class Resources(BaseModel):
    """Per-sandbox resource caps (from the task's [resources])."""

    cpu: float = 2
    memory_gb: float = 4
    disk_gb: float = 8
    pids: int = 512


class Budgets(BaseModel):
    """Whole-trial budgets, enforced by the harness (not the environment)."""

    timeout_seconds: int = 600
    max_steps: int = 40
    max_tokens: int = 200_000
    verifier_timeout_seconds: int = 120


class VerifierSpec(BaseModel):
    """The verifier contract: a command to run + a file to read the reward from."""

    entrypoint: str = "tests/run.sh"          # relative to the task bundle
    inject_path: str = "/opt/verifier"        # where tests/ is copied at grade time
    reward_path: str = "/logs/verifier/reward.json"


class Task(BaseModel):
    """A resolved task. Produced by `Registry.resolve(task_id)`.

    `image_ref` is an ALREADY-BUILT image (build is a separate concern, not the
    environment's). `tests_ref`/`solution_ref` are used by the verifier and by
    task validation -- never baked into the agent-visible image.
    """

    task_id: str
    image_ref: str
    instruction: str
    workdir: str = "/workspace"
    network: str = "none"                     # egress off by default
    resources: Resources = Field(default_factory=Resources)
    budgets: Budgets = Field(default_factory=Budgets)
    verifier: VerifierSpec = Field(default_factory=VerifierSpec)
    tests_ref: str | None = None              # host path / artifact ref to tests/
    solution_ref: str | None = None           # reference solution (validation only)


class ToolSchema(BaseModel):
    """A tool the agent may call, in tool-calling form (JSON Schema args)."""

    name: str
    description: str
    parameters: dict                          # JSON Schema for the tool's arguments


class Observation(BaseModel):
    """The result of one tool call, fed back to the agent and logged in the trajectory."""

    tool: str
    result: dict
    truncated: bool = False
    bytes_omitted: int = 0


class Message(BaseModel):
    """One chat message (the unit a PromptTemplate renders context into)."""

    role: str
    content: str


class Action(BaseModel):
    """The agent's decision for one step: a tool call."""

    tool: str
    args: dict = Field(default_factory=dict)
    raw: str | None = None                    # raw model text this was parsed from (audit)


class Step(BaseModel):
    """One (action, observation) pair plus the model I/O that produced it.
    The model_* fields are empty for non-model agents (e.g. the scripted stub)."""

    index: int
    action: Action
    observation: Observation
    model_input: list[Message] = Field(default_factory=list)
    model_output: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


class AgentConfig(BaseModel):
    """What pins a policy: model id + the rendering/parsing versions + sampling."""

    model: str
    prompt_template: str = "default"
    parser: str = "default"
    sampling: dict = Field(default_factory=dict)


class TrajectoryContext(BaseModel):
    """Everything the harness hands the agent each turn. The agent is a pure
    function of this -- it holds no history of its own."""

    instruction: str
    tool_schemas: list[ToolSchema] = Field(default_factory=list)
    history: list[Step] = Field(default_factory=list)
    budgets_left: Budgets = Field(default_factory=Budgets)


class HaltReason(str, Enum):
    """Why the harness loop stopped. The full TrialStatus (passed/failed_tests) is
    assigned later by the worker after the verifier runs."""

    submitted = "submitted"          # agent called submit -> awaiting verification
    timed_out = "timed_out"          # a budget tripped
    agent_failed = "agent_failed"    # agent errored


class RunResult(BaseModel):
    """The harness loop's output (loop-level, pre-verification)."""

    halt_reason: HaltReason
    n_steps: int
    steps: list[Step] = Field(default_factory=list)


class ModelResponse(BaseModel):
    """What a ModelClient returns for one completion."""

    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0

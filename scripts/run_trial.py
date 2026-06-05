"""Run one real trial and print the trajectory + reward.

Usage:
    python scripts/run_trial.py --backend anthropic --model claude-sonnet-4-6
    python scripts/run_trial.py --backend openai --model gpt-4.1

API keys are read from the environment (ANTHROPIC_API_KEY / OPENAI_API_KEY); this
script never prints them. Makes real API calls and runs a real Docker container.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from miniharbor.agent import AnthropicClient, ModelAgent, OpenAIChatClient
from miniharbor.environment.docker import DockerEnvironment, build_image
from miniharbor.harness import Harness
from miniharbor.models import Budgets, Message, Task
from miniharbor.toolserver import ToolServer


def build_client(backend: str, model: str):
    if backend == "anthropic":
        return AnthropicClient(model)
    if backend == "openai":
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        return OpenAIChatClient(base, model, api_key=os.environ.get("OPENAI_API_KEY", "none"))
    raise SystemExit(f"unknown backend: {backend}")


async def verify(env: DockerEnvironment, bundle: str) -> dict:
    for fname in ("run.sh", "test_hidden.py"):
        content = open(os.path.join(bundle, "tests", fname), "rb").read()
        await env.write_file(f"/opt/verifier/{fname}", content)
    await env.exec("bash /opt/verifier/run.sh", timeout_s=120)
    return json.loads(await env.read_file("/logs/verifier/reward.json"))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="tasks/logfixbench/seed_001")
    ap.add_argument("--backend", default="anthropic", choices=["anthropic", "openai"])
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--max-steps", type=int, default=15)
    args = ap.parse_args()

    bundle = os.path.abspath(args.task)
    instruction = open(os.path.join(bundle, "instruction.md")).read()
    client = build_client(args.backend, args.model)

    print(f"preflight: pinging {args.backend}/{args.model} ...")
    ping = await client.complete([Message(role="user", content="Reply with just: ok")])
    print(f"  model replied: {ping.text.strip()[:80]!r}\n")

    print(f"building image for {args.task} ...")
    img = await build_image(bundle)
    task = Task(task_id=os.path.basename(bundle), image_ref=img, instruction=instruction)

    print(f"running trial (max_steps={args.max_steps}) ...\n")
    async with DockerEnvironment(task) as env:
        tools = ToolServer(env)
        harness = Harness(ModelAgent(client), tools, Budgets(max_steps=args.max_steps))
        result = await harness.run(task.instruction)

        for s in result.steps:
            args_str = json.dumps(s.action.args)[:200]
            obs = json.dumps(s.observation.result)[:300]
            print(f"[step {s.index}] {s.action.tool} {args_str}\n   -> {obs}\n")

        print(f"halt: {result.halt_reason.value}   steps: {result.n_steps}")
        try:
            reward = await verify(env, bundle)
            print(f"reward: {reward}")
        except Exception as exc:  # noqa: BLE001
            print(f"verifier error: {exc}")


if __name__ == "__main__":
    asyncio.run(main())

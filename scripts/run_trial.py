"""Run one real trial via run_trial() and print the trajectory + reward.

Usage:
    python scripts/run_trial.py --backend anthropic --model claude-sonnet-4-6
    python scripts/run_trial.py --backend openai --model gpt-4.1

API keys are read from the environment (ANTHROPIC_API_KEY / OPENAI_API_KEY); this
script never prints them. Makes real API calls and runs a real Docker container.
The full trajectory is written under <run-dir>/<trial_id>/trajectory.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from miniharbor.agent import AnthropicClient, ModelAgent, OpenAIChatClient
from miniharbor.environment.docker import DockerEnvironment, build_image
from miniharbor.models import Budgets, Message, Task
from miniharbor.run import run_job


def build_client(backend: str, model: str):
    if backend == "anthropic":
        return AnthropicClient(model)
    if backend == "openai":
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        return OpenAIChatClient(base, model, api_key=os.environ.get("OPENAI_API_KEY", "none"))
    raise SystemExit(f"unknown backend: {backend}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="tasks/logfixbench/seed_001")
    ap.add_argument("--backend", default="anthropic", choices=["anthropic", "openai"])
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--max-steps", type=int, default=15)
    ap.add_argument("--run-dir", default="runs")
    args = ap.parse_args()

    bundle = os.path.abspath(args.task)
    instruction = open(os.path.join(bundle, "instruction.md")).read()
    client = build_client(args.backend, args.model)

    print(f"preflight: pinging {args.backend}/{args.model} ...")
    ping = await client.complete([Message(role="user", content="Reply with just: ok")])
    print(f"  model replied: {ping.text.strip()[:80]!r}\n")

    print(f"building image for {args.task} ...")
    img = await build_image(bundle)
    task = Task(task_id=os.path.basename(bundle), image_ref=img, instruction=instruction,
                tests_ref=os.path.join(bundle, "tests"))

    print(f"running trial (max_steps={args.max_steps}) ...\n")
    report = await run_job(
        [task], ModelAgent(client),
        run_dir=args.run_dir, env_factory=DockerEnvironment,
        attempts=1, concurrency=1, budgets=Budgets(max_steps=args.max_steps),
    )
    tr = report.trials[0]

    if tr.trajectory_ref:
        traj = json.load(open(tr.trajectory_ref))
        for s in traj["steps"]:
            args_str = json.dumps(s["action"]["args"])[:200]
            obs = json.dumps(s["observation"]["result"])[:300]
            print(f"[step {s['index']}] {s['action']['tool']} {args_str}\n   -> {obs}\n")

    print(f"status: {tr.status.value}   steps: {tr.n_steps}   "
          f"reward: {tr.reward.model_dump() if tr.reward else None}")
    print(f"log: {tr.trajectory_ref}")
    print(f"run manifest: {os.path.join(args.run_dir, 'manifest.json')}   pass@1: {report.pass_at_1}")
    if tr.error:
        print(f"error: {tr.error}")


if __name__ == "__main__":
    asyncio.run(main())

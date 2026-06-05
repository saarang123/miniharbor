import json
import os

from miniharbor.environment import FakeEnvironment
from miniharbor.logger import FileTrajectoryLogger
from miniharbor.models import Task, Trajectory
from miniharbor.verifier import FileContractVerifier

SEED1_TESTS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "tasks", "logfixbench", "seed_001", "tests")
)


async def test_verifier_injects_and_reads_reward():
    env = FakeEnvironment()
    await env.start()
    task = Task(task_id="t", image_ref="img", instruction="x", tests_ref=SEED1_TESTS)
    env._fs[task.verifier.reward_path] = b'{"reward":1.0,"passed":true,"breakdown":{"passed":8,"total":8}}'

    reward = await FileContractVerifier().verify(env, task)
    assert reward.passed and reward.reward == 1.0
    # the tests/ files were injected into the sandbox at inject_path
    assert f"{task.verifier.inject_path}/run.sh" in env._fs


def test_logger_writes_parseable_trajectory(tmp_path):
    ref = FileTrajectoryLogger(str(tmp_path)).write(Trajectory(trial_id="t1", task_id="task"))
    assert os.path.exists(ref)
    assert json.load(open(ref))["trial_id"] == "t1"

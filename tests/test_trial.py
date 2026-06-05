import os

from miniharbor.agent import ScriptedAgent
from miniharbor.environment import FakeEnvironment
from miniharbor.models import Action, Task, TrialStatus
from miniharbor.run import run_job
from miniharbor.trial import run_trial

SEED1_TESTS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "tasks", "logfixbench", "seed_001", "tests")
)


def _passing_env(task):
    env = FakeEnvironment()
    env._fs[task.verifier.reward_path] = b'{"reward":1.0,"passed":true,"breakdown":{}}'
    return env


async def test_run_trial_returns_trajectory_passed():
    task = Task(task_id="t", image_ref="img", instruction="do it", tests_ref=SEED1_TESTS)
    traj = await run_trial(task, ScriptedAgent([Action(tool="submit")]), _passing_env(task))
    assert traj.status == TrialStatus.passed
    assert traj.reward.reward == 1.0
    assert traj.harness_version and traj.toolserver_version    # provenance stamped


async def test_run_trial_failed_tests():
    task = Task(task_id="t", image_ref="img", instruction="do it", tests_ref=SEED1_TESTS)
    env = FakeEnvironment()
    env._fs[task.verifier.reward_path] = b'{"reward":0.3,"passed":false,"breakdown":{}}'
    traj = await run_trial(task, ScriptedAgent([Action(tool="submit")]), env)
    assert traj.status == TrialStatus.failed_tests


async def test_run_trial_agent_failed():
    class _Boom(ScriptedAgent):
        async def act(self, ctx):
            raise RuntimeError("boom")

    task = Task(task_id="t", image_ref="img", instruction="x")
    traj = await run_trial(task, _Boom([]), FakeEnvironment())
    assert traj.status == TrialStatus.agent_failed
    assert traj.reward is None


async def test_run_job_aggregates_and_writes_manifest(tmp_path):
    task = Task(task_id="t", image_ref="img", instruction="do it", tests_ref=SEED1_TESTS)
    report = await run_job(
        [task], ScriptedAgent([Action(tool="submit")]),
        run_dir=str(tmp_path), env_factory=_passing_env, attempts=2, concurrency=2,
    )
    assert report.n_trials == 2
    assert report.pass_at_1 == 1.0
    assert report.status_counts.get("passed") == 2
    assert os.path.exists(os.path.join(str(tmp_path), "manifest.json"))
    # each trial persisted a trajectory
    assert all(os.path.exists(t.trajectory_ref) for t in report.trials)

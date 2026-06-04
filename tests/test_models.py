from miniharbor.models import ExecResult, Task


def test_execresult_timed_out_defaults_false():
    r = ExecResult(stdout="", stderr="", exit_code=0, duration_ms=5)
    assert r.timed_out is False


def test_task_defaults():
    t = Task(task_id="x", image_ref="img:latest", instruction="do it")
    assert t.network == "none"                 # egress off by default
    assert t.workdir == "/workspace"
    assert t.resources.cpu == 2
    assert t.verifier.reward_path == "/logs/verifier/reward.json"
    assert t.verifier.inject_path == "/opt/verifier"

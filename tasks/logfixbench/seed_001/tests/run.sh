#!/usr/bin/env bash
# Verifier entrypoint. Runs the hidden tests against the final /workspace state
# and writes a structured reward to /logs/verifier/reward.json.
#
# Contract:
#   - this file + test_hidden.py are copied into the sandbox at inject_path
#     (see task.toml [verifier]); $HERE resolves to that path.
#   - exit code is ignored; the reward FILE is the authoritative result.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p /logs/verifier
cd /workspace

pytest -q "$HERE/test_hidden.py" \
  --json-report --json-report-file=/tmp/report.json >/dev/null 2>&1 || true

python3 - <<'PY'
import json
try:
    s = json.load(open("/tmp/report.json"))["summary"]
    total = s.get("total", 0) or 1
    passed = s.get("passed", 0)
    reward = passed / total
    ok = total > 0 and s.get("failed", 0) == 0 and s.get("error", 0) == 0
except Exception as exc:  # noqa: BLE001
    total, passed, reward, ok = 0, 0, 0.0, False
    detail = f"verifier error: {exc}"
else:
    detail = None

out = {
    "reward": round(reward, 4),
    "passed": bool(ok),
    "breakdown": {"passed": passed, "total": total},
}
if detail:
    out["detail"] = detail
json.dump(out, open("/logs/verifier/reward.json", "w"))
PY

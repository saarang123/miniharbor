FROM python:3.12-slim

WORKDIR /workspace

RUN pip install --no-cache-dir pytest==8.* pytest-json-report==1.5.*

# Bake the task's initial state + public tests into the image.
# Hidden tests (tests/) and the reference solution (solution/) are NOT copied;
# the verifier injects tests/ at grade time, after the agent has halted.
COPY workspace/ /workspace/

CMD ["sleep", "infinity"]

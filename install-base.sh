#!/usr/bin/env bash
# install-base.sh -- install the binaries MiniHarbor needs and set up the dev env.
# Primary target: a Debian/Ubuntu Linux host (worker / sandbox host), via apt.
# Idempotent: safe to re-run.
#
# macOS note: `brew install docker` installs only the CLI -- there is no daemon.
# For a daemon on macOS use:  brew install colima docker && colima start
# then run just the Python section:  python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
set -euo pipefail

log() { printf '\n[install-base] %s\n' "$*"; }

# --- OS guard -------------------------------------------------------------
if [[ "$(uname -s)" != "Linux" ]]; then
  log "Not Linux. On macOS, get a daemon with: brew install colima docker && colima start"
  log "Then: python3 -m venv .venv && ./.venv/bin/pip install -e '.[dev]'"
  exit 0
fi

if ! command -v apt-get >/dev/null 2>&1; then
  log "This script targets Debian/Ubuntu (apt). Adapt the package step for your distro."
  exit 1
fi

SUDO=""
[[ "$(id -u)" -ne 0 ]] && SUDO="sudo"

# --- system packages ------------------------------------------------------
log "Installing system packages"
$SUDO apt-get update -y
$SUDO apt-get install -y --no-install-recommends \
  ca-certificates curl git \
  python3 python3-venv python3-pip \
  docker.io \
  patch coreutils

# --- docker daemon --------------------------------------------------------
log "Enabling the docker daemon"
$SUDO systemctl enable --now docker 2>/dev/null || log "no systemd? start the docker daemon manually"
if [[ -n "$SUDO" ]]; then
  $SUDO groupadd -f docker
  $SUDO usermod -aG docker "$USER" || true
  log "Added $USER to the docker group -- log out/in (or run 'newgrp docker') for it to take effect"
fi

# --- python env -----------------------------------------------------------
log "Creating the Python venv and installing the package (with dev extras)"
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -e ".[dev]"

# --- warm the base image used by the example tasks ------------------------
log "Pre-pulling the base image used by example tasks"
docker pull python:3.12-slim || log "skipped pull (daemon not ready yet -- re-run after relogin)"

log "Done. Verify:  docker run --rm hello-world  &&  ./.venv/bin/pytest -q"
log "Full container flow:  ./.venv/bin/pytest tests/integration -q"

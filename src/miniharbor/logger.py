"""Trajectory logging: persist the full record of a trial.

Versioned -- the captured schema is pinned (`logger_version` on every trajectory)
so a record's meaning is stable and downstream training can branch on it. The sink
is swappable behind the interface (local files now; object store / streaming later).
"""

from __future__ import annotations

import abc
import os

from .models import Trajectory

LOGGER_VERSION = "v1"


class TrajectoryLogger(abc.ABC):
    version: str = LOGGER_VERSION

    @abc.abstractmethod
    def write(self, trajectory: Trajectory) -> str:
        """Persist a trajectory; return an opaque ref (e.g. a path or object key)."""


class FileTrajectoryLogger(TrajectoryLogger):
    """Writes one trajectory.json per trial under <run_dir>/<trial_id>/."""

    version = LOGGER_VERSION

    def __init__(self, run_dir: str):
        self._run_dir = run_dir

    def write(self, trajectory: Trajectory) -> str:
        trajectory.logger_version = self.version       # stamp provenance at write time
        out_dir = os.path.join(self._run_dir, trajectory.trial_id)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "trajectory.json")
        with open(path, "w") as fh:
            fh.write(trajectory.model_dump_json(indent=2))
        return path

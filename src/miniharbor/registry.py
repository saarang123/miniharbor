"""Task registry: resolves a task_id to a concrete `Task`.

This is the seam that lets "where the task comes from" change without touching
anything else. Today: a local directory of task bundles. Later: a remote catalog
(`<org/name@version>`) -- swap the implementation, callers are unchanged.

The Environment never sees the Registry: a trial is started with a task_id, the
caller resolves it here, and hands the resulting `Task` to the Environment.
"""

from __future__ import annotations

import abc

from .models import Task


class Registry(abc.ABC):
    @abc.abstractmethod
    async def resolve(self, task_id: str) -> Task:
        """Resolve a task_id to a concrete, ready-to-run Task (image already built)."""

    @abc.abstractmethod
    async def list(self) -> list[str]:
        """List available task_ids."""


# A LocalRegistry (reads tasks/<family>/<seed>/task.toml, builds/looks up the
# image, returns a Task) is the first concrete implementation -- left for the
# build step that needs it. It implements `resolve` by parsing the bundle's
# task.toml and pairing it with the built image_ref.

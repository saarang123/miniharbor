# Registry

The task/dataset catalog. It resolves a task or dataset selector to a concrete, validated, content-addressed task version that a job can reference. At v1 it is a local directory of task bundles plus a small index; the interface is shaped so registered remote datasets (`<org/name@version>`) swap in without changing callers.

> Port. v1 adapter: local task directory + index. Swap-to: registered datasets resolved from a remote catalog.

## What it resolves

A `Job.task_set` is a list of selectors. The registry turns each into one or more `(task_name, task_version, image_ref)` entries that trials reference:

```python
class TaskVersion(BaseModel):
    name: str
    version: str
    image_ref: str                 # content-addressed build digest
    bundle_path: str               # where the task bundle lives
    validation: ValidationReport   # the four-gate result
    status: str                    # "ready" | "rejected" | "unvalidated"

class Registry(Protocol):
    async def resolve(self, selector: str) -> list[TaskVersion]: ...     # name, family, or dataset
    async def get(self, name: str, version: str) -> TaskVersion: ...
    async def register(self, bundle_path: str) -> TaskVersion: ...       # build + validate + index
    async def list(self) -> list[TaskVersion]: ...
```

Selectors: a single task name, a family (`logfixbench`), or a dataset reference. v1 supports local names and families; the dataset reference is the swap-to.

## Registration = build + validate + index

`register` is where a bundle becomes usable:

```
1. read task.toml; locate environment/, tests/, solution/, instruction.md
2. build the image (BuildService) → record content digest as image_ref
3. validate (the four gates, see ../data-model.md §2):
     builds · solvable (solution passes) · non-trivial (empty agent fails) · deterministic (twice → same)
4. write ValidationReport; set status=ready or rejected
5. index: {name, version, image_ref, bundle_path, validation, status}
```

A task only becomes `ready` (and thus selectable in a job) if all four gates pass. This is what keeps rewards meaningful before any trials run.

## Versioning and content addressing

- A task `version` is bumped whenever the bundle changes (instruction, tests, environment). Past trials keep referencing the version they ran against.
- `image_ref` is a content digest, never a mutable tag, so a trial always runs the exact image that was validated.
- A dataset is a named, versioned set of task versions, so "run dataset D@v3" is fully reproducible.

## On-disk layout (v1)

```
tasks/
├── logfixbench/
│   ├── seed_001/          # a task bundle (see ../data-model.md §1)
│   ├── seed_002/
│   └── ...
└── index.json             # {name, version, image_ref, status, validation_ref}
```

The index is the v1 "catalog"; the swap-to replaces it with the metadata store and a remote dataset resolver, behind the same `Registry` interface.

## Relationship to the generator

Synthetic task families (e.g. a generated debugging family) produce bundles that are fed to `register`. Generation is cheap; validation is the product — the four gates plus a baseline-agent difficulty check decide whether a generated task is kept. The generator is upstream of the registry and out of scope for the core loop; the registry only cares that a bundle exists and can be validated.

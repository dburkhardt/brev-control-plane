# Brev Control Plane

`brev-control-plane` is a small Python CLI scaffold for planning and tracking
NVIDIA Brev instances that run distributed shell jobs.

The project is intentionally generic. It is meant for arbitrary command-line
workloads such as batch scripts, test shards, data processing jobs, or other
shell commands that can run across a temporary fleet.

## Current Scope

Implemented commands:

- `doctor` checks local prerequisites.
- `fleet plan` produces a dry-run fleet plan.
- `fleet apply` creates explicitly typed instances when `--yes` is present.
- `fleet exec` runs a generic shell command on instances matching a name prefix.
- `fleet down` deletes instances matching a name prefix when `--yes` is present.
- `inventory refresh` records `brev ls --json` output in a local SQLite database.
- `jobs validate` validates a generic shell-job JSON spec.

Safety defaults:

- `fleet plan` never creates instances.
- `fleet apply` requires an explicit instance type and `--yes`.
- `fleet down` only deletes names matching a prefix and requires `--yes`.
- Fleet planning emits JSON that can be reviewed by another tool or human.

Future versions can add explicit, confirmation-gated provisioning and cleanup
commands while keeping planning and state management testable.

## Install

```bash
python3 -m pip install -e ".[test]"
```

The runtime package uses the Python standard library. Tests use `pytest`.

## CLI Examples

Check local setup:

```bash
brev-control-plane doctor
```

Create a dry-run plan for four workers:

```bash
brev-control-plane fleet plan \
  --workers 4 \
  --cpu-min-vcpus 8 \
  --cpu-min-memory-gb 32 \
  --region us-west \
  --name-prefix worker
```

Create two explicitly typed workers:

```bash
brev-control-plane fleet apply \
  --workers 2 \
  --type n2d-highcpu-2 \
  --name-prefix worker \
  --yes
```

Run a command on those workers:

```bash
brev-control-plane fleet exec \
  --name-prefix worker \
  -- bash -lc 'hostname && curl -s https://ifconfig.me'
```

Delete those workers:

```bash
brev-control-plane fleet down --name-prefix worker --yes
```

Refresh local inventory from Brev:

```bash
brev-control-plane inventory refresh --db ./fleet.sqlite3
```

Validate a job spec:

```bash
brev-control-plane jobs validate ./job.json
```

Example `job.json`:

```json
{
  "command": "bash -lc 'python3 -m pytest -q'",
  "env": {
    "JOB_MODE": "ci"
  },
  "artifacts": [
    "logs/",
    "reports/"
  ],
  "max_runtime_seconds": 3600
}
```

## Development

Run tests:

```bash
python3 -m pytest -q
```

The Brev adapter is isolated behind `BrevClient`, so tests can use fake runners
without requiring the real `brev` binary.

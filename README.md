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
- `fleet bootstrap-workers` installs this package on matching instances and
  launches queue workers.
- `fleet exec` runs a generic shell command on instances matching a name prefix.
- `fleet check` runs generic host capability probes on matching instances.
- `fleet down` deletes instances matching a name prefix when `--yes` is present,
  then waits for cleanup unless `--no-wait` is set.
- `inventory refresh` records `brev ls --json` output in a local SQLite database.
- `jobs validate` validates a generic shell-job JSON spec.
- `jobs run` copies an optional source bundle, runs a generic shell-job spec,
  and can collect requested artifacts from bundle-backed jobs.
- `queue serve`, `queue submit`, `queue status`, and `queue wait` expose a small
  token-protected HTTP queue for generic shell jobs.
- `worker run` leases generic shell jobs, runs them in a local work directory,
  hashes requested outputs, and reports completion or failure.

Safety defaults:

- `fleet plan` never creates instances.
- `fleet apply` requires an explicit instance type and `--yes`.
- `fleet apply` can reject before instance creation with `--max-workers` and the
  cost guard trio `--budget-usd`, `--estimated-hourly-usd`, and `--max-hours`.
- `fleet down` only deletes names matching a prefix and requires `--yes`.
- Live fleet and job commands can use `--require-org` to fail fast if the active
  Brev org is not the expected org.
- Live fleet and job commands can use `--db` to record audit events in SQLite.
  For live exec and job commands, this persists unredacted full command strings
  and command output/error metadata. Do not enable it for secret-bearing
  commands or output unless that local SQLite record is acceptable.
- Fleet planning emits JSON that can be reviewed by another tool or human.

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
  --max-workers 4 \
  --budget-usd 20 \
  --estimated-hourly-usd 1.25 \
  --max-hours 4 \
  --require-org personal \
  --db ./fleet.sqlite3 \
  --yes
```

Check generic machine capabilities:

```bash
brev-control-plane fleet check \
  --name-prefix worker \
  --require-org personal \
  --db ./fleet.sqlite3
```

Run a command on those workers:

```bash
brev-control-plane fleet exec \
  --name-prefix worker \
  --require-org personal \
  --db ./fleet.sqlite3 \
  -- bash -lc 'hostname && curl -s https://ifconfig.me'
```

Run a generic bundle job:

```bash
brev-control-plane jobs run ./job.json \
  --name-prefix worker \
  --require-org personal \
  --db ./fleet.sqlite3 \
  --concurrency 4 \
  --artifact-dir /tmp/brev-control-plane-artifacts \
  --host
```

Delete those workers:

```bash
brev-control-plane fleet down \
  --name-prefix worker \
  --require-org personal \
  --db ./fleet.sqlite3 \
  --yes
```

Refresh local inventory from Brev:

```bash
brev-control-plane inventory refresh --db ./fleet.sqlite3
```

Validate a job spec:

```bash
brev-control-plane jobs validate ./job.json
```

Start a local queue server:

```bash
export BREV_QUEUE_TOKEN="replace-with-a-random-token"
brev-control-plane queue serve \
  --db ./queue.sqlite3 \
  --host 127.0.0.1 \
  --port 8080 \
  --token-env BREV_QUEUE_TOKEN
```

Submit a generic queued job:

```bash
brev-control-plane queue submit \
  --server-url http://127.0.0.1:8080 \
  --token-env BREV_QUEUE_TOKEN \
  --experiment-id batch-a \
  --output reports/result.json \
  --max-runtime-seconds 600 \
  --max-attempts 2 \
  -- python3 run.py
```

Run a worker loop:

```bash
brev-control-plane worker run \
  --server-url http://127.0.0.1:8080 \
  --token-env BREV_QUEUE_TOKEN \
  --work-dir /tmp/brev-control-plane-work
```

Bootstrap queue workers on matching instances:

```bash
brev-control-plane fleet bootstrap-workers \
  --name-prefix worker \
  --repo-url https://example.invalid/org/repo.git \
  --server-url http://queue.example.invalid:8080 \
  --token-env BREV_QUEUE_TOKEN
```

The bootstrap command renders scripts that reference the token environment
variable by name; it does not embed the token value in the generated command.

Example `job.json`:

```json
{
  "command": "python3 -m pytest -q",
  "env": {
    "JOB_MODE": "ci"
  },
  "bundle": {
    "source": "./example-project",
    "exclude": [
      ".git",
      ".venv",
      "runs",
      "dist"
    ]
  },
  "artifacts": [
    "reports/",
    "logs/job.log"
  ],
  "max_runtime_seconds": 3600
}
```

Artifact paths are relative to the remote job working directory created from the
bundle. `jobs validate` rejects unsafe artifact entries such as absolute paths,
empty strings, `.`, `..`, Windows drive paths, backslash paths, and parent
traversal. `jobs run` requires a bundle when artifacts are requested, archives
the requested paths on the remote machine, and extracts them locally under
`--artifact-dir/<run-id>/<instance-name>/`.

## Development

Run tests:

```bash
python3 -m pytest -q
```

The Brev adapter is isolated behind `BrevClient`, so tests can use fake runners
without requiring the real `brev` binary.

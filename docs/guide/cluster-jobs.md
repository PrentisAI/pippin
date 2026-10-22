# GPU jobs: a SLURM cluster or a GPU machine over SSH

The experiment's container is where the research agent writes code and tries it
on small inputs. Training a model or a long run belongs on a GPU. AURORA reaches
GPUs in one of two ways:

- **a SLURM cluster** (`compute.slurm`): jobs go through the queue and run on a
  node in the experiment's image, with the workspace mounted (shared storage);
- **one GPU machine over SSH** (`compute.ssh`): a lab server or a cloud VM with
  no scheduler. The workspace is copied there, the job gets free GPUs, and its
  files are copied back when it ends ([below](#a-gpu-machine-over-ssh)).

Either way the agent uses the same `aurora job` commands and keeps working while
a job runs:

```
agent (in its container, this machine)          cluster node (GPU)
  writes /app/train.py, tests it on 100 rows
  aurora job submit --gpus 1 -- python train.py ──► same image, /app = the same workspace
  aurora job await 1, ends its turn                runs for hours, writes /app/results/...
  (paused: no model calls, its time not counted)
  resumed with "job 1 completed, exit 0" ◄────────  ends: exit code, output
  reads /app/results/...
```

The job runs the experiment's own Docker image (through pyxis/enroot,
`srun --container-image`) with the experiment's workspace mounted as `/app`, so
it sees the agent's code and data, and its results land where the agent looks.
GPUs are held only while a job runs, not while the agent thinks or waits for you.

## Requirements (SLURM)

- SLURM with the [pyxis](https://github.com/NVIDIA/pyxis) plugin on the nodes
  (`srun --help` lists `--container-image`), and `enroot` on the machine running
  AURORA (to save the image for the nodes).
- The workspace on storage the nodes see (`AURORA_HOME` on a shared file system)
  and `sandbox.mount_workspace: true` (the default).
- The Docker container (`sandbox.docker`). Agents may work from the host (the
  default) or inside the container (`sandbox.agent_in_container`); either way
  they use the same `aurora job` command. Inside the container it needs
  `python3` in the image.

## Configuration (SLURM)

In **Settings → Cluster jobs**, or in the config file:

```yaml
compute:
  slurm:
    enabled: true
    submit_via: [ssh, login-node]     # how to reach sbatch; leave out if it runs here
    partition: gpu
    partitions: [gpu-low]             # others the agent may choose
    job_name_prefix: alice-aurora     # some clusters want your user name first
    max_gpus_per_job: 4
    max_jobs: 4                       # queued or running at once, per experiment
    max_time: "24:00:00"
    max_gpu_hours: 200                # per experiment; leave out for no limit
```

All keys are listed in the [configuration reference](../reference/configuration.md#computeslurm).
CPUs and memory follow the GPUs (`cpus_per_gpu`, `mem_per_gpu_gb`); a job may
ask for less. `extra_args` adds `#SBATCH` options (e.g. `--exclude=node3`).

## What the agent does

The research agent is told about the cluster in its instructions and gets one
extra command, `aurora job` (on this machine, not inside `docker exec`; for
agents inside the container, in the container):

| Command | |
|---|---|
| `aurora job submit --name train --gpus 1 --time 2:00:00 -- python train.py` | submit; prints the job's number and where its output goes |
| `aurora job status [<id>]` | the jobs and their states |
| `aurora job await <id> [<id> ...]` | then end the turn: the agent is resumed when the jobs end |
| `aurora job wait <id> [--minutes 9]` | wait in place for a short job (at most 9 minutes a call) |
| `aurora job logs <id> [--tail 200]` | its output |
| `aurora job cancel <id>` | cancel it |

After `--`, several words are one command (`python train.py --lr 3e-4`); a single
quoted argument is a shell line (`"python a.py && python b.py"`). Every request is
checked against the limits above before anything is submitted. Each job's files
are in `/app/.aurora/jobs/<id>-<name>/`: `run.sh` (the command), `slurm.out`
(its output) and `exit_code`.

Packages the agent installed in the container are not in the image: the agent is
told to install them into `/app` (a virtual environment in `/app/.venv`, say) or
in the job's command.

## Waiting for a job

For anything longer than a few minutes the agent runs `aurora job await <ids>`
and ends its turn with a note of what it waits for. Then:

- its session is paused: no model calls, so waiting costs nothing;
- its research job's clock stops: `agent.research_timeout_minutes` counts only
  the time the agent works, not the hours a training run takes;
- when the awaited jobs have ended, the same session is resumed at once (with
  all its context) and shown how each ended, its exit code and where its output is;
- while they have not, it is resumed now and then for a **check-in** and shown
  their state as `aurora job status` prints it: after 10 minutes, three times,
  then 20, 30, 45 and every 60 minutes (`check_in_minutes`). The agent decides
  what the state means: a job pending because it asks more than the partition
  allows is cancelled and submitted again; if SLURM did not answer, the states
  may be out of date. Then it awaits again or goes on;
- a message you send meanwhile wakes the agent at once, with your message.

The intervals start over whenever awaited jobs end or you write. A failed query
to SLURM is never taken for the jobs having ended. A job whose submission fails
never gets this far: `aurora job submit` reports the error at once.

The experiment's own limits still apply while it waits: `budget.max_hours` is
wall-clock time, so for long training raise it as well. The stream shows
*Waiting for cluster job ...* and *Resumed*, and the front desk can tell you what
the agent waits for.

Jobs the agent does not await keep running when its session ends; a later job or
the analysis can pick up their results. When the experiment ends, its jobs still
queued or running are cancelled (`cancel_on_finish`).

## A GPU machine over SSH

For a GPU server without SLURM (a lab workstation with 8 GPUs, a cloud VM),
turn on `compute.ssh` instead of `compute.slurm` (not both). In **Settings → GPU
machine (SSH)**, or in the config file:

```yaml
compute:
  ssh:
    enabled: true
    host: gpu-box                     # a Host in ~/.ssh/config, or user@address
    ssh_options: [-p, "2222"]         # anything else ssh needs
    remote_dir: aurora-jobs           # workspaces live here (relative: the home directory)
    docker: true                      # run jobs in Docker there; false: directly
    setup: ""                         # run before every job, e.g. "source ~/miniconda3/bin/activate ml"
    gpus: [0, 1, 2, 3]                # GPUs jobs may use; leave out for all
    busy_memory_mb: 2000              # a GPU using more is someone else's
    max_gpus_per_job: 4
    max_jobs: 4
    max_time: "24:00:00"
    max_gpu_hours: 200
```

**Requirements.** The machine running AURORA reaches the GPU machine with
`ssh <host>` and a key, with no password prompt (AURORA uses `BatchMode`), and
both have `rsync`. The GPU machine has `nvidia-smi`, `pkill` and `timeout` (any
Linux), and, with `docker: true`, Docker with GPU support (the NVIDIA container
toolkit: `docker run --gpus all ...` works). **Settings → Setup** checks that the
machine answers and how many GPUs it has.

**What happens to a job:**

1. *GPUs.* AURORA asks `nvidia-smi` which GPUs are free: among `gpus`, those no
   job of this console holds and whose memory use is at most `busy_memory_mb`
   (so other people's work is left alone). With too few free, the job waits,
   shown as *pending* with the reason (*waiting for 2 free GPU(s) (1 free now of 4)*),
   and starts as soon as enough are free: queued jobs are tried every 20 seconds.
2. *Files.* The experiment's workspace is copied to
   `<remote_dir>/<experiment>/` with `rsync` (only what changed; a file newer
   there is kept). `sync_exclude` lists paths not copied (`.git` and caches by default).
3. *Running.* With `docker: true` the job runs in the experiment's image with
   only its GPUs visible (`--gpus "device=2,3"`), the copied workspace as `/app`,
   CPUs and memory from `cpus_per_gpu` and `mem_per_gpu_gb`, as your user on
   that machine. If the machine does not have the image, AURORA pulls it there,
   or else sends it (`docker save | ssh ... docker load`) once. With
   `docker: false` the job runs directly in the copied workspace with
   `CUDA_VISIBLE_DEVICES` set; the agent is told to use relative paths and set
   up its packages in the job's command (or use `setup`).
4. *Time limit.* `timeout` stops it at its limit; it then ends as `TIMEOUT`.
5. *The end.* AURORA checks the job every 20 seconds. When it ends, the
   workspace is copied back (files newer here are kept) and its GPUs are free
   again. `aurora job logs` shows the output live while it runs; the copy in the
   workspace arrives at the end.

Cancelling stops the job's whole process session (and its container). When the
experiment ends, its jobs still waiting or running are stopped and what they
wrote until then is copied back (`cancel_on_finish`).

## In the console

When an experiment submits its first job, a **Jobs** button appears on its right
bar: every job with its state, node (or machine and GPUs), resources, time, exit
code and command, the GPU hours used, a link to its output, and *cancel*. Job
events also appear in the stream (submitted, running, ended), and the
`compute_job` notification can tell you when one ends.

**GPU usage.** Above the jobs, a small table shows what the GPUs are doing: use
(%), memory, and which job holds each one. On a SLURM cluster it covers the GPUs
of the running jobs (AURORA runs `nvidia-smi` in the job's allocation, with
`srun --jobid ... --overlap`); on a GPU machine it covers every GPU jobs may use,
marked *free*, held by a job, held by another experiment, or *in use (not
AURORA)*. It refreshes every 30 seconds. When a running job's GPUs sit near 0%,
the panel says so: the job may be loading data or running on the CPU.

## How it works (SLURM)

- **Image.** The container's image is saved once as a squashfs file
  (`enroot import`) under `compute.slurm.image_dir` (default
  `AURORA_HOME/compute/images`), named by the image's ID, and reused. Large images
  take a few GB there.
- **Files.** Jobs run as your cluster user, while files written in the container
  belong to root. AURORA makes the workspace writable for jobs before each one is
  submitted (and files the agent creates afterwards are writable too), so a job
  can write next to the agent's code.
- **Agents inside the container** reach the job manager through its socket's
  directory, mounted as `/aurora/run`, with a small client (standard-library
  Python) mounted as `/aurora/lib/aurora_job.py` and `aurora` on the `PATH`.
- **Following jobs.** `squeue` while a job is queued or running, then
  `scontrol show job` and the job's `exit_code` file; SLURM accounting
  (`sacct`) is not needed.
- **Safety.** The agent cannot change the batch script: it gives a command, which
  runs inside the container on the node, and options checked against the limits.
  The scripts and job records are kept under `AURORA_HOME/compute/`, outside the
  directory the agent may edit, and a job is cancelled only by its own name.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `could not submit: ... Invalid partition` | `partition` is not a partition of the cluster (`sinfo -s`) |
| the job ends at once with no output | the node does not see the workspace or the image: put `AURORA_HOME` (or `image_dir`) on shared storage |
| `enroot import ... failed` | `enroot` missing on this machine, or no room in `image_dir` |
| `nvidia-smi` not found in the job | the node's enroot has no NVIDIA hook; ask the cluster's admins |
| a warning about the job name | set `job_name_prefix` to start with your cluster user name |
| (SSH) *could not reach* / *Permission denied* | `ssh -o BatchMode=yes <host> true` must work as the user running AURORA |
| (SSH) a job stays *waiting for free GPUs* | the GPUs are busy (someone's memory is above `busy_memory_mb`), or `gpus` lists fewer than it asks |
| (SSH) *could not get image ... onto* the machine | Docker missing there, or no room for the image; or use `docker: false` |
| (SSH) `could not select device driver "" with capabilities: [[gpu]]` | the NVIDIA container toolkit is missing on the GPU machine |
| GPU usage shows *Could not read* | SLURM: `srun --overlap` not allowed for your jobs; SSH: `nvidia-smi` missing there |

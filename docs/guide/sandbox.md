# Sandbox and security

Pippin's agents write and run code. This page says what they can and cannot
touch.

## With Docker (default)

Each campaign gets its own container, started from `sandbox.image` with
`sandbox.cpus`, `sandbox.memory_mb` and network access per `sandbox.network`.
The Claude Code sessions run on the host but are restricted by Claude Code's
permission system (`--permission-mode dontAsk` with an allow-list):

| The agents can | The agents cannot |
|---|---|
| read and edit files inside the campaign directory | read or edit any other file on the host |
| run shell commands as `docker exec` into *their* container | run shell commands on the host, or `docker run`/`cp`/anything else |
| search and fetch the web | — |

With [GPU jobs](cluster-jobs.md) on, the agents may also run `aurora job`
on the host, which only passes their requests to the experiment's job manager.
Anything not on the allow-list is refused without asking. Inputs you list under
`inputs` are copied to `/app/inputs`; the agents do not see the original paths.

The container's `/app` is the host directory `<campaign>/workspace/`
(`sandbox.mount_workspace`, on by default): what the agents write there is on
the host at once, nothing is copied, and a resumed campaign finds its files in
place. Files written in the container belong to root while it runs; when the
container is removed they are handed back to you. If Docker runs on another
machine, set `mount_workspace: false`: `/app` then lives in the container and is
copied to `<campaign>/workspace/` after every research job and at the end.

To look at something it made in the container (a figure, say), an agent opens
it under `<campaign>/workspace/` (without the mount: it copies that one file to
`<campaign>/view/`; copying anywhere else on the host is refused).

Agent sessions get a minimal environment: your OpenRouter key (as Claude Code's
token) and basic variables such as `PATH` and `HOME`; other secrets in your
environment are not passed on.

### Agents inside the container (experimental)

```yaml
sandbox:
  agent_in_container: true
```

The research agent then runs inside the campaign container itself rather than on
the host. There it may do anything (install packages, run loops and pipes, open
the figures it makes) without the host's allow-list getting in the way; the
container is the boundary. The other roles (screening, planning, analysis,
writing, both auditors, the front desk) stay on the host.

The API key does not enter the container. A small proxy container holds it; the
campaign container gets a random token that only this proxy accepts, and the
two are joined by an internal network. With `network: false` the campaign
container reaches the proxy and nothing else. The agent's sessions are kept in
`<campaign>/agent_home/`, so a resumed campaign continues them.

Requirements and limits: `mount_workspace` on; an image with glibc (Debian,
Ubuntu, most Python and CUDA images; not Alpine); the Claude Code executable on
the host is mounted into the container read-only. The agent shares the
container's memory with what it runs: if a large job runs the container out of
memory and the agent's session dies with it, the job continues that session
once. If Claude Code cannot run in the image, agents work from the host as usual
(a warning is logged).

### CPU and memory

The defaults (4 CPUs, 8 GB) suit small tasks. For large data, give the
container more, in the config:

```yaml
sandbox:
  cpus: 32
  memory_mb: 65536
```

or per experiment under *Advanced settings* on the proposal card. While an
experiment runs you can change its limits at any time: click *Container* in the
experiment's header (or ask the front desk). The running container is updated
in place (`docker update`; nothing restarts), the research agent is told, and a
resume or re-run keeps the new limits. Limits above what the Docker host has
are refused; lowering the memory below what the container uses can kill its
processes.

Pick an image that already has the libraries your task needs, or let the
agents install them (`sandbox.network: true`). For sensitive data, use
`sandbox.network: false` and a prepared image.

## Without Docker

```yaml
sandbox:
  docker: false
```

The agents work directly in the campaign directory on the host and can only
read and write files there — no shell commands. That is enough for writing and
analysis of provided material, not for running experiments. To let them run
commands on the host:

```yaml
sandbox:
  docker: false
  allow_host_bash: true      # only on a disposable machine, for tasks you trust
```

With `allow_host_bash` the agents can run any command as your user.

## Acceptance tests

```yaml
sandbox:
  tests_dir: ./acceptance-tests     # test.sh, or pytest files
```

After each research job the tests are copied into the container as `/tests`,
run (`bash /tests/test.sh`, else `pytest /tests`) and removed again; a job counts
as done when they pass (exit code 0). Failing jobs are resumed with the test
output, up to three times. The agents never see the tests while they work.

## The web console

The console has no authentication and runs campaigns with your API key. It
listens on 127.0.0.1 by default; use an SSH tunnel for remote access.

## Reporting a vulnerability

See `SECURITY.md` in the repository.

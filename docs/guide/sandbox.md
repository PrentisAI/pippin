# Sandbox and security

AURORA's agents write and run code. This page says what they can and cannot
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

Anything not on the allow-list is refused without asking. Inputs you list under
`inputs` are copied into the container (`/app/inputs`); the agents do not see
the original paths. When the campaign ends `/app` is copied back to
`<campaign>/workspace/` and the container is removed.

Agent sessions get a minimal environment: your OpenRouter key (as Claude Code's
token) and basic variables such as `PATH` and `HOME`; other secrets in your
environment are not passed on.

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

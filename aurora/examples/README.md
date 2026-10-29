# Examples that come with Pippin

Each folder is a finished run that a new console installs as a read-only project
(`ex_<id>`), see `aurora/examples/__init__.py`:

| File | What it is |
|---|---|
| `example.json` | the card (title, blurb, field, what it shows), the project and its run |
| `run/` | the run's directory, slimmed: state files, phases, audit records, and the workspace's code and outputs |
| `events.jsonl` | the run's stream |

Paths of the console the run happened in are `{AURORA_HOME}` and `{PROJECT_ID}`,
filled in when the example is installed.

`citi-bike` was built from a run in a development console with:

```bash
python scripts/build_example.py --home <AURORA_HOME> --project <project id> \
  --run 20261019-025018_citi-bike-usage-patterns --id citi-bike --title "Citi Bike usage patterns" \
  --field data --blurb "When and where New Yorkers ride, and how it changes with the seasons: from a question about 79 million trips to a report with eight figures." \
  --shows ideation screening "a decision" planning research "live auditor" analysis report \
  --keep-chat "i am wondering why the screening stage doesn't favor any of the three proposals" \
              "can i provide an ideation of my own so that no ideation stage needs to be rerun" \
  --scrub ".claude-<user>=.claude" "data-<user>-=" --out aurora/examples/citi-bike
```

The build keeps no downloaded data, no uploaded files and no Claude Code session
records, and stops if anything looks like a key; check the result for names and
paths before committing it.

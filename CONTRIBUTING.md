# Contributing

Thanks for helping improve AURORA. Bug reports, documentation fixes, new
examples and code changes are all welcome.

## Reporting a problem

Open an issue with what you ran (the config, without keys), what happened, and
the end of the log. `aurora doctor` output helps. For a campaign that went
wrong, the files in `<campaign>/audit_records/`, `human_gates.jsonl` and
`phases/research/research_loop.jsonl` usually show where.

## Making a change

```bash
pip install -e ".[dev,docs]"
pytest && ruff check aurora tests
python scripts/gen_config_reference.py      # if you changed aurora/config.py
```

- Keep pull requests focused; describe the user-visible effect.
- Add or update tests (they run offline in mock mode).
- Document new configuration fields with a `description`; the reference is
  generated from it.
- Update the docs page that describes the behaviour you changed.
- Frontend changes: `cd frontend && npm run build && npm run lint`.

By contributing you agree that your contribution is licensed under the MIT
license of this repository.

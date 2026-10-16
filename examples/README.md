# Examples

| Example | What it shows | Time / cost |
|---|---|---|
| `aurora demo` (built in) | the whole pipeline offline with scripted mock models | seconds, free |
| [quickstart](quickstart/) | a real campaign on a small dataset with a known answer | minutes, ~US$1-3 |
| [open_ended](open_ended/) | the full pipeline from a broad question: multi-agent ideation, screening, planning, research, analysis, writing | 1-3 h, ~US$15-40 |
| [cu_zn_segregation](cu_zn_segregation/) | the materials-science case study of the paper (needs a simulation container) | days, see its README |

Paths in a config file are relative to the file, so examples run from anywhere:

```bash
aurora run examples/quickstart/aurora.yaml
```

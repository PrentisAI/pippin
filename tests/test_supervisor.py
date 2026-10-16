from aurora.core.supervisor import parse_verdicts
from aurora.models.audit import AuditOutcome


def test_parses_verdict_lines():
    text = ('The agent fits on the training set only.\n'
            'VERDICT {"outcome": "advise", "reason": "no held-out split", "suggestion": "hold out 20%"}')
    [v] = parse_verdicts(text)
    assert v.outcome == AuditOutcome.ADVISE and v.suggestion == "hold out 20%"


def test_all_four_outcomes_and_unknowns():
    text = "\n".join(f'VERDICT {{"outcome": "{o}", "reason": "r"}}'
                     for o in ("continue", "interrupt", "escalate", "replan"))
    assert [v.outcome.value for v in parse_verdicts(text)] == ["continue", "interrupt", "escalate", "advise"]


def test_ignores_text_without_verdicts_or_with_bad_json():
    assert parse_verdicts("all good") == []
    assert parse_verdicts("VERDICT {not json}") == []

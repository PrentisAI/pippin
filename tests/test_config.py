import pytest
import yaml
from pydantic import ValidationError

from aurora.config import AuroraConfig, load_config


def test_only_objective_is_required():
    cfg = AuroraConfig(objective="Does X improve Y?")
    assert cfg.campaign_id.startswith("does-x-improve-y-")
    assert cfg.sandbox.docker and cfg.audit.milestone and cfg.audit.live
    assert cfg.models.agent.startswith("anthropic/")


def test_unknown_keys_are_rejected():
    with pytest.raises(ValidationError, match="modles"):
        AuroraConfig(objective="x", modles={})
    with pytest.raises(ValidationError, match="imgae"):
        AuroraConfig(objective="x", sandbox={"imgae": "python:3.12"})


def test_skip_phases_are_validated():
    assert AuroraConfig(objective="x", skip_phases=["writing"]).skip_phases == ["writing"]
    with pytest.raises(ValidationError, match="unknown phases"):
        AuroraConfig(objective="x", skip_phases=["roundtable"])


def test_mock_mode_replaces_every_model_and_external_dependency():
    cfg = AuroraConfig(objective="x", mock_mode=True)
    models = cfg.models.model_dump()
    assert all(str(v).startswith("mock-") or all(s.startswith("mock-") for s in v) for v in models.values())
    assert not cfg.sandbox.docker and not cfg.audit.live and not cfg.human.frontdesk


def test_api_key_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    assert AuroraConfig(objective="x").api_key_value == "sk-env"
    assert AuroraConfig(objective="x", api_key="sk-cfg").api_key_value == "sk-cfg"
    assert AuroraConfig(objective="x").claude_code_base_url == "https://openrouter.ai/api"


def test_load_config_with_overrides(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"objective": "from file", "ideation": {"mode": "independent"}}))
    cfg = load_config(path, objective="from cli", campaign_id=None)
    assert cfg.objective == "from cli"
    assert cfg.ideation.mode == "independent"
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.yaml")

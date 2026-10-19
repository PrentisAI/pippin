import pytest

from aurora.config import AuroraConfig
from aurora.core import requirements
from aurora.core.requirements import RuntimeRequirementError, check_runtime_requirements


def test_mock_mode_needs_nothing(monkeypatch):
    monkeypatch.setattr(requirements.shutil, "which", lambda _: None)
    check_runtime_requirements(AuroraConfig(objective="x", mock_mode=True))


def test_lists_every_missing_dependency(monkeypatch):
    monkeypatch.setattr(requirements.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeRequirementError) as err:
        check_runtime_requirements(AuroraConfig(objective="x", inputs=["no/such/dir"]))
    msg = str(err.value)
    for needle in ("Claude Code CLI not found", "Docker is required", "No API key",
                   "Input not found: no/such/dir", "mock_mode: true"):
        assert needle in msg


def test_passes_when_everything_is_there(monkeypatch):
    monkeypatch.setattr(requirements.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    check_runtime_requirements(AuroraConfig(objective="x", sandbox={"docker": False}))


def test_unknown_model_ids_are_reported_with_suggestions(monkeypatch):
    monkeypatch.setattr(requirements, "available_models",
                        lambda *a, **k: {"anthropic/claude-opus-5.5", "anthropic/claude-sonnet-5.5",
                                         "anthropic/claude-haiku-5.5", "openai/gpt-5.6-sol",
                                         "google/gemini-3.1-pro-preview", "moonshotai/kimi-k3"})
    cfg = AuroraConfig(objective="x", models={"research": "anthropic/claude-opus-55"})
    [problem] = requirements.unknown_models(cfg)
    assert "models.research" in problem and "anthropic/claude-opus-5.5" in problem
    assert requirements.unknown_models(AuroraConfig(objective="x")) == []

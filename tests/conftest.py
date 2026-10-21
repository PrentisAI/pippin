import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own AURORA_HOME and working directory, and no API keys."""
    monkeypatch.setenv("AURORA_HOME", str(tmp_path / "aurora_home"))
    monkeypatch.chdir(tmp_path)
    for key in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AURORA_SERVER_CONFIG"):
        monkeypatch.delenv(key, raising=False)
    # no network in tests: model IDs are not checked against OpenRouter unless a test says so
    from aurora.core import requirements
    monkeypatch.setattr(requirements, "available_models", lambda *a, **k: None)
    from aurora.api.routes import campaigns, skills
    from aurora.auth.store import auth_store
    auth_store.reset()
    campaigns.store.reset()
    campaigns._campaigns.clear()
    skills._store = None
    yield tmp_path
    auth_store.reset()
    campaigns.store.reset()
    skills._store = None

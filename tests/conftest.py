import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own AURORA_HOME and working directory, and no API keys."""
    monkeypatch.setenv("AURORA_HOME", str(tmp_path / "aurora_home"))
    monkeypatch.chdir(tmp_path)
    for key in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AURORA_SERVER_CONFIG"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path

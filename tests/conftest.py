import httpx
import pytest


def _offline() -> httpx.AsyncClient:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network in tests", request=request)
    return httpx.AsyncClient(transport=httpx.MockTransport(refuse))


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
    from aurora.auth.writes import write_log
    from aurora.brainstorm.store import bs_store
    from aurora.feed import sources as feed_sources
    from aurora.feed.store import feed_store
    from aurora.literature import indexes
    from aurora.literature.annotations import annotations
    from aurora.literature.library import library
    from aurora.literature.zotero import zotero_store
    from aurora.memory.store import mem_store
    from aurora.figures import figures
    from aurora.notebook.store import nb_store
    from aurora.review.store import review_store
    bs_store.reset()
    feed_store.reset()
    auth_store.reset()
    library.reset()
    zotero_store.reset()
    annotations.reset()
    review_store.reset()
    write_log.reset()
    mem_store.reset()
    nb_store.reset()
    from aurora.memory import onpage
    onpage.reset()
    figures.reset()
    indexes.clear_cache()
    # no network for the feed either: tests that need sources bring their own
    monkeypatch.setattr(feed_sources, "_shared", feed_sources.Sources(client=_offline()))
    # no network: the scholarly indexes are asked only by tests that bring their own
    monkeypatch.setattr(indexes, "_shared", indexes.Indexes(use=()))
    campaigns.store.reset()
    campaigns._campaigns.clear()
    skills._store = None
    yield tmp_path
    feed_store.reset()
    bs_store.reset()
    auth_store.reset()
    library.reset()
    zotero_store.reset()
    annotations.reset()
    review_store.reset()
    write_log.reset()
    mem_store.reset()
    figures.reset()
    campaigns.store.reset()
    skills._store = None


@pytest.fixture(autouse=True)
def no_conductor(monkeypatch):
    """No agent joins a brainstorm unasked, unless a test turns the conductor on (its own tests do)."""
    from aurora.brainstorm import conductor
    monkeypatch.setattr(conductor, "ENABLED", False)

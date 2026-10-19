import pytest
from tests.support import candidate_fixture


@pytest.fixture
def candidate():
    return candidate_fixture()


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch, candidate):
    """Discovery tests must never read private profiles or write the user's DB/logs."""
    import agent.discovery as discovery
    import agent.logging_config as logging_config
    import agent.db as db
    monkeypatch.setattr(discovery, 'profile', lambda: candidate)
    monkeypatch.setattr(discovery, 'active_api_sources', lambda: ([], []))
    for module in (discovery, logging_config, db):
        monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'data').mkdir()
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    yield
    import logging
    logger = logging.getLogger('agent')
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

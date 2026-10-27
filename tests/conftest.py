import os
import pytest

from dotenv import load_dotenv

def _is_live_run_enabled(config: pytest.Config) -> bool:
    """Return True when live tests are explicitly requested."""
    env_flag = os.getenv("RUN_LIVE_TESTS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    markexpr = (config.getoption("-m") or "").strip().lower()
    return env_flag or ("live" in markexpr)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip live tests by default unless explicitly enabled."""
    if _is_live_run_enabled(config):
        return

    skip_live = pytest.mark.skip(
        reason=(
            "Live provider test skipped by default. "
            "Run `pytest -m live` or `RUN_LIVE_TESTS=1 pytest`."
        )
    )
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(autouse=True)
def mock_env_vars(request):
    """Ensure all required environment variables are set for tests."""
    from app.config import get_settings
    markers = [marker.name for marker in request.node.iter_markers()]
    if "live" in markers:
        # Resolve absolute path to project root .env
        dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
        loaded = load_dotenv(dotenv_path, override=True)
        get_settings.cache_clear()
        return

    from app import storage
    from app.services import runtime_config
    from app.services.config_store import ConfigStore
    from app.models.runtime_config_models import RuntimeConfig
    from app.runtime import ACTIVE_SESSIONS

    monkeypatch = request.getfixturevalue("monkeypatch")
    temporary = request.getfixturevalue("tmp_path")
    for key, value in {"SARVAM_API_KEY": "test_sarvam", "DEEPGRAM_API_KEY": "test_dg", "CEREBRAS_API_KEY": "test_cb", "CARTESIA_API_KEY": "test_ct"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(storage, "DATA_DIR", temporary / "data")
    store = ConfigStore()
    store._config = RuntimeConfig.default()
    monkeypatch.setattr(runtime_config, "_store", store)
    ACTIVE_SESSIONS.clear()
    get_settings.cache_clear()
    yield
    ACTIVE_SESSIONS.clear()
    get_settings.cache_clear()

import pytest

from quantdesk.analytics.simulation import synthetic_ohlcv


@pytest.fixture
def ohlcv():
    return synthetic_ohlcv(days=900, seed=11)


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Point every stateful file (paper account, audit log, kill switch, caches) at a temp dir."""
    from quantdesk import config

    config.get_settings.cache_clear()
    s = config.get_settings()
    s.paths.audit_log = str(tmp_path / "audit.jsonl")
    s.paths.paper_account = str(tmp_path / "paper.json")
    s.paths.kill_switch_file = str(tmp_path / "KILL")
    s.data.cache_dir = str(tmp_path / "cache")
    s.llm.cache_file = str(tmp_path / "llm.json")
    monkeypatch.setattr(config.Settings, "path", lambda self, rel: _tmp_path(tmp_path, rel))
    yield s
    config.get_settings.cache_clear()


def _tmp_path(tmp_path, rel):
    from pathlib import Path

    p = Path(rel)
    if not p.is_absolute():
        p = tmp_path / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

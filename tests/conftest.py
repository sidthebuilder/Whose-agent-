import json

import pytest

from whoseagent import paths
from whoseagent.registry import load

from helpers import ANTHROPIC, JIO, MICROSOFT, rdap_fixture


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test gets its own cache, logs and outbox, checks against the 31 October snapshot (never the live
    copy, which changes), and sees no Switch credentials from the environment."""
    monkeypatch.setattr(paths, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(paths, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(paths, "OUTBOX", tmp_path / "outbox")
    monkeypatch.setenv("WHOSEAGENT_USE_SNAPSHOT", "1")
    for k in ("SWITCH_API_ENDPOINT", "SWITCH_API_TOKEN", "SWITCH_AGENT_ID"):
        monkeypatch.delenv(k, raising=False)
    yield tmp_path


@pytest.fixture(scope="session")
def reg():
    return load(prefer_live=False)


@pytest.fixture
def rdap_cache(isolated):
    """The real registry answers, as the cache would hold them, so commands can run with --offline."""
    records = {}
    for name in (MICROSOFT, ANTHROPIC, JIO):
        r = rdap_fixture(name)
        records[f"{r['start']}-{r['end']}"] = r
    paths.CACHE.mkdir(parents=True, exist_ok=True)
    (paths.CACHE / "rdap.json").write_text(json.dumps(records), encoding="utf-8")
    return records

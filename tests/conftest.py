from pathlib import Path

import pytest


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_home = tmp_path / "config"
    config_home.mkdir()

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    return config_home

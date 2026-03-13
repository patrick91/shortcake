from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pygit2

from shortcake._git._pygit2 import fetch_remote
from tests._git_helpers import Repo


def test_fetch_remote_success(temp_repo: Repo) -> None:
    remote = MagicMock()

    with patch(
        "shortcake._git._pygit2.open_pygit2_repo",
        return_value=SimpleNamespace(remotes={"origin": remote}),
    ) as mock_open:
        assert fetch_remote(temp_repo)

    mock_open.assert_called_once_with(temp_repo)
    remote.fetch.assert_called_once_with()


def test_fetch_remote_failure(temp_repo: Repo) -> None:
    remote = MagicMock()
    remote.fetch.side_effect = pygit2.GitError("fetch failed")

    with patch(
        "shortcake._git._pygit2.open_pygit2_repo",
        return_value=SimpleNamespace(remotes={"origin": remote}),
    ):
        assert not fetch_remote(temp_repo)

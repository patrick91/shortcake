"""Tests for the metadata module."""

from pathlib import Path

import pytest

from shortcake.metadata import (
    MetadataStore,
    delete_branch_metadata,
    get_all_branch_metadata,
    get_branch_metadata,
    get_children,
    update_branch_metadata,
)


def test_metadata_store_not_in_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    non_repo = tmp_path / "not_a_repo"
    non_repo.mkdir()
    monkeypatch.chdir(non_repo)

    with pytest.raises(FileNotFoundError, match="Not in a git repository"):
        MetadataStore()


def test_get_branch_metadata_empty(isolated_git_repo: Path, isolated_config: Path):
    metadata = get_branch_metadata("nonexistent")
    assert metadata == {}


def test_update_and_get_branch_metadata(isolated_git_repo: Path, isolated_config: Path):
    update_branch_metadata("feature", parent="main", pr_number=123)

    metadata = get_branch_metadata("feature")
    assert metadata["parent"] == "main"
    assert metadata["pr_number"] == 123


def test_delete_branch_metadata(isolated_git_repo: Path, isolated_config: Path):
    update_branch_metadata("feature", parent="main")
    assert get_branch_metadata("feature") != {}

    delete_branch_metadata("feature")
    assert get_branch_metadata("feature") == {}


def test_get_all_branch_metadata(isolated_git_repo: Path, isolated_config: Path):
    update_branch_metadata("feature1", parent="main")
    update_branch_metadata("feature2", parent="feature1")

    all_metadata = get_all_branch_metadata()
    assert "feature1" in all_metadata
    assert "feature2" in all_metadata
    assert all_metadata["feature1"]["parent"] == "main"
    assert all_metadata["feature2"]["parent"] == "feature1"


def test_get_children(isolated_git_repo: Path, isolated_config: Path):
    update_branch_metadata("feature1", parent="main")
    update_branch_metadata("feature2", parent="feature1")
    update_branch_metadata("feature3", parent="feature1")

    children = get_children("feature1")
    assert set(children) == {"feature2", "feature3"}

    children_of_main = get_children("main")
    assert "feature1" in children_of_main


def test_get_children_no_children(isolated_git_repo: Path, isolated_config: Path):
    update_branch_metadata("leaf", parent="main")

    children = get_children("leaf")
    assert children == []


def test_metadata_store_corrupted_file(isolated_git_repo: Path, isolated_config: Path):
    # Create a corrupted metadata file
    git_dir = isolated_git_repo / ".git"
    metadata_file = git_dir / "shortcake-metadata.json"
    metadata_file.write_text("{ invalid json")

    # Should recover gracefully
    metadata = get_branch_metadata("feature")
    assert metadata == {}

    # Writing should work and fix the file
    update_branch_metadata("feature", parent="main")
    metadata = get_branch_metadata("feature")
    assert metadata["parent"] == "main"


def test_metadata_store_missing_branches_key(isolated_git_repo: Path, isolated_config: Path):
    # Create a file without branches key
    git_dir = isolated_git_repo / ".git"
    metadata_file = git_dir / "shortcake-metadata.json"
    metadata_file.write_text('{"version": 1}')

    # Should handle gracefully
    metadata = get_branch_metadata("feature")
    assert metadata == {}


def test_update_branch_metadata_with_all_fields(isolated_git_repo: Path, isolated_config: Path):
    update_branch_metadata(
        "feature",
        parent="main",
        parent_revision="abc123",
        pr_number=42,
        pr_url="https://github.com/example/repo/pull/42",
    )

    metadata = get_branch_metadata("feature")
    assert metadata["parent"] == "main"
    assert metadata["parent_revision"] == "abc123"
    assert metadata["pr_number"] == 42
    assert metadata["pr_url"] == "https://github.com/example/repo/pull/42"


def test_update_branch_metadata_partial_update(isolated_git_repo: Path, isolated_config: Path):
    # Initial metadata
    update_branch_metadata("feature", parent="main", pr_number=1)

    # Partial update should preserve existing fields
    update_branch_metadata("feature", pr_number=2)

    metadata = get_branch_metadata("feature")
    assert metadata["parent"] == "main"  # Preserved
    assert metadata["pr_number"] == 2  # Updated

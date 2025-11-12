"""Pydantic models for test data structures."""

from pydantic import BaseModel, Field


class PRMetadata(BaseModel):
    """Metadata stored in git notes for tracking PR information."""

    pr_number: int | None = Field(None, description="GitHub PR number")
    parent: str | None = Field(None, description="Parent branch name")
    html_url: str | None = Field(None, description="GitHub PR URL")


class GitHubPRRef(BaseModel):
    """GitHub PR head/base reference."""

    ref: str
    sha: str


class GitHubPR(BaseModel):
    """GitHub Pull Request response."""

    number: int
    title: str
    body: str
    state: str
    head: GitHubPRRef
    base: GitHubPRRef
    html_url: str
    mergeable: bool | None = None


class GitHubBranchCommit(BaseModel):
    """GitHub branch commit information."""

    sha: str
    url: str


class GitHubBranch(BaseModel):
    """GitHub branch response."""

    name: str
    commit: GitHubBranchCommit

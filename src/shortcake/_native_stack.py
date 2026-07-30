"""Reconcile Shortcake PRs with GitHub's native stack resource."""

from dataclasses import dataclass
from enum import Enum

import httpx

from shortcake._github import GitHubClient, NativeStack

NATIVE_STACK_MINIMUM_MESSAGE = "GitHub native stacks require at least two PRs."
NATIVE_STACK_UNAVAILABLE_MESSAGE = (
    "Stacked pull requests are not available for this repository yet."
)


class NativeStackAction(str, Enum):
    """Outcome of native stack reconciliation."""

    CREATED = "created"
    UPDATED = "updated"
    RECREATED = "recreated"
    UNCHANGED = "unchanged"
    FALLBACK = "fallback"
    UNAVAILABLE = "unavailable"
    DIVERGED = "diverged"
    FAILED = "failed"


_SYNCED_ACTIONS = {
    NativeStackAction.CREATED,
    NativeStackAction.UPDATED,
    NativeStackAction.RECREATED,
    NativeStackAction.UNCHANGED,
}


@dataclass(frozen=True)
class NativeStackSyncResult:
    """Result of publishing a Shortcake stack as a native GitHub stack."""

    action: NativeStackAction
    stack_number: int | None = None
    message: str | None = None

    @property
    def synced(self) -> bool:
        return self.action in _SYNCED_ACTIONS

    def to_data(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "number": self.stack_number,
            "message": self.message,
        }


class NativeStackPreparationAction(str, Enum):
    """Outcome of checking for a native stack before PR mutations."""

    NONE = "none"
    UNSTACKED = "unstacked"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True)
class NativeStackPreparation:
    action: NativeStackPreparationAction
    remote_stack: NativeStack | None = None
    message: str | None = None

    @property
    def can_continue(self) -> bool:
        return self.action not in {
            NativeStackPreparationAction.BLOCKED,
            NativeStackPreparationAction.FAILED,
        }


def _is_prefix(prefix: list[int], values: list[int]) -> bool:
    return len(prefix) <= len(values) and values[: len(prefix)] == prefix


def _http_error_message(error: httpx.HTTPStatusError) -> str:
    try:
        message = error.response.json().get("message")
    except ValueError:
        message = None
    return str(message or error.response.status_code)


def _discover_stacks(gh: GitHubClient, pull_requests: list[int]) -> list[NativeStack]:
    """Find every distinct native stack containing one of the supplied PRs."""
    by_id: dict[int, NativeStack] = {}
    for pr_number in dict.fromkeys(pull_requests):
        for stack in gh.list_stacks(pull_request=pr_number):
            by_id[stack.id] = stack
    return list(by_id.values())


def get_native_stack_for_pr(gh: GitHubClient, pull_request: int) -> NativeStack | None:
    """Return the native stack containing a PR, if any."""
    stacks = gh.list_stacks(pull_request=pull_request)
    return stacks[0] if stacks else None


def fallback_native_stack(message: str) -> NativeStackSyncResult:
    """Return a deliberate PR-body fallback result."""
    return NativeStackSyncResult(NativeStackAction.FALLBACK, message=message)


def prepare_native_stack_restructure(
    gh: GitHubClient,
    ordered_existing_prs: list[int],
    *,
    owned_existing_prs: set[int],
    needs_restructure: bool,
    allow_recreate: bool,
) -> NativeStackPreparation:
    """Unstack before changing bases when a complete local stack owns the PRs.

    GitHub only supports appending to a stack. Reordering, inserting in the
    middle, moving, or removing a layer requires unstacking first. Scoped submit
    never does that because excluded PRs may be affected.
    """
    if not ordered_existing_prs:
        return NativeStackPreparation(NativeStackPreparationAction.NONE)

    try:
        stacks = _discover_stacks(gh, ordered_existing_prs)
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            return NativeStackPreparation(
                NativeStackPreparationAction.UNAVAILABLE,
                message=NATIVE_STACK_UNAVAILABLE_MESSAGE,
            )
        return NativeStackPreparation(
            NativeStackPreparationAction.FAILED,
            message=f"Could not inspect the GitHub stack: {_http_error_message(error)}",
        )
    except httpx.RequestError as error:
        return NativeStackPreparation(
            NativeStackPreparationAction.FAILED,
            message=f"Could not inspect the GitHub stack: {error}",
        )

    if not stacks:
        return NativeStackPreparation(NativeStackPreparationAction.NONE)
    if len(stacks) > 1:
        return NativeStackPreparation(
            NativeStackPreparationAction.BLOCKED,
            message="The selected PRs belong to multiple native GitHub stacks.",
        )

    remote = stacks[0]
    if not needs_restructure:
        return NativeStackPreparation(
            NativeStackPreparationAction.NONE,
            remote_stack=remote,
        )

    remote_open = set(remote.open_pr_numbers)
    if not remote_open.issubset(owned_existing_prs):
        unknown = sorted(remote_open - owned_existing_prs)
        return NativeStackPreparation(
            NativeStackPreparationAction.BLOCKED,
            remote_stack=remote,
            message=(
                "GitHub stack "
                f"#{remote.number} also contains unselected PRs "
                + ", ".join(f"#{number}" for number in unknown)
                + "."
            ),
        )
    if not allow_recreate:
        return NativeStackPreparation(
            NativeStackPreparationAction.BLOCKED,
            remote_stack=remote,
            message=(
                f"GitHub stack #{remote.number} must be recreated; "
                "run 'sc submit --stack' to update the whole stack."
            ),
        )

    try:
        gh.unstack(remote.number)
    except httpx.HTTPStatusError as error:
        if error.response.status_code != 404:
            return NativeStackPreparation(
                NativeStackPreparationAction.FAILED,
                remote_stack=remote,
                message=f"Could not unstack on GitHub: {_http_error_message(error)}",
            )
    except httpx.RequestError as error:
        return NativeStackPreparation(
            NativeStackPreparationAction.FAILED,
            remote_stack=remote,
            message=f"Could not unstack on GitHub: {error}",
        )

    return NativeStackPreparation(
        NativeStackPreparationAction.UNSTACKED,
        remote_stack=remote,
    )


def reconcile_native_stack(
    gh: GitHubClient,
    desired_prs: list[int],
    *,
    recreated: bool = False,
) -> NativeStackSyncResult:
    """Create, adopt, or append to a native stack without local state."""
    desired = list(dict.fromkeys(desired_prs))
    if not desired:
        return fallback_native_stack(NATIVE_STACK_MINIMUM_MESSAGE)
    if len(desired) > 100:
        return fallback_native_stack("GitHub native stacks support at most 100 PRs.")

    try:
        stacks = _discover_stacks(gh, desired)
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            return NativeStackSyncResult(
                NativeStackAction.UNAVAILABLE,
                message=NATIVE_STACK_UNAVAILABLE_MESSAGE,
            )
        return NativeStackSyncResult(
            NativeStackAction.FAILED,
            message=(
                f"Could not inspect native GitHub stacks: {_http_error_message(error)}"
            ),
        )
    except httpx.RequestError as error:
        return NativeStackSyncResult(
            NativeStackAction.FAILED,
            message=f"Could not inspect native GitHub stacks: {error}",
        )

    if len(stacks) > 1:
        return NativeStackSyncResult(
            NativeStackAction.DIVERGED,
            message="The pull requests belong to multiple native GitHub stacks.",
        )

    if not stacks:
        if len(desired) < 2:
            return fallback_native_stack(NATIVE_STACK_MINIMUM_MESSAGE)
        try:
            stack = gh.create_stack(desired)
        except httpx.HTTPStatusError as error:
            return NativeStackSyncResult(
                NativeStackAction.FAILED,
                message=(
                    "Could not create native GitHub stack: "
                    f"{_http_error_message(error)}"
                ),
            )
        except httpx.RequestError as error:
            return NativeStackSyncResult(
                NativeStackAction.FAILED,
                message=f"Could not create native GitHub stack: {error}",
            )
        return NativeStackSyncResult(
            NativeStackAction.RECREATED if recreated else NativeStackAction.CREATED,
            stack.number,
        )

    remote = stacks[0]
    current = remote.open_pr_numbers
    if current == desired or _is_prefix(desired, current):
        return NativeStackSyncResult(NativeStackAction.UNCHANGED, remote.number)

    if current and _is_prefix(current, desired):
        delta = desired[len(current) :]
        try:
            stack = gh.add_to_stack(remote.number, delta)
        except httpx.HTTPStatusError as error:
            return NativeStackSyncResult(
                NativeStackAction.FAILED,
                remote.number,
                f"Could not update native GitHub stack: {_http_error_message(error)}",
            )
        except httpx.RequestError as error:
            return NativeStackSyncResult(
                NativeStackAction.FAILED,
                remote.number,
                f"Could not update native GitHub stack: {error}",
            )
        return NativeStackSyncResult(NativeStackAction.UPDATED, stack.number)

    return NativeStackSyncResult(
        NativeStackAction.DIVERGED,
        remote.number,
        (
            f"GitHub stack #{remote.number} differs from the selected Shortcake "
            "stack and was left unchanged."
        ),
    )

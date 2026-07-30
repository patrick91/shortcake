from unittest.mock import MagicMock

import httpx

from shortcake._github import GitHubClient, NativeStack, NativeStackPullRequest
from shortcake._native_stack import (
    NATIVE_STACK_UNAVAILABLE_MESSAGE,
    NativeStackAction,
    NativeStackPreparationAction,
    NativeStackSyncResult,
    fallback_native_stack,
    get_native_stack_for_pr,
    prepare_native_stack_restructure,
    reconcile_native_stack,
)


def _stack(
    numbers: list[int],
    *,
    number: int = 7,
    stack_id: int | None = None,
    merged: set[int] | None = None,
) -> NativeStack:
    merged = merged or set()
    return NativeStack(
        id=stack_id if stack_id is not None else 9000 + number,
        number=number,
        node_id=f"S_{number}",
        url=f"https://api.github.com/repos/o/r/stacks/{number}",
        base_ref="main",
        is_open=any(pr not in merged for pr in numbers),
        created_at="2026-07-30T10:00:00Z",
        pull_requests=tuple(
            NativeStackPullRequest(
                number=pr,
                state="closed" if pr in merged else "open",
                is_draft=False,
                merged_at="2026-07-30T12:00:00Z" if pr in merged else None,
                head_ref=f"branch-{pr}",
                head_sha=f"sha-{pr}",
            )
            for pr in numbers
        ),
    )


def _http_error(status: int, message: str | None = "broken") -> httpx.HTTPStatusError:
    kwargs = (
        {"json": {"message": message}} if message is not None else {"text": "broken"}
    )
    response = httpx.Response(
        status,
        request=httpx.Request("GET", "https://api.github.com/test"),
        **kwargs,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        return error
    raise AssertionError("Expected HTTPStatusError")


def _client() -> MagicMock:
    return MagicMock(spec=GitHubClient)


def test_native_stack_result_serialization_and_synced_state() -> None:
    result = NativeStackSyncResult(NativeStackAction.CREATED, 7)
    fallback = fallback_native_stack("use PR bodies")

    assert result.synced is True
    assert result.to_data() == {
        "action": "created",
        "number": 7,
        "message": None,
    }
    assert fallback.synced is False
    assert fallback.message == "use PR bodies"


def test_get_native_stack_for_pr() -> None:
    gh = _client()
    native = _stack([10, 11])
    gh.list_stacks.side_effect = [[native], []]

    assert get_native_stack_for_pr(gh, 10) == native
    assert get_native_stack_for_pr(gh, 99) is None


def test_prepare_without_existing_prs_is_noop() -> None:
    result = prepare_native_stack_restructure(
        _client(),
        [],
        owned_existing_prs=set(),
        needs_restructure=True,
        allow_recreate=True,
    )

    assert result.action == NativeStackPreparationAction.NONE
    assert result.can_continue is True


def test_prepare_handles_unavailable_and_api_failures() -> None:
    unavailable = _client()
    unavailable.list_stacks.side_effect = _http_error(404)
    server_error = _client()
    server_error.list_stacks.side_effect = _http_error(500)
    network_error = _client()
    network_error.list_stacks.side_effect = httpx.RequestError("offline")

    unavailable_result = prepare_native_stack_restructure(
        unavailable,
        [10],
        owned_existing_prs={10},
        needs_restructure=True,
        allow_recreate=True,
    )
    server_result = prepare_native_stack_restructure(
        server_error,
        [10],
        owned_existing_prs={10},
        needs_restructure=True,
        allow_recreate=True,
    )
    network_result = prepare_native_stack_restructure(
        network_error,
        [10],
        owned_existing_prs={10},
        needs_restructure=True,
        allow_recreate=True,
    )

    assert unavailable_result.action == NativeStackPreparationAction.UNAVAILABLE
    assert unavailable_result.can_continue is True
    assert unavailable_result.message == NATIVE_STACK_UNAVAILABLE_MESSAGE
    assert server_result.action == NativeStackPreparationAction.FAILED
    assert server_result.can_continue is False
    assert server_result.message is not None and "broken" in server_result.message
    assert network_result.action == NativeStackPreparationAction.FAILED
    assert network_result.message is not None and "offline" in network_result.message


def test_prepare_handles_no_stack_multiple_stacks_and_no_restructure() -> None:
    no_stack = _client()
    no_stack.list_stacks.return_value = []
    multiple = _client()
    multiple.list_stacks.side_effect = [
        [_stack([10], number=1)],
        [_stack([11], number=2)],
    ]
    unchanged = _client()
    native = _stack([10, 11])
    unchanged.list_stacks.return_value = [native]

    no_stack_result = prepare_native_stack_restructure(
        no_stack,
        [10],
        owned_existing_prs={10},
        needs_restructure=True,
        allow_recreate=True,
    )
    multiple_result = prepare_native_stack_restructure(
        multiple,
        [10, 11],
        owned_existing_prs={10, 11},
        needs_restructure=True,
        allow_recreate=True,
    )
    unchanged_result = prepare_native_stack_restructure(
        unchanged,
        [10, 11],
        owned_existing_prs={10, 11},
        needs_restructure=False,
        allow_recreate=True,
    )

    assert no_stack_result.action == NativeStackPreparationAction.NONE
    assert multiple_result.action == NativeStackPreparationAction.BLOCKED
    assert multiple_result.can_continue is False
    assert unchanged_result.action == NativeStackPreparationAction.NONE
    assert unchanged_result.remote_stack == native


def test_prepare_refuses_unknown_or_scoped_prs() -> None:
    unknown = _client()
    unknown.list_stacks.return_value = [_stack([10, 11])]
    scoped = _client()
    scoped.list_stacks.return_value = [_stack([10])]

    unknown_result = prepare_native_stack_restructure(
        unknown,
        [10],
        owned_existing_prs={10},
        needs_restructure=True,
        allow_recreate=True,
    )
    scoped_result = prepare_native_stack_restructure(
        scoped,
        [10],
        owned_existing_prs={10},
        needs_restructure=True,
        allow_recreate=False,
    )

    assert unknown_result.action == NativeStackPreparationAction.BLOCKED
    assert unknown_result.message is not None and "#11" in unknown_result.message
    assert scoped_result.action == NativeStackPreparationAction.BLOCKED
    assert (
        scoped_result.message is not None
        and "sc submit --stack" in scoped_result.message
    )


def test_prepare_unstacks_owned_remote_stack() -> None:
    gh = _client()
    native = _stack([10, 11])
    gh.list_stacks.return_value = [native]
    gh.unstack.return_value = None

    result = prepare_native_stack_restructure(
        gh,
        [10, 11],
        owned_existing_prs={10, 11},
        needs_restructure=True,
        allow_recreate=True,
    )

    assert result.action == NativeStackPreparationAction.UNSTACKED
    assert result.remote_stack == native
    gh.unstack.assert_called_once_with(7)


def test_prepare_tolerates_already_removed_stack() -> None:
    gh = _client()
    gh.list_stacks.return_value = [_stack([10, 11])]
    gh.unstack.side_effect = _http_error(404)

    result = prepare_native_stack_restructure(
        gh,
        [10, 11],
        owned_existing_prs={10, 11},
        needs_restructure=True,
        allow_recreate=True,
    )

    assert result.action == NativeStackPreparationAction.UNSTACKED


def test_prepare_reports_unstack_failures() -> None:
    server_error = _client()
    server_error.list_stacks.return_value = [_stack([10, 11])]
    server_error.unstack.side_effect = _http_error(500)
    network_error = _client()
    network_error.list_stacks.return_value = [_stack([10, 11])]
    network_error.unstack.side_effect = httpx.RequestError("offline")

    server_result = prepare_native_stack_restructure(
        server_error,
        [10, 11],
        owned_existing_prs={10, 11},
        needs_restructure=True,
        allow_recreate=True,
    )
    network_result = prepare_native_stack_restructure(
        network_error,
        [10, 11],
        owned_existing_prs={10, 11},
        needs_restructure=True,
        allow_recreate=True,
    )

    assert server_result.action == NativeStackPreparationAction.FAILED
    assert server_result.message is not None and "broken" in server_result.message
    assert network_result.action == NativeStackPreparationAction.FAILED
    assert network_result.message is not None and "offline" in network_result.message


def test_reconcile_falls_back_for_unsupported_sizes() -> None:
    too_small_client = _client()
    too_small_client.list_stacks.return_value = []
    too_small = reconcile_native_stack(too_small_client, [10])
    too_large = reconcile_native_stack(_client(), list(range(101)))

    assert too_small.action == NativeStackAction.FALLBACK
    assert too_small.message is not None and "at least two" in too_small.message
    assert too_large.action == NativeStackAction.FALLBACK
    assert too_large.message is not None and "at most 100" in too_large.message


def test_reconcile_preserves_stack_with_one_open_pr() -> None:
    gh = _client()
    gh.list_stacks.return_value = [_stack([10, 11], merged={10})]

    result = reconcile_native_stack(gh, [11])

    assert result.action == NativeStackAction.UNCHANGED
    assert result.stack_number == 7
    gh.create_stack.assert_not_called()


def test_reconcile_handles_discovery_failures() -> None:
    unavailable = _client()
    unavailable.list_stacks.side_effect = _http_error(404)
    server_error = _client()
    server_error.list_stacks.side_effect = _http_error(500, None)
    network_error = _client()
    network_error.list_stacks.side_effect = httpx.RequestError("offline")

    unavailable_result = reconcile_native_stack(unavailable, [10, 11])
    server_result = reconcile_native_stack(server_error, [10, 11])
    network_result = reconcile_native_stack(network_error, [10, 11])

    assert unavailable_result.action == NativeStackAction.UNAVAILABLE
    assert unavailable_result.message == NATIVE_STACK_UNAVAILABLE_MESSAGE
    assert server_result.action == NativeStackAction.FAILED
    assert server_result.message is not None and "500" in server_result.message
    assert network_result.action == NativeStackAction.FAILED
    assert network_result.message is not None and "offline" in network_result.message


def test_reconcile_refuses_prs_in_multiple_stacks() -> None:
    gh = _client()
    gh.list_stacks.side_effect = [
        [_stack([10], number=1)],
        [_stack([11], number=2)],
    ]

    result = reconcile_native_stack(gh, [10, 11])

    assert result.action == NativeStackAction.DIVERGED
    assert result.synced is False


def test_reconcile_creates_or_recreates_stack() -> None:
    created = _client()
    created.list_stacks.return_value = []
    created.create_stack.return_value = _stack([10, 11])
    recreated = _client()
    recreated.list_stacks.return_value = []
    recreated.create_stack.return_value = _stack([10, 11], number=9)

    created_result = reconcile_native_stack(created, [10, 10, 11])
    recreated_result = reconcile_native_stack(recreated, [10, 11], recreated=True)

    assert created_result.action == NativeStackAction.CREATED
    assert created_result.stack_number == 7
    created.create_stack.assert_called_once_with([10, 11])
    assert recreated_result.action == NativeStackAction.RECREATED
    assert recreated_result.stack_number == 9


def test_reconcile_reports_create_failures() -> None:
    server_error = _client()
    server_error.list_stacks.return_value = []
    server_error.create_stack.side_effect = _http_error(422)
    network_error = _client()
    network_error.list_stacks.return_value = []
    network_error.create_stack.side_effect = httpx.RequestError("offline")

    server_result = reconcile_native_stack(server_error, [10, 11])
    network_result = reconcile_native_stack(network_error, [10, 11])

    assert server_result.action == NativeStackAction.FAILED
    assert server_result.message is not None and "broken" in server_result.message
    assert network_result.action == NativeStackAction.FAILED
    assert network_result.message is not None and "offline" in network_result.message


def test_reconcile_adopts_matching_or_remote_superset() -> None:
    exact = _client()
    exact.list_stacks.return_value = [_stack([10, 11])]
    superset = _client()
    superset.list_stacks.return_value = [_stack([10, 11, 12])]

    exact_result = reconcile_native_stack(exact, [10, 11])
    superset_result = reconcile_native_stack(superset, [10, 11])

    assert exact_result == NativeStackSyncResult(NativeStackAction.UNCHANGED, 7)
    assert superset_result == NativeStackSyncResult(NativeStackAction.UNCHANGED, 7)


def test_reconcile_appends_only_new_top_prs() -> None:
    gh = _client()
    gh.list_stacks.return_value = [_stack([10, 11])]
    gh.add_to_stack.return_value = _stack([10, 11, 12])

    result = reconcile_native_stack(gh, [10, 11, 12])

    assert result == NativeStackSyncResult(NativeStackAction.UPDATED, 7)
    gh.add_to_stack.assert_called_once_with(7, [12])


def test_reconcile_reports_append_failures() -> None:
    server_error = _client()
    server_error.list_stacks.return_value = [_stack([10])]
    server_error.add_to_stack.side_effect = _http_error(422)
    network_error = _client()
    network_error.list_stacks.return_value = [_stack([10])]
    network_error.add_to_stack.side_effect = httpx.RequestError("offline")

    server_result = reconcile_native_stack(server_error, [10, 11])
    network_result = reconcile_native_stack(network_error, [10, 11])

    assert server_result.action == NativeStackAction.FAILED
    assert server_result.stack_number == 7
    assert server_result.message is not None and "broken" in server_result.message
    assert network_result.action == NativeStackAction.FAILED
    assert network_result.message is not None and "offline" in network_result.message


def test_reconcile_leaves_divergent_stack_unchanged() -> None:
    gh = _client()
    gh.list_stacks.return_value = [_stack([11, 10])]

    result = reconcile_native_stack(gh, [10, 11])

    assert result.action == NativeStackAction.DIVERGED
    assert result.stack_number == 7
    assert result.message is not None and "left unchanged" in result.message
